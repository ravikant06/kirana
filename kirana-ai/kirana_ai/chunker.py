"""
Step 2 of the pipeline: split each document into overlapping chunks.

Why chunk at all?
  - Embedding models have input limits, and one vector for a whole document
    blurs many topics together. Smaller pieces give sharper, more
    retrievable vectors.

Why overlap?
  - A sentence that straddles a chunk boundary would otherwise be cut in
    half and lose meaning. Overlap repeats the tail of one chunk at the head
    of the next so no idea is split cleanly in two.

CHUNK_SIZE / CHUNK_OVERLAP are measured in characters. As a rough rule,
1 token ~ 4 characters of English, so 800 chars ~ 200 tokens.

Chunks also carry metadata (heading, tenant, doc fields). That
metadata is stored in the Qdrant payload only — it is never added to `text`,
so chunk boundaries and embeddings are unaffected by it.
"""
import re

from kirana_ai import config

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$", re.MULTILINE)


def _chunk_spans(text: str, chunk_size: int, overlap: int) -> list[tuple[int, int]]:
    """The sliding-window boundary logic, returned as (start, end) offsets."""
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")

    spans = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))

        # Prefer to cut at a paragraph or sentence end within the last 20% of the window.
        if end < len(text):
            window = text[start:end]
            cut = max(window.rfind("\n\n"), window.rfind(". "), window.rfind("\n"))
            if cut > chunk_size * 0.8:
                end = start + cut + 1

        if text[start:end].strip():
            spans.append((start, end))

        if end >= len(text):
            break
        start = end - overlap  # step back so neighbours share `overlap` chars
    return spans


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Sliding window over the text. Tries to end each chunk at a newline
    or sentence boundary so chunks read naturally, but never exceeds chunk_size."""
    return [text[s:e].strip() for s, e in _chunk_spans(text, chunk_size, overlap)]


def _heading_offsets(text: str) -> list[tuple[int, int, str]]:
    """Every Markdown heading as (offset, level, title), in document order."""
    return [(m.start(), len(m.group(1)), m.group(2)) for m in _HEADING_RE.finditer(text)]


def _heading_for(offset: int, headings: list[tuple[int, int, str]]) -> tuple[str | None, str | None]:
    """
    Nearest heading at or above `offset`, plus the H1 > ... > Hn trail.

    A chunk starting mid-section inherits that section's heading, which is how
    "within 48 hours" keeps its link to "Fresh produce".
    """
    trail: dict[int, str] = {}
    current = None
    for pos, level, title in headings:
        if pos > offset:
            break
        trail = {lvl: t for lvl, t in trail.items() if lvl < level}
        trail[level] = title
        current = title
    if current is None:
        return None, None
    path = " > ".join(trail[lvl] for lvl in sorted(trail))
    return current, path


def chunk_documents(
    documents: list[dict],
    chunk_size: int,
    overlap: int,
    tenant_id: str | None = None,
    batch_id: str | None = None,
) -> list[dict]:
    """
    Turn documents into chunk dicts. Each chunk keeps a pointer to its source
    so we can cite it later, plus filterable metadata:
        {"chunk_id": "policy-returns-2", "source": "policy-returns.md",
         "doc_id": "policy-returns", "chunk_index": 2, "text": "...",
         "tenant_id": "kirana", "doc_type": "policy",
         "heading": "Fresh produce",
         "heading_path": "Returns Policy > Fresh produce", ...}
    """
    tenant_id = tenant_id or config.TENANT_ID
    all_chunks = []

    for doc in documents:
        text = doc["text"]
        headings = _heading_offsets(text)
        doc_meta = doc.get("metadata", {})

        for i, (start, end) in enumerate(_chunk_spans(text, chunk_size, overlap)):
            piece = text[start:end].strip()
            heading, heading_path = _heading_for(start, headings)

            chunk = {
                # --- identity ---
                "chunk_id": f"{doc['id']}-{i}",
                "doc_id": doc["id"],
                "source": doc["source"],
                "chunk_index": i,
                "text": piece,
                # --- tenancy ---
                "tenant_id": tenant_id,
                # --- structure ---
                "doc_type": doc_meta.get("doc_type", config.DEFAULT_DOC_TYPE),
                # --- lifecycle ---
                "content_hash": doc_meta.get("content_hash"),
                "ingested_at": doc_meta.get("ingested_at"),
                "ingested_ts": doc_meta.get("ingested_ts"),
                "char_count": len(piece),
            }
            if batch_id:
                chunk["batch_id"] = batch_id
            if heading:
                chunk["heading"] = heading
                chunk["heading_path"] = heading_path

            if "title" in doc_meta:
                chunk["title"] = doc_meta["title"]

            all_chunks.append(chunk)

    return all_chunks
