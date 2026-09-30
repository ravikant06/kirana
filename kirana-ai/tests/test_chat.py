"""
Chat turns against a real Postgres: threads, messages, history, llm_calls.

The agent runs with a FakeAdapter and a stubbed search, so no LLM, no Qdrant.
"""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from conftest import FakeAdapter
from kirana_ai import agent, chat, config, usage
from kirana_ai.llm import LLMError, LLMResponse, ToolCall, Usage

CHUNK = {"chunk_id": "policy-returns-0", "doc_id": "policy-returns", "source": "policy-returns.md",
         "title": "Returns Policy", "heading": "Return windows", "chunk_index": 0,
         "text": "Packaged staples: 7 days from delivery", "score": 0.8}


@pytest.fixture(autouse=True)
def no_qdrant(monkeypatch):
    monkeypatch.setattr(agent, "_run_search", lambda args, tenant, k: [CHUNK])


@pytest.fixture
def priced(monkeypatch):
    monkeypatch.setattr(usage, "load_prices",
                        lambda: {"fake-model": usage.Price(Decimal("1"), Decimal("2"))})


def _search() -> LLMResponse:
    return LLMResponse(tool_calls=(ToolCall(id="1", name="search_docs",
                                            arguments={"query": "return rice"}),),
                       usage=Usage(800, 20))


def _answer(text: str) -> LLMResponse:
    return LLMResponse(text=text, usage=Usage(1500, 60))


def _rows(db, sql, **params):
    with db.connect() as conn:
        return conn.execute(text(sql), params).all()


def test_a_turn_saves_thread_messages_and_one_row_per_llm_call(ai_db, priced):
    fake = FakeAdapter([_search(), _answer("Unopened: 7 days.\nSources: policy-returns.md")])

    result = chat.send(user_id=7, text="Can I return rice?", llm=fake)

    assert _rows(ai_db, "SELECT user_id, title FROM threads") == [(7, "Can I return rice?")]
    messages = _rows(ai_db, "SELECT role, content, citations FROM messages ORDER BY created_at")
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[1].citations == [{"source": "policy-returns.md", "doc_id": "policy-returns",
                                      "title": "Returns Policy"}]

    calls = _rows(ai_db, "SELECT turn_id, status, input_tokens, output_tokens, cost_usd "
                         "FROM llm_calls ORDER BY id")
    assert [(c.status, c.input_tokens, c.output_tokens) for c in calls] == [
        ("ok", 800, 20), ("ok", 1500, 60)]
    # Every call points at the assistant message the turn produced.
    assert {c.turn_id for c in calls} == {result.message_id}
    assert calls[0].cost_usd == Decimal("0.000840")      # 800*1/1M + 20*2/1M

    assert result.usage["llm_calls"] == 2
    assert result.usage["input_tokens"] == 2300
    assert result.usage["cost_usd"] == Decimal("0.002460")    # (840 + 1620) / 1M


def test_follow_up_sends_the_earlier_turn_as_history(ai_db):
    first = chat.send(7, "Can I return opened rice?",
                      llm=FakeAdapter([_answer("Only if defective.")]))
    fake = FakeAdapter([_answer("Sealed: within 7 days.")])

    chat.send(7, "and if it's sealed?", thread_id=first.thread_id, llm=fake)

    assert [m.text for m in fake.calls[0]] == [
        "Can I return opened rice?", "Only if defective.", "and if it's sealed?"]


def test_history_off_sends_only_the_question(ai_db, monkeypatch):
    first = chat.send(7, "Can I return opened rice?", llm=FakeAdapter([_answer("Only if defective.")]))
    monkeypatch.setattr(config, "HISTORY_TURNS", 0)
    fake = FakeAdapter([_answer("?")])

    chat.send(7, "and if it's sealed?", thread_id=first.thread_id, llm=fake)

    assert [m.text for m in fake.calls[0]] == ["and if it's sealed?"]


def test_history_is_capped_to_the_last_turns(ai_db, monkeypatch):
    monkeypatch.setattr(config, "HISTORY_TURNS", 2)
    thread_id = None
    for i in range(4):
        thread_id = chat.send(7, f"q{i}", thread_id=thread_id,
                              llm=FakeAdapter([_answer(f"a{i}")])).thread_id
    fake = FakeAdapter([_answer("a4")])

    chat.send(7, "q4", thread_id=thread_id, llm=fake)

    assert [m.text for m in fake.calls[0]] == ["q2", "a2", "q3", "a3", "q4"]


def test_someone_elses_thread_is_not_found(ai_db):
    first = chat.send(7, "hi", llm=FakeAdapter([_answer("hello")]))

    with pytest.raises(chat.ThreadNotFound):
        chat.send(8, "show me", thread_id=first.thread_id, llm=FakeAdapter([]))
    with pytest.raises(chat.ThreadNotFound):
        chat.send(7, "x", thread_id=uuid.uuid4(), llm=FakeAdapter([]))


def test_failed_turn_keeps_the_question_and_the_cost_row(ai_db):
    fake = FakeAdapter([_search(), LLMError("Gemini call failed: 503")])

    with pytest.raises(LLMError):
        chat.send(7, "Can I return rice?", llm=fake)

    assert [r.role for r in _rows(ai_db, "SELECT role FROM messages")] == ["user"]
    calls = _rows(ai_db, "SELECT status, error FROM llm_calls ORDER BY id")
    assert [c.status for c in calls] == ["ok", "error"]   # the first call was made, and billed
    assert "503" in calls[1].error


def test_unanswered_question_is_left_out_of_history(ai_db):
    first = chat.send(7, "q1", llm=FakeAdapter([_answer("a1")]))
    with pytest.raises(LLMError):
        chat.send(7, "q2 (fails)", thread_id=first.thread_id, llm=FakeAdapter([LLMError("down")]))
    fake = FakeAdapter([_answer("a3")])

    chat.send(7, "q3", thread_id=first.thread_id, llm=fake)

    assert [m.text for m in fake.calls[0]] == ["q1", "a1", "q3"]


def test_unpriced_model_records_null_cost(ai_db, monkeypatch):
    monkeypatch.setattr(usage, "load_prices", lambda: {})

    result = chat.send(7, "hi", llm=FakeAdapter([_answer("hello")]))

    assert _rows(ai_db, "SELECT cost_usd FROM llm_calls") == [(None,)]
    assert result.usage["cost_usd"] is None


def test_threads_are_listed_newest_first_per_shopper(ai_db):
    older = chat.send(7, "older", llm=FakeAdapter([_answer("a")]))
    newer = chat.send(7, "newer", llm=FakeAdapter([_answer("b")]))
    chat.send(8, "not mine", llm=FakeAdapter([_answer("c")]))
    chat.send(7, "again", thread_id=older.thread_id, llm=FakeAdapter([_answer("d")]))

    assert [t.id for t in chat.list_threads(7)] == [older.thread_id, newer.thread_id]
