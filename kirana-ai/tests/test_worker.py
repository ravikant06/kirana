"""
The ingest worker against a real Postgres, with MinIO, Qdrant and the embedding API faked.

Kafka itself is not involved: `worker.process()` is everything that happens for one
message between poll() and commit(), and that is what these tests drive.
"""
import json
import uuid

import pytest
from minio.error import S3Error
from sqlalchemy import text

from kirana_ai import embeddings, kb, loader, storage, vector_store, worker
from kirana_ai.db.models import DocType

POLICY = b"# Returns Policy\n\nUnopened rice can be returned within 7 days of delivery.\n"


def minimal_pdf(pages: list[str]) -> bytes:
    """A real, tiny PDF with one line of text per page (Helvetica), built by hand."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None,
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for line in pages:
        stream = f"BT /F1 12 Tf 72 720 Td ({line}) Tj ET".encode()
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream.decode()}\nendstream")
        content_ref = len(objs)
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                    f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_ref} 0 R >>")
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return out


class FakeObject:
    def __init__(self, data): self.data = data
    def read(self): return self.data
    def close(self): pass
    def release_conn(self): pass


class FakeMinio:
    """Files in a dict. Signing policies is local work, so it is delegated to the real client."""
    def __init__(self, real): self.files = {}; self.real = real
    def presigned_post_policy(self, policy): return self.real.presigned_post_policy(policy)
    def get_object(self, bucket, key):
        if key not in self.files:
            raise S3Error(None, "NoSuchKey", "gone", key, "r", "h")
        return FakeObject(self.files[key])


class FakeQdrant:
    """Records what the worker does to the index, per document."""
    def __init__(self): self.chunks = {}; self.upserts = 0; self.fail = 0
    def delete(self, _client, doc_id): self.chunks.pop(doc_id, None)
    def upsert(self, _client, chunks, _vectors, _sparse):
        if self.fail:
            self.fail -= 1
            raise ConnectionError("qdrant down")
        self.upserts += 1
        for c in chunks:
            self.chunks.setdefault(c["doc_id"], []).append(c)
        return len(chunks)


@pytest.fixture
def env(ai_db, monkeypatch):
    with ai_db.begin() as conn:
        conn.execute(text("TRUNCATE documents"))
    minio, qdrant = FakeMinio(storage.client()), FakeQdrant()
    monkeypatch.setattr(storage, "client", lambda: minio)
    monkeypatch.setattr(vector_store, "get_client", lambda: object())
    monkeypatch.setattr(vector_store, "ensure_collection", lambda _c: None)
    monkeypatch.setattr(vector_store, "delete_document_points", qdrant.delete)
    monkeypatch.setattr(vector_store, "upsert_chunks", qdrant.upsert)
    monkeypatch.setattr(embeddings, "embed_documents", lambda chunks: [[0.0, 1.0]] * len(chunks))
    monkeypatch.setattr(worker, "RETRY_BASE_SECONDS", 0)
    return ai_db, minio, qdrant


def _uploaded(minio, data=POLICY, content_type="text/markdown", file_name="returns.md"):
    """What the API does (a pending row + signed policy), then the browser's upload."""
    doc, ticket = kb.create_upload("Returns Policy", DocType.POLICY, file_name, content_type,
                                   len(data), uploaded_by="admin")
    minio.files[ticket["object_key"]] = data
    return doc, ticket


def _event(kind, key, meta=None, size=10):
    name = "s3:ObjectCreated:Post" if kind == "created" else "s3:ObjectRemoved:Delete"
    obj = {"key": key.replace("/", "%2F"), "size": size, "eTag": "abc"}
    if meta is not None:
        obj["userMetadata"] = meta
    return json.dumps({"Records": [{"eventName": name, "s3": {"object": obj}}]}).encode()


def _status(db, doc_id):
    with db.connect() as conn:
        return conn.execute(text("SELECT status, chunk_count, page_count, error FROM documents WHERE id = :i"),
                            {"i": str(doc_id)}).one()


def _no_dlt(reason, attempts):
    pytest.fail(f"unexpected dead letter: {reason}")


def test_upload_event_indexes_the_document(env):
    db, minio, qdrant = env
    doc, t = _uploaded(minio)

    outcome = worker.process(_event("created", t["object_key"]), _no_dlt)

    assert outcome.startswith("indexed")
    row = _status(db, doc.id)
    assert row.status == "ready" and row.chunk_count >= 1 and row.error is None
    chunk = qdrant.chunks[str(doc.id)][0]
    assert chunk["source"] == "returns.md" and chunk["title"] == "Returns Policy"
    assert chunk["doc_type"] == "policy" and chunk["tenant_id"] == "kirana"


def test_replayed_event_is_harmless(env):
    """At-least-once: the same event twice (worker killed before commit) leaves the same index."""
    db, minio, qdrant = env
    doc, t = _uploaded(minio)
    worker.process(_event("created", t["object_key"]), _no_dlt)
    before = [c["chunk_id"] for c in qdrant.chunks[str(doc.id)]]

    outcome = worker.process(_event("created", t["object_key"]), _no_dlt)

    assert outcome.startswith("unchanged")                  # same text: not even re-embedded
    assert [c["chunk_id"] for c in qdrant.chunks[str(doc.id)]] == before
    assert qdrant.upserts == 1


def test_new_version_replaces_old_chunks(env):
    db, minio, qdrant = env
    doc, t = _uploaded(minio, data=POLICY * 30)              # long: many chunks
    worker.process(_event("created", t["object_key"]), _no_dlt)
    minio.files[t["object_key"]] = POLICY                    # re-uploaded, much shorter

    worker.process(_event("created", t["object_key"]), _no_dlt)

    assert len(qdrant.chunks[str(doc.id)]) == _status(db, doc.id).chunk_count == 1


def test_delete_event_removes_chunks_and_marks_deleted(env):
    db, minio, qdrant = env
    doc, t = _uploaded(minio)
    worker.process(_event("created", t["object_key"]), _no_dlt)

    worker.process(_event("removed", t["object_key"]), _no_dlt)

    assert str(doc.id) not in qdrant.chunks
    assert _status(db, doc.id).status == "deleted"


def test_upload_event_for_a_deleted_document_is_ignored(env):
    db, minio, qdrant = env
    doc, t = _uploaded(minio)
    with db.begin() as conn:
        conn.execute(text("UPDATE documents SET status = 'deleted' WHERE id = :i"), {"i": str(doc.id)})

    assert worker.process(_event("created", t["object_key"]), _no_dlt).startswith("skipped")
    assert qdrant.chunks == {}


def test_pdf_pages_reach_the_chunks(env):
    db, minio, qdrant = env
    pdf = minimal_pdf(["Returns within 7 days.", "Refunds within 5 days."])
    doc, t = _uploaded(minio, data=pdf, content_type="application/pdf", file_name="r.pdf")

    worker.process(_event("created", t["object_key"]), _no_dlt)

    assert _status(db, doc.id).page_count == 2
    assert qdrant.chunks[str(doc.id)][0]["page"] == 1


def test_scanned_pdf_fails_once_without_retry_or_dead_letter(env):
    db, minio, qdrant = env
    doc, t = _uploaded(minio, data=minimal_pdf(["", ""]), content_type="application/pdf")

    outcome = worker.process(_event("created", t["object_key"]), _no_dlt)

    row = _status(db, doc.id)
    assert outcome.startswith("failed") and row.status == "failed"
    assert "scanned" in row.error


def test_transient_failure_is_retried_then_succeeds(env):
    db, minio, qdrant = env
    qdrant.fail = 2                                          # down for 2 attempts, up for the 3rd
    doc, t = _uploaded(minio)

    assert worker.process(_event("created", t["object_key"]), _no_dlt).startswith("indexed")
    assert _status(db, doc.id).status == "ready"


def test_persistent_failure_goes_to_the_dead_letter_topic(env):
    db, minio, qdrant = env
    qdrant.fail = 99
    doc, t = _uploaded(minio)
    dead = []

    outcome = worker.process(_event("created", t["object_key"]),
                             lambda reason, attempts: dead.append((reason, attempts)))

    assert dead == [("ConnectionError: qdrant down", worker.RETRY_ATTEMPTS)]
    assert outcome.startswith("dead-lettered")
    row = _status(db, doc.id)
    assert row.status == "failed" and "redrive" in row.error


def test_malformed_message_is_dead_lettered_immediately(env):
    dead = []
    worker.process(b"not json", lambda reason, attempts: dead.append(attempts))
    assert dead == [0]


def test_out_of_band_upload_with_valid_metadata_is_accepted(env):
    """AD14: an admin's `mc cp` with complete metadata becomes a document."""
    db, minio, qdrant = env
    doc_id = uuid.uuid4()
    key = f"{doc_id}/faq.md"
    minio.files[key] = POLICY
    meta = {"X-Amz-Meta-Document-Id": str(doc_id), "X-Amz-Meta-Title": "Store%20FAQ",
            "X-Amz-Meta-Doc-Type": "faq", "content-type": "text/markdown"}

    worker.process(_event("created", key, meta), _no_dlt)

    with db.connect() as conn:
        row = conn.execute(text("SELECT title, doc_type, status, uploaded_by FROM documents")).one()
    assert tuple(row) == ("Store FAQ", "faq", "ready", "out-of-band")


def test_out_of_band_upload_without_metadata_is_recorded_as_failed(env):
    db, minio, qdrant = env
    doc_id = uuid.uuid4()
    minio.files[f"{doc_id}/x.md"] = POLICY

    worker.process(_event("created", f"{doc_id}/x.md", {}), _no_dlt)

    row = _status(db, doc_id)
    assert row.status == "failed" and "metadata" in row.error
    assert qdrant.chunks == {}


def test_event_parsing_handles_minio_quirks():
    doc_id = uuid.uuid4()
    (e,) = worker.parse_events(_event("created", f"{doc_id}/a.md",
                                      {"X-Amz-Meta-Title": "Diwali%20%E2%82%B9299"}))
    assert e.object_key == f"{doc_id}/a.md"                 # URL-encoded key decoded
    assert e.metadata["title"] == "Diwali ₹299"             # canonicalised key, decoded title
    with pytest.raises(worker.PermanentError):
        worker.parse_events(_event("created", "not-a-uuid/a.md"))


def test_page_offsets_survive_a_blank_first_page():
    text_, starts = loader.parse_bytes(minimal_pdf(["", "Second page text."]), "application/pdf")
    assert text_[starts[1]:].startswith("Second page text.")


class FakeProducer:
    def __init__(self, error=None, stuck=0): self.error, self.stuck = error, stuck
    def produce(self, topic, on_delivery, **kwargs): self.cb = on_delivery
    def flush(self, timeout):
        self.cb(self.error, None)
        return self.stuck


def test_dead_letter_write_must_be_confirmed():
    """A rejected or timed-out dead-letter write must raise, so the offset is never committed."""
    worker.produce_confirmed(FakeProducer(), "t", key=b"k", value=b"v")          # delivered: fine
    with pytest.raises(RuntimeError, match="could not write"):
        worker.produce_confirmed(FakeProducer(error="broker down"), "t", key=b"k", value=b"v")
    with pytest.raises(RuntimeError, match="timed out"):
        worker.produce_confirmed(FakeProducer(stuck=1), "t", key=b"k", value=b"v")


def test_failed_dead_letter_write_escapes_process(env):
    """process() must not swallow it: the caller would commit a message that went nowhere."""
    db, minio, qdrant = env
    qdrant.fail = 99
    _, t = _uploaded(minio)

    def broken_dlt(reason, attempts):
        raise RuntimeError("could not write to kb.documents.v1-dlt")

    with pytest.raises(RuntimeError):
        worker.process(_event("created", t["object_key"]), broken_dlt)
