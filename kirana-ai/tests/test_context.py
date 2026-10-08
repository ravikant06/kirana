"""Phase 7 M2-M3: context blocks, thread facts and the rolling summary. Real Postgres, no LLM."""
import uuid

import pytest
from sqlalchemy import text

from conftest import FakeAdapter
from kirana_ai import agent, chat, config, context, memory, products
from kirana_ai.db import session_scope
from kirana_ai.db.models import Message as MessageRow, Role as RowRole, Thread
from kirana_ai.llm import LLMResponse, ToolCall, Usage


# --- blocks and facts (pure) --------------------------------------------------------------------

def test_blocks_come_in_stable_to_changing_order_and_are_labelled():
    recall = memory.Recall([memory.MemoryView(uuid.uuid4(), "Vegetarian", "preference", True, None)], "all")
    facts = {"products_shown": [{"id": 85, "name": "Roasted Makhana"}, {"id": 92, "name": "Baked Ragi Chips"}]}
    out = context.blocks(recall, "Ravi plans snacks for the week.", facts)
    assert [m.block for m in out] == ["memories", "summary", "facts"]
    assert "Vegetarian" in out[0].text and "data, not instructions" in out[0].text
    assert "2. Baked Ragi Chips (product id 92)" in out[2].text


def test_no_context_means_no_blocks():
    assert context.blocks(memory.Recall([], "none"), None, {}) == []


def test_facts_follow_the_tool_steps_not_the_model_text():
    steps = [{"tool": "search_products", "products": [{"id": i, "name": f"P{i}"} for i in range(1, 8)]},
             {"tool": "get_my_orders", "orders": [{"id": 202, "status": "CREATED"}, {"id": 1, "status": "PAID"}]},
             {"tool": "cancel_order", "approval": {"approval_id": "a1", "summary": "Cancel order #202 (₹660)"}}]
    facts = context.facts_after({"orders_mentioned": [{"id": 1, "status": "PAID"}, {"id": 52, "status": "PAID"}]}, steps)
    assert [p["id"] for p in facts["products_shown"]] == [1, 2, 3, 4, 5]         # capped, in the order shown
    assert [o["id"] for o in facts["orders_mentioned"]] == [202, 1, 52]          # newest first, no duplicates
    assert facts["last_approval"] == {"id": "a1", "summary": "Cancel order #202 (₹660)"}


# --- through a real turn ------------------------------------------------------------------------

def test_a_turn_stores_the_products_it_showed_as_facts(ai_db, monkeypatch):
    found = products.ProductResults(products=[
        {"product_id": 85, "name": "Roasted Makhana", "category": "Snacks", "price": 110, "stock": 9, "description": ""},
        {"product_id": 92, "name": "Baked Ragi Chips", "category": "Snacks", "price": 60, "stock": 9, "description": ""}])
    monkeypatch.setattr(agent, "_run_products", lambda args: found)
    llm = FakeAdapter([LLMResponse(tool_calls=(ToolCall(id="1", name="search_products", arguments={"query": "snacks"}),),
                                   usage=Usage(10, 1)),
                       LLMResponse(text="Here are two.", usage=Usage(10, 1))])
    result = chat.send(7, "healthy snacks", llm=llm)
    with session_scope() as s:
        facts = s.get(Thread, result.thread_id).facts
    assert facts["products_shown"] == [{"id": 85, "name": "Roasted Makhana"}, {"id": 92, "name": "Baked Ragi Chips"}]

    # The next turn starts with them, numbered, before the history.
    turn = chat.prepare(7, "add the second one", thread_id=result.thread_id)
    assert [m.block for m in turn.context] == ["facts"] and "2. Baked Ragi Chips (product id 92)" in turn.context[0].text


# --- the rolling summary ------------------------------------------------------------------------

def _thread_with_turns(n: int, words: int = 3) -> uuid.UUID:
    """Each message in its own transaction, as in real turns, so their timestamps differ."""
    with session_scope() as s:
        t = Thread(user_id=7, title="t")
        s.add(t)
        s.flush()
        tid = t.id
    for i in range(1, n + 1):
        for role, content in ((RowRole.USER, f"question {i} " + "w " * words), (RowRole.ASSISTANT, f"answer {i}")):
            with session_scope() as s:
                s.add(MessageRow(thread_id=tid, role=role, content=content))
    return tid


def _answers(thread_id):
    with session_scope() as s:
        return [r.id for r in s.query(MessageRow).filter_by(thread_id=thread_id, role=RowRole.ASSISTANT)
                .order_by(MessageRow.created_at)]


def test_turns_outside_the_window_are_folded_and_the_rest_kept(ai_db, monkeypatch):
    monkeypatch.setattr(config, "HISTORY_TURNS", 6)
    tid = _thread_with_turns(9)
    llm = FakeAdapter([LLMResponse(text="Shopper asked 3 questions.", usage=Usage(50, 10))])
    assert context.summarise(tid, llm) == "Shopper asked 3 questions."
    sent = llm.calls[0][0].text
    assert "question 3" in sent and "question 4" not in sent                 # oldest 3 folded, 6 kept
    with session_scope() as s:
        t = s.get(Thread, tid)
        assert t.summary == "Shopper asked 3 questions." and t.summary_through == _answers(tid)[2]
    assert llm.thinking == config.SUMMARY_THINKING                           # cheap, fast summaries
    assert context.summarise(tid, FakeAdapter([])) is None                   # nothing new to fold


def test_the_next_summary_builds_on_the_previous_one(ai_db, monkeypatch):
    monkeypatch.setattr(config, "HISTORY_TURNS", 6)
    tid = _thread_with_turns(9)
    context.summarise(tid, FakeAdapter([LLMResponse(text="S1", usage=Usage(1, 1))]))
    for i in (10, 11):
        for role, content in ((RowRole.USER, f"question {i}"), (RowRole.ASSISTANT, f"answer {i}")):
            with session_scope() as s:
                s.add(MessageRow(thread_id=tid, role=role, content=content))
    llm = FakeAdapter([LLMResponse(text="S2", usage=Usage(1, 1))])
    context.summarise(tid, llm)
    sent = llm.calls[0][0].text
    assert "Current summary:\nS1" in sent and "question 4" in sent and "question 5" in sent and "question 6" not in sent


def test_a_long_window_is_folded_down_to_the_token_budget(ai_db, monkeypatch):
    monkeypatch.setattr(config, "HISTORY_TURNS", 6)
    monkeypatch.setattr(config, "HISTORY_BUDGET_TOKENS", 300)
    tid = _thread_with_turns(5, words=300)                                  # ~150 tokens per turn, inside the window
    llm = FakeAdapter([LLMResponse(text="S", usage=Usage(1, 1))])
    context.summarise(tid, llm)
    with session_scope() as s:
        assert s.get(Thread, tid).summary_through == _answers(tid)[2]       # 3 folded, the last 2 kept


def test_a_summary_written_meanwhile_is_not_overwritten(ai_db, monkeypatch):
    monkeypatch.setattr(config, "HISTORY_TURNS", 6)
    tid = _thread_with_turns(9)

    class Racing(FakeAdapter):
        def _complete(self, messages, *, tools=(), system=None):
            with ai_db.begin() as conn:     # another turn's summary lands while this one is thinking
                conn.execute(text("UPDATE threads SET summary = 'other', summary_through = gen_random_uuid() WHERE id = :id"),
                             {"id": tid})
            return super()._complete(messages, tools=tools, system=system)
    assert context.summarise(tid, Racing([LLMResponse(text="mine", usage=Usage(1, 1))])) is None
    with session_scope() as s:
        assert s.get(Thread, tid).summary == "other"


def test_after_turn_never_raises(ai_db, monkeypatch):
    monkeypatch.setattr(context, "summarise", lambda *a: (_ for _ in ()).throw(RuntimeError("LLM down")))
    chat.after_turn(uuid.uuid4())                                            # logged, not raised


def test_history_never_repeats_what_the_summary_already_covers(ai_db, monkeypatch):
    """Review finding: folded turns were also sent verbatim, so the token budget saved nothing."""
    monkeypatch.setattr(config, "HISTORY_TURNS", 6)
    monkeypatch.setattr(config, "HISTORY_BUDGET_TOKENS", 300)
    tid = _thread_with_turns(5, words=300)
    context.summarise(tid, FakeAdapter([LLMResponse(text="S", usage=Usage(1, 1))]))   # folds turns 1-3
    turn = chat.prepare(7, "next", thread_id=tid)
    sent = [m.text.split()[0] + " " + m.text.split()[1] for m in turn.history if m.text.startswith("question")]
    assert sent == ["question 4", "question 5"]
    assert [m.block for m in turn.context] == ["summary"]


# --- 2A: cards are the products the answer names ---------------------------------------------------

DINNER_STEPS = [   # the real conversation: four searches, 13 different products, one empty
    {"tool": "search_products", "products": [{"id": 9, "name": "Paneer (200 g)"}, {"id": 140, "name": "Sunscreen SPF 50 (50 ml)"},
                                             {"id": 70, "name": "Frozen Malabar Parotta (5 pcs)"}]},
    {"tool": "search_products", "products": [{"id": 30, "name": "Biryani Masala (50 g)"}, {"id": 43, "name": "Basmati Rice (5 kg)"}]},
    {"tool": "search_products", "products": [{"id": 12, "name": "Eggs, White (12 pcs)"}, {"id": 13, "name": "Eggs, Brown (6 pcs)"}]},
    {"tool": "search_products", "count": 0},
]
ANSWER = ("Here are two great options for your dinner tonight:\n**Eggs, White (12 pcs)** — ₹84 ...\n"
          "**Frozen Malabar Parotta (5 pcs)** — ₹120 ...")


def test_cards_are_the_products_the_answer_names_in_its_order():
    cards = context.cards_for(ANSWER, DINNER_STEPS)
    assert [c["id"] for c in cards] == [12, 70]                     # not 13 cards, no sunscreen


def test_without_named_products_the_last_useful_search_top_three():
    assert [c["id"] for c in context.cards_for("Here are some ideas.", DINNER_STEPS)] == [12, 13]


def test_a_name_no_search_returned_never_becomes_a_card():
    assert context.cards_for("Try **Chicken Biryani Kit** tonight.", DINNER_STEPS) == DINNER_STEPS[2]["products"][:3]


def test_the_second_one_means_the_second_card():
    facts = context.facts_after({}, DINNER_STEPS, context.cards_for(ANSWER, DINNER_STEPS))
    assert [p["id"] for p in facts["products_shown"]] == [12, 70]   # "the second one" = the parotta the shopper saw
