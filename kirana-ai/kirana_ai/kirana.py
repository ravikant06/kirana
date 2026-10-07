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


class SessionExpired(Exception):
    """Kirana refused the shopper's token (it expired during the turn)."""


class NotPermitted(Exception):
    """Kirana answered 403: this account may not do that. Permanent, unlike an outage: never retry."""


def _get(path: str, token: str | None = None, **params):
    # The shopper's own token, forwarded as-is (Phase 5): Kirana decides whose data this is.
    headers = {"Authorization": f"Bearer {token}"} if token else None
    try:
        r = _client().get(path, params=params, headers=headers)
    except httpx.HTTPError as exc:
        raise UpstreamUnavailable("kirana", f"{path}: {type(exc).__name__}: {exc}") from exc
    if r.status_code == 404:
        return None
    if r.status_code == 401 and token:
        raise SessionExpired()
    if r.status_code == 403:
        raise NotPermitted()
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


def my_orders(token: str) -> list[dict]:
    """The signed-in shopper's orders, newest first. Kirana picks the user from the token."""
    return _get("/orders", token=token) or []


def my_order(token: str, order_id: int) -> dict | None:
    """One order, or None: missing and someone else's look the same (Kirana answers 404 for both)."""
    return _get(f"/orders/{order_id}", token=token)


def exchange(user_token: str, scopes: tuple[str, ...]) -> str:
    """
    Kirana's token exchange (RFC 8693 style, Phase 6 M3): the shopper's token in, a narrower one out.
    The AI service authenticates itself as a client (HTTP Basic), so Kirana knows who the actor is.
    401/403 here mean the shopper's token can't be narrowed to these scopes: a refusal, not an outage.
    """
    body = {"grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "subject_token": user_token, "scope": " ".join(scopes)}
    try:
        r = _client().post("/auth/token-exchange", json=body,
                           auth=(config.AI_CLIENT_ID, config.AI_CLIENT_SECRET))
    except httpx.HTTPError as exc:
        raise UpstreamUnavailable("kirana", f"token exchange: {type(exc).__name__}: {exc}") from exc
    if r.status_code == 401:
        raise SessionExpired()
    if r.status_code in (400, 403):
        raise NotPermitted()
    if r.status_code >= 400:
        raise UpstreamUnavailable("kirana", f"token exchange answered {r.status_code}: {r.text[:200]}")
    return r.json()["access_token"]


def _post(path: str, token: str, body: dict, idempotency_key: str) -> tuple[int, dict | None]:
    """A write, always with an Idempotency-Key (Kirana Stage 7): a retry replays, never repeats."""
    try:
        r = _client().post(path, json=body, headers={"Authorization": f"Bearer {token}",
                                                      "Idempotency-Key": idempotency_key})
    except httpx.HTTPError as exc:
        raise UpstreamUnavailable("kirana", f"{path}: {type(exc).__name__}: {exc}") from exc
    if r.status_code == 401:
        raise SessionExpired()
    if r.status_code == 403:
        raise NotPermitted()
    if r.status_code >= 500:
        raise UpstreamUnavailable("kirana", f"{path} answered {r.status_code}: {r.text[:200]}")
    return r.status_code, (r.json() if r.content else None)


def cancel_order(token: str, order_id: int, idempotency_key: str) -> tuple[int, dict | None]:
    return _post(f"/orders/{order_id}/cancel", token, None, idempotency_key)


def add_to_cart(token: str, product_id: int, quantity: int, idempotency_key: str) -> tuple[int, dict | None]:
    return _post("/cart/items", token, {"productId": product_id, "quantity": quantity}, idempotency_key)


def my_cart(token: str) -> dict | None:
    return _get("/cart", token=token)
