"""
OpenAI adapter (Adaptee: the `openai` SDK).

Mismatches absorbed here:
  - tools are wrapped in {"type": "function", "function": {...}}
  - JSON Schema is passed through unchanged (no dialect conversion needed)
  - tool results are their own message role, correlated by tool_call_id
"""
import json
from functools import cache
from collections.abc import Iterator, Sequence
from typing import Any

from kirana_ai.llm.base import LLMAdapter
from kirana_ai.llm.registry import register
from kirana_ai.llm.types import LLMError, LLMResponse, Message, Role, TextDelta, ToolCall, ToolSpec, Usage


@cache
def _sdk_client(cls: type, api_key: str) -> Any:
    """One SDK client (and its HTTP connection pool) per process, shared by all adapters."""
    return cls(api_key=api_key)


@register
class OpenAIAdapter(LLMAdapter):
    provider = "openai"

    def __init__(self, model: str, api_key: str) -> None:
        super().__init__(model, api_key)
        try:
            from openai import OpenAI  # imported lazily: optional dependency
        except ImportError as exc:  # pragma: no cover
            raise SystemExit(
                "The openai package is not installed. `pip install openai` "
                "or set LLM_PROVIDER=gemini."
            ) from exc
        self._client = _sdk_client(OpenAI, api_key)

    def _to_messages(self, messages: Sequence[Message], system: str | None) -> list[dict]:
        out: list[dict] = []
        if system:
            out.append({"role": "system", "content": system})
        for msg in messages:
            if msg.role is Role.USER:
                out.append({"role": "user", "content": msg.text or ""})
            elif msg.role is Role.ASSISTANT:
                turn: dict[str, Any] = {"role": "assistant", "content": msg.text}
                if msg.tool_calls:
                    turn["tool_calls"] = [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                        }
                        for c in msg.tool_calls
                    ]
                out.append(turn)
            else:
                result = msg.tool_result
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": result.id,
                        "content": json.dumps(result.content),
                    }
                )
        return out

    @staticmethod
    def _to_tools(tools: Sequence[ToolSpec]) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,  # plain JSON Schema, as-is
                },
            }
            for t in tools
        ]

    def _stream(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] = (),
        system: str | None = None,
    ) -> Iterator[TextDelta | LLMResponse]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self._to_messages(messages, system),
            "stream": True,
            "stream_options": {"include_usage": True},   # usage arrives in a final, choice-less chunk
        }
        if tools:
            kwargs["tools"] = self._to_tools(tools)
        texts: list[str] = []
        calls: dict[int, dict] = {}       # tool calls stream in fragments, keyed by index
        usage = None
        try:
            for chunk in self._client.chat.completions.create(**kwargs):
                if chunk.usage is not None:
                    details = getattr(chunk.usage, "prompt_tokens_details", None)
                    usage = Usage(input_tokens=chunk.usage.prompt_tokens,
                                  output_tokens=chunk.usage.completion_tokens,
                                  cached_input_tokens=getattr(details, "cached_tokens", None))
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    texts.append(delta.content)
                    yield TextDelta(delta.content)
                for tc in delta.tool_calls or []:
                    c = calls.setdefault(tc.index, {"id": None, "name": "", "args": ""})
                    c["id"] = tc.id or c["id"]
                    if tc.function and tc.function.name:
                        c["name"] += tc.function.name
                    if tc.function and tc.function.arguments:
                        c["args"] += tc.function.arguments
        except Exception as exc:
            raise LLMError(f"OpenAI call failed: {exc}") from exc
        tool_calls = tuple(ToolCall(id=c["id"], name=c["name"], arguments=json.loads(c["args"] or "{}"))
                           for _, c in sorted(calls.items()))
        yield LLMResponse(text=None if tool_calls else ("".join(texts) or None),
                          tool_calls=tool_calls, usage=usage)

    def _complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] = (),
        system: str | None = None,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self._to_messages(messages, system),
        }
        if tools:
            kwargs["tools"] = self._to_tools(tools)

        try:
            response = self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise LLMError(f"OpenAI call failed: {exc}") from exc

        choice = response.choices[0].message
        calls = tuple(
            ToolCall(id=c.id, name=c.function.name, arguments=json.loads(c.function.arguments or "{}"))
            for c in (choice.tool_calls or [])
        )
        usage = None
        if response.usage is not None:
            # completion_tokens already includes any reasoning tokens.
            usage = Usage(input_tokens=response.usage.prompt_tokens,
                          output_tokens=response.usage.completion_tokens)
        return LLMResponse(text=None if calls else choice.content, tool_calls=calls,
                           usage=usage, raw=response)
