"""
The ingest worker (Phase 2 M3–M4): Kafka kb.documents.v1 → parsed, chunked, embedded → Qdrant.

    python -m kirana_ai.worker          # one member of consumer group kirana-ai-ingest

Run two and Kafka shares the topic's 3 partitions between them.

Delivery is at-least-once. The offset is committed only after a message is fully
handled (indexed, deleted, marked failed, or dead-lettered), so a worker killed
half-way gets the same message again on restart. That is safe because handling is
idempotent:
  - chunk point ids are deterministic, and a document's old chunks are deleted
    before its new ones are written: replaying an upload gives the same index;
  - unchanged text (same content hash) is not re-embedded;
  - a delete of something already gone is a no-op.

Failures are split in two, because retrying only helps one of them:
  - permanent (no text in the PDF, bad metadata): marked `failed` with the reason, no retry;
  - transient (embedding API, Qdrant, Postgres, MinIO down): retried in place with backoff,
    then sent to kb.documents.v1-dlt and marked `failed`. `cli redrive` replays it later.
Retrying in place blocks the partition for a few seconds, and that is the point: the
next event on this partition may be the same document's delete, and it must not overtake.
"""
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import unquote

from minio.error import S3Error

from kirana_ai import chunker, config, consumer, embeddings, loader, sparse, storage, vector_store
# Shared with every worker; re-exported for callers that used them from here.
from kirana_ai.consumer import PermanentError, dlt_producer, produce_confirmed, retrying  # noqa: F401
from kirana_ai.db import session_scope
from kirana_ai.db.models import DocStatus, DocType, Document

log = logging.getLogger("kirana_ai.worker")

GROUP = "kirana-ai-ingest"
RETRY_ATTEMPTS = 3
RETRY_BASE_SECONDS = 1.0


@dataclass(frozen=True)
class KbEvent:
    kind: str                       # "created" | "removed"
    document_id: uuid.UUID
    object_key: str                 # decoded: {document_id}/{file_name}
    metadata: dict = field(default_factory=dict)   # x-amz-meta-* without the prefix, lower-case
    size: int | None = None
    etag: str | None = None

    @property
    def file_name(self) -> str:
        return self.object_key.split("/", 1)[1]


# --- reading MinIO's event ------------------------------------------------------------

def parse_events(value: bytes) -> list[KbEvent]:
    """MinIO's S3-style event JSON -> our events. Malformed input is permanent: retrying won't fix it."""
    try:
        records = json.loads(value)["Records"]
    except (ValueError, KeyError, TypeError) as exc:
        raise PermanentError(f"not a MinIO bucket event: {exc}") from exc
    events = []
    for record in records:
        name = record.get("eventName", "")
        obj = record.get("s3", {}).get("object", {})
        key = unquote(obj.get("key", ""))           # MinIO URL-encodes the key (checked in M1)
        head, _, tail = key.partition("/")
        try:
            document_id = uuid.UUID(head)
        except ValueError as exc:
            raise PermanentError(f"object key {key!r} does not start with a document id") from exc
        if not tail:
            raise PermanentError(f"object key {key!r} has no file name")
        # Keys arrive canonicalised (X-Amz-Meta-Doc-Type): normalise, and decode the title.
        meta = {k.lower().removeprefix("x-amz-meta-"): v for k, v in (obj.get("userMetadata") or {}).items()}
        if "title" in meta:
            meta["title"] = unquote(meta["title"])
        if name.startswith("s3:ObjectCreated"):
            events.append(KbEvent("created", document_id, key, meta, obj.get("size"), obj.get("eTag")))
        elif name.startswith("s3:ObjectRemoved"):
            events.append(KbEvent("removed", document_id, key))
        else:
            log.info("ignoring %s for %s", name, key)
    return events


# --- the work ---------------------------------------------------------------------------

def handle(event: KbEvent) -> str:
    return ingest(event) if event.kind == "created" else remove(event)


def ingest(event: KbEvent) -> str:
    doc = _document_for(event)
    if doc.status in (DocStatus.DELETED, DocStatus.DELETING):
        # Deleted before this upload event was processed: the delete event cleans up.
        return f"skipped: document is {doc.status.value}"
    _update(doc.id, status=DocStatus.INDEXING, size_bytes=event.size or doc.size_bytes,
            etag=event.etag, error=None)

    try:
        response = storage.client().get_object(config.KB_BUCKET, event.object_key)
        data = response.read()
        response.close()
        response.release_conn()
    except S3Error as exc:
        if exc.code == "NoSuchKey":
            # Deleted since the upload: the delete event, behind this one, will finish the job.
            return "skipped: the file is already gone"
        raise

    try:
        text, page_starts = loader.parse_bytes(data, doc.content_type)
    except loader.UnreadableDocument as exc:
        raise PermanentError(str(exc)) from exc

    digest = loader.content_hash(text)
    if digest == doc.content_hash and doc.chunk_count:
        # Same text as what is indexed (a replayed event, or a re-upload of an identical file).
        _update(doc.id, status=DocStatus.READY)
        return "unchanged: same text already indexed"

    now = datetime.now(timezone.utc)
    source = {
        "id": str(doc.id),
        "source": event.file_name,
        "text": text,
        "page_starts": page_starts,
        "metadata": {"doc_type": doc.doc_type.value, "title": doc.title, "content_hash": digest,
                     "ingested_at": now.isoformat(timespec="seconds"), "ingested_ts": int(now.timestamp())},
    }
    chunks = chunker.chunk_documents([source], config.CHUNK_SIZE, config.CHUNK_OVERLAP)
    vectors = embeddings.embed_documents(chunks)
    sparse_vectors = sparse.encode_documents([c["text"] for c in chunks]) if config.HYBRID_SEARCH else None

    client = vector_store.get_client()
    vector_store.ensure_collection(client)
    # Delete-then-upsert: a shorter new version must not leave the old version's tail chunks.
    vector_store.delete_document_points(client, str(doc.id))
    vector_store.upsert_chunks(client, chunks, vectors, sparse_vectors)

    _update(doc.id, status=DocStatus.READY, chunk_count=len(chunks),
            page_count=len(page_starts) or None, content_hash=digest, error=None)
    return f"indexed: {len(chunks)} chunk(s)" + (f", {len(page_starts)} page(s)" if page_starts else "")


def remove(event: KbEvent) -> str:
    vector_store.delete_document_points(vector_store.get_client(), str(event.document_id))
    with session_scope() as session:
        doc = session.get(Document, event.document_id)
        if doc is not None and doc.status != DocStatus.DELETED:
            doc.status = DocStatus.DELETED
    return "removed from the index"


def _document_for(event: KbEvent) -> Document:
    """
    The document row for an upload event.

    No row means the file was put into the bucket without our API (an admin's `mc cp`).
    AD14: accept it when its metadata is complete and consistent; otherwise record it as failed.
    """
    with session_scope() as session:
        doc = session.get(Document, event.document_id)
        if doc is not None:
            return doc
        meta = event.metadata
        problem = None
        if meta.get("document-id") != str(event.document_id):
            problem = "metadata document-id is missing or does not match the object key"
        elif meta.get("doc-type") not in {t.value for t in DocType}:
            problem = f"metadata doc-type {meta.get('doc-type')!r} is not policy, faq or guide"
        elif not meta.get("title"):
            problem = "metadata title is missing"
        doc = Document(
            id=event.document_id,
            title=(meta.get("title") or event.file_name)[:200],
            doc_type=DocType(meta["doc-type"]) if problem is None else DocType.GUIDE,
            file_name=event.file_name,
            object_key=event.object_key,
            content_type=meta.get("content-type") or "application/octet-stream",
            size_bytes=event.size,
            status=DocStatus.UPLOADED,
            uploaded_by=meta.get("uploaded-by") or "out-of-band",
            upload_expires_at=datetime.now(timezone.utc),
        )
        session.add(doc)
    if problem:
        raise PermanentError(f"uploaded without the API, and {problem}")
    return doc


def _update(doc_id: uuid.UUID, **values) -> None:
    with session_scope() as session:
        doc = session.get(Document, doc_id)
        if doc is not None:
            for name, value in values.items():
                setattr(doc, name, value)


def mark_failed(document_id: uuid.UUID | None, reason: str) -> None:
    if document_id is not None:
        _update(document_id, status=DocStatus.FAILED, error=reason[:1000])


# --- one Kafka message ---------------------------------------------------------------------

def process(value: bytes, dead_letter, sleep=time.sleep) -> str:
    """
    Handle one message completely: afterwards its offset can be committed, whatever happened.

    `dead_letter(reason, attempts)` sends the original message to the DLT.
    """
    try:
        events = parse_events(value)
    except PermanentError as exc:
        dead_letter(str(exc), 0)      # nothing to mark: we don't even know the document
        return f"dead-lettered: {exc}"
    outcomes = [
        retrying(lambda event=event: handle(event), attempts=RETRY_ATTEMPTS, base_seconds=RETRY_BASE_SECONDS,
                 dead_letter=dead_letter, on_failed=lambda reason, event=event: mark_failed(event.document_id, reason),
                 sleep=sleep)
        for event in events
    ]
    return "; ".join(outcomes) or "nothing to do"


def run() -> None:
    # A consumer declares the topics it depends on (idempotent), like Kirana's KafkaConfig does,
    # instead of relying on a one-time `cli kafka-setup`. Found when a Kafka reset removed them and
    # the worker waited silently on "Unknown topic" while MinIO buffered the upload event.
    from kirana_ai import kafka
    for topic, outcome in kafka.ensure_topics().items():
        log.info("topic %s: %s", topic, outcome)
    consumer.run(config.KB_TOPIC, GROUP, config.KB_DLT, process)


if __name__ == "__main__":
    run()
