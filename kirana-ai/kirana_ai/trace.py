"""
Step-by-step tracing of a query, printed to the terminal.

Off by default. Turn it on with `--trace` on either entry point, or by
setting RAG_TRACE=1. RAG_TRACE_FULL=1 additionally disables truncation of
long bodies such as prompts.

The point is to make every boundary visible: what text was embedded, what
filter Qdrant received, what prompt the model saw, what it asked for back.
"""
import json
import os
import sys
import time
from contextlib import contextmanager
from typing import Any

WIDTH = 78
_INDENT = "  "

_enabled = os.getenv("RAG_TRACE", "").lower() in {"1", "true", "yes"}
_full = os.getenv("RAG_TRACE_FULL", "").lower() in {"1", "true", "yes"}
_step = 0


def enable(on: bool = True, full: bool = False) -> None:
    global _enabled, _full
    _enabled = on
    _full = _full or full


def is_on() -> bool:
    return _enabled


def is_full() -> bool:
    """True when RAG_TRACE_FULL / --trace-full asked for untruncated output."""
    return _full


def reset() -> None:
    """Restart step numbering — one query, one sequence."""
    global _step
    _step = 0


def _w(text: str = "") -> None:
    print(text, file=sys.stdout)


def _truncate(text: str, limit: int = 1200) -> str:
    if _full or len(text) <= limit:
        return text
    return f"{text[:limit]}\n... [{len(text) - limit} more chars; RAG_TRACE_FULL=1 to show]"


def section(title: str, level: int = 0) -> None:
    """A numbered step header. level>0 marks a nested sub-step."""
    global _step
    if not _enabled:
        return
    pad = _INDENT * level
    if level == 0:
        _step += 1
        label = f"[{_step}] {title}"
        _w()
        _w(f"{'━' * 3} {label} {'━' * max(3, WIDTH - len(label) - 5)}")
    else:
        _w(f"{pad}┌─ {title}")


def kv(key: str, value: Any, level: int = 0) -> None:
    if not _enabled:
        return
    _w(f"{_INDENT * level}{key:<18}: {value}")


def body(label: str, text: str, level: int = 0, limit: int = 1200) -> None:
    """A multi-line block, gutter-marked so it is obviously verbatim data."""
    if not _enabled:
        return
    pad = _INDENT * level
    _w(f"{pad}{label} ({len(text)} chars):")
    for line in _truncate(text, limit).splitlines():
        _w(f"{pad}│ {line}")


def bullets(label: str, items: list[str], level: int = 0) -> None:
    if not _enabled:
        return
    pad = _INDENT * level
    _w(f"{pad}{label}:")
    for item in items:
        _w(f"{pad}│ {item}")


def result(summary: str, elapsed: float | None = None, level: int = 0) -> None:
    """Closing line of a step: what came back, and how long it took."""
    if not _enabled:
        return
    pad = _INDENT * level
    timing = f"{elapsed * 1000:.0f} ms  " if elapsed is not None else ""
    _w(f"{pad}← {timing}{summary}")


@contextmanager
def timed():
    """Yields a one-element list that receives the elapsed seconds."""
    holder: list[float] = [0.0]
    start = time.perf_counter()
    try:
        yield holder
    finally:
        holder[0] = time.perf_counter() - start


def preview_vector(vector: list[float], n: int = 6) -> str:
    head = ", ".join(f"{v:+.4f}" for v in vector[:n])
    norm = sum(v * v for v in vector) ** 0.5
    return f"dim={len(vector)}  [{head}, ...]  |v|={norm:.4f}"


def describe_filter(query_filter: Any) -> list[str]:
    """Render a Qdrant Filter as readable lines instead of a pydantic dump."""
    if query_filter is None:
        return ["(none)"]
    lines: list[str] = []
    for condition in getattr(query_filter, "must", None) or []:
        key = getattr(condition, "key", "?")
        match = getattr(condition, "match", None)
        rng = getattr(condition, "range", None)
        if match is not None:
            if getattr(match, "value", None) is not None:
                lines.append(f"{key} == {match.value!r}")
            elif getattr(match, "any", None) is not None:
                lines.append(f"{key} in {list(match.any)!r}")
            else:
                lines.append(f"{key} ~ {match}")
        elif rng is not None:
            parts = [f"{op} {getattr(rng, op)}" for op in ("gte", "lte", "gt", "lt")
                     if getattr(rng, op, None) is not None]
            lines.append(f"{key} range({', '.join(parts)})")
        else:
            lines.append(f"{key} {condition}")
    return lines or ["(empty)"]


def compact_json(value: Any, limit: int = 300) -> str:
    try:
        text = json.dumps(value, default=str, ensure_ascii=False)
    except Exception:
        text = str(value)
    return text if _full or len(text) <= limit else text[:limit] + "..."


def usage_of(raw: Any) -> str | None:
    """Best-effort token usage, whatever the provider calls it."""
    meta = getattr(raw, "usage_metadata", None) or getattr(raw, "usage", None)
    if meta is None:
        return None
    fields = (
        ("prompt_token_count", "input_tokens", "prompt_tokens"),
        ("candidates_token_count", "output_tokens", "completion_tokens"),
        ("total_token_count", "total_tokens"),
    )
    labels = ("in", "out", "total")
    parts = []
    for label, names in zip(labels, fields):
        for name in names:
            value = getattr(meta, name, None)
            if value is not None:
                parts.append(f"{label}={value}")
                break
    return "  ".join(parts) or None
