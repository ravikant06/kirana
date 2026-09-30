"""
Anthropic adapter (Adaptee: the `anthropic` SDK).

Mismatches absorbed here:
  - tools use `input_schema` rather than `parameters`
  - content is a list of typed blocks; tool calls arrive as `tool_use` blocks
  - tool results go back as `tool_result` blocks on a user turn
  - `max_tokens` is required, not optional
"""
from collections.abc import Sequence
from typing import Any

from kirana_ai.llm.base import LLMAdapter
from kirana_ai.llm.registry import register
from kirana_ai.llm.types import LLMError, LLMResponse, Message, Role, ToolCall, ToolSpec, Usage

MAX_TOKENS = 2048


@register
class AnthropicAdapter(LLMAdapter):
    provider = "anthropic"

    def __init__(self, model: str, api_key: str) -> None:
        super().__init__(model, api_key)
        try:
            from anthropic import Anthropic  # imported lazily: optional dependency
        except ImportError as exc:  # pragma: no cover
            raise SystemExit(
                "The anthropic package is not installed. `pip install anthropic` "
                "or set LLM_PROVIDER=gemini."
            ) from exc
        self._client = Anthropic(api_key=api_key)

    def _to_messages(self, messages: Sequence[Message]) -> list[dict]:
        out: list[dict] = []
        for msg in messages:
            if msg.role is Role.USER:
                out.append({"role": "user", "content": msg.text or ""})
            elif msg.role is Role.ASSISTANT:
                blocks: list[dict[str, Any]] = []
                if msg.text:
                    blocks.append({"type": "text", "text": msg.text})
                for call in msg.tool_calls:
                    blocks.append(
                        {"type": "tool_use", "id": call.id, "name": call.name,
                         "input": call.arguments}
                    )
                out.append({"role": "assistant", "content": blocks})
            else:
                result = msg.tool_result
                out.append(
                    {
                        "role": "user",
                        "content": [
                            {"type": "tool_result", "tool_use_id": result.id,
                             "content": str(result.content)}
                        ],
                    }
                )
        return out

    @staticmethod
    def _to_tools(tools: Sequence[ToolSpec]) -> list[dict]:
        return [
            {"name": t.name, "description": t.description, "input_schema": t.parameters}
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
            "max_tokens": MAX_TOKENS,
            "messages": self._to_messages(messages),
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = self._to_tools(tools)

        try:
            response = self._client.messages.create(**kwargs)
        except Exception as exc:
            raise LLMError(f"Anthropic call failed: {exc}") from exc

        calls = tuple(
            ToolCall(id=b.id, name=b.name, arguments=dict(b.input or {}))
            for b in response.content
            if b.type == "tool_use"
        )
        text = next((b.text for b in response.content if b.type == "text"), None)
        usage = Usage(input_tokens=response.usage.input_tokens,
                      output_tokens=response.usage.output_tokens)
        return LLMResponse(text=None if calls else text, tool_calls=calls,
                           usage=usage, raw=response)
