"""
Phase 6 M4/M5: actions with human approval, against a real Postgres, with Kirana faked.
The properties under test: nothing runs without a click; what runs is the server's copy; exactly once.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from conftest import bearer
from kirana_ai import actions, api, kirana, policy
from kirana_ai.db import session_scope
from kirana_ai.db.models import Message as MessageRow, Thread
from kirana_ai.errors import UpstreamUnavailable

SHOPPER = frozenset({"orders:read", "orders:write", "cart:read", "cart:write", "chat"})
OPEN_ORDER = {"id": 5, "status": "CREATED", "total": 230.0,
              "items": [{"productName": "Paneer (200 g)", "quantity": 2}]}


class Creds:
    def __init__(self):
        self.asked = []

    def token(self, *scopes):
        self.asked.append(tuple(sorted(scopes)))
        return "narrow:" + " ".join(sorted(scopes))


class FakeKirana:
    """Kirana's order and cart endpoints, with its idempotency: a repeated key replays the first answer."""

    def __init__(self, monkeypatch, order=OPEN_ORDER):
        self.order, self.calls, self.answers = dict(order) if order else None, [], {}
        monkeypatch.setattr(kirana, "my_order", lambda token, oid: self.order if self.order and oid == self.order["id"] else None)
        monkeypatch.setattr(kirana, "cancel_order", self.cancel)
        monkeypatch.setattr(kirana, "add_to_cart", self.add)
        monkeypatch.setattr(kirana, "my_cart", lambda token: {"items": [], "total": 160.0})
        monkeypatch.setattr(kirana, "live", lambda ids: {i: {"id": i, "name": f"Product {i}", "price": 40.0, "stock": 9}
                                                         for i in ids if i < 1000})

    def cancel(self, token, order_id, key):
        self.calls.append(("cancel", token, order_id, key))
        if key not in self.answers:
            if self.order["status"] != "CREATED":
                self.answers[key] = (409, {"title": "Order not awaiting payment", "detail": "Order 5 is CANCELLED"})
            else:
                self.order["status"] = "CANCELLED"
                self.answers[key] = (200, dict(self.order))
        return self.answers[key]

    def add(self, token, pid, qty, key):
        self.calls.append(("add", token, pid, qty, key))
        return 200, {"items": []}


@pytest.fixture
def thread(ai_db):
    with session_scope() as s:
        t = Thread(user_id=7, title="t")
        s.add(t)
        s.flush()
        return t.id


def shopper(thread_id=None):
    return policy.Caller(user_id=7, scopes=SHOPPER, credentials=Creds(), thread_id=thread_id)


def propose_cancel(thread_id, order_id=5):
    payload, card = actions.propose(shopper(thread_id), "cancel_order", {"order_id": order_id})
    return payload, card, (uuid.UUID(card["approval_id"]) if card else None)


# --- propose: nothing happens, a card is built from the server's data ---------------------

def test_propose_creates_a_pending_action_and_acts_on_nothing(monkeypatch, thread):
    k = FakeKirana(monkeypatch)
    payload, card, aid = propose_cancel(thread)
    assert payload["status"] == "awaiting_confirmation" and "never say it is done" in payload["note"]
    assert card["summary"] == "Cancel order #5 (₹230)" and card["lines"] == ["2 × Paneer (200 g)"]
    assert k.calls == [] and actions.get(7, aid).status == "pending"


def test_a_paid_or_missing_order_gets_no_card(monkeypatch, thread):
    FakeKirana(monkeypatch, order={**OPEN_ORDER, "status": "PAID"})
    payload, card, _ = propose_cancel(thread)
    assert card is None and "only orders awaiting payment" in payload["error"]
    payload, card, _ = propose_cancel(thread, order_id=99)
    assert card is None and payload["found"] is False


# --- decide: the click ----------------------------------------------------------------------

def test_confirm_runs_once_with_a_write_token_and_the_approval_id_as_key(monkeypatch, thread):
    k = FakeKirana(monkeypatch)
    _, _, aid = propose_cancel(thread)
    creds = Creds()
    view = actions.decide(7, creds, aid, "confirm")
    assert view.status == "done" and view.message == "Done: order #5 is cancelled."
    assert creds.asked == [("orders:write",)]                          # minted at the click, for this only
    assert k.calls == [("cancel", "narrow:orders:write", 5, str(aid))]
    with session_scope() as s:                                           # the thread hears it from Kirana's answer
        assert s.query(MessageRow).filter_by(thread_id=thread).one().content == view.message


def test_a_second_click_replays_the_outcome(monkeypatch, thread):
    k = FakeKirana(monkeypatch)
    _, _, aid = propose_cancel(thread)
    first = actions.decide(7, Creds(), aid, "confirm")
    second = actions.decide(7, Creds(), aid, "confirm")
    assert first.status == second.status == "done" and len(k.calls) == 1


def test_two_clicks_that_both_reach_kirana_report_once(monkeypatch, thread):
    k = FakeKirana(monkeypatch)
    _, _, aid = propose_cancel(thread)
    real, inner, started = k.cancel, [], []

    def first_click_in_flight(token, order_id, key):
        # While click A waits on Kirana, click B runs end to end (it also gets Kirana's answer).
        if not started:
            started.append(True)
            inner.append(actions.decide(7, Creds(), aid, "confirm"))
        return real(token, order_id, key)
    monkeypatch.setattr(kirana, "cancel_order", first_click_in_flight)

    outer = actions.decide(7, Creds(), aid, "confirm")
    assert inner[0].status == outer.status == "done"
    assert len(k.calls) == 2 and len({c[-1] for c in k.calls}) == 1          # same key: Kirana acted once
    with session_scope() as s:
        assert s.query(MessageRow).filter_by(thread_id=thread).count() == 1  # and it's reported once


def test_a_retry_after_kirana_was_down_reuses_the_same_key(monkeypatch, thread):
    k = FakeKirana(monkeypatch)
    _, _, aid = propose_cancel(thread)
    real = k.cancel

    def down_once(token, order_id, key):
        monkeypatch.setattr(kirana, "cancel_order", real)
        k.calls.append(("timeout", key))
        raise UpstreamUnavailable("kirana", "timeout")
    monkeypatch.setattr(kirana, "cancel_order", down_once)
    with pytest.raises(UpstreamUnavailable):
        actions.decide(7, Creds(), aid, "confirm")
    assert actions.get(7, aid).status == "executing"                     # not lost, not failed
    assert actions.decide(7, Creds(), aid, "confirm").status == "done"
    assert [c[-1] for c in k.calls] == [str(aid), str(aid)]              # same Idempotency-Key both times


def test_reject_never_calls_kirana(monkeypatch, thread):
    k = FakeKirana(monkeypatch)
    _, _, aid = propose_cancel(thread)
    assert actions.decide(7, Creds(), aid, "reject").status == "rejected"
    assert actions.decide(7, Creds(), aid, "confirm").status == "rejected"   # single use
    assert k.calls == []


def test_an_expired_request_does_not_run(monkeypatch, thread, ai_db):
    k = FakeKirana(monkeypatch)
    _, _, aid = propose_cancel(thread)
    with ai_db.begin() as conn:
        conn.execute(text("UPDATE pending_actions SET expires_at = now() - interval '1 second' WHERE id = :id"),
                     {"id": aid})
    assert actions.decide(7, Creds(), aid, "confirm").status == "expired" and k.calls == []


def test_someone_elses_approval_is_not_found(monkeypatch, thread):
    FakeKirana(monkeypatch)
    _, _, aid = propose_cancel(thread)
    with pytest.raises(actions.ApprovalNotFound):
        actions.decide(8, Creds(), aid, "confirm")


def test_an_edited_row_is_refused(monkeypatch, thread, ai_db):
    k = FakeKirana(monkeypatch)
    _, _, aid = propose_cancel(thread)
    with ai_db.begin() as conn:      # someone changes which order the approved click would cancel
        conn.execute(text("""UPDATE pending_actions SET arguments = '{"order_id": 6}' WHERE id = :id"""), {"id": aid})
    with pytest.raises(actions.ApprovalTampered):
        actions.decide(7, Creds(), aid, "confirm")
    assert k.calls == []


def test_kirana_refusing_is_a_failed_action_with_its_reason(monkeypatch, thread):
    k = FakeKirana(monkeypatch)
    _, _, aid = propose_cancel(thread)
    k.order["status"] = "CANCELLED"                                       # it changed after the card was shown
    view = actions.decide(7, Creds(), aid, "confirm")
    assert view.status == "failed" and "Order 5 is CANCELLED" in view.message


# --- add to cart (M5) -------------------------------------------------------------------------

def test_cart_needs_real_products_and_adds_each_line_once(monkeypatch, thread):
    k = FakeKirana(monkeypatch)
    payload, card = actions.propose(shopper(thread), "add_to_cart", {"items": [{"product_id": 4242, "quantity": 1}]})
    assert card is None and "never invent ids" in payload["error"]
    payload, card = actions.propose(shopper(thread), "add_to_cart",
                                    {"items": [{"product_id": 3, "quantity": 2}, {"product_id": 4, "quantity": 2}]})
    assert card["lines"] == ["2 × Product 3 (₹40 each)", "2 × Product 4 (₹40 each)"]
    aid = card["approval_id"]
    creds = Creds()
    view = actions.decide(7, creds, uuid.UUID(aid), "confirm")
    assert view.status == "done" and view.message.endswith("Cart total ₹160.")       # Kirana's total
    assert creds.asked == [("cart:read", "cart:write")]
    assert [c[-1] for c in k.calls] == [f"{aid}:3", f"{aid}:4"]


# --- the HTTP side ----------------------------------------------------------------------------

def test_approval_endpoints(monkeypatch, thread):
    FakeKirana(monkeypatch)
    monkeypatch.setattr(api.auth, "Credentials", lambda token: Creds())
    _, _, aid = propose_cancel(thread)
    client = TestClient(api.app, raise_server_exceptions=False)
    assert client.get(f"/v1/approvals/{aid}", headers=bearer(8)).status_code == 404       # not yours
    assert client.post(f"/v1/approvals/{aid}", json={"decision": "maybe"}, headers=bearer(7)).status_code == 400
    r = client.post(f"/v1/approvals/{aid}", json={"decision": "confirm"}, headers=bearer(7))
    assert r.status_code == 200 and r.json()["status"] == "done"
