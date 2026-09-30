"""
Database access: one engine per process, sessions per unit of work.

    from kirana_ai.db import session_scope
    with session_scope() as session:      # commits on success, rolls back on error
        session.add(thread)

Transactions are short and explicit: a chat turn never holds a database
transaction open while it waits seconds for an LLM (the Stage 2 pool lesson).
"""
from collections.abc import Iterator
from contextlib import contextmanager
from functools import cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from kirana_ai import config


@cache
def engine() -> Engine:
    return create_engine(
        config.DATABASE_URL,
        echo=config.SQL_ECHO,
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=True,   # a restarted Postgres drops pooled connections; check before use
    )


@cache
def _session_factory() -> sessionmaker[Session]:
    # expire_on_commit=False: objects stay readable after commit, so a turn can
    # commit and then build its response without an extra SELECT per object.
    return sessionmaker(engine(), expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = _session_factory()()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
