"""
OpenAI adapter (Adaptee: the `openai` SDK).

Mismatches absorbed here:
  - tools are wrapped in {"type": "function", "function": {...}}
  - JSON Schema is passed through unchanged (no dialect conversion needed)
  - tool results are their own message role, correlated by tool_call_id
"""
import json
from functools import cache
from collections.abc import Sequence
from typing import Any

from kirana_ai.llm.base import LLMAdapter
from kirana_ai.llm.registry import register
from kirana_ai.llm.types import LLMError, LLMResponse, Message, Role, ToolCall, ToolSpec, Usage


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
