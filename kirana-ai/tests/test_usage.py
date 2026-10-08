"""Call records from the adapter, and cost from pricing (no database)."""
from decimal import Decimal

import pytest

from conftest import FakeAdapter
from kirana_ai.llm import CallRecord, LLMError, LLMResponse, Message, Usage
from kirana_ai.usage import Price, cost_of, load_prices


def test_listener_gets_usage_and_latency_for_a_successful_call():
    records: list[CallRecord] = []
    fake = FakeAdapter([LLMResponse(text="hi", usage=Usage(120, 30))])
    fake.add_listener(records.append)

    fake.complete([Message.user("hello")])

    (record,) = records
    assert record.ok and record.provider == "fake" and record.model == "fake-model"
    assert record.usage == Usage(120, 30)
    assert record.latency_ms >= 0


def test_failed_call_is_reported_then_raised():
    records: list[CallRecord] = []
    fake = FakeAdapter([LLMError("quota exceeded")])
    fake.add_listener(records.append)

    with pytest.raises(LLMError):
        fake.complete([Message.user("hello")])

    assert records[0].ok is False
    assert records[0].error == "quota exceeded"


def test_a_broken_listener_never_breaks_the_call():
    def broken(_record):
        raise RuntimeError("database down")

    fake = FakeAdapter([LLMResponse(text="still answers")])
    fake.add_listener(broken)

    assert fake.complete([Message.user("hello")]).text == "still answers"


def _record(model="m", usage=Usage(1_000, 200)) -> CallRecord:
    return CallRecord(provider="fake", model=model, ok=True, latency_ms=5, usage=usage)


def test_cost_is_exact_decimal():
    prices = {"m": Price(Decimal("0.30"), Decimal("2.50"))}
    # 1000 * 0.30 / 1M + 200 * 2.50 / 1M = 0.0003 + 0.0005
    assert cost_of(_record(), prices) == Decimal("0.000800")


def test_unknown_price_or_usage_is_none_not_zero():
    prices = {"m": Price(Decimal("0.30"), Decimal("2.50"))}
    assert cost_of(_record(model="other"), prices) is None
    assert cost_of(_record(usage=None), prices) is None


def test_pricing_file_skips_models_without_both_prices(tmp_path):
    path = tmp_path / "pricing.yaml"
    path.write_text("models:\n"
                    "  priced: {input_per_mtok: 0.3, output_per_mtok: 2.5}\n"
                    "  half: {input_per_mtok: 0.3, output_per_mtok: null}\n")
    prices = load_prices.__wrapped__(path)
    assert set(prices) == {"priced"}
    assert prices["priced"].input_per_mtok == Decimal("0.3")   # no float error


def test_streaming_records_one_call_with_usage():
    from kirana_ai.llm import TextDelta
    records: list[CallRecord] = []
    fake = FakeAdapter([LLMResponse(text="hello there", usage=Usage(12, 4))])
    fake.add_listener(records.append)

    items = list(fake.stream([Message.user("hi")]))

    assert [type(i).__name__ for i in items] == ["TextDelta", "LLMResponse"]
    assert records[0].ok and records[0].usage == Usage(12, 4)


def test_abandoned_stream_is_recorded_as_such():
    """The browser closed the stream: the call may still be billed, so it is recorded."""
    records: list[CallRecord] = []
    fake = FakeAdapter([LLMResponse(text="hello there", usage=Usage(12, 4))])
    fake.add_listener(records.append)

    stream = fake.stream([Message.user("hi")])
    next(stream)          # the first delta arrives...
    stream.close()        # ...and the client goes away

    assert records[0].ok is False and records[0].error == "stream abandoned by the client"


def test_judge_verdict_parsing_tolerates_fences_and_garbage():
    from eval.judge import parse_verdict
    v = parse_verdict('```json\n{"answered": true, "correct": false, "faithful": true, "reason": "x"}\n```')
    assert (v["answered"], v["correct"], v["faithful"]) == (True, False, True)
    bad = parse_verdict("I think it is fine")
    assert bad["answered"] is None and "unparseable" in bad["reason"]


# --- Phase 7 M1: what the prompt is made of, and the cache ------------------------------------

def test_each_call_records_tokens_per_block_scaled_to_the_reported_total():
    from dataclasses import replace
    from kirana_ai.llm import ToolResult, ToolSpec
    seen = []
    llm = FakeAdapter([LLMResponse(text="ok", usage=Usage(1000, 10))])
    llm.add_listener(seen.append)
    messages = [replace(Message.user("an earlier question " * 20), block="history"),
                Message.tool(ToolResult(id="1", name="search_docs", content={"results": ["x" * 400]})),
                replace(Message.user("the new question"), block="message")]
    llm.complete(messages, tools=[ToolSpec("t", "a tool", {"type": "object"})], system="be helpful " * 30)
    blocks = seen[0].context
    assert set(blocks) == {"system", "tools", "history", "tool_rounds", "message"}
    assert abs(sum(blocks.values()) - 1000) <= len(blocks)          # adds up to what the provider billed
    assert blocks["tool_rounds"] > blocks["message"]


def test_cached_input_is_priced_at_the_cached_rate_only_when_known():
    record = CallRecord(provider="x", model="m", ok=True, latency_ms=1,
                        usage=Usage(input_tokens=1_000_000, output_tokens=0, cached_input_tokens=800_000))
    full = Price(Decimal("1.50"), Decimal("9.00"))
    discounted = Price(Decimal("1.50"), Decimal("9.00"), cached_input_per_mtok=Decimal("0.375"))
    assert cost_of(record, {"m": full}) == Decimal("1.500000")          # no known discount: never underestimate
    assert cost_of(record, {"m": discounted}) == Decimal("0.600000")    # 200k x 1.50 + 800k x 0.375


def test_recorder_stores_cached_tokens_and_the_breakdown(ai_db):
    import uuid
    from sqlalchemy import text
    from kirana_ai.usage import CallRecorder
    recorder = CallRecorder(None, uuid.uuid4(), prices={})
    recorder(CallRecord(provider="fake", model="m", ok=True, latency_ms=5,
                        usage=Usage(100, 5, cached_input_tokens=60), context={"system": 40, "history": 60}))
    with ai_db.begin() as conn:
        row = conn.execute(text("select cached_tokens, context from llm_calls")).one()
    assert row.cached_tokens == 60 and row.context == {"system": 40, "history": 60}
    assert recorder.summary()["cached_input_tokens"] == 60
