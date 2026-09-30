"""
The Target interface of the Adapter pattern.

    Client   -> agent.py, which knows only this interface
    Target   -> LLMAdapter (below)
    Adaptee  -> google-genai / openai / anthropic SDKs
    Adapter  -> GeminiAdapter, OpenAIAdapter, AnthropicAdapter

Deliberately narrow (Interface Segregation): this describes chat completion
with tools and nothing else. Embeddings are a different capability with a
different shape, so they would get their own `EmbeddingAdapter` rather than
being bolted on here.
"""
import json
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from typing import ClassVar

from kirana_ai import trace
from kirana_ai.llm.types import CallRecord, LLMError, LLMResponse, Message, Role, ToolSpec

log = logging.getLogger(__name__)

#: Called once per LLM call, success or failure. See LLMAdapter.add_listener.
CallListener = Callable[[CallRecord], None]


class LLMAdapter(ABC):
    """Chat completion with optional tool calling, in provider-neutral terms."""

    #: Registry key, e.g. "gemini". Set by each concrete adapter.
    provider: ClassVar[str]

    def __init__(self, model: str, api_key: str) -> None:
        self.model = model
        self.api_key = api_key
        self._listeners: list[CallListener] = []

    def add_listener(self, listener: CallListener) -> None:
        """
        Observe every call: provider, model, tokens, latency, success or error.

        This is how cost recording plugs in (kirana_ai/usage.py) without the
        LLM layer knowing anything about the database.

        Listeners belong to this instance, so an adapter must never be shared by
        concurrent turns: one turn's recorder would record the other's calls.
        get_adapter() is cheap (the SDK client underneath is cached per process),
        so each turn gets its own.
        """
        self._listeners.append(listener)

    def remove_listener(self, listener: CallListener) -> None:
        self._listeners.remove(listener)

    def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] = (),
        system: str | None = None,
    ) -> LLMResponse:
        """
        Template Method: time and trace the call, delegate translation to _complete().

        Every adapter gets identical timing, tracing and call records for free,
        and that code lives in one place rather than being copied per provider.
        A failed call is reported to listeners too: it may still have cost money.
        """
        start = time.perf_counter()
        try:
            reply = self._traced_complete(messages, tools=tools, system=system)
        except LLMError as exc:
            self._notify(CallRecord(provider=self.provider, model=self.model, ok=False,
                                    latency_ms=_ms_since(start), error=str(exc)))
            raise
        self._notify(CallRecord(provider=self.provider, model=self.model, ok=True,
                                latency_ms=_ms_since(start), usage=reply.usage))
        return reply

    def _notify(self, record: CallRecord) -> None:
        # Observability must never break the thing it observes: a listener that
        # fails is logged, and the chat carries on.
        for listener in self._listeners:
            try:
                listener(record)
            except Exception:
                log.exception("LLM call listener failed")

    def _traced_complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] = (),
        system: str | None = None,
    ) -> LLMResponse:
        if not trace.is_on():
            return self._complete(messages, tools=tools, system=system)

        trace.section(f"LLM CALL -> {self.provider} / {self.model}")
        if system:
            trace.body("system instruction", system, limit=700)
        chars = self._context_chars(messages, system)
        trace.kv(
            "context size",
            f"{len(messages)} turn(s), {chars:,} chars (~{chars // 4:,} tokens)",
        )
        trace.bullets(
            f"messages ({len(messages)})",
            [self._describe(m) for m in messages],
        )
        # The retrieved text lives inside the tool turns. Show it in full mode,
        # since "what does the model actually see?" is the whole question.
        if trace.is_full():
            for i, message in enumerate(messages):
                if message.role is Role.TOOL:
                    trace.body(
                        f"  tool result [{i}] {message.tool_result.name}",
                        json.dumps(message.tool_result.content, indent=2,
                                   ensure_ascii=False, default=str),
                    )
        trace.kv("tools offered", ", ".join(t.name for t in tools) or "(none)")

        with trace.timed() as elapsed:
            reply = self._complete(messages, tools=tools, system=system)

        if reply.wants_tools:
            summary = f"{len(reply.tool_calls)} tool call(s)"
            trace.result(summary, elapsed[0])
            for call in reply.tool_calls:
                trace.bullets(
                    "  requested",
                    [f"{call.name}({trace.compact_json(call.arguments)})"],
                )
        else:
            trace.result("text answer", elapsed[0])
            trace.body("  answer", reply.text or "", limit=700)

        if reply.usage:
            trace.kv("tokens", f"in={reply.usage.input_tokens}  out={reply.usage.output_tokens}")
        return reply

    @staticmethod
    def _describe(message: Message) -> str:
        """One-line rendering of a turn, for the trace."""
        if message.role is Role.TOOL:
            result = message.tool_result
            content = result.content
            count = content.get("count", "?")
            if "results" in content:
                items = "; ".join(
                    f"{r['source']}"
                    + (f" > {r['heading']}" if r.get("heading") else "")
                    + f" ({r['score']}, {len(r.get('text', ''))} chars)"
                    for r in content["results"]
                )
                return f"[tool:{result.name}] -> {count} chunk(s): {items}"
            if "documents" in content:
                items = ", ".join(d.get("doc_id", "?") for d in content["documents"])
                return f"[tool:{result.name}] -> {count} doc(s): {items}"
            return f"[tool:{result.name}] -> {trace.compact_json(content)}"
        if message.tool_calls:
            calls = ", ".join(
                f"{c.name}({trace.compact_json(c.arguments, limit=160)})"
                for c in message.tool_calls
            )
            return f"[{message.role.value}] calls: {calls}"
        text = (message.text or "").replace("\n", " ")
        return f"[{message.role.value}] {text[:120]}"

    @abstractmethod
    def _complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] = (),
        system: str | None = None,
    ) -> LLMResponse:
        """
        Send a conversation, get one reply. Implementations must:
          - translate `messages` into the provider's own turn format
          - translate `tools` into the provider's tool envelope
          - return either text or tool_calls, never provider objects
        """

    @staticmethod
    def _context_chars(messages: Sequence[Message], system: str | None) -> int:
        """
        Rough size of what is being re-sent this round.

        Every tool result stays in the conversation and is sent again on each
        subsequent call, so this number grows with loop depth — the main cost
        driver of agentic retrieval.
        """
        total = len(system or "")
        for message in messages:
            total += len(message.text or "")
            for call in message.tool_calls:
                total += len(str(call.arguments))
            if message.tool_result:
                total += len(str(message.tool_result.content))
        return total

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} model={self.model!r}>"


def _ms_since(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)
