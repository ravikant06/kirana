"""Phase 7 M4: long-term memory. Postgres is the truth; Qdrant only finds candidates."""
import uuid

import pytest

from conftest import bearer
from kirana_ai import actions, config, memory, policy, vector_store
from kirana_ai.db import session_scope
from kirana_ai.db.models import Memory


@pytest.fixture
def no_index(monkeypatch):
    calls = []
    monkeypatch.setattr(memory, "_index", lambda view, user_id: calls.append(view.id))
    monkeypatch.setattr(vector_store, "get_client", lambda: None)
    monkeypatch.setattr(vector_store, "delete_document_points", lambda client, doc_id, collection: calls.append(("del", doc_id)))
    return calls


def test_save_list_pin_delete(ai_db, no_index):
    m = memory.save(7, " Vegetarian ", "preference")
    assert memory.save(7, "vegetarian").id == m.id                    # saying it twice stores it once
    memory.save(8, "Someone else's")
    assert [x.text for x in memory.list_for(7)] == ["Vegetarian"]
    assert memory.set_pinned(7, m.id, True).pinned
    memory.delete(7, m.id)
    assert memory.list_for(7) == [] and ("del", str(m.id)) in no_index
    with pytest.raises(memory.MemoryNotFound):
        memory.delete(8, m.id)                                         # not theirs, and already gone


def test_small_memory_sets_are_loaded_whole_without_a_search(ai_db, no_index, monkeypatch):
    monkeypatch.setattr(vector_store, "search", lambda *a, **k: pytest.fail("no search needed"))
    for t in ("Vegetarian", "Family of 4", "Dislikes mushrooms"):
        memory.save(7, t)
    recall = memory.for_prompt(7, "what's for dinner?")
    assert recall.mode == "all" and {m.text for m in recall.memories} == {"Vegetarian", "Family of 4", "Dislikes mushrooms"}


def test_over_budget_pinned_plus_relevant_and_every_hit_rechecked_in_postgres(ai_db, no_index, monkeypatch):
    monkeypatch.setattr(config, "MEMORY_BUDGET_TOKENS", 40)
    pinned = memory.save(7, "Vegetarian")
    memory.set_pinned(7, pinned.id, True)
    mushrooms = memory.save(7, "Dislikes mushrooms")
    deleted = memory.save(7, "Wants chocolate in every order")
    memory.delete(7, deleted.id)
    others = [memory.save(7, f"Unrelated fact number {i}") for i in range(6)]
    stranger = memory.save(8, "Another shopper's secret")
    # The index is stale: it still returns the deleted memory and, wrongly, another user's.
    monkeypatch.setattr(vector_store, "search", lambda *a, **k: [
        {"memory_id": str(deleted.id)}, {"memory_id": str(stranger.id)}, {"memory_id": str(mushrooms.id)}])
    monkeypatch.setattr(memory.embeddings, "embed_text", lambda text: [0.0])
    recall = memory.for_prompt(7, "cook dinner tonight")
    assert recall.mode == "search"
    assert [m.text for m in recall.memories] == ["Vegetarian", "Dislikes mushrooms"]   # never deleted, never foreign
    assert len(others) == 6


def test_memory_card_saves_only_after_the_click(ai_db, no_index):
    caller = policy.Caller(user_id=7, scopes=frozenset({"chat"}), credentials=object())
    payload, card = actions.propose(caller, "remember_preference", {"text": "We are a family of 4", "kind": "fact"})
    assert card["summary"] == "Save to your memory" and "“We are a family of 4”" in card["lines"][0]
    assert memory.list_for(7) == []                                   # nothing saved by proposing
    view = actions.decide(7, None, uuid.UUID(card["approval_id"]), "confirm")
    assert view.status == "done" and view.message == "Saved to your memory: “We are a family of 4”."
    with session_scope() as s:
        row = s.query(Memory).filter_by(user_id=7).one()
        assert row.kind == "fact" and str(row.approval_id) == card["approval_id"]  # provenance: which click


def test_memory_endpoints_are_per_shopper(ai_db, no_index):
    from fastapi.testclient import TestClient
    from kirana_ai import api
    m = memory.save(7, "Vegetarian")
    client = TestClient(api.app, raise_server_exceptions=False)
    assert [x["text"] for x in client.get("/v1/memories", headers=bearer(7)).json()] == ["Vegetarian"]
    assert client.get("/v1/memories", headers=bearer(8)).json() == []
    assert client.delete(f"/v1/memories/{m.id}", headers=bearer(8)).status_code == 404
    assert client.patch(f"/v1/memories/{m.id}", json={"pinned": True}, headers=bearer(7)).json()["pinned"] is True
    assert client.delete(f"/v1/memories/{m.id}", headers=bearer(7)).status_code == 204


def test_dedupe_is_exact_text_not_a_pattern(ai_db, no_index):
    """Review finding: ILIKE treated % and _ in the shopper's text as wildcards."""
    first = memory.save(7, "Likes 100X whole wheat")
    other = memory.save(7, "Likes 100% whole wheat")                  # as a pattern, '%' matched the 'X'
    assert other.id != first.id
    assert memory.save(7, "likes 100% WHOLE wheat").id == other.id    # case-insensitive, still exact


# --- updates: "I'm non-vegetarian" when "Vegetarian" is saved ---------------------------------------

def test_an_update_card_replaces_the_old_memory_on_confirm(ai_db, no_index):
    old = memory.save(7, "Vegetarian")
    caller = policy.Caller(user_id=7, scopes=frozenset({"chat"}), credentials=object())
    payload, card = actions.propose(caller, "remember_preference", {"text": "Non-vegetarian", "replaces": "vegetarian"})
    assert card["summary"] == "Update your memory" and card["lines"][:2] == ["Replace “Vegetarian”", "with “Non-vegetarian”"]
    assert [m.text for m in memory.list_for(7)] == ["Vegetarian"]                 # nothing changes before the click
    view = actions.decide(7, None, uuid.UUID(card["approval_id"]), "confirm")
    assert view.message == "Updated your memory: “Non-vegetarian” (replaces “Vegetarian”)."
    assert [m.text for m in memory.list_for(7)] == ["Non-vegetarian"]             # never both
    assert ("del", str(old.id)) in no_index                                        # its index point goes too


def test_replaces_must_name_one_of_your_own_saved_memories(ai_db, no_index):
    memory.save(8, "Vegetarian")                                                   # someone else's
    caller = policy.Caller(user_id=7, scopes=frozenset({"chat"}), credentials=object())
    payload, card = actions.propose(caller, "remember_preference", {"text": "Non-vegetarian", "replaces": "Vegetarian"})
    assert card is None and "No saved memory" in payload["error"]
    assert [m.text for m in memory.list_for(8)] == ["Vegetarian"]                  # untouched
