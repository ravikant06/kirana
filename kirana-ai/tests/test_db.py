"""
Schema tests against a real Postgres 17 (Testcontainers, so Docker must be running).

The `pg` fixture (conftest.py) is set up the way the real database is:
infra/seed/ai-schema.sql as the superuser, then `alembic upgrade head` as kirana_ai.
"""
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, InvalidRequestError
from sqlalchemy.orm import Session

from kirana_ai.db.models import Base, Message, Role, Thread


def _only_ai(name, type_, _parent):
    return type_ != "schema" or name == "ai"


def test_migrations_match_the_models(pg):
    """The Python equivalent of ddl-auto=validate: no drift between models and schema."""
    with pg.connect() as conn:
        conn.dialect.default_schema_name = "public"   # same as migrations/env.py
        ctx = MigrationContext.configure(conn, opts={
            "include_schemas": True, "include_name": _only_ai,
            "version_table_schema": "ai",
        })
        diffs = compare_metadata(ctx, Base.metadata)
    assert diffs == []


def test_ai_role_cannot_read_kirana_tables(pg):
    with pg.connect() as conn, pytest.raises(Exception, match="permission denied"):
        conn.execute(text("SELECT * FROM public.orders"))


def test_unqualified_names_resolve_to_ai(pg):
    with pg.connect() as conn:
        assert conn.execute(text("SHOW search_path")).scalar() == "ai"
        assert conn.execute(text("SELECT count(*) FROM threads")).scalar() >= 0


def test_thread_round_trip_and_cascade(pg):
    with Session(pg, expire_on_commit=False) as session:
        thread = Thread(user_id=7, title="Returns")
        session.add(thread)
        session.flush()
        session.add_all([
            Message(thread_id=thread.id, role=Role.USER, content="Can I return rice?"),
            Message(thread_id=thread.id, role=Role.ASSISTANT, content="Within 7 days.",
                    citations=[{"source": "policy-returns.md"}]),
        ])
        session.commit()
        thread_id = thread.id

    with Session(pg) as session:
        roles = session.execute(
            text("SELECT role FROM messages WHERE thread_id = :t ORDER BY created_at"),
            {"t": thread_id},
        ).scalars().all()
        assert roles == ["user", "assistant"]            # stored by value, lowercase

        session.delete(session.get(Thread, thread_id))
        session.commit()
        left = session.execute(
            text("SELECT count(*) FROM messages WHERE thread_id = :t"), {"t": thread_id}
        ).scalar()
        assert left == 0                                  # ON DELETE CASCADE


def test_relationships_refuse_to_lazy_load(pg):
    """Reading thread.messages without asking for it must fail loudly, not run a hidden query."""
    with Session(pg) as session:
        thread = Thread(user_id=8, title="x")
        session.add(thread)
        session.commit()
        with pytest.raises(InvalidRequestError):
            _ = thread.messages
        session.delete(thread)
        session.commit()


def test_role_check_constraint(pg):
    with Session(pg) as session:
        thread = Thread(user_id=9, title="x")
        session.add(thread)
        session.flush()
        with pytest.raises(IntegrityError, match="ck_messages_role"):
            session.execute(
                text("INSERT INTO messages (id, thread_id, role, content) "
                     "VALUES (gen_random_uuid(), :t, 'system', 'x')"),
                {"t": thread.id},
            )
