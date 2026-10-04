"""The product indexer (Phase 4 M2), with Qdrant and the embedding API faked."""
import json

import pytest

from kirana_ai import catalog, embeddings, vector_store


class FakeProducts:
    """A tiny stand-in for the kirana_products collection."""
    def __init__(self): self.points = {}; self.embedded = 0

    def retrieve(self, collection, ids, with_payload):
        return [type("P", (), {"payload": self.points[i]})() for i in ids if i in self.points]


@pytest.fixture
def index(monkeypatch):
    store = FakeProducts()
    monkeypatch.setattr(vector_store, "get_client", lambda: store)
    monkeypatch.setattr(vector_store, "ensure_collection", lambda *a, **k: None)
    monkeypatch.setattr(vector_store, "ensure_payload_indexes", lambda *a, **k: None)

    def upsert(client, chunks, vectors, sparse_vectors, collection):
        assert collection == "kirana_products"
        for c in chunks:
            store.points[catalog._point_id(c["product_id"])] = c
    monkeypatch.setattr(vector_store, "upsert_chunks", upsert)
    monkeypatch.setattr(vector_store, "delete_document_points",
                        lambda client, doc_id, collection: store.points.pop(catalog._point_id(int(doc_id)), None))

    def embed(chunks):
        store.embedded += len(chunks)
        return [[0.1, 0.2]] * len(chunks)
    monkeypatch.setattr(embeddings, "embed_documents", embed)
    monkeypatch.setattr(catalog, "RETRY_BASE_SECONDS", 0)
    return store


def _event(kind, pid, **data):
    return json.dumps({"eventId": "e1", "type": kind, "occurredAt": "2026-10-04T10:00:00Z",
                       "productId": pid, "data": data or None}).encode()


def _no_dlt(reason, attempts):
    pytest.fail(f"unexpected dead letter: {reason}")


def test_upsert_indexes_descriptive_fields_only(index):
    outcome = catalog.process(_event("ProductUpserted", 7, name="Roasted Makhana", category="Snacks",
                                     description="Low calorie, high protein."), _no_dlt)

    assert outcome == "ProductUpserted 7: indexed"
    point = index.points[catalog._point_id(7)]
    assert point["text"] == "Roasted Makhana\nSnacks\nLow calorie, high protein."
    assert point["category"] == "Snacks" and point["tenant_id"] == "kirana"
    assert "price" not in point and "stock" not in point


def test_an_unchanged_text_is_not_re_embedded(index):
    event = _event("ProductUpserted", 7, name="Makhana", category="Snacks", description="Roasted.")
    catalog.process(event, _no_dlt)

    # e.g. a price-only edit: Kirana still sends ProductUpserted, with the same text
    assert catalog.process(event, _no_dlt) == "ProductUpserted 7: unchanged"
    assert index.embedded == 1


def test_delete_removes_the_point(index):
    catalog.process(_event("ProductUpserted", 7, name="Makhana"), _no_dlt)

    catalog.process(_event("ProductDeleted", 7), _no_dlt)

    assert index.points == {}


def test_malformed_event_is_dead_lettered(index):
    dead = []
    catalog.process(b'{"type": "ProductUpserted"}', lambda reason, attempts: dead.append(attempts))
    assert dead == [0]


def test_a_down_qdrant_is_retried_then_dead_lettered(index, monkeypatch):
    def down(*a, **k):
        raise ConnectionError("qdrant down")
    monkeypatch.setattr(vector_store, "upsert_chunks", down)
    dead = []

    outcome = catalog.process(_event("ProductUpserted", 7, name="Makhana"),
                              lambda reason, attempts: dead.append((reason, attempts)))

    assert dead == [("ConnectionError: qdrant down", catalog.RETRY_ATTEMPTS)]
    assert "dead-lettered" in outcome
