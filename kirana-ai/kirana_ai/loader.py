"""
Step 1 of the pipeline: read raw documents.

A "document" here is just a dict — the pipeline needs no richer type.

Phase 0-1 read Markdown and text files from kb/seed/. Phase 2 swaps the
source for the MinIO bucket and adds PDFs; everything after this step stays
the same, because it only ever sees these dicts.
"""
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from kirana_ai import config

SUPPORTED_EXTENSIONS = {".md", ".txt"}

_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)


def _doc_type(stem: str) -> str:
    """Map a filename to a coarse document class using config.DOC_TYPE_RULES."""
    lowered = stem.lower()
    for prefix, doc_type in config.DOC_TYPE_RULES:
        if lowered.startswith(prefix):
            return doc_type
    return config.DEFAULT_DOC_TYPE


def load_documents(kb_dir: Path) -> list[dict]:
    """
    Return one dict per file:
        {
          "id": "policy-returns",          # filename without extension
          "source": "policy-returns.md",   # shown to the user in citations
          "text": "...full file text...",
          "metadata": {"doc_type": "policy", "title": "...", ...}
        }
    """
    if not kb_dir.exists():
        raise SystemExit(f"Knowledge-base directory not found: {kb_dir}")

    ingested_at = datetime.now(timezone.utc)
    documents = []
    for path in sorted(kb_dir.iterdir()):
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            print(f"  skipping empty file: {path.name}")
            continue

        metadata: dict = {"doc_type": _doc_type(path.stem)}
        title = _H1_RE.search(text)
        if title:
            metadata["title"] = title.group(1).strip()
        metadata.update(
            {
                # Lets a later ingest skip documents that have not changed (Phase 2).
                "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
                "ingested_at": ingested_at.isoformat(timespec="seconds"),
                "ingested_ts": int(ingested_at.timestamp()),
            }
        )

        documents.append(
            {"id": path.stem, "source": path.name, "text": text, "metadata": metadata}
        )

    if not documents:
        raise SystemExit(f"No .md or .txt files with content found in {kb_dir}")
    return documents
