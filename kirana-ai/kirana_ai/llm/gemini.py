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
import hashlib
import itertools
import json
import logging
import time
from functools import cache
from collections.abc import Iterator, Sequence
from typing import Any

from google import genai
from google.genai import types

from kirana_ai.llm.base import LLMAdapter
from kirana_ai.llm.registry import register
from kirana_ai.llm.types import LLMError, LLMResponse, Message, Role, TextDelta, ToolCall, ToolSpec, Usage

log = logging.getLogger("kirana_ai.llm.gemini")

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


@cache
def _sdk_client(api_key: str) -> genai.Client:
    """One SDK client (and its HTTP connection pool) per process, shared by all adapters."""
    return genai.Client(api_key=api_key)


@register
class GeminiAdapter(LLMAdapter):
    provider = "gemini"

    def __init__(self, model: str, api_key: str) -> None:
        super().__init__(model, api_key)
        self._client = _sdk_client(api_key)
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
        return _merge_same_role(contents)

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
    def _stream(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] = (),
        system: str | None = None,
    ) -> Iterator[TextDelta | LLMResponse]:
        texts: list[str] = []
        calls: list[ToolCall] = []
        usage = None
        chunks = None
        try:
            chunks = self._client.models.generate_content_stream(
                model=self.model,
                contents=self._to_contents(messages),
                config=types.GenerateContentConfig(**self._config(tools, system)),
            )
            for chunk in chunks:
                content = chunk.candidates[0].content if chunk.candidates else None
                for p in (content.parts if content and content.parts else []):
                    if p.function_call:
                        # Tool calls arrive whole, not in pieces.
                        calls.append(ToolCall(id=f"gemini-{next(self._ids)}", name=p.function_call.name,
                                              arguments=dict(p.function_call.args or {}),
                                              provider_state=p.thought_signature))
                    elif p.text and not p.thought:
                        texts.append(p.text)
                        yield TextDelta(p.text)
                if chunk.usage_metadata:
                    usage = _usage(chunk)       # the last chunk carries the totals
        except Exception as exc:
            raise LLMError(f"Gemini call failed: {exc}") from exc
        finally:
            # Stopped early (client gone): close the SDK's stream so its HTTP connection is
            # released now, not whenever garbage collection gets to it.
            if chunks is not None and hasattr(chunks, "close"):
                chunks.close()
        yield LLMResponse(text=None if calls else ("".join(texts) or None),
                          tool_calls=tuple(calls), usage=usage)

    def _config(self, tools: Sequence[ToolSpec], system: str | None) -> dict[str, Any]:
        from kirana_ai import config

        # We drive the tool loop ourselves, so the SDK never may (also silences its warning).
        config_kwargs: dict[str, Any] = {
            "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True),
        }
        cached = self._prefix_cache(system, tools) if config.GEMINI_EXPLICIT_CACHE else None
        if cached:
            # Explicit cache (Phase 7 M5): system prompt and tools live in a cache object, billed at
            # the cached rate; the request names the cache instead of resending them.
            config_kwargs["cached_content"] = cached
        else:
            if system:
                config_kwargs["system_instruction"] = system
            if tools:
                config_kwargs["tools"] = self._to_tools(tools)
        thinking = self.thinking or config.GEMINI_THINKING_LEVEL
        if thinking:
            config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_level=thinking.upper())
        return config_kwargs

    def _prefix_cache(self, system: str | None, tools: Sequence[ToolSpec]) -> str | None:
        """
        The name of an explicit cache holding this exact system prompt + tools, created on first
        use and reused until shortly before it expires. Any failure (a prefix below the provider's
        minimum cacheable size, quota, an older model) falls back to sending the prefix normally.
        """
        if not system or not tools:
            return None
        key = hashlib.sha256(json.dumps([self.model, system, [[t.name, t.description, t.parameters]
                                                              for t in tools]]).encode()).hexdigest()
        hit = _CACHES.get(key)
        if hit and hit[1] > time.time() + 60:
            return hit[0]
        try:
            created = self._client.caches.create(model=self.model, config=types.CreateCachedContentConfig(
                system_instruction=system, tools=self._to_tools(tools), ttl=f"{_CACHE_TTL_S}s",
                display_name="kirana-ai-prefix"))
        except Exception as exc:   # noqa: BLE001 - caching is an optimisation, never a failure
            log.warning("explicit cache not created, sending the prefix normally: %s", exc)
            _CACHES[key] = (None, time.time() + 600)       # don't retry on every call
            return None
        _CACHES[key] = (created.name, time.time() + _CACHE_TTL_S)
        return created.name

    def _complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] = (),
        system: str | None = None,
    ) -> LLMResponse:
        config_kwargs = self._config(tools, system)
        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=self._to_contents(messages),
                config=types.GenerateContentConfig(**config_kwargs),
            )
        except Exception as exc:
            raise LLMError(f"Gemini call failed: {exc}") from exc

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
        return LLMResponse(text=text, tool_calls=calls, usage=_usage(response), raw=response)


def _usage(response: Any) -> Usage | None:
    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        return None
    output = meta.candidates_token_count
    if meta.thoughts_token_count:          # billed as output, never shown
        output = (output or 0) + meta.thoughts_token_count
    # Implicit prompt caching (Phase 7): tokens of the prompt's prefix served from Gemini's cache.
    return Usage(input_tokens=meta.prompt_token_count, output_tokens=output,
                 cached_input_tokens=getattr(meta, "cached_content_token_count", None))


_CACHE_TTL_S = 3600
_CACHES: dict[str, tuple[str | None, float]] = {}   # prefix hash -> (cache name, expires at)


def _merge_same_role(contents: list[types.Content]) -> list[types.Content]:
    """
    Consecutive turns of the same role become one (Phase 7): the context blocks (saved memories,
    summary, thread facts) are separate user messages before the history, and some providers
    reject two user turns in a row.
    """
    merged: list[types.Content] = []
    for content in contents:
        if merged and merged[-1].role == content.role:
            merged[-1] = types.Content(role=content.role, parts=[*merged[-1].parts, *content.parts])
        else:
            merged.append(content)
    return merged
