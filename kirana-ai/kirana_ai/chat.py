"""
One chat turn: the conversation around the agent.

    result = chat.send(user_id=7, text="Can I return opened rice?")
    chat.send(user_id=7, text="and if it's sealed?", thread_id=result.thread_id)

A turn runs in three steps, and only the short ones touch a transaction:

    1. tx  load (or create) the thread, check the shopper owns it, read the
           history, save the shopper's message
    2. --  run the agent: several LLM calls, seconds long. No transaction is
           open; each LLM call is recorded in its own (kirana_ai/usage.py)
    3. tx  save the assistant's message, bump the thread

Holding one transaction across step 2 would pin a pooled connection for the
whole LLM wait, so a handful of slow chats could exhaust the pool (Kirana's
Stage 2 lesson). The price: if step 2 fails, the shopper's message is saved
without an answer. History building skips such messages.
"""
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from kirana_ai import agent, config
from kirana_ai.db import session_scope
from kirana_ai.db.models import Message as MessageRow
from kirana_ai.db.models import Role as RowRole
from kirana_ai.db.models import Thread
from kirana_ai.llm import LLMAdapter, Message, get_adapter
from kirana_ai.usage import CallRecorder

TITLE_LENGTH = 80


class ThreadNotFound(Exception):
    """No such thread for this shopper. Deliberately the same for 'missing' and
    'someone else's', so the answer never confirms that a thread id exists."""


@dataclass(frozen=True)
class TurnResult:
    thread_id: uuid.UUID
    message_id: uuid.UUID
    reply: str
    citations: list[dict]
    steps: list[dict]
    usage: dict = field(default_factory=dict)


def send(
    user_id: int,
    text: str,
    thread_id: uuid.UUID | None = None,
    llm: LLMAdapter | None = None,
) -> TurnResult:
    # --- 1. short transaction: thread, ownership, history, the shopper's message
    with session_scope() as session:
        if thread_id is None:
            thread = Thread(user_id=user_id, title=_title(text))
            session.add(thread)
            session.flush()                       # INSERT now: the id default is applied at flush
        else:
            thread = session.get(Thread, thread_id)
            if thread is None or thread.user_id != user_id:
                raise ThreadNotFound(str(thread_id))
        history = load_history(session, thread.id, config.HISTORY_TURNS)
        session.add(MessageRow(thread_id=thread.id, role=RowRole.USER, content=text))
        thread_id = thread.id

    # --- 2. no transaction: the agent, with every LLM call recorded as it happens.
    # The assistant message's id is chosen now, so llm_calls rows can point at it
    # before it exists (and even if it never does).
    turn_id = uuid.uuid4()
    llm = llm or get_adapter()
    recorder = CallRecorder(thread_id, turn_id)
    llm.add_listener(recorder)
    try:
        chunks, reply, steps = agent.answer(text, history=history, llm=llm)
    finally:
        llm.remove_listener(recorder)
    citations = citations_for(chunks, reply)

    # --- 3. short transaction: the answer
    with session_scope() as session:
        session.add(MessageRow(id=turn_id, thread_id=thread_id, role=RowRole.ASSISTANT,
                               content=reply, citations=citations, tool_steps=steps))
        session.execute(update(Thread).where(Thread.id == thread_id)
                        .values(updated_at=func.now()))

    return TurnResult(thread_id=thread_id, message_id=turn_id, reply=reply,
                      citations=citations, steps=steps, usage=recorder.summary())


def load_history(session: Session, thread_id: uuid.UUID, turns: int) -> list[Message]:
    """
    The last `turns` complete exchanges, oldest first, as plain text (AD11).

    Only user -> assistant pairs are kept. A shopper message whose turn failed
    has no answer; sending it would put two user messages in a row, and the
    model would answer the failed question again.
    """
    if turns <= 0:
        return []
    rows = session.execute(
        select(MessageRow.role, MessageRow.content)
        .where(MessageRow.thread_id == thread_id)
        .order_by(MessageRow.created_at.desc())
        .limit(turns * 2 + 1)                   # +1: room for one unanswered message
    ).all()

    history: list[Message] = []
    pending_user: str | None = None
    for role, content in reversed(rows):
        if role is RowRole.USER:
            pending_user = content               # a previous unanswered one is dropped
        elif pending_user is not None:
            history += [Message.user(pending_user), Message.assistant(content)]
            pending_user = None
    return history[-turns * 2:]


def citations_for(chunks: Sequence[dict], reply: str) -> list[dict]:
    """
    Documents the answer says it used: retrieved sources named in the reply.

    Deliberately simple for now. Phase 3 checks citations properly; until then
    a source counts only if it was retrieved this turn AND the reply names it.
    """
    seen: dict[str, dict] = {}
    for chunk in chunks:
        source = chunk["source"]
        if source in reply and source not in seen:
            seen[source] = {"source": source, "doc_id": chunk["doc_id"],
                            "title": chunk.get("title")}
    return list(seen.values())


def list_threads(user_id: int, limit: int = 50) -> list[Thread]:
    with session_scope() as session:
        return list(session.scalars(
            select(Thread).where(Thread.user_id == user_id)
            .order_by(Thread.updated_at.desc()).limit(limit)
        ))


def _title(text: str) -> str:
    line = " ".join(text.split())
    return line if len(line) <= TITLE_LENGTH else line[: TITLE_LENGTH - 1] + "…"


def total_cost(summary: dict) -> str:
    """'$0.000315' or 'unknown' — for printing a turn's usage."""
    cost: Decimal | None = summary.get("cost_usd")
    return "unknown (no price in pricing.yaml)" if cost is None else f"${cost:.6f}"
