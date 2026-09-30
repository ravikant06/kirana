"""
Build Qdrant filters from plain, user-facing parameters.

Two rules shape this module:

1. `tenant_id` is ALWAYS applied. A caller that forgets it gets
   config.TENANT_ID, never an unscoped search. Leaking across a scope
   boundary is the one failure mode worth making structurally impossible.

2. Everything else is optional and defaults to None (no restriction). Each
   filter narrows the candidate set *before* the vector search runs, so
   top_k is filled from the matching subset rather than trimmed afterwards.
"""
from qdrant_client.http import models

from kirana_ai import config

# payload field <- filter name. Anything else raises, so a typo like
# `doc_typ="policy"` fails loudly instead of silently matching everything.
_MATCH_FIELDS = {
    "doc_type": "doc_type",
    "source": "source",
    "doc_id": "doc_id",
}
ALLOWED_FILTERS = set(_MATCH_FIELDS)


def _as_list(value: str | list[str]) -> list[str]:
    return [value] if isinstance(value, str) else list(value)


def build_filter(tenant_id: str | None = None, **where) -> models.Filter:
    """
    Compose a Qdrant filter. `where` accepts any key in ALLOWED_FILTERS;
    string or list values both work:

        build_filter(doc_type="policy")
        build_filter(source=["policy-returns.md", "faq.md"])
    """
    unknown = set(where) - ALLOWED_FILTERS
    if unknown:
        raise ValueError(
            f"Unknown filter(s): {', '.join(sorted(unknown))}. "
            f"Allowed: {', '.join(sorted(ALLOWED_FILTERS))}"
        )

    # Tenant scope is not optional.
    conditions: list[models.FieldCondition] = [
        models.FieldCondition(
            key="tenant_id",
            match=models.MatchValue(value=tenant_id or config.TENANT_ID),
        )
    ]

    for name, field in _MATCH_FIELDS.items():
        value = where.get(name)
        if value:
            conditions.append(
                models.FieldCondition(key=field, match=models.MatchAny(any=_as_list(value)))
            )

    return models.Filter(must=conditions)
