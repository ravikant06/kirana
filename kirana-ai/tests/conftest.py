"""
Shared fixtures.

    pg          a real Postgres 17 (Testcontainers), set up exactly like the real one:
                infra/seed/ai-schema.sql as superuser, then `alembic upgrade head`
    ai_db       pg, plus the app's own engine pointed at it (for code using session_scope)
    FakeAdapter an LLM that replays scripted replies (or raises), no network
"""
from collections.abc import Sequence
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from kirana_ai import config, db
from kirana_ai.llm import LLMAdapter, LLMResponse, Message, ToolSpec

AI_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_SQL = AI_ROOT.parent / "infra" / "seed" / "ai-schema.sql"


class FakeAdapter(LLMAdapter):
    """Replays scripted replies and records what it was sent. An Exception in the script is raised."""

    provider = "fake"

    def __init__(self, replies: list[LLMResponse | Exception], model: str = "fake-model") -> None:
        super().__init__(model=model, api_key="none")
        self._replies = list(replies)
        self.calls: list[list[Message]] = []

    def _complete(self, messages: Sequence[Message], *, tools: Sequence[ToolSpec] = (),
                  system: str | None = None) -> LLMResponse:
        self.calls.append(list(messages))
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture(scope="session")
def pg():
    from testcontainers.postgres import PostgresContainer  # deprecated alias, still shipped

    with PostgresContainer("postgres:17", username="kirana", password="kirana",
                           dbname="kirana", driver="psycopg") as container:
        admin_url = container.get_connection_url()
        with psycopg.connect(admin_url.replace("+psycopg", ""), autocommit=True) as conn:
            conn.execute(SCHEMA_SQL.read_text())
            # Stand-in for one of Kirana's tables, owned by the superuser as in real life.
            conn.execute("CREATE TABLE public.orders (id BIGINT PRIMARY KEY)")

        ai_url = admin_url.replace("kirana:kirana@", "kirana_ai:kirana_ai@")
        cfg = Config(str(AI_ROOT / "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", ai_url)
        command.upgrade(cfg, "head")

        engine = create_engine(ai_url)
        yield engine
        engine.dispose()


@pytest.fixture
def ai_db(pg, monkeypatch):
    """Point kirana_ai.db at the test database, and start every test from empty tables."""
    monkeypatch.setattr(config, "DATABASE_URL", pg.url.render_as_string(hide_password=False))
    db.engine.cache_clear()
    db._session_factory.cache_clear()
    with pg.begin() as conn:
        conn.execute(text("TRUNCATE threads, messages, llm_calls, tool_decisions, pending_actions, memories"))
    yield pg
    db.engine().dispose()
    db.engine.cache_clear()
    db._session_factory.cache_clear()


@pytest.fixture
def anyio_backend():
    return "asyncio"


# --- Phase 5: tokens signed like Kirana's, verified by the real auth.verify ---------------

from datetime import datetime, timedelta, timezone  # noqa: E402

import jwt  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

from kirana_ai import auth  # noqa: E402

TEST_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
TEST_KID = "test-key"


def make_token(user_id: int = 7, *, key=None, kid: str = TEST_KID, aud="kirana-ai", iss: str = "kirana",
               expires_in: int = 3600, alg: str = "RS256", role: str = "SHOPPER", scope: str = "orders:read orders:write cart:read cart:write chat") -> str:
    now = datetime.now(timezone.utc)
    claims = {"sub": str(user_id), "aud": aud, "iss": iss, "iat": now, "exp": now + timedelta(seconds=expires_in),
              "role": role, "scope": scope}
    return jwt.encode(claims, key or TEST_KEY, algorithm=alg, headers={"kid": kid})


def bearer(user_id: int = 7, **kw) -> dict:
    return {"Authorization": f"Bearer {make_token(user_id, **kw)}"}


def admin_bearer(user_id: int = 1) -> dict:
    return bearer(user_id, role="ADMIN", scope="orders:read orders:write cart:read cart:write chat catalog:write users:read kb:write system")


class FakeJwks:
    """Stands in for PyJWKClient: knows one key id, like Kirana's JWKS with one key."""

    def get_signing_key_from_jwt(self, token):
        kid = jwt.get_unverified_header(token).get("kid")
        if kid != TEST_KID:
            raise jwt.PyJWKClientError(f'Unable to find a signing key that matches: "{kid}"')
        return type("Key", (), {"key": TEST_KEY.public_key()})()


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch):
    monkeypatch.setattr(auth, "_jwks", lambda: FakeJwks())
