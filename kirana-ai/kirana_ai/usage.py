"""
Cost accounting: every LLM call becomes one row in ai.llm_calls.

A CallRecorder is attached to the adapter for one chat turn:

    recorder = CallRecorder(thread_id, turn_id)
    llm.add_listener(recorder)
    ...run the turn...
    recorder.summary()     # totals for this turn, for the API response

Each row is committed on its own, straight after its call returns, so the
record exists even if the turn fails later. A failed call is recorded too:
it may have been billed, and failures are part of what we need to see.
"""
import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal
from functools import cache
from pathlib import Path

import yaml

from kirana_ai import config
from kirana_ai.db import session_scope
from kirana_ai.db.models import CallStatus, LlmCall
from kirana_ai.llm import CallRecord

log = logging.getLogger(__name__)

PRICING_FILE = config.PROJECT_ROOT / "pricing.yaml"
_PER_MTOK = Decimal(1_000_000)


@dataclass(frozen=True)
class Price:
    input_per_mtok: Decimal
    output_per_mtok: Decimal


@cache
def load_prices(path: Path = PRICING_FILE) -> dict[str, Price]:
    """Models with both prices set. Anything else is unpriced, so its cost is NULL."""
    data = yaml.safe_load(path.read_text()) or {}
    prices = {}
    for model, entry in (data.get("models") or {}).items():
        entry = entry or {}
        if entry.get("input_per_mtok") is None or entry.get("output_per_mtok") is None:
            continue
        # str() first: Decimal(0.3) would carry the float's binary error.
        prices[model] = Price(Decimal(str(entry["input_per_mtok"])),
                              Decimal(str(entry["output_per_mtok"])))
    return prices


def cost_of(record: CallRecord, prices: dict[str, Price]) -> Decimal | None:
    """Tokens x price, or None when the price or the token counts are unknown."""
    price = prices.get(record.model)
    usage = record.usage
    if price is None or usage is None or usage.input_tokens is None or usage.output_tokens is None:
        return None
    cost = (usage.input_tokens * price.input_per_mtok
            + usage.output_tokens * price.output_per_mtok) / _PER_MTOK
    return cost.quantize(Decimal("0.000001"))


class CallRecorder:
    """LLM call listener that writes ai.llm_calls rows and keeps turn totals."""

    def __init__(self, thread_id: uuid.UUID | None, turn_id: uuid.UUID | None,
                 prices: dict[str, Price] | None = None) -> None:
        self.thread_id = thread_id
        self.turn_id = turn_id
        self.prices = load_prices() if prices is None else prices
        self.calls: list[tuple[CallRecord, Decimal | None]] = []

    def __call__(self, record: CallRecord) -> None:
        cost = cost_of(record, self.prices)
        self.calls.append((record, cost))
        usage = record.usage
        # Own short transaction: never shared with the turn's other writes.
        with session_scope() as session:
            session.add(LlmCall(
                thread_id=self.thread_id,
                turn_id=self.turn_id,
                provider=record.provider,
                model=record.model,
                status=CallStatus.OK if record.ok else CallStatus.ERROR,
                input_tokens=usage.input_tokens if usage else None,
                output_tokens=usage.output_tokens if usage else None,
                latency_ms=record.latency_ms,
                cost_usd=cost,
                error=record.error,
            ))

    def summary(self) -> dict:
        """Turn totals. cost_usd is None if any call in the turn was unpriced."""
        costs = [cost for _, cost in self.calls]
        return {
            "llm_calls": len(self.calls),
            "input_tokens": sum((r.usage.input_tokens or 0) for r, _ in self.calls if r.usage),
            "output_tokens": sum((r.usage.output_tokens or 0) for r, _ in self.calls if r.usage),
            "latency_ms": sum(r.latency_ms for r, _ in self.calls),
            "cost_usd": None if not costs or None in costs else sum(costs, Decimal(0)),
        }
