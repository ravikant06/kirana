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
import logging
import re
import time
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from kirana_ai import agent, config, context, memory, policy
from kirana_ai.db import session_scope
from kirana_ai.db.models import Message as MessageRow
from kirana_ai.db.models import Role as RowRole
from kirana_ai.db.models import Thread
from kirana_ai.llm import LLMAdapter, Message, get_adapter
from kirana_ai.usage import CallRecorder

TITLE_LENGTH = 80
log = logging.getLogger("kirana_ai.chat")


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
    # Phase 7 (2A): the product cards shown with this answer, chosen by context.cards_for.
    product_ids: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class PreparedTurn:
    """Step 1 done: the thread exists and is the shopper's, the question is saved."""
    thread_id: uuid.UUID
    text: str
    history: list[Message]
    # Who the turn acts for (Phase 6): permissions for the policy layer, and credentials that hand
    # out narrowed tokens. Held for this turn only: never saved, never logged, never shown to the model.
    caller: policy.Caller = field(default_factory=policy.Caller, repr=False)
    # Phase 7: the context blocks before the history (saved memories, summary, thread facts).
    context: list[Message] = field(default_factory=list)


@dataclass(frozen=True)
class TurnEvent:
    """
    Progress of a running turn: the agent's events (status, step, token, reset),
    then exactly one `done` whose data is the TurnResult.
    """
    kind: str
    data: object


def send(
    user_id: int,
    text: str,
    thread_id: uuid.UUID | None = None,
    llm: LLMAdapter | None = None,
    caller: policy.Caller | None = None,
) -> TurnResult:
    """One whole turn, returned at the end (the JSON API, the CLI, the tests)."""
    for event in run(prepare(user_id, text, thread_id, caller), llm):
        if event.kind == "done":
            return event.data
    raise RuntimeError("turn ended without a result")


def prepare(user_id: int, text: str, thread_id: uuid.UUID | None = None,
            caller: policy.Caller | None = None) -> PreparedTurn:
    """
    Step 1, a short transaction: thread, ownership, history, the shopper's message.

    Separate from run() so the streaming API can reject a foreign thread with a plain
    404 *before* it starts a 200 event stream: once streaming starts, the status is sent.

    Phase 7: also the thread's summary and facts (short-term memory), and, after the
    transaction, the shopper's saved memories (long-term; a search may call the embedding
    API, which must never happen while a transaction is open).
    """
    with session_scope() as session:
        if thread_id is None:
            thread = Thread(user_id=user_id, title=_title(text))
            session.add(thread)
            session.flush()                       # INSERT now: the id default is applied at flush
        else:
            thread = _owned(session, user_id, thread_id)
        covered = thread.summary_through if config.SUMMARY_ENABLED else None
        history = load_history(session, thread.id, config.HISTORY_TURNS, after=covered)
        summary, facts, thread_id = thread.summary, dict(thread.facts or {}), thread.id
        session.add(MessageRow(thread_id=thread.id, role=RowRole.USER, content=text))
    recall = memory.for_prompt(user_id, text)
    return PreparedTurn(thread_id=thread_id, text=text, history=history,
                        caller=caller or policy.Caller(user_id=user_id),
                        context=context.blocks(recall, summary if config.SUMMARY_ENABLED else None,
                                               facts if config.FACTS_ENABLED else {}))


def run(turn: PreparedTurn, llm: LLMAdapter | None = None) -> Iterator[TurnEvent]:
    """
    Steps 2 and 3: the agent (no transaction, every LLM call recorded as it happens),
    then a short transaction for the answer. Yields the agent's events as they happen.

    If the consumer stops (the browser closed the stream), the generator is closed: the
    LLM call in flight is recorded as abandoned, and no answer is saved.
    """
    # The assistant message's id is chosen now, so llm_calls rows can point at it
    # before it exists (and even if it never does).
    turn_id = uuid.uuid4()
    llm = llm or get_adapter()
    recorder = CallRecorder(turn.thread_id, turn_id)
    llm.add_listener(recorder)
    started = time.perf_counter()
    first_token_ms = None
    result = None
    turn.caller.thread_id, turn.caller.turn_id = turn.thread_id, turn_id
    events = agent.answer_stream(turn.text, history=turn.history, llm=llm, caller=turn.caller,
                                 decisions=policy.record_to_db, context=turn.context)
    try:
        for event in events:
            if event.kind == "done":
                result = event.data
            else:
                if event.kind == "token" and first_token_ms is None:
                    first_token_ms = int((time.perf_counter() - started) * 1000)
                yield TurnEvent(event.kind, event.data)
    finally:
        # Order matters. Close the agent's generator first: that closes the LLM stream in
        # flight, whose "abandoned" record must reach the recorder. Removing the recorder
        # first would drop exactly the call the client walked away from.
        events.close()
        llm.remove_listener(recorder)
    chunks, reply, steps = result["chunks"], result["answer"], result["steps"]
    cards = context.cards_for(reply, steps)
    citations = citations_for(chunks, reply)
    unverified = unverified_sources(chunks, reply)
    if unverified:
        log.warning("thread %s cites sources it did not retrieve: %s", turn.thread_id, unverified)

    # --- 3. short transaction: the answer
    with session_scope() as session:
        session.add(MessageRow(id=turn_id, thread_id=turn.thread_id, role=RowRole.ASSISTANT,
                               content=reply, citations=citations, tool_steps=steps,
                               product_ids=[c["id"] for c in cards]))
        # Phase 7 M3: the facts move on with this turn's tool steps, in the same transaction
        # as the answer that produced them. Locked: two turns of one thread can't lose an update.
        thread = session.execute(select(Thread).where(Thread.id == turn.thread_id).with_for_update()).scalar_one()
        thread.facts = context.facts_after(thread.facts or {}, steps, cards)
        thread.updated_at = func.now()

    usage = {**recorder.summary(), "first_token_ms": first_token_ms,
             "unverified_sources": unverified}
    yield TurnEvent("done", TurnResult(thread_id=turn.thread_id, message_id=turn_id, reply=reply,
                                       citations=citations, steps=steps, usage=usage, product_ids=[c["id"] for c in cards]))


def after_turn(thread_id: uuid.UUID, llm: LLMAdapter | None = None) -> None:
    """
    Work that runs after the shopper has the answer (Phase 7, AD34): fold turns that fell out of
    the window into the thread's summary. A failure here only delays the summary to the next turn;
    it never reaches the shopper.
    """
    if not config.SUMMARY_ENABLED:
        return
    try:
        context.summarise(thread_id, llm)
    except Exception:   # noqa: BLE001
        log.exception("summary for thread %s failed; the next turn will retry", thread_id)


def load_history(session: Session, thread_id: uuid.UUID, turns: int,
                 after: uuid.UUID | None = None) -> list[Message]:
    """
    The last `turns` complete exchanges, oldest first, as plain text (AD11).

    Only user -> assistant pairs are kept. A shopper message whose turn failed
    has no answer; sending it would put two user messages in a row, and the
    model would answer the failed question again.

    `after` (Phase 7): the last message the thread's summary covers. Only later turns are sent,
    so nothing reaches the model twice (once summarised, once verbatim) and folding turns to
    meet the token budget actually shrinks the prompt.
    """
    if turns <= 0:
        return []
    query = select(MessageRow.role, MessageRow.content).where(MessageRow.thread_id == thread_id)
    if after is not None:
        covered = select(MessageRow.created_at).where(MessageRow.id == after).scalar_subquery()
        query = query.where(MessageRow.created_at > covered)
    rows = session.execute(
        query.order_by(MessageRow.created_at.desc())
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
    Documents the answer says it used: a source counts only if it was retrieved this turn AND
    the reply names it, so a source the model never read is never shown as a chip (those are
    caught by unverified_sources). Whether the source really supports each claim is measured
    offline (eval.run_answers, faithfulness), not checked per answer.
    """
    seen: dict[str, dict] = {}
    for chunk in chunks:
        source = chunk["source"]
        if source not in reply:
            continue
        cite = seen.setdefault(source, {"source": source, "doc_id": chunk["doc_id"],
                                        "title": chunk.get("title"), "pages": []})
        # PDFs: the pages of the retrieved chunks from this document, in order.
        if chunk.get("page") and chunk["page"] not in cite["pages"]:
            cite["pages"] = sorted([*cite["pages"], chunk["page"]])
    return list(seen.values())


_CITED_FILE = re.compile(r"[\w.-]+\.(?:md|txt|pdf)\b", re.IGNORECASE)


def unverified_sources(chunks: Sequence[dict], reply: str) -> list[str]:
    """
    File names the reply cites that were NOT retrieved this turn.

    A model can name a plausible source it never read (from history, or invented). Those
    are never shown as citations; this makes them visible, so the rate can be measured.
    """
    retrieved = {c["source"] for c in chunks}
    tail = reply[reply.lower().rfind("sources"):] if "sources" in reply.lower() else ""
    return sorted({name for name in _CITED_FILE.findall(tail) if name not in retrieved})


def list_threads(user_id: int, limit: int = 50) -> list[Thread]:
    with session_scope() as session:
        return list(session.scalars(
            select(Thread).where(Thread.user_id == user_id)
            .order_by(Thread.updated_at.desc()).limit(limit)
        ))


def get_thread(user_id: int, thread_id: uuid.UUID) -> tuple[Thread, list[MessageRow]]:
    """A thread and all its messages: two queries, both explicit (relationships are lazy="raise")."""
    with session_scope() as session:
        thread = _owned(session, user_id, thread_id)
        messages = list(session.scalars(
            select(MessageRow).where(MessageRow.thread_id == thread_id)
            .order_by(MessageRow.created_at)
        ))
        return thread, messages


def delete_thread(user_id: int, thread_id: uuid.UUID) -> None:
    """Messages go with it (ON DELETE CASCADE); its llm_calls rows stay, as cost history."""
    with session_scope() as session:
        session.delete(_owned(session, user_id, thread_id))


def _owned(session: Session, user_id: int, thread_id: uuid.UUID) -> Thread:
    thread = session.get(Thread, thread_id)
    if thread is None or thread.user_id != user_id:
        raise ThreadNotFound(str(thread_id))
    return thread


def _title(text: str) -> str:
    line = " ".join(text.split())
    return line if len(line) <= TITLE_LENGTH else line[: TITLE_LENGTH - 1] + "…"


def total_cost(summary: dict) -> str:
    """'$0.000315' or 'unknown' — for printing a turn's usage."""
    cost: Decimal | None = summary.get("cost_usd")
    return "unknown (no price in pricing.yaml)" if cost is None else f"${cost:.6f}"
