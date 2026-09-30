"""
Gemini adapter (Adaptee: the `google-genai` SDK).

Absorbs three mismatches between our neutral types and Gemini's API:

  1. Schema dialect - JSON Schema uses lowercase type names ("object"),
     Gemini's Schema enum wants uppercase ("OBJECT").
  2. Role naming    - we say "assistant", Gemini says "model".
  3. Call ids       - Gemini function calls carry no id, but OpenAI and
     Anthropic require one to correlate results, so we synthesise them.
  4. Thought signatures - thinking models require the signature attached
     to a function call to be echoed back when the turn is replayed. It
     rides along as ToolCall.provider_state, opaque to every caller.
"""
import itertools
from collections.abc import Sequence
from typing import Any

from google import genai
from google.genai import types

from kirana_ai.llm.base import LLMAdapter
from kirana_ai.llm.registry import register
from kirana_ai.llm.types import LLMResponse, Message, Role, ToolCall, ToolSpec

_SCHEMA_KEYS = ("description", "enum", "required")


def _to_gemini_schema(node: Any) -> Any:
    """Recursively convert JSON Schema to Gemini's Schema dialect."""
    if not isinstance(node, dict):
        return node

    out: dict[str, Any] = {}
    if "type" in node:
        out["type"] = str(node["type"]).upper()
    for key in _SCHEMA_KEYS:
        if key in node:
            out[key] = node[key]
    if "properties" in node:
        out["properties"] = {k: _to_gemini_schema(v) for k, v in node["properties"].items()}
    if "items" in node:
        out["items"] = _to_gemini_schema(node["items"])
    return out


@register
class GeminiAdapter(LLMAdapter):
    provider = "gemini"

    def __init__(self, model: str, api_key: str) -> None:
        super().__init__(model, api_key)
        self._client = genai.Client(api_key=api_key)
        self._ids = itertools.count(1)

    # --- translation: ours -> Gemini -------------------------------------
    def _to_contents(self, messages: Sequence[Message]) -> list[types.Content]:
        contents: list[types.Content] = []
        for msg in messages:
            if msg.role is Role.USER:
                contents.append(
                    types.Content(role="user", parts=[types.Part(text=msg.text or "")])
                )
            elif msg.role is Role.ASSISTANT:
                parts = []
                if msg.text:
                    parts.append(types.Part(text=msg.text))
                for call in msg.tool_calls:
                    parts.append(
                        types.Part(
                            function_call=types.FunctionCall(
                                name=call.name, args=call.arguments
                            ),
                            # Gemini's thinking models reject a replayed call
                            # whose signature is missing.
                            thought_signature=call.provider_state,
                        )
                    )
                contents.append(types.Content(role="model", parts=parts))
            else:  # Role.TOOL — Gemini carries results on a "user" turn
                result = msg.tool_result
                contents.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_function_response(
                                name=result.name, response=result.content
                            )
                        ],
                    )
                )
        return contents

    def _to_tools(self, tools: Sequence[ToolSpec]) -> list[types.Tool]:
        return [
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(
                        name=t.name,
                        description=t.description,
                        parameters=_to_gemini_schema(t.parameters),
                    )
                    for t in tools
                ]
            )
        ]

    # --- the Target interface --------------------------------------------
    def _complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] = (),
        system: str | None = None,
    ) -> LLMResponse:
        config_kwargs: dict[str, Any] = {}
        if system:
            config_kwargs["system_instruction"] = system
        if tools:
            config_kwargs["tools"] = self._to_tools(tools)
            # We drive the tool loop ourselves, so the SDK must not.
            config_kwargs["automatic_function_calling"] = (
                types.AutomaticFunctionCallingConfig(disable=True)
            )

        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=self._to_contents(messages),
                config=types.GenerateContentConfig(**config_kwargs),
            )
        except Exception as exc:
            raise SystemExit(f"Gemini call failed: {exc}") from exc

        parts = (response.candidates[0].content.parts or []) if response.candidates else []
        calls = tuple(
            ToolCall(
                id=f"gemini-{next(self._ids)}",
                name=p.function_call.name,
                arguments=dict(p.function_call.args or {}),
                provider_state=p.thought_signature,
            )
            for p in parts
            if p.function_call
        )
        text = None if calls else (response.text or None)
        return LLMResponse(text=text, tool_calls=calls, raw=response)
