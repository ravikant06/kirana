"""
Who is asking (Phase 5): verify the shopper's Kirana token, never trust a header.

Kirana signs tokens with RS256 and publishes the public key at /.well-known/jwks.json. We fetch
it once and cache it by key id (`kid`); a token with an unknown kid triggers one refetch, which
is how a key rotation reaches us. We hold no secret: we can verify tokens, never mint them.

What is checked, and why each matters:
  - algorithm RS256 only: rejects `alg: none`, and HS256 "signed" with the public key as a secret;
  - signature against Kirana's key: an edited `sub` breaks it;
  - `iss` = kirana, `aud` contains kirana-ai: a token minted for another app is refused;
  - `exp` (30 s leeway for clock skew): a stolen token stops working within the hour.

Permissions come from the token's `scope` claim (Kirana's role → permission bundle): `chat` to
use the assistant, `kb:write` to manage the knowledge base. We check permissions, never role
names, and never ask Kirana per request: the token is the whole answer (a role change applies at
the next sign-in).

The verified token is also kept: the order tools forward it to Kirana unchanged, so Kirana
decides whose orders they are (the AI never sends a user id).
"""
import logging
from dataclasses import dataclass
from functools import cache

import jwt

from kirana_ai import config
from kirana_ai.errors import UpstreamUnavailable

log = logging.getLogger("kirana_ai.auth")

ALGORITHMS = ["RS256"]
LEEWAY_SECONDS = 30


class InvalidToken(Exception):
    """Missing, malformed, forged, expired or not meant for us: always 401."""


class Forbidden(Exception):
    """A valid token without the permission: 403."""


@dataclass(frozen=True)
class Caller:
    user_id: int
    token: str          # forwarded to Kirana by the order tools; never logged, never sent to the LLM
    role: str = ""
    scopes: frozenset[str] = frozenset()

    def require(self, scope: str) -> "Caller":
        if scope not in self.scopes:
            raise Forbidden(f"needs permission '{scope}'; your role {self.role or '?'} doesn't have it")
        return self


@cache
def _jwks() -> jwt.PyJWKClient:
    # Keys cached in memory; refetched on an unknown kid (rotation) at most once per lookup.
    return jwt.PyJWKClient(config.KIRANA_JWKS_URL, cache_keys=True, lifespan=3600,
                           timeout=int(config.KIRANA_TIMEOUT_SECONDS))


def verify(token: str) -> Caller:
    try:
        key = _jwks().get_signing_key_from_jwt(token)
    except jwt.PyJWKClientConnectionError as exc:
        # Can't fetch Kirana's keys: we can't tell a good token from a forged one. Fail closed.
        raise UpstreamUnavailable("kirana", f"JWKS: {exc}") from exc
    except jwt.PyJWTError as exc:
        raise InvalidToken(f"unknown signing key or malformed token ({type(exc).__name__})") from exc
    try:
        claims = jwt.decode(token, key.key, algorithms=ALGORITHMS, audience=config.TOKEN_AUDIENCE,
                            issuer=config.TOKEN_ISSUER, leeway=LEEWAY_SECONDS,
                            options={"require": ["exp", "sub", "iss", "aud"]})
    except jwt.ExpiredSignatureError as exc:
        raise InvalidToken("token expired") from exc
    except jwt.PyJWTError as exc:
        raise InvalidToken(str(exc)) from exc
    try:
        return Caller(user_id=int(claims["sub"]), token=token, role=claims.get("role", ""),
                      scopes=frozenset((claims.get("scope") or "").split()))
    except ValueError as exc:
        raise InvalidToken("subject is not a user id") from exc


def from_header(authorization: str | None) -> Caller:
    if not authorization or not authorization[:7].lower() == "bearer ":
        raise InvalidToken("sign in first: Authorization: Bearer <token> is required")
    return verify(authorization[7:].strip())


class Credentials:
    """
    The shopper's verified token, held privately for one request, and the only way to get a token
    for Kirana (Phase 6 M3). Tools never see the shopper's own token, which carries every permission
    they have: they ask for exactly the scopes they need, and Kirana's token exchange returns a
    short-lived token with only those, marked as acting for the shopper via kirana-ai (`act` claim).

    A read turn can therefore never hold a token that writes; a write token is minted only by the
    approval endpoint, after the shopper's click, for that one action.
    """

    def __init__(self, user_token: str) -> None:
        self._user_token = user_token
        self._cache: dict[tuple[str, ...], str] = {}

    def token(self, *scopes: str) -> str:
        key = tuple(sorted(scopes))
        if key not in self._cache:
            from kirana_ai import kirana
            self._cache[key] = kirana.exchange(self._user_token, key)
        return self._cache[key]

    def __repr__(self) -> str:          # never print a token, not even by accident in a log line
        return "Credentials(<hidden>)"
