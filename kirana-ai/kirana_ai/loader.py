"""
Step 1 of the pipeline: read raw documents.

A "document" here is just a dict — the pipeline needs no richer type.

Phase 0-1 read Markdown and text files from kb/seed/. Phase 2 swaps the
source for the MinIO bucket and adds PDFs; everything after this step stays
the same, because it only ever sees these dicts.
"""
import hashlib
import io
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


class UnreadableDocument(Exception):
    """The file can never be indexed as it is (no text layer, corrupt, wrong type). Not worth a retry."""


def parse_bytes(data: bytes, content_type: str) -> tuple[str, list[int]]:
    """
    An uploaded file's text, plus the offset where each page starts (empty for text files).

    PDF pages are joined with a blank line, so the chunker's paragraph-boundary logic
    treats a page break like a paragraph break, and a chunk's start offset tells which
    page it came from.
    """
    if content_type == "application/pdf":
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
        try:
            reader = PdfReader(io.BytesIO(data))
            pages = [(page.extract_text() or "").strip() for page in reader.pages]
        except (PdfReadError, ValueError, KeyError) as exc:
            raise UnreadableDocument(f"not a readable PDF ({exc})") from exc
        starts, parts, offset = [], [], 0
        for text in pages:
            starts.append(offset)
            parts.append(text)
            offset += len(text) + 2
        # Not stripped: the page offsets above index into exactly this string.
        text = "\n\n".join(parts)
        if not text.strip():
            raise UnreadableDocument(
                f"no text found in {len(pages)} page(s): probably a scanned image. "
                "Upload a PDF with selectable text, or run OCR first."
            )
        return text, starts
    try:
        text = data.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise UnreadableDocument("not valid UTF-8 text") from exc
    if not text:
        raise UnreadableDocument("the file is empty")
    return text, []


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def describe(stem: str, text: str) -> dict:
    """Title (the first # heading) and doc_type (from the file name) of a seed document."""
    meta: dict = {"doc_type": _doc_type(stem)}
    title = _H1_RE.search(text)
    meta["title"] = title.group(1).strip() if title else stem
    return meta


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

        metadata = describe(path.stem, text)
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
