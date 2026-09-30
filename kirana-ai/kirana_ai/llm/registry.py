"""
Provider registry — the Open/Closed seam.

A new provider registers itself with @register and becomes usable via
config.LLM_PROVIDER. No existing module changes.
"""
from kirana_ai.llm.base import LLMAdapter

_REGISTRY: dict[str, type[LLMAdapter]] = {}


def register(cls: type[LLMAdapter]) -> type[LLMAdapter]:
    """Class decorator: add a concrete adapter to the registry."""
    if not getattr(cls, "provider", None):
        raise TypeError(f"{cls.__name__} must define a `provider` class attribute")
    _REGISTRY[cls.provider] = cls
    return cls


def available() -> list[str]:
    return sorted(_REGISTRY)


def get_adapter(provider: str | None = None, model: str | None = None) -> LLMAdapter:
    """
    Factory. Resolves provider name -> concrete adapter instance.

    Credentials and model default come from config, so callers say
    `get_adapter()` and get whatever the environment is configured for.

    Returns a new adapter every time: one per chat turn (see LLMAdapter.add_listener).
    """
    from kirana_ai import config  # local import keeps this module import-cheap

    name = (provider or config.LLM_PROVIDER).lower()
    if name not in _REGISTRY:
        raise SystemExit(
            f"Unknown LLM provider {name!r}. Available: {', '.join(available()) or 'none'}"
        )
    adapter_cls = _REGISTRY[name]
    return adapter_cls(
        model=model or config.GENERATION_MODEL,
        api_key=config.require_api_key(),
    )
