"""
Step 3 of the pipeline: turn text into vectors with the Gemini embedding API.

Uses the official `google-genai` SDK (`from google import genai`).
Gemini-only for now; an EmbeddingAdapter comes with the model-migration phase.
"""
from functools import cache

from google import genai
from google.genai import types

from kirana_ai import config, trace

# Gemini lets you tell the model what the embedding is for. Documents and
# queries get slightly different treatment, which improves retrieval.
_DOC_TASK = "RETRIEVAL_DOCUMENT"
_QUERY_TASK = "RETRIEVAL_QUERY"

_BATCH_SIZE = 100  # API limit on strings per embed_content call


@cache
def _client() -> genai.Client:
    """One shared client, created on first use so importing this module needs no API key."""
    return genai.Client(api_key=config.require_api_key())


def _embed(texts: list[str], task_type: str) -> list[list[float]]:
    vectors = []
    for i in range(0, len(texts), _BATCH_SIZE):
        batch = texts[i : i + _BATCH_SIZE]
        if trace.is_on():
            trace.section(f"EMBEDDING -> {config.EMBEDDING_MODEL}")
            trace.kv("task_type", task_type)
            trace.kv("dimensions", config.EMBEDDING_DIM)
            trace.kv("batch size", len(batch))
            trace.bullets("input", [f"{t[:100]!r}" for t in batch[:3]])

        with trace.timed() as elapsed:
            try:
                response = _client().models.embed_content(
                    model=config.EMBEDDING_MODEL,
                    contents=batch,
                    config=types.EmbedContentConfig(
                        task_type=task_type,
                        output_dimensionality=config.EMBEDDING_DIM,
                    ),
                )
            except Exception as exc:  # network / auth / quota problems
                raise SystemExit(f"Gemini embedding call failed: {exc}") from exc

        batch_vectors = [e.values for e in response.embeddings]
        if trace.is_on() and batch_vectors:
            trace.result(trace.preview_vector(batch_vectors[0]), elapsed[0])
        vectors.extend(batch_vectors)
    return vectors


def embed_text(text: str) -> list[float]:
    """Embed a single user question."""
    return _embed([text], _QUERY_TASK)[0]


def embed_documents(chunks: list[dict]) -> list[list[float]]:
    """Embed the `text` field of every chunk, preserving order."""
    return _embed([c["text"] for c in chunks], _DOC_TASK)
