"""
The tool policy layer (Phase 6 M1): the gate between "the model proposes a tool call" and "the
tool runs". The model proposes; this deterministic code decides allow, deny, or ask a human.

    LLM ──► {tool, args} ──► authorize(caller, tool, args, used) ──► allow     → run the tool
                                                                 ├─► deny      → a tool error the model explains
                                                                 └─► approval  → a pending action + a card (M4)

One registry, RULES, holds everything the agent may do: per tool, the permission it needs (from the
token's `scope`), its risk tier, how many calls a turn may make, and how its arguments are checked.
A tool missing from RULES does not exist, whatever the model says.

Checked twice, on purpose:
  - offering: tools_for() shows the model only the tools this caller may use (capability removal);
  - enforcing: authorize() runs before every call, because a model can call a tool it was never
    shown (a hallucination, an injected instruction, an old tool list in the history).

Permissions answer "may this kind of user do this?". The policy adds what permissions can't:
the tool must exist, its arguments must make sense, a turn has a budget, and writes wait for a
human. Ownership ("is order 5 yours?") is still Kirana's to decide, on every call.

Every decision is recorded (ai.tool_decisions), so an audit or the security eval can say who
asked for what, what was decided, why, and by which layer.
"""
import enum
import hashlib
import json
import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("kirana_ai.policy")


class Risk(str, enum.Enum):
    READ_PUBLIC = "read-public"        # catalogue, store policies: anyone signed in
    READ_PERSONAL = "read-personal"    # the caller's own orders
    WRITE = "write"                    # changes something: never without a human's click


class Outcome(str, enum.Enum):
    ALLOW = "allow"
    DENY = "deny"
    APPROVAL = "approval"              # allowed to propose; runs only after the shopper confirms


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    reason: str
    layer: str = "policy"              # who decided: "policy" here; "kirana" for its 403/404 (recorded later)

    @property
    def allowed(self) -> bool:
        return self.outcome is not Outcome.DENY


# --- argument rules: return an error message, or None when the arguments are fine -------------

def _no_args(args: dict) -> str | None:
    return None


def _positive_int(name: str, maximum: int = 10**12):
    def check(args: dict) -> str | None:
        value = args.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
            return f"{name} must be a whole number"
        if not 1 <= int(value) <= maximum:
            return f"{name} must be between 1 and {maximum}"
        return None
    return check


def _optional_price(args: dict) -> str | None:
    price = args.get("max_price")
    if price in (None, ""):
        return None
    if isinstance(price, bool) or not isinstance(price, (int, float)) or not 0 < price <= 100_000:
        return "max_price must be a positive amount in rupees"
    return None


def _order_status(args: dict) -> str | None:
    status = args.get("status")
    if status in (None, "", "CREATED", "PAID", "CANCELLED", "FAILED"):
        return None
    return "status must be one of CREATED, PAID, CANCELLED, FAILED"


MAX_CART_LINES = 5
MAX_QUANTITY = 10


def _cart_items(args: dict) -> str | None:
    items = args.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_CART_LINES:
        return f"items must be a list of 1 to {MAX_CART_LINES} products"
    for item in items:
        if not isinstance(item, dict):
            return "each item needs product_id and quantity"
        for key, maximum in (("product_id", 10**12), ("quantity", MAX_QUANTITY)):
            error = _positive_int(key, maximum)(item)
            if error:
                return error
    return None


def _memory_text(args: dict) -> str | None:
    text = args.get("text")
    if not isinstance(text, str) or not 2 <= len(text.strip()) <= 200:
        return "text must be 2 to 200 characters"
    if args.get("kind") not in (None, "", "preference", "fact"):
        return "kind must be preference or fact"
    replaces = args.get("replaces")
    if replaces not in (None, "") and (not isinstance(replaces, str) or len(replaces) > 300):
        return "replaces must be the text of a saved memory"
    return None


@dataclass(frozen=True)
class Rule:
    tool: str
    scope: str | None              # permission the token must carry; None = any signed-in caller
    risk: Risk
    max_per_turn: int              # a hijacked or looping model can't call a tool forever
    check_args: Callable[[dict], str | None] = _no_args


RULES: dict[str, Rule] = {r.tool: r for r in (
    Rule("search_products", None, Risk.READ_PUBLIC, 3, _optional_price),
    Rule("search_docs", None, Risk.READ_PUBLIC, 4),
    Rule("list_documents", None, Risk.READ_PUBLIC, 2),
    Rule("get_my_orders", "orders:read", Risk.READ_PERSONAL, 2, _order_status),
    Rule("get_order", "orders:read", Risk.READ_PERSONAL, 5, _positive_int("order_id")),
    Rule("cancel_order", "orders:write", Risk.WRITE, 1, _positive_int("order_id")),
    Rule("add_to_cart", "cart:write", Risk.WRITE, 1, _cart_items),
    # Phase 7: saving to long-term memory is a write too: proposed by the model, confirmed by a click.
    Rule("remember_preference", "chat", Risk.WRITE, 2, _memory_text),
)}


@dataclass
class Caller:
    """
    Who this turn acts for. `credentials` (auth.Credentials) hands out narrowed tokens for Kirana;
    the tools never see the shopper's own token. Anonymous (CLI, evals): no scopes, no credentials.
    """
    user_id: int | None = None
    scopes: frozenset[str] = frozenset()
    credentials: Any = None
    thread_id: Any = None
    turn_id: Any = None

    def has(self, scope: str | None) -> bool:
        return scope is None or (self.credentials is not None and scope in self.scopes)


ANONYMOUS = Caller()


def tools_for(caller: Caller, specs: dict[str, Any]) -> list[Any]:
    """Offering: the tool specs this caller may see, in the registry's order."""
    return [specs[name] for name, rule in RULES.items() if name in specs and caller.has(rule.scope)]


def authorize(caller: Caller, tool: str, args: dict, used: Counter) -> Decision:
    """Enforcing: before every call, in the order an attacker would probe."""
    rule = RULES.get(tool)
    if rule is None:
        return Decision(Outcome.DENY, "unknown-tool")
    if not caller.has(rule.scope):
        return Decision(Outcome.DENY, f"missing-scope:{rule.scope}")
    error = rule.check_args(args or {})
    if error:
        return Decision(Outcome.DENY, f"bad-args:{error}")
    if used[tool] >= rule.max_per_turn:
        return Decision(Outcome.DENY, f"budget:{rule.max_per_turn}-per-turn")
    if rule.risk is Risk.WRITE:
        return Decision(Outcome.APPROVAL, "write-needs-approval")
    return Decision(Outcome.ALLOW, f"ok:{rule.risk.value}")


def refusal(decision: Decision) -> dict:
    """What the model sees for a denial: a reason it can explain, never a crash."""
    kind, _, detail = decision.reason.partition(":")
    message = {
        "unknown-tool": "There is no such tool. Answer with the tools you have.",
        "missing-scope": "This account is not allowed to do that. Tell the shopper plainly; do not retry or guess.",
        "bad-args": f"Invalid arguments: {detail}. Fix them or ask the shopper.",
        "budget": "Tool budget for this question used up. Answer with what you already have.",
    }.get(kind, "Not permitted.")
    return {"error": message, "denied_by": decision.layer, "count": 0}


def args_hash(args: dict) -> str:
    return hashlib.sha256(json.dumps(args or {}, sort_keys=True, default=str).encode()).hexdigest()[:16]


# --- the decision log ------------------------------------------------------------------------

@dataclass
class DecisionRecord:
    caller: Caller
    tool: str
    arguments: dict
    decision: Decision
    extra: dict = field(default_factory=dict)


Recorder = Callable[[DecisionRecord], bool]


def log_only(record: DecisionRecord) -> bool:
    log.info("policy user=%s tool=%s args#=%s decision=%s reason=%s layer=%s",
             record.caller.user_id, record.tool, args_hash(record.arguments),
             record.decision.outcome.value, record.decision.reason, record.decision.layer)
    return True


def record_to_db(record: DecisionRecord) -> bool:
    """
    One ai.tool_decisions row, in its own short transaction (like llm_calls: an audit row must not
    depend on the turn succeeding). Returns False if it could not be written; the caller decides what
    that means (a write is refused without its audit row, a read goes on with a warning).
    """
    log_only(record)
    from kirana_ai.db import session_scope
    from kirana_ai.db.models import ToolDecision
    try:
        with session_scope() as session:
            session.add(ToolDecision(
                thread_id=record.caller.thread_id, turn_id=record.caller.turn_id,
                user_id=record.caller.user_id, tool=record.tool[:60],
                arguments=record.arguments or {}, args_hash=args_hash(record.arguments),
                decision=record.decision.outcome.value, reason=record.decision.reason[:200],
                layer=record.decision.layer,
            ))
        return True
    except Exception as exc:   # noqa: BLE001 - an audit failure is reported, never raised into the turn
        log.error("could not record the policy decision for %s: %s", record.tool, exc)
        return False
