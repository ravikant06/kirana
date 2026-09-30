"""
The agent loop, driven by a FakeAdapter — no network, no API key, no provider SDK.

If agent.py contained any Gemini-specific code these tests could not exist,
which is the point of the Adapter pattern: the Client depends on the
abstraction only.
"""
from conftest import FakeAdapter
from kirana_ai import agent
from kirana_ai.llm import LLMResponse, Message, ToolCall
from kirana_ai.llm.registry import available


def _chunk(i: int = 1) -> dict:
    return {"chunk_id": f"c{i}", "source": "policy-returns.md", "heading": "Return windows",
            "chunk_index": i, "text": "Packaged staples: 7 days from delivery", "score": 0.8}


def _search(query: str, **where) -> LLMResponse:
    return LLMResponse(tool_calls=(ToolCall(id=query, name="search_docs",
                                            arguments={"query": query, **where}),))


def test_all_providers_registered():
    for name in ("gemini", "openai", "anthropic"):
        assert name in available()


def test_tool_spec_is_plain_json_schema_without_tenant():
    params = agent.SEARCH_DOCS.parameters
    assert params["type"] == "object"          # lowercase = JSON Schema
    assert params["required"] == ["query"]
    assert "tenant_id" not in params["properties"]   # never model-selectable


def test_one_search_then_answer(monkeypatch):
    monkeypatch.setattr(agent, "_run_search", lambda args, tenant, k: [_chunk()])
    fake = FakeAdapter([
        _search("return window rice", doc_type="policy"),
        LLMResponse(text="Unopened rice can be returned within 7 days.\nSources: policy-returns.md"),
    ])

    chunks, answer, steps = agent.answer("can I return rice?", llm=fake)

    assert answer.startswith("Unopened rice")
    assert steps == [{"tool": "search_docs", "query": "return window rice",
                      "where": {"doc_type": "policy"}, "count": 1}]
    assert len(chunks) == 1
    # The second call carries the tool result back: user, assistant(tool call), tool.
    assert len(fake.calls[1]) == 3
    assert fake.calls[1][2].tool_result.content["count"] == 1


def test_searches_again_after_empty_result(monkeypatch):
    results = iter([[], [_chunk()]])
    monkeypatch.setattr(agent, "_run_search", lambda args, tenant, k: next(results))
    fake = FakeAdapter([
        _search("x", doc_type="faq"),
        _search("x"),
        LLMResponse(text="I couldn't find that in our store policies."),
    ])

    _, answer, steps = agent.answer("?", llm=fake)

    assert [s["count"] for s in steps] == [0, 1]
    assert "couldn't find" in answer


def test_invented_filter_becomes_a_tool_error_not_a_crash(monkeypatch):
    """The real _run_search runs up to build_filter, which rejects the unknown key."""
    fake = FakeAdapter([
        _search("x", severity="SEV-1"),
        LLMResponse(text="Sorry."),
    ])

    _, answer, steps = agent.answer("?", llm=fake)

    assert steps[0]["count"] == 0
    assert "Unknown filter" in fake.calls[1][2].tool_result.content["error"]
    assert answer == "Sorry."


def test_budget_exhausted_forces_an_answer_without_tools(monkeypatch):
    monkeypatch.setattr(agent, "_run_search", lambda args, tenant, k: [])
    fake = FakeAdapter([_search(f"q{i}") for i in range(agent.MAX_STEPS)]
                       + [LLMResponse(text="final")])

    _, answer, steps = agent.answer("?", llm=fake)

    assert len(steps) == agent.MAX_STEPS
    assert answer == "final"


def test_history_is_sent_before_the_new_question(monkeypatch):
    """The model is stateless: a follow-up works only because earlier turns are resent."""
    monkeypatch.setattr(agent, "_run_search", lambda args, tenant, k: [_chunk()])
    history = [Message.user("Can I return opened rice?"),
               Message.assistant("Only if it is defective.")]
    fake = FakeAdapter([LLMResponse(text="Sealed rice: within 7 days.")])

    agent.answer("and if it's sealed?", history=history, llm=fake)

    sent = fake.calls[0]
    assert [m.text for m in sent] == ["Can I return opened rice?", "Only if it is defective.",
                                      "and if it's sealed?"]
