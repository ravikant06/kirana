"""
Provider-neutral data types.

These are the vocabulary the rest of the application speaks. No SDK type from
Gemini, OpenAI or Anthropic may appear above this layer — that constraint is
what makes the adapters swappable.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Role(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True)
class ToolSpec:
    """
    A tool, described once in plain JSON Schema.

    Every provider accepts JSON Schema; they disagree only on the envelope
    around it. Keeping the spec provider-neutral means adding a provider costs
    one adapter, not one rewrite of every tool.
    """
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    """
    A model's request to run a tool. `id` correlates the call with its result.

    `provider_state` is an opaque token some providers require to be echoed
    back verbatim when the turn is replayed (Gemini's thought_signature, for
    example). Nothing above the adapter layer may read or interpret it — it is
    carried, not understood.
    """
    id: str
    name: str
    arguments: dict[str, Any]
    provider_state: Any = None


@dataclass(frozen=True)
class ToolResult:
    """What our code returns for one ToolCall."""
    id: str
    name: str
    content: dict[str, Any]


@dataclass(frozen=True)
class Message:
    """One conversation turn, in neutral form."""
    role: Role
    text: str | None = None
    tool_calls: tuple[ToolCall, ...] = field(default_factory=tuple)
    tool_result: ToolResult | None = None

    @staticmethod
    def user(text: str) -> "Message":
        return Message(role=Role.USER, text=text)

    @staticmethod
    def assistant(text: str | None = None, tool_calls: tuple[ToolCall, ...] = ()) -> "Message":
        return Message(role=Role.ASSISTANT, text=text, tool_calls=tool_calls)

    @staticmethod
    def tool(result: ToolResult) -> "Message":
        return Message(role=Role.TOOL, tool_result=result)


@dataclass(frozen=True)
class LLMResponse:
    """
    One model reply. Either it answered (`text`) or it wants tools run
    (`tool_calls`) — `raw` is kept only for debugging, never for control flow.
    """
    text: str | None = None
    tool_calls: tuple[ToolCall, ...] = field(default_factory=tuple)
    raw: Any = None

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)
