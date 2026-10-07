"""
Product search (Phase 4 M3): what runs behind the search_products tool.

    retrieve (hybrid, ~30)  ->  [rerank (cross-encoder), off by default: AD22]  ->  hydrate live from Kirana
        ->  drop out of stock, drop over max_price  ->  top 5

Why each step:
  - retrieve wide: dense + BM25 finds candidates by meaning and by exact words ("ragi", "NPOP");
  - rerank: a cross-encoder reads query and product *together*, which ranks better than
    comparing two separately-made vectors, but is too slow to run on the whole catalogue,
    hence only on the retrieved few (the classic two-stage design);
  - hydrate: price and stock are never in the index (the "embed descriptions, fetch facts"
    rule); they come from Kirana, now, for every query. A product deleted since it was
    indexed is simply absent from Kirana's answer, so it can never be shown;
  - filter on live data: "under ₹200" is checked against today's price, not an indexed one.
"""
import logging
import time
from dataclasses import dataclass, field
from functools import cache

from qdrant_client.http import models

from kirana_ai import config, embeddings, kirana, sparse, trace, vector_store

log = logging.getLogger("kirana_ai.products")

CANDIDATES = 30
RESULTS = 5


@dataclass
class ProductResults:
    products: list[dict]
    candidates: int = 0
    out_of_stock: int = 0
    over_price: int = 0
    gone: int = 0                       # indexed, but deleted in Kirana since
    timings_ms: dict = field(default_factory=dict)


@cache
def _reranker():
    from fastembed.rerank.cross_encoder import TextCrossEncoder
    return TextCrossEncoder(config.RERANK_MODEL)


def rerank(query: str, candidates: list[dict]) -> list[dict]:
    """Reorder by cross-encoder score (kept on each candidate as `rerank_score`)."""
    if not candidates:
        return candidates
    scores = list(_reranker().rerank(query, [c["text"] for c in candidates]))
    for c, s in zip(candidates, scores):
        c["rerank_score"] = round(float(s), 3)
    return sorted(candidates, key=lambda c: c["rerank_score"], reverse=True)


def _filter(category: str | None) -> models.Filter:
    must = [models.FieldCondition(key="tenant_id", match=models.MatchValue(value=config.TENANT_ID))]
    if category:
        must.append(models.FieldCondition(key="category", match=models.MatchValue(value=category)))
    return models.Filter(must=must)


def retrieve(query: str, category: str | None, limit: int = CANDIDATES) -> list[dict]:
    client = vector_store.get_client()
    return vector_store.search(
        client, embeddings.embed_text(query), limit, query_filter=_filter(category),
        sparse_vector=sparse.encode_query(query) if config.HYBRID_SEARCH else None,
        collection=config.PRODUCTS_COLLECTION,
    )


def search(query: str, category: str | None = None, max_price: float | None = None,
           use_reranker: bool | None = None) -> ProductResults:
    use_reranker = config.RERANK_ENABLED if use_reranker is None else use_reranker
    t = time.perf_counter
    marks = {}

    start = t()
    candidates = retrieve(query, category)
    marks["retrieve"] = t() - start

    start = t()
    if use_reranker:
        candidates = rerank(query, candidates)
    marks["rerank"] = t() - start

    start = t()
    live = kirana.live([c["product_id"] for c in candidates[:50]])
    marks["hydrate"] = t() - start

    result = ProductResults(products=[], candidates=len(candidates))
    for c in candidates:
        row = live.get(c["product_id"])
        if row is None:
            result.gone += 1
            continue
        if row["stock"] <= 0:
            result.out_of_stock += 1
            continue
        if max_price is not None and row["price"] > max_price:
            result.over_price += 1
            continue
        result.products.append({
            "product_id": row["id"], "name": row["name"], "category": row.get("category"),
            "price": row["price"], "stock": row["stock"],          # live, from Kirana
            "description": c.get("description"),
            "similarity": c.get("similarity"), "rerank_score": c.get("rerank_score"),
        })
        if len(result.products) == RESULTS:
            break
    result.timings_ms = {k: round(v * 1000) for k, v in marks.items()}

    if trace.is_on():
        trace.section("PRODUCT SEARCH")
        trace.kv("query / category / max_price", f"{query!r} / {category} / {max_price}")
        trace.kv("candidates", result.candidates)
        trace.kv("dropped", f"out of stock {result.out_of_stock}, over price {result.over_price}, gone {result.gone}")
        trace.kv("timings", result.timings_ms)
        trace.bullets("results", [f"#{p['product_id']} {p['name']} ₹{p['price']} (stock {p['stock']})"
                                  for p in result.products] or ["(none)"])
    return result
