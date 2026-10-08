"""
Long-term memory (Phase 7 M4): what a shopper asked us to remember, across every chat.

    write   remember_preference(text) → policy: WRITE → "Save to your memory?" card → the shopper's click
            → save(): a row in ai.memories (source of truth), then a point in Qdrant user_memories
    read    for_prompt(): all active memories if they fit MEMORY_BUDGET_TOKENS; otherwise the pinned
            ones plus the most relevant to this message, found in Qdrant and RE-CHECKED in Postgres
    delete  delete(): the row is marked deleted (from that moment it is never used), then the point goes

Two stores, two jobs (AD39). Postgres answers "what is remembered, who agreed, can it be deleted";
Qdrant answers "which memories matter for this message". Qdrant is derived and can lag (an upsert or
a delete that failed): every search hit is checked against Postgres, so a deleted or expired memory
can never reach the prompt, and reindex() repairs the index from Postgres.

Why not always search (AD40)? Searching means embedding the message: an API call, ~600 ms. A shopper
with a handful of memories just gets all of them: faster, cheaper, and nothing is missed.

Known gap: deleting a memory stops it being loaded, but a thread's summary or old messages may
still mention it. A complete "forget" (rewriting affected summaries) is left for Phase 8 (privacy).

What may be remembered is the shopper's own words, confirmed by a click. Nothing here reads documents
or tool results: a memory written from retrieved text would be an injection replayed into every
future chat (memory poisoning).
"""
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from qdrant_client.http import models
from sqlalchemy import func, select

from kirana_ai import config, embeddings, sparse, vector_store
from kirana_ai.db import session_scope
from kirana_ai.db.models import Memory

log = logging.getLogger("kirana_ai.memory")

INDEXES = {"user_id": models.PayloadSchemaType.INTEGER, "doc_id": models.PayloadSchemaType.KEYWORD}


@dataclass
class MemoryView:
    id: uuid.UUID
    text: str
    kind: str
    pinned: bool
    created_at: datetime


class MemoryNotFound(Exception):
    """Missing, deleted or someone else's: the same answer (404)."""


def _view(m: Memory) -> MemoryView:
    return MemoryView(id=m.id, text=m.text, kind=m.kind, pinned=m.pinned, created_at=m.created_at)


def tokens(text: str) -> int:
    """Rough token count (~4 characters each, plus a list marker): enough for a budget."""
    return len(text) // 4 + 4


# --- write / delete ----------------------------------------------------------------------------

def find_active(user_id: int, text: str) -> MemoryView | None:
    """This shopper's active memory with exactly this text (case-insensitive), if any."""
    with session_scope() as session:
        row = session.scalars(select(Memory).where(
            Memory.user_id == user_id, Memory.status == "active",
            func.lower(Memory.text) == text.strip().lower())).first()   # exact (not ILIKE: % and _ are wildcards)
        return _view(row) if row else None


def save(user_id: int, text: str, kind: str = "preference", source_thread: uuid.UUID | None = None,
         approval_id: uuid.UUID | None = None, replaces: uuid.UUID | None = None,
         with_replaced: bool = False):
    """
    Add a memory, or update one (replaces): the new row and the old one's deletion commit together,
    so the shopper never has both, or neither. Returns the view (and, with with_replaced, the text
    of the memory it replaced, or None).
    """
    replaced_text = None
    with session_scope() as session:
        if replaces is not None:
            old = session.get(Memory, replaces, with_for_update=True)
            if old is not None and old.user_id == user_id and old.status == "active":
                old.status = "deleted"
                replaced_text = old.text
            else:
                replaces = None                   # gone or not theirs: nothing to replace
        existing = session.scalars(select(Memory).where(
            Memory.user_id == user_id, Memory.status == "active",
            func.lower(Memory.text) == text.strip().lower())).first()
        if existing:                              # saying it twice doesn't store it twice
            view = _view(existing)
        else:
            row = Memory(user_id=user_id, text=text.strip(), kind=kind, status="active",
                         source_thread=source_thread, approval_id=approval_id,
                         confirmed_at=datetime.now(timezone.utc))
            session.add(row)
            session.flush()
            view = _view(row)
            existing = None
    if existing is None:
        _index(view, user_id)                     # after the commit: the row is what counts
    if replaces is not None:
        try:
            vector_store.delete_document_points(vector_store.get_client(), str(replaces), config.MEMORIES_COLLECTION)
        except Exception as exc:   # noqa: BLE001 - reads re-check Postgres; reindex removes it
            log.warning("replaced memory %s still indexed: %s", replaces, exc)
    return (view, replaced_text) if with_replaced else view


def _index(view: MemoryView, user_id: int) -> None:
    """Best effort: a failure leaves the row unindexed until reindex(); reads still check Postgres."""
    try:
        client = vector_store.get_client()
        vector_store.ensure_collection(client, config.MEMORIES_COLLECTION, INDEXES)
        point = {"chunk_id": f"memory-{view.id}", "doc_id": str(view.id), "memory_id": str(view.id),
                 "user_id": user_id, "text": view.text}
        sparse_vectors = sparse.encode_documents([view.text]) if config.HYBRID_SEARCH else None
        vector_store.upsert_chunks(client, [point], embeddings.embed_documents([point]), sparse_vectors,
                                   collection=config.MEMORIES_COLLECTION)
    except Exception as exc:   # noqa: BLE001
        log.warning("memory %s saved but not indexed (reindex will fix it): %s", view.id, exc)


def delete(user_id: int, memory_id: uuid.UUID) -> None:
    with session_scope() as session:
        row = session.get(Memory, memory_id)
        if row is None or row.user_id != user_id or row.status != "active":
            raise MemoryNotFound()
        row.status = "deleted"            # from this commit on, it is never loaded
    try:
        vector_store.delete_document_points(vector_store.get_client(), str(memory_id), config.MEMORIES_COLLECTION)
    except Exception as exc:   # noqa: BLE001 - the Postgres check already hides it
        log.warning("memory %s deleted but its point remains (reindex will remove it): %s", memory_id, exc)


def set_pinned(user_id: int, memory_id: uuid.UUID, pinned: bool) -> MemoryView:
    with session_scope() as session:
        row = session.get(Memory, memory_id)
        if row is None or row.user_id != user_id or row.status != "active":
            raise MemoryNotFound()
        row.pinned = pinned
        return _view(row)


def list_for(user_id: int) -> list[MemoryView]:
    """Active, unexpired memories: pinned first, then newest."""
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        rows = session.scalars(select(Memory).where(
            Memory.user_id == user_id, Memory.status == "active",
            (Memory.expires_at.is_(None)) | (Memory.expires_at > now),
        ).order_by(Memory.pinned.desc(), Memory.created_at.desc())).all()
        return [_view(r) for r in rows]


# --- read for the prompt -----------------------------------------------------------------------

@dataclass
class Recall:
    memories: list[MemoryView]
    mode: str          # "none", "all" (fit the budget) or "search" (pinned + most relevant)


def for_prompt(user_id: int | None, message: str) -> Recall:
    if user_id is None:
        return Recall([], "none")
    active = list_for(user_id)
    if not active:
        return Recall([], "none")
    if sum(tokens(m.text) for m in active) <= config.MEMORY_BUDGET_TOKENS:
        return Recall(active, "all")

    chosen = [m for m in active if m.pinned]
    used = sum(tokens(m.text) for m in chosen)
    by_id = {str(m.id): m for m in active}
    try:
        hits = vector_store.search(
            vector_store.get_client(), embeddings.embed_text(message), config.MEMORY_TOP_K * 2,
            query_filter=models.Filter(must=[models.FieldCondition(key="user_id", match=models.MatchValue(value=user_id))]),
            sparse_vector=sparse.encode_query(message) if config.HYBRID_SEARCH else None,
            collection=config.MEMORIES_COLLECTION)
    except Exception as exc:   # noqa: BLE001 - without the index, the pinned ones still go in
        log.warning("memory search unavailable, pinned memories only: %s", exc)
        hits = []
    added = 0
    for hit in hits:
        m = by_id.get(str(hit.get("memory_id")))          # the Postgres check: active, this user, unexpired
        if m is None or m in chosen or used + tokens(m.text) > config.MEMORY_BUDGET_TOKENS:
            continue
        chosen.append(m)
        used += tokens(m.text)
        added += 1
        if added >= config.MEMORY_TOP_K:
            break
    return Recall(chosen, "search")


# --- repair --------------------------------------------------------------------------------------

def reindex() -> dict[str, int]:
    """Make Qdrant match Postgres: index active memories that are missing, remove points for anything else."""
    client = vector_store.get_client()
    vector_store.ensure_collection(client, config.MEMORIES_COLLECTION, INDEXES)
    indexed = vector_store.indexed_doc_ids(client, config.MEMORIES_COLLECTION)
    with session_scope() as session:
        active = {str(m.id): (m.user_id, _view(m)) for m in session.scalars(
            select(Memory).where(Memory.status == "active")).all()}
    added = removed = 0
    for mid, (user_id, view) in active.items():
        if mid not in indexed:
            _index(view, user_id)
            added += 1
    for mid in indexed - set(active):
        vector_store.delete_document_points(client, mid, config.MEMORIES_COLLECTION)
        removed += 1
    return {"active": len(active), "indexed_now": added, "removed": removed}
