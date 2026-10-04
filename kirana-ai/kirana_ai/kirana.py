"""
Kirana's REST API, as the AI service sees it (Phase 4 onwards).

The ownership rule in code: the AI service never reads Kirana's tables; it asks Kirana.
That way Kirana's own rules (soft-deleted products are gone, stock is live) apply to the
assistant too, and Kirana can change its schema without breaking us.

Every call has a timeout. A slow or failing Kirana becomes UpstreamUnavailable (503 for a
chat; a retry for a worker), never a hung request.
"""
from functools import cache

import httpx

from kirana_ai import config
from kirana_ai.errors import UpstreamUnavailable


@cache
def _client() -> httpx.Client:
    # One pooled client per process; it is thread-safe for requests.
    return httpx.Client(base_url=config.KIRANA_API_URL, timeout=config.KIRANA_TIMEOUT_SECONDS,
                        headers={"Accept": "application/json"})


def _get(path: str, **params):
    try:
        r = _client().get(path, params=params)
    except httpx.HTTPError as exc:
        raise UpstreamUnavailable("kirana", f"{path}: {type(exc).__name__}: {exc}") from exc
    if r.status_code == 404:
        return None
    if r.status_code >= 400:
        raise UpstreamUnavailable("kirana", f"{path} answered {r.status_code}: {r.text[:200]}")
    return r.json()


def product(product_id: int) -> dict | None:
    """ProductDetail, or None if it does not exist or is soft-deleted."""
    return _get(f"/products/{product_id}")


def product_pages(size: int = 100):
    """Every live product summary, page by page (Kirana's own paging)."""
    page = 0
    while True:
        batch = _get("/products", page=page, size=size)
        yield from batch["content"]
        page += 1
        if page >= batch["totalPages"]:
            return


def live(product_ids: list[int]) -> dict[int, dict]:
    """
    Current price, stock, category and thumbnail by id (GET /products/batch, at most 50 ids).
    Deleted or unknown ids are simply absent. Never cached here: it is asked for because it is live.
    """
    if not product_ids:
        return {}
    rows = _get("/products/batch", ids=",".join(str(i) for i in product_ids[:50])) or []
    return {row["id"]: row for row in rows}
