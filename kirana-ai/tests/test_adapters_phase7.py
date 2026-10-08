"""Phase 7 adapter changes: context blocks as consecutive user turns, explicit prompt cache, thinking override."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from kirana_ai import config
from kirana_ai.llm import Message, ToolSpec
from kirana_ai.llm.types import Role, ToolResult

gemini = pytest.importorskip("kirana_ai.llm.gemini")


def _adapter():
    return gemini.GeminiAdapter("gemini-test", "not-a-real-key")


def test_gemini_merges_consecutive_user_turns_into_one():
    msgs = [replace(Message.user("memories"), block="memories"), replace(Message.user("summary"), block="summary"),
            Message.user("question")]
    contents = _adapter()._to_contents(msgs)
    assert len(contents) == 1 and [p.text for p in contents[0].parts] == ["memories", "summary", "question"]


def test_anthropic_merges_consecutive_user_turns():
    anthropic = pytest.importorskip("kirana_ai.llm.anthropic")
    adapter = object.__new__(anthropic.AnthropicAdapter)
    out = adapter._to_messages([Message.user("memories"), Message.user("question"),
                                Message.assistant("hi"), Message.user("next")])
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    assert [b["text"] for b in out[0]["content"]] == ["memories", "question"]


class FakeCaches:
    def __init__(self, fail=False):
        self.fail, self.created = fail, 0

    def create(self, model, config):
        self.created += 1
        if self.fail:
            raise RuntimeError("cached content is below the minimum token count")
        return SimpleNamespace(name="cachedContents/abc")


def test_explicit_cache_replaces_system_and_tools_and_is_reused(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_EXPLICIT_CACHE", True)
    gemini._CACHES.clear()
    a = _adapter()
    a._client = SimpleNamespace(caches=FakeCaches())
    tools = [ToolSpec("t", "a tool", {"type": "object", "properties": {}})]
    first = a._config(tools, "system prompt")
    second = a._config(tools, "system prompt")
    assert first["cached_content"] == "cachedContents/abc" and "system_instruction" not in first and "tools" not in first
    assert second["cached_content"] == "cachedContents/abc" and a._client.caches.created == 1


def test_explicit_cache_failure_falls_back_to_sending_the_prefix(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_EXPLICIT_CACHE", True)
    gemini._CACHES.clear()
    a = _adapter()
    a._client = SimpleNamespace(caches=FakeCaches(fail=True))
    cfg = a._config([ToolSpec("t", "d", {"type": "object", "properties": {}})], "system prompt")
    assert "cached_content" not in cfg and cfg["system_instruction"] == "system prompt"


def test_thinking_override_for_one_adapter(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_THINKING_LEVEL", None)
    a = _adapter()
    assert "thinking_config" not in a._config([], None)
    a.thinking = "minimal"
    assert a._config([], None)["thinking_config"].thinking_level.name == "MINIMAL"
