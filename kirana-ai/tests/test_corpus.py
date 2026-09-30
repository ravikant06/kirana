"""Offline checks on the seed knowledge base and the golden set (no Qdrant, no API)."""
import json
from pathlib import Path

import pytest

from kirana_ai import chunker, config, filters, loader

GOLDEN = Path(__file__).parent.parent / "eval" / "golden"


@pytest.fixture(scope="module")
def chunks() -> list[dict]:
    docs = loader.load_documents(config.KB_DIR)
    return chunker.chunk_documents(docs, config.CHUNK_SIZE, config.CHUNK_OVERLAP)


def test_every_chunk_is_scoped_and_typed(chunks):
    assert chunks
    for c in chunks:
        assert c["tenant_id"] == config.TENANT_ID
        assert c["doc_type"] in config.DOC_TYPES
        assert c["chunk_id"] == f"{c['doc_id']}-{c['chunk_index']}"


def test_filter_always_carries_tenant():
    must = filters.build_filter(doc_type="policy").must
    assert must[0].key == "tenant_id"


def test_unknown_filter_is_rejected():
    with pytest.raises(ValueError):
        filters.build_filter(severity="SEV-1")


@pytest.mark.parametrize("path", sorted(GOLDEN.glob("*.json")), ids=lambda p: p.stem)
def test_every_gold_substring_is_retrievable(path, chunks):
    """A case whose answer no chunk contains would count as a retrieval miss forever."""
    texts = [c["text"].lower() for c in chunks]
    for case in json.loads(path.read_text())["cases"]:
        gold = case["gold_substring"].lower()
        assert any(gold in t for t in texts), f"{case['id']}: {gold!r} is in no chunk"
