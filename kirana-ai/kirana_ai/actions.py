"""
Actions with human approval (Phase 6 M4, M5): the agent proposes, the shopper confirms, code acts.

    model calls cancel_order(5)
      └─ policy: write → "approval"
      └─ propose(): pre-check with a READ token (does order 5 exist for this shopper, is it cancellable?)
                    → ai.pending_actions row: the exact arguments, single use, expires in 5 minutes,
                      sealed with an HMAC over (id, user, tool, arguments)
                    → SSE approval_required → a card built from the server's copy, not the model's text
      └─ the model is told "awaiting confirmation": it must not claim the action happened

    shopper clicks Confirm → POST /v1/approvals/{id}
      └─ decide(): lock the row, check owner, seal, status and expiry → status executing
                   → exchange the shopper's token for a 2-minute WRITE token with only this scope
                   → Kirana POST …/cancel with Idempotency-Key = approval id
                   → done / failed, and an assistant message written from Kirana's answer

Exactly once, end to end: a double click, a retry after a timeout, or a crash after Kirana acted
all reuse the same Idempotency-Key, so Kirana replays its first answer instead of acting again.

The model never executes anything and never chooses what the card says. A prompt injection can at
most propose an action, which a human sees, with the server's real arguments, and can reject.
"""
import hashlib
import hmac
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update, func

from kirana_ai import config, kirana, memory
from kirana_ai.db import session_scope
from kirana_ai.db.models import Message as MessageRow
from kirana_ai.db.models import PendingAction, Role as RowRole, Thread
from kirana_ai.errors import UpstreamUnavailable

log = logging.getLogger("kirana_ai.actions")

# The Kirana scopes an action's write token needs. Saving a memory writes only our own table: no token.
WRITE_SCOPES = {"cancel_order": ("orders:write",), "add_to_cart": ("cart:write", "cart:read"),
                "remember_preference": ()}
FINAL = {"done", "failed", "rejected", "expired"}


class ApprovalNotFound(Exception):
    """Missing, or someone else's: the same answer for both (404)."""


class ApprovalTampered(Exception):
    """The stored row no longer matches its seal: refuse to act on it."""


@dataclass
class ApprovalView:
    id: uuid.UUID
    tool: str
    summary: str
    lines: list[str]
    status: str
    message: str | None
    expires_at: datetime


def _seal(action_id: uuid.UUID, user_id: int, tool: str, arguments: dict) -> str:
    payload = json.dumps([str(action_id), user_id, tool, arguments], sort_keys=True, default=str)
    return hmac.new(config.APPROVAL_HMAC_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _view(row: PendingAction) -> ApprovalView:
    return ApprovalView(id=row.id, tool=row.tool, summary=row.summary, lines=list(row.lines or []),
                        status=row.status, message=row.message, expires_at=row.expires_at)


# --- propose: called by the agent, inside a turn ---------------------------------------------

def propose(caller, tool: str, args: dict) -> tuple[dict, dict | None]:
    """
    Returns (what the model sees, the approval card data or None). Nothing is executed here.
    Pre-checks use a read token only: they exist to explain a refusal early ("order 5 is already
    paid"), not to enforce it. Kirana enforces again when the action runs.
    """
    try:
        if tool == "cancel_order":
            prepared = _prepare_cancel(caller, int(args["order_id"]))
        elif tool == "remember_preference":
            prepared = _prepare_memory(caller, args)
        else:
            prepared = _prepare_cart(args["items"])
    except kirana.NotPermitted:
        return {"error": "This account is not allowed to do that.", "denied_by": "kirana", "count": 0}, None
    except kirana.SessionExpired:
        return {"error": "The shopper's sign-in has expired. Ask them to sign in again.", "count": 0}, None
    if "error" in prepared:
        return prepared, None

    action_id = uuid.uuid4()
    expires = datetime.now(timezone.utc) + timedelta(seconds=config.APPROVAL_TTL_SECONDS)
    with session_scope() as session:
        session.add(PendingAction(
            id=action_id, thread_id=caller.thread_id, user_id=caller.user_id, tool=tool,
            arguments=prepared["arguments"], summary=prepared["summary"], lines=prepared["lines"],
            seal=_seal(action_id, caller.user_id, tool, prepared["arguments"]),
            status="pending", expires_at=expires,
        ))
    card = {"approval_id": str(action_id), "tool": tool, "summary": prepared["summary"],
            "lines": prepared["lines"], "expires_at": expires.isoformat()}
    for_model = {"status": "awaiting_confirmation", "approval_id": str(action_id), "summary": prepared["summary"],
                 "count": 1,
                 "note": "Nothing has happened yet. The shopper sees a card with Confirm and Reject. Ask them "
                         "to confirm there; never say it is done."}
    return for_model, card


def _prepare_cancel(caller, order_id: int) -> dict:
    order = kirana.my_order(caller.credentials.token("orders:read"), order_id)
    if order is None:
        return {"error": f"No order {order_id} on this shopper's account.", "found": False, "count": 0}
    if order.get("status") != "CREATED":
        return {"error": f"Order {order_id} is {order.get('status')}: only orders awaiting payment (CREATED) "
                         "can be cancelled. Explain this; for a paid order, point to the returns policy.",
                "count": 0}
    lines = [f"{i.get('quantity')} × {i.get('productName')}" for i in order.get("items") or []]
    return {"arguments": {"order_id": order_id},
            "summary": f"Cancel order #{order_id} (₹{order.get('total'):g})", "lines": lines}


def _prepare_memory(caller, args: dict) -> dict:
    """
    The card shows exactly what would be saved, and what it would replace; the shopper can reject it.

    `replaces` (an update, not an add): the model names a saved memory by its text; code resolves
    it to one of THIS shopper's active memories, and stores its id in the action. The model can
    point at a memory, but only the click removes it, and never someone else's.
    """
    text = " ".join(args["text"].split())
    kind = args.get("kind") or "preference"
    arguments = {"text": text, "kind": kind}
    replaces = (args.get("replaces") or "").strip()
    if not replaces:
        return {"arguments": arguments, "summary": "Save to your memory",
                "lines": [f"“{text}”", "Used in your future chats. You can delete it any time."]}
    old = memory.find_active(caller.user_id, replaces)
    if old is None:
        return {"error": f"No saved memory reads '{replaces}'. Use the exact text from the saved memories, "
                         "or propose without replaces.", "count": 0}
    arguments["replaces_id"] = str(old.id)
    return {"arguments": arguments, "summary": "Update your memory",
            "lines": [f"Replace “{old.text}”", f"with “{text}”", "Used in your future chats. You can delete it any time."]}


def _prepare_cart(items: list[dict]) -> dict:
    wanted = {int(i["product_id"]): int(i["quantity"]) for i in items}
    live = kirana.live(list(wanted))
    missing = [pid for pid in wanted if pid not in live]
    if missing:
        return {"error": f"Unknown product ids {missing}: find products with search_products first; never invent ids.",
                "count": 0}
    out = [pid for pid in wanted if live[pid].get("stock", 0) < wanted[pid]]
    if out:
        names = ", ".join(live[pid]["name"] for pid in out)
        return {"error": f"Not enough stock for: {names}. Suggest something else.", "count": 0}
    lines = [f"{q} × {live[pid]['name']} (₹{live[pid]['price']:g} each)" for pid, q in wanted.items()]
    arguments = {"items": [{"product_id": pid, "quantity": q} for pid, q in wanted.items()]}
    return {"arguments": arguments, "summary": f"Add {len(wanted)} item(s) to your cart", "lines": lines}


# --- decide: called by POST /v1/approvals/{id}, after the shopper's click --------------------

def get(user_id: int, action_id: uuid.UUID) -> ApprovalView:
    with session_scope() as session:
        row = session.get(PendingAction, action_id)
        if row is None or row.user_id != user_id:
            raise ApprovalNotFound()
        if row.status == "pending" and row.expires_at <= datetime.now(timezone.utc):
            row.status = "expired"
            row.message = "This request expired. Ask again if you still want it."
        return _view(row)


def decide(user_id: int, credentials, action_id: uuid.UUID, decision: str) -> ApprovalView:
    # Step 1, a short locked transaction: may this row be acted on, by this user, now?
    with session_scope() as session:
        row = session.execute(select(PendingAction).where(PendingAction.id == action_id)
                              .with_for_update()).scalar_one_or_none()
        if row is None or row.user_id != user_id:
            raise ApprovalNotFound()
        if not hmac.compare_digest(row.seal, _seal(row.id, row.user_id, row.tool, row.arguments)):
            log.error("pending action %s failed its seal check: refusing to act", action_id)
            raise ApprovalTampered()
        if row.status in FINAL:
            return _view(row)                       # a repeated click gets the first outcome
        if row.status == "pending" and row.expires_at <= datetime.now(timezone.utc):
            return _finish(session, row, "expired", "This request expired. Ask again if you still want it.")
        if decision == "reject":
            if row.status == "executing":
                return _view(row)                   # too late to reject: it is already running
            return _finish(session, row, "rejected", "Okay, I didn't do it.")
        row.status = "executing"                    # pending → executing; executing stays (a retry)
        tool, arguments, thread_id = row.tool, dict(row.arguments), row.thread_id

    # Step 2, no transaction held: exchange for a write token, then act at Kirana, idempotently.
    try:
        if tool == "remember_preference":
            replaces = uuid.UUID(arguments["replaces_id"]) if arguments.get("replaces_id") else None
            saved, replaced = memory.save(user_id, arguments["text"], arguments.get("kind", "preference"),
                                          source_thread=thread_id, approval_id=action_id, replaces=replaces,
                                          with_replaced=True)
            message = (f"Updated your memory: “{saved.text}” (replaces “{replaced}”)." if replaced
                       else f"Saved to your memory: “{saved.text}”.")
            status, result = "done", {"memory_id": str(saved.id)}
        else:
            token = credentials.token(*WRITE_SCOPES[tool])
            status, message, result = _execute(tool, arguments, token, str(action_id))
    except kirana.NotPermitted:
        status, message, result = "failed", "Your account is not allowed to do that.", None
    except kirana.SessionExpired:
        status, message, result = "failed", "Your sign-in expired. Sign in again and ask again.", None
    except UpstreamUnavailable:
        log.warning("action %s: Kirana unavailable; left executing so a retry can finish it", action_id)
        raise
    if status == "executing":
        return get(user_id, action_id)              # another click is finishing it right now

    # Step 3, a short transaction: the outcome, and an assistant message built from Kirana's answer.
    with session_scope() as session:
        row = session.execute(select(PendingAction).where(PendingAction.id == action_id)
                              .with_for_update()).scalar_one()
        if row.status in FINAL:
            # Two clicks can both get Kirana's (replayed) answer; only the first one reports it.
            return _view(row)
        view = _finish(session, row, status, message, result)
        if thread_id is not None:
            session.add(MessageRow(thread_id=thread_id, role=RowRole.ASSISTANT, content=message))
            session.execute(update(Thread).where(Thread.id == thread_id).values(updated_at=func.now()))
        return view


def _execute(tool: str, arguments: dict, token: str, key: str) -> tuple[str, str, dict | None]:
    if tool == "cancel_order":
        order_id = arguments["order_id"]
        code, body = kirana.cancel_order(token, order_id, key)
        if code == 409 and (body or {}).get("title") == "Request in progress":
            return "executing", "", None
        if code == 200:
            return "done", f"Done: order #{order_id} is cancelled.", {"order": body}
        return "failed", f"Couldn't cancel order #{order_id}: {(body or {}).get('detail', 'Kirana refused')}.", body

    added = []
    for item in arguments["items"]:
        pid, qty = item["product_id"], item["quantity"]
        code, body = kirana.add_to_cart(token, pid, qty, f"{key}:{pid}")
        if code == 409 and (body or {}).get("title") == "Request in progress":
            return "executing", "", None
        if code >= 400:
            return "failed", f"Couldn't add product {pid}: {(body or {}).get('detail', 'Kirana refused')}.", body
        added.append(pid)
    cart = kirana.my_cart(token) or {}
    total = cart.get("total")
    # The total comes from Kirana's cart, never from the model's arithmetic.
    message = f"Done: added {len(added)} item(s) to your cart." + (f" Cart total ₹{total:g}." if total is not None else "")
    return "done", message, {"cart": cart}


def _finish(session, row: PendingAction, status: str, message: str, result: dict | None = None) -> ApprovalView:
    row.status = status
    row.message = message
    row.result = result
    row.decided_at = datetime.now(timezone.utc)
    return _view(row)
