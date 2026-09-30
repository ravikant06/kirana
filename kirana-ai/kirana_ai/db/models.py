"""
The AI service's tables, all in schema `ai`.

    threads    one conversation, owned by one Kirana shopper
    messages   the turns of a thread, as the shopper sees them
    llm_calls  one row per LLM call: tokens, latency, cost

Conventions (the Python mirror of Kirana's):
  - Enums are VARCHAR + CHECK, stored by name (Kirana: EnumType.STRING), never
    a Postgres ENUM type, so adding a value is a one-line migration.
  - Time is timestamptz, set by the database (server_default now()).
  - Relationships are lazy="raise": touching thread.messages without loading
    it explicitly raises instead of silently running one query per thread
    (the N+1 from Kirana's Stage 2). Loading is always a visible decision.
  - No foreign keys to Kirana. `user_id` is a Kirana user id, but that table
    lives in another service's schema; we store the id and trust Kirana.

The schema itself is owned by Alembic (migrations/). The test suite checks
that these models and the migrations agree, like Hibernate's ddl-auto=validate.
"""
import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Deterministic constraint names, so migrations can refer to them.
NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(schema="ai", naming_convention=NAMING)


def _enum(cls: type[enum.Enum], name: str) -> Enum:
    """VARCHAR + CHECK holding the enum's lowercase value."""
    return Enum(
        cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=20,
        values_callable=lambda members: [m.value for m in members],
        validate_strings=True,
    )


class Role(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"


class CallStatus(str, enum.Enum):
    OK = "ok"
    ERROR = "error"


class Thread(Base):
    __tablename__ = "threads"

    # UUIDs, not sequences: thread and message ids appear in URLs, so they should
    # not be guessable or reveal how many chats exist. Ownership is still checked.
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int] = mapped_column(BigInteger)
    title: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list["Message"]] = relationship(
        back_populates="thread",
        order_by="Message.created_at",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
    )

    __table_args__ = (
        # "My threads, newest first" is the only list query.
        Index("ix_threads_user_id_updated_at", "user_id", updated_at.desc()),
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    thread_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ai.threads.id", ondelete="CASCADE")
    )
    role: Mapped[Role] = mapped_column(_enum(Role, "role"))
    content: Mapped[str] = mapped_column(Text)
    # Shown in the UI, never resent to the LLM (AD11: history is text only).
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default="[]")
    tool_steps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    thread: Mapped[Thread] = relationship(back_populates="messages", lazy="raise")

    __table_args__ = (Index("ix_messages_thread_id_created_at", "thread_id", "created_at"),)


class LlmCall(Base):
    """
    One LLM call. Written in its own transaction, as soon as the call returns.

    No foreign keys on purpose: a turn that fails half way has no assistant
    message to point at, but its LLM calls were still made and still cost money.
    Cost records must survive failed turns, so they cannot depend on them.
    """
    __tablename__ = "llm_calls"

    # Internal and high-volume: a plain identity column is enough.
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    thread_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # The assistant message this turn produces; its id is chosen before the turn starts.
    turn_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    provider: Mapped[str] = mapped_column(String(30))
    model: Mapped[str] = mapped_column(String(100))
    status: Mapped[CallStatus] = mapped_column(_enum(CallStatus, "status"))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int] = mapped_column(Integer)
    # Exact decimal, not double: these are fractions of a cent, summed over thousands
    # of rows. The price used is frozen here at call time, since list prices change.
    # NULL means the model is missing from pricing.yaml, not that the call was free.
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_llm_calls_turn_id", "turn_id"),
        Index("ix_llm_calls_created_at", "created_at"),
    )
