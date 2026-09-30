"""
Provider-neutral LLM access (Adapter pattern).

    from kirana_ai.llm import get_adapter, Message, ToolSpec

    llm = get_adapter()                      # honours config.LLM_PROVIDER
    reply = llm.complete([Message.user("hi")], tools=[...], system="...")

Adding a provider means adding one module here and decorating the class with
@register — no caller changes.
"""
from kirana_ai.llm.base import LLMAdapter
from kirana_ai.llm.registry import available, get_adapter, register
from kirana_ai.llm.types import (
    LLMResponse,
    Message,
    Role,
    ToolCall,
    ToolResult,
    ToolSpec,
)

# Importing the concrete adapters is what populates the registry. Their SDK
# imports are lazy (inside __init__), so an uninstalled provider costs nothing
# until someone actually selects it.
from kirana_ai.llm import anthropic, gemini, openai  # noqa: E402,F401  (side-effect: registration)

__all__ = [
    "LLMAdapter", "LLMResponse", "Message", "Role",
    "ToolCall", "ToolResult", "ToolSpec",
    "available", "get_adapter", "register",
]
