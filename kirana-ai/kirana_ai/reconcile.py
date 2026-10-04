"""
Operations on the knowledge base that don't come from events (Phase 2 M4).

    seed()      upload kb/seed/*.md into MinIO, through the same path as the admin UI
    redrive()   replay kb.documents.v1-dlt onto kb.documents.v1, once the cause is fixed
    reindex()   compare MinIO, ai.documents and Qdrant, and repair every difference

Why reindex exists at all, when events drive everything: at-least-once delivery says an
event is not lost *once Kafka has it*. Before that it can be (MinIO's queue_dir is local
to one MinIO node; a bucket rule can be misconfigured; a topic can be deleted; a bug can
mark an event done without doing it). The sources of truth are the files in MinIO; the
index is derived data. A derived index needs a way to be rebuilt from its source, and
that is this module. Every action is idempotent, so it is safe to run next to the worker.
"""
import io
import time
from datetime import datetime, timezone

from sqlalchemy import select

from kirana_ai import config, kb, loader, storage, vector_store, worker
from kirana_ai.db import session_scope
from kirana_ai.db.models import DocStatus, DocType, Document

SEED_TYPES = {".md": "text/markdown", ".txt": "text/plain"}


# --- seed ---------------------------------------------------------------------------------

def seed() -> list[str]:
    """Upload each seed file once (skipped if a live document with that file name exists)."""
    with session_scope() as session:
        existing = set(session.scalars(
            select(Document.file_name).where(Document.status != DocStatus.DELETED)))
    done = []
    for path in sorted(config.KB_DIR.iterdir()):
        content_type = SEED_TYPES.get(path.suffix.lower())
        if content_type is None:
            continue
        if path.name in existing:
            done.append(f"skip   {path.name} (already in the knowledge base)")
            continue
        data = path.read_bytes()
        meta = loader.describe(path.stem, data.decode("utf-8"))
        doc, ticket = kb.create_upload(meta["title"], DocType(meta["doc_type"]),
                                       path.name, content_type, len(data), uploaded_by="seed")
        # Server-side put, with the same key and metadata the signed browser upload would carry.
        fields = ticket["form_fields"]
        storage.client().put_object(
            config.KB_BUCKET, ticket["object_key"], io.BytesIO(data), len(data),
            content_type=content_type,
            metadata={k: v for k, v in fields.items() if k.startswith("x-amz-meta-")},
        )
        done.append(f"upload {path.name} -> {ticket['object_key']}")
    return done


# --- redrive ------------------------------------------------------------------------------

def redrive(quiet_seconds: float = 5.0) -> int:
    """Copy every waiting dead letter back to its original topic. Returns how many."""
    from confluent_kafka import Consumer

    consumer = Consumer(worker.consumer_config(group="kirana-ai-redrive"))
    producer = worker.dlt_producer()
    consumer.subscribe([config.KB_DLT])
    moved = 0
    deadline = time.monotonic() + quiet_seconds
    try:
        while time.monotonic() < deadline:
            msg = consumer.poll(1.0)
            if msg is None or msg.error():
                continue
            headers = dict(msg.headers() or [])
            target = headers.get("original-topic", config.KB_TOPIC.encode()).decode()
            # Back to the *original partition*, not wherever our client would hash the key.
            # "Same key -> same partition" holds only within one partitioner: MinIO's Kafka
            # client and librdkafka hash keys differently, so producing by key alone put a
            # re-driven upload on another partition than its document's later delete event,
            # which could then overtake it (seen in the M4 drill).
            partition = int(headers["original-partition"]) if "original-partition" in headers else -1
            worker.produce_confirmed(producer, target, key=msg.key(), value=msg.value(),
                                     partition=partition,
                                     headers=[("redriven-from", config.KB_DLT.encode())])
            consumer.commit(message=msg, asynchronous=False)   # only after it is safely re-sent
            moved += 1
            deadline = time.monotonic() + quiet_seconds
    finally:
        consumer.close()
    return moved


# --- reindex --------------------------------------------------------------------------------

def reindex(dry_run: bool = False) -> list[str]:
    """Make Qdrant and ai.documents agree with the files in MinIO. Returns what was (or would be) done."""
    mc = storage.client()
    objects = {o.object_name: o for o in mc.list_objects(config.KB_BUCKET, recursive=True)}
    with session_scope() as session:
        rows = {d.object_key: d for d in session.scalars(
            select(Document).where(Document.status != DocStatus.DELETED))}
    client = vector_store.get_client()
    indexed = vector_store.indexed_doc_ids(client) if client.collection_exists(config.COLLECTION_NAME) else set()

    actions: list[str] = []

    def act(description: str, fn) -> None:
        actions.append(("would " if dry_run else "") + description)
        if not dry_run:
            try:
                outcome = fn()
                if outcome:
                    actions[-1] += f" -> {outcome}"
            except Exception as exc:
                actions[-1] += f" -> error: {type(exc).__name__}: {exc}"

    # 1. Files in MinIO that are not indexed (lost event, failed earlier, never processed).
    for key, obj in objects.items():
        row = rows.get(key)
        doc_id = key.split("/", 1)[0]
        needs = row is None or row.status != DocStatus.READY or doc_id not in indexed
        if needs:
            act(f"index  {key} ({'no row' if row is None else row.status.value})",
                lambda key=key: _reingest(_event_from_object(mc, key)))

    # 2. Rows whose file is gone: drop their chunks and mark them deleted.
    now = datetime.now(timezone.utc)
    for key, row in rows.items():
        if key in objects:
            continue
        if row.status == DocStatus.PENDING and row.upload_expires_at > now:
            continue                      # the browser may still be uploading
        act(f"delete {key} ({row.status.value}, file missing)",
            lambda row=row: worker.remove(worker.KbEvent("removed", row.id, row.object_key)))

    # 3. Chunks in Qdrant that belong to no file (e.g. Phase 0's local seed ingest).
    live_ids = {key.split("/", 1)[0] for key in objects}
    for doc_id in sorted(indexed - live_ids):
        act(f"purge  chunks of {doc_id} (no file)",
            lambda doc_id=doc_id: vector_store.delete_document_points(client, doc_id) or "purged")

    return actions or ["nothing to do: MinIO, ai.documents and Qdrant agree"]


def _reingest(event: "worker.KbEvent") -> str:
    try:
        return worker.ingest(event)
    except worker.PermanentError as exc:
        worker.mark_failed(event.document_id, str(exc))
        return f"failed: {exc}"


def _event_from_object(mc, key: str) -> "worker.KbEvent":
    """Rebuild the upload event from the object itself: its metadata lives on the object."""
    stat = mc.stat_object(config.KB_BUCKET, key)
    meta = {k.lower().removeprefix("x-amz-meta-"): v for k, v in (stat.metadata or {}).items()
            if k.lower().startswith("x-amz-meta-") or k.lower() == "content-type"}
    if "title" in meta:
        from urllib.parse import unquote
        meta["title"] = unquote(meta["title"])
    return worker.KbEvent("created", worker.uuid.UUID(key.split("/", 1)[0]), key, meta,
                          stat.size, stat.etag)
