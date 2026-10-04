"""
Step 4 of the pipeline: store vectors in Qdrant and search them.

Each Qdrant "point" = one chunk:
    id      -> a UUID derived from chunk_id (Qdrant requires int or UUID ids)
    vector  -> the embedding
    payload -> chunk_id, doc_id, source, chunk_index, text, tenant_id,
               doc_type, heading, title, ...  (plain metadata)

Payload fields used in filters get an index. Without one Qdrant still
filters correctly, but by scanning rather than by lookup.
"""
import uuid
from functools import cache

from qdrant_client import QdrantClient
from qdrant_client.http import models
from qdrant_client.http.exceptions import UnexpectedResponse

from kirana_ai import config, trace
from kirana_ai.errors import UpstreamUnavailable


@cache
def get_client() -> QdrantClient:
    """
    One client per process. Creating it makes no network call; a Qdrant that
    is down shows up on the first real request, as UpstreamUnavailable.
    """
    return QdrantClient(url=config.QDRANT_URL, timeout=5)


def _unavailable(exc: Exception) -> UpstreamUnavailable:
    if isinstance(exc, UnexpectedResponse) and exc.status_code == 404:
        return UpstreamUnavailable(
            "knowledge base",
            f"collection {config.COLLECTION_NAME!r} does not exist. "
            "Run `python -m kirana_ai.cli seed-kb` and start the worker, or `cli reindex`.",
        )
    return UpstreamUnavailable(
        "qdrant",
        f"cannot reach Qdrant at {config.QDRANT_URL} ({type(exc).__name__}). "
        "Is it running?  cd infra && docker compose up -d qdrant",
    )


# How deep each branch searches before fusion. Fusion needs candidates
# below top_k to reorder; too shallow and RRF has nothing to work with.
PREFETCH_LIMIT = 20

# Payload fields we filter on, and the index type each needs.
PAYLOAD_INDEXES = {
    "tenant_id": models.PayloadSchemaType.KEYWORD,
    "doc_id": models.PayloadSchemaType.KEYWORD,
    "doc_type": models.PayloadSchemaType.KEYWORD,
    "source": models.PayloadSchemaType.KEYWORD,
    "ingested_ts": models.PayloadSchemaType.INTEGER,
}


# Each point carries two vectors under these names.
DENSE = "dense"    # meaning   — Gemini embedding, cosine
SPARSE = "sparse"  # words     — BM25 weights, see src/sparse.py


def ensure_collection(client: QdrantClient) -> None:
    """
    Create the collection if missing.

    Two named vectors per point. The sparse one sets `Modifier.IDF`, which
    tells Qdrant to compute inverse document frequency itself at query time —
    so adding a document does not invalidate the vectors already stored.
    """
    if not client.collection_exists(config.COLLECTION_NAME):
        client.create_collection(
            collection_name=config.COLLECTION_NAME,
            vectors_config={
                DENSE: models.VectorParams(
                    size=config.EMBEDDING_DIM, distance=models.Distance.COSINE
                )
            },
            sparse_vectors_config={
                SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)
            },
        )
    elif config.HYBRID_SEARCH:
        _require_sparse_schema(client)
    ensure_payload_indexes(client)


def _require_sparse_schema(client: QdrantClient) -> None:
    """
    A collection built before hybrid search has no sparse vector, and writing
    one into it fails deep inside the client with an opaque 400. Fail here
    instead, with the fix in the message.
    """
    params = client.get_collection(config.COLLECTION_NAME).config.params
    if SPARSE in (params.sparse_vectors or {}):
        return
    raise SystemExit(
        f"Collection {config.COLLECTION_NAME!r} predates hybrid search: it has no "
        f"{SPARSE!r} vector.\n"
        "Drop it in the Qdrant dashboard (http://localhost:6335/dashboard), then\n"
        "  python -m kirana_ai.cli reindex"
    )


def ensure_payload_indexes(client: QdrantClient) -> None:
    """Create a payload index per filterable field. Safe to call repeatedly."""
    for field_name, field_schema in PAYLOAD_INDEXES.items():
        try:
            client.create_payload_index(
                collection_name=config.COLLECTION_NAME,
                field_name=field_name,
                field_schema=field_schema,
            )
        except Exception:
            pass  # already present


def _point_id(chunk_id: str) -> str:
    # Deterministic UUID: re-ingesting the same chunk overwrites instead of duplicating.
    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))


def upsert_chunks(
    client: QdrantClient,
    chunks: list[dict],
    vectors: list[list[float]],
    sparse_vectors: list[models.SparseVector] | None = None,
) -> int:
    """Write one point per chunk, carrying its dense and (optionally) BM25 vector."""
    sparse_vectors = sparse_vectors or [None] * len(chunks)
    points = []
    for chunk, dense, sparse_vec in zip(chunks, vectors, sparse_vectors, strict=True):
        vector: dict = {DENSE: dense}
        if sparse_vec is not None:
            vector[SPARSE] = sparse_vec
        points.append(
            models.PointStruct(id=_point_id(chunk["chunk_id"]), vector=vector, payload=chunk)
        )
    client.upsert(collection_name=config.COLLECTION_NAME, points=points)
    return len(points)


def _query(client, query_vector, top_k, query_filter, sparse_vector):
    """The Qdrant call behind search(), dense or hybrid."""
    if sparse_vector is None:
        # Dense only: one similarity search over the meaning vectors.
        result = client.query_points(
            collection_name=config.COLLECTION_NAME,
            query=query_vector,
            using=DENSE,
            limit=top_k,
            query_filter=query_filter,
            with_payload=True,
        )
    else:
        # Hybrid: run both searches, then let Qdrant fuse the two ranked
        # lists with Reciprocal Rank Fusion. Each branch fetches deeper
        # than top_k so fusion has something to work with, and the filter
        # is applied to BOTH — a filter that leaked on one branch would be
        # a tenancy bug, not just a quality issue.
        result = client.query_points(
            collection_name=config.COLLECTION_NAME,
            prefetch=[
                models.Prefetch(query=query_vector, using=DENSE,
                                limit=PREFETCH_LIMIT, filter=query_filter),
                models.Prefetch(query=sparse_vector, using=SPARSE,
                                limit=PREFETCH_LIMIT, filter=query_filter),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=top_k,
            with_payload=True,
        )
    return result


def search(
    client: QdrantClient,
    query_vector: list[float],
    top_k: int,
    query_filter: models.Filter | None = None,
    sparse_vector: models.SparseVector | None = None,
) -> list[dict]:
    """
    Return the top_k best chunks, each with its score.

    Pass `sparse_vector` to run hybrid retrieval: dense and BM25 search in
    parallel, fused by Reciprocal Rank Fusion. Omit it for dense only.

    `query_filter` is applied during the search (pre-filter), so top_k chunks
    come from the matching subset — not from an unfiltered top_k that is then
    trimmed down.
    """
    if trace.is_on():
        trace.section("QDRANT SEARCH")
        trace.kv("collection", config.COLLECTION_NAME)
        trace.kv("limit (top_k)", top_k)
        trace.kv("mode", "hybrid (dense + BM25, RRF)" if sparse_vector is not None else "dense only")
        trace.kv("query vector", trace.preview_vector(query_vector))
        trace.bullets("filter (must)", trace.describe_filter(query_filter))

    with trace.timed() as elapsed:
        try:
            result = _query(client, query_vector, top_k, query_filter, sparse_vector)
        except Exception as exc:
            raise _unavailable(exc) from exc

    chunks = [{**hit.payload, "score": hit.score} for hit in result.points]
    if trace.is_on():
        trace.result(f"{len(chunks)} hit(s)", elapsed[0])
        trace.bullets(
            "results",
            [
                f"{i}. score={c['score']:.3f}  {c['source']}"
                f"{' > ' + c['heading'] if c.get('heading') else ''}"
                f"  (chunk {c['chunk_index']})"
                for i, c in enumerate(chunks, 1)
            ]
            or ["(none — filter matched nothing)"],
        )
    return chunks


# Document-level fields surfaced when enumerating the corpus.
_DOC_FIELDS = ("source", "title", "doc_type")


def list_documents(
    client: QdrantClient,
    query_filter: models.Filter | None = None,
    limit: int = 500,
) -> list[dict]:
    """
    Enumerate the distinct documents matching a filter.

    Uses `scroll`, not vector search: this answers "what exists" exactly,
    where a top_k similarity search can only ever return its best k guesses
    and cannot tell the caller whether anything was left out.
    """
    if trace.is_on():
        trace.section("QDRANT SCROLL (enumerate)")
        trace.kv("collection", config.COLLECTION_NAME)
        trace.kv("scan limit", limit)
        trace.bullets("filter (must)", trace.describe_filter(query_filter))

    with trace.timed() as elapsed:
        try:
            points, _ = client.scroll(
                collection_name=config.COLLECTION_NAME,
                scroll_filter=query_filter,
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )
        except Exception as exc:
            raise _unavailable(exc) from exc

    documents: dict[str, dict] = {}
    for point in points:
        payload = point.payload or {}
        doc_id = payload.get("doc_id", "?")
        entry = documents.get(doc_id)
        if entry is None:
            entry = {"doc_id": doc_id, "chunks": 0}
            entry.update({f: payload[f] for f in _DOC_FIELDS if payload.get(f)})
            documents[doc_id] = entry
        entry["chunks"] += 1

    result = sorted(documents.values(), key=lambda d: d["doc_id"])
    if trace.is_on():
        trace.result(f"{len(points)} chunk(s) -> {len(result)} document(s)", elapsed[0])
        trace.bullets(
            "documents",
            [
                f"{d['doc_id']}  ({d.get('doc_type', '?')}, {d['chunks']} chunks)"
                for d in result
            ]
            or ["(none)"],
        )
    return result


def delete_document_points(client: QdrantClient, doc_id: str) -> None:
    """Remove every chunk of one document (by payload doc_id). A no-op if it has none."""
    try:
        client.delete(
            collection_name=config.COLLECTION_NAME,
            points_selector=models.FilterSelector(filter=models.Filter(must=[
                models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id)),
            ])),
            wait=True,
        )
    except Exception as exc:
        raise _unavailable(exc) from exc


def indexed_doc_ids(client: QdrantClient) -> set[str]:
    """Every doc_id that has chunks in the collection (for reconciliation)."""
    ids: set[str] = set()
    offset = None
    try:
        while True:
            points, offset = client.scroll(collection_name=config.COLLECTION_NAME, limit=1000,
                                           offset=offset, with_payload=["doc_id"], with_vectors=False)
            ids.update(p.payload["doc_id"] for p in points if p.payload)
            if offset is None:
                return ids
    except Exception as exc:
        raise _unavailable(exc) from exc
