"""
The product index (Phase 4): Kirana's catalog, embedded for semantic search.

    python -m kirana_ai.catalog        # worker: Kafka catalog.v1 -> kirana_products (group kirana-ai-catalog)
    python -m kirana_ai.cli index-products   # snapshot: (re)build from Kirana's API, drop what is gone

One point per product, not chunks: a product is short, and it is the unit the shopper wants
back. What is embedded is descriptive only (name, category, description). Price and stock are
never stored here: search_products reads them live from Kirana for every query.

Two ways in, and both are needed:
  - events (catalog.v1, from Kirana's outbox) carry *changes*, in order per product;
  - a snapshot (rebuild) provides the *starting state*: products created before the events
    existed, a new collection, or a reindex after a lost event.

Idempotent like the document worker: the point id is derived from the product id, and a
text hash skips re-embedding when nothing searchable changed (e.g. a price-only edit, which
still emits ProductUpserted).
"""
import hashlib
import json
import logging
import time
import uuid
from datetime import datetime, timezone

from kirana_ai import config, consumer, embeddings, kirana, sparse, vector_store
from kirana_ai.consumer import PermanentError

log = logging.getLogger("kirana_ai.catalog")

GROUP = "kirana-ai-catalog"
RETRY_ATTEMPTS = 3
RETRY_BASE_SECONDS = 1.0
INDEXES = {
    "tenant_id": vector_store.models.PayloadSchemaType.KEYWORD,
    "doc_id": vector_store.models.PayloadSchemaType.KEYWORD,
    "category": vector_store.models.PayloadSchemaType.KEYWORD,
    # For lookups by id (and the dashboard's `product_id:134` filter, which types a value from
    # its index). Exact match only: nobody asks for a range of product ids, so no sorted copy.
    "product_id": vector_store.models.IntegerIndexParams(
        type=vector_store.models.IntegerIndexType.INTEGER, lookup=True, range=False),
}


def product_text(name: str, category: str | None, description: str | None) -> str:
    """What gets embedded. The category is in the text too, so 'snacks' matches by meaning."""
    return "\n".join(part for part in (name, category, description) if part)


def _point_id(product_id: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"product-{product_id}"))


def _indexed_hash(client, product_id: int) -> str | None:
    points = client.retrieve(config.PRODUCTS_COLLECTION, [_point_id(product_id)], with_payload=["text_hash"])
    return points[0].payload.get("text_hash") if points else None


def index_product(product_id: int, name: str, description: str | None, category: str | None) -> str:
    client = vector_store.get_client()
    vector_store.ensure_collection(client, config.PRODUCTS_COLLECTION, INDEXES)
    text = product_text(name, category, description)
    text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
    if _indexed_hash(client, product_id) == text_hash:
        return "unchanged"
    point = {
        "chunk_id": f"product-{product_id}",          # -> the same deterministic point id
        "doc_id": str(product_id), "product_id": product_id,
        "name": name, "category": category, "description": description,
        "text": text, "text_hash": text_hash, "tenant_id": config.TENANT_ID,
        "indexed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    vectors = embeddings.embed_documents([point])
    # BM25 weights for one text: its length normalises against itself. Fine for short product
    # texts; Qdrant applies IDF across the whole collection at query time.
    sparse_vectors = sparse.encode_documents([text]) if config.HYBRID_SEARCH else None
    vector_store.upsert_chunks(client, [point], vectors, sparse_vectors, collection=config.PRODUCTS_COLLECTION)
    return "indexed"


def remove_product(product_id: int) -> str:
    vector_store.delete_document_points(vector_store.get_client(), str(product_id),
                                        collection=config.PRODUCTS_COLLECTION)
    return "removed"


# --- events -----------------------------------------------------------------------------------

def parse_event(value: bytes) -> tuple[str, int, dict | None]:
    """Kirana's outbox envelope -> (type, productId, data). Malformed input is permanent."""
    try:
        event = json.loads(value)
        return event["type"], int(event["productId"]), event.get("data")
    except (ValueError, KeyError, TypeError) as exc:
        raise PermanentError(f"not a catalog event: {exc}") from exc


def handle(kind: str, product_id: int, data: dict | None) -> str:
    if kind == "ProductUpserted":
        if not data or not data.get("name"):
            raise PermanentError("ProductUpserted without a name")
        return index_product(product_id, data["name"], data.get("description"), data.get("category"))
    if kind == "ProductDeleted":
        return remove_product(product_id)
    return f"ignored {kind}"


def process(value: bytes, dead_letter, sleep=time.sleep) -> str:
    try:
        kind, product_id, data = parse_event(value)
    except PermanentError as exc:
        dead_letter(str(exc), 0)
        return f"dead-lettered: {exc}"
    outcome = consumer.retrying(lambda: handle(kind, product_id, data),
                                attempts=RETRY_ATTEMPTS, base_seconds=RETRY_BASE_SECONDS,
                                dead_letter=dead_letter,
                                on_failed=lambda reason: log.warning("product %s: %s", product_id, reason),
                                sleep=sleep)
    return f"{kind} {product_id}: {outcome}"


# --- snapshot -----------------------------------------------------------------------------------

def rebuild() -> dict:
    """Index every live product from Kirana's API, then drop points for products that are gone."""
    counts = {"indexed": 0, "unchanged": 0, "removed": 0}
    live_ids = set()
    for summary in kirana.product_pages():
        detail = kirana.product(summary["id"])          # the list has no description
        if detail is None:
            continue
        live_ids.add(detail["id"])
        counts[index_product(detail["id"], detail["name"], detail.get("description"), detail.get("category"))] += 1
    client = vector_store.get_client()
    if client.collection_exists(config.PRODUCTS_COLLECTION):
        for doc_id in vector_store.indexed_doc_ids(client, collection=config.PRODUCTS_COLLECTION):
            if int(doc_id) not in live_ids:
                remove_product(int(doc_id))
                counts["removed"] += 1
    return counts


if __name__ == "__main__":
    consumer.run(config.CATALOG_TOPIC, GROUP, config.CATALOG_DLT, process)
