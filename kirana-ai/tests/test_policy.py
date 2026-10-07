"""Phase 6 M1: the tool policy layer. The model proposes; this decides. No network, no LLM."""
from collections import Counter

import pytest

from conftest import FakeAdapter
from kirana_ai import agent, policy
from kirana_ai.llm import LLMResponse, ToolCall, Usage

SHOPPER = frozenset({"orders:read", "orders:write", "cart:read", "cart:write", "chat"})


class Creds:
    def token(self, *scopes):
        return "t"


def shopper():
    return policy.Caller(user_id=2, scopes=SHOPPER, credentials=Creds())


def decide(tool, args=None, caller=None, used=None):
    return policy.authorize(caller or shopper(), tool, args or {}, used or Counter())


def test_every_agent_tool_has_a_rule_and_every_rule_a_tool():
    assert set(policy.RULES) == set(agent.SPECS)


@pytest.mark.parametrize("tool, args, outcome, reason", [
    ("search_docs", {"query": "returns"}, "allow", "ok:read-public"),
    ("get_order", {"order_id": 5}, "allow", "ok:read-personal"),
    ("refund_everything", {}, "deny", "unknown-tool"),                          # G2
    ("get_order", {"order_id": "1 OR 1=1"}, "deny", "bad-args:order_id must be a whole number"),
    ("get_order", {"order_id": -1}, "deny", "bad-args:order_id must be between 1 and 1000000000000"),
    ("get_my_orders", {"status": "SHIPPED"}, "deny", "bad-args:status must be one of CREATED, PAID, CANCELLED, FAILED"),
    ("search_products", {"query": "x", "max_price": -5}, "deny", "bad-args:max_price must be a positive amount in rupees"),
    ("add_to_cart", {"items": [{"product_id": 1, "quantity": 99}]}, "deny", "bad-args:quantity must be between 1 and 10"),
    ("cancel_order", {"order_id": 5}, "approval", "write-needs-approval"),
    ("add_to_cart", {"items": [{"product_id": 1, "quantity": 2}]}, "approval", "write-needs-approval"),
])
def test_decisions(tool, args, outcome, reason):
    d = decide(tool, args)
    assert (d.outcome.value, d.reason) == (outcome, reason)


def test_missing_permission_is_denied_even_if_the_tool_exists():
    chat_only = policy.Caller(user_id=2, scopes=frozenset({"chat"}), credentials=Creds())
    assert decide("cancel_order", {"order_id": 5}, chat_only).reason == "missing-scope:orders:write"
    assert decide("get_order", {"order_id": 5}, policy.ANONYMOUS).reason == "missing-scope:orders:read"


def test_budget_per_turn():
    used = Counter(get_order=5)
    assert decide("get_order", {"order_id": 6}, used=used).reason == "budget:5-per-turn"


def test_offering_hides_what_enforcing_would_deny():
    reader = policy.Caller(user_id=2, scopes=frozenset({"chat", "orders:read"}), credentials=Creds())
    offered = {t.name for t in policy.tools_for(reader, agent.SPECS)}
    assert "get_order" in offered and "cancel_order" not in offered and "add_to_cart" not in offered


# --- through the agent loop -----------------------------------------------------------------

def _turn(monkeypatch, *calls, caller=None, recorder=None):
    searched = []
    monkeypatch.setattr(agent, "_run_search", lambda args, tenant, k: searched.append(args) or [])
    replies = [LLMResponse(tool_calls=(c,), usage=Usage(10, 1)) for c in calls] + [LLMResponse(text="ok", usage=Usage(10, 1))]
    llm = FakeAdapter(replies)
    recorded = []
    events = list(agent.answer_stream("q", llm=llm, caller=caller or shopper(),
                                      decisions=recorder or (lambda r: recorded.append(r) or True)))
    return searched, llm, [e.data for e in events if e.kind == "step"], recorded


def test_g2_an_unknown_tool_no_longer_runs_search_docs(monkeypatch):
    searched, llm, steps, recorded = _turn(monkeypatch, ToolCall(id="1", name="refund_everything", arguments={"query": "x"}))
    assert searched == []                                     # before: fell through to search_docs
    assert steps[0]["decision"] == "deny" and steps[0]["reason"] == "unknown-tool"
    assert "no such tool" in llm.calls[1][-1].tool_result.content["error"]
    assert recorded[0].tool == "refund_everything"            # logged as the model named it


def test_every_call_is_recorded_with_its_decision(monkeypatch):
    _, _, steps, recorded = _turn(monkeypatch,
                                  ToolCall(id="1", name="search_docs", arguments={"query": "returns"}),
                                  ToolCall(id="2", name="nope", arguments={}))
    assert [(r.tool, r.decision.outcome.value) for r in recorded] == [("search_docs", "allow"), ("nope", "deny")]


def test_a_write_without_its_audit_row_is_refused(monkeypatch):
    proposed = []
    from kirana_ai import actions
    monkeypatch.setattr(actions, "propose", lambda *a: proposed.append(a) or ({}, None))
    _, _, steps, _ = _turn(monkeypatch, ToolCall(id="1", name="cancel_order", arguments={"order_id": 5}),
                           recorder=lambda r: False)          # the audit table is down
    assert proposed == [] and steps[0]["reason"] == "audit-unavailable"


def test_refusals_are_explainable_tool_results():
    r = policy.refusal(policy.Decision(policy.Outcome.DENY, "missing-scope:orders:read"))
    assert r["denied_by"] == "policy" and "not allowed" in r["error"] and r["count"] == 0


def test_a_rule_without_dispatch_code_is_refused_not_run_as_search(monkeypatch):
    monkeypatch.setitem(policy.RULES, "new_tool", policy.Rule("new_tool", None, policy.Risk.READ_PUBLIC, 1))
    searched, _, steps, _ = _turn(monkeypatch, ToolCall(id="1", name="new_tool", arguments={"query": "x"}))
    assert searched == [] and steps[0]["denied_by"] == "policy"
