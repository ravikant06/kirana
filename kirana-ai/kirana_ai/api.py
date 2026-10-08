"""
The HTTP API (FastAPI). Run:

    uvicorn kirana_ai.api:app --reload --port 8000

The browser reaches it through Kirana's Vite proxy: /ai/v1/chat -> :8000/v1/chat.
docs/ai-contract.md is the spec; kirana_ai/schemas.py holds its shapes.

Routes are plain `def`, not `async def`. chat.send blocks for seconds (LLM,
Qdrant and Postgres calls are all synchronous), and FastAPI runs a plain `def`
route in a worker thread, so a slow chat never blocks the event loop. The
catch: that thread pool (40 threads by default) caps how many chats run at
once. Streaming in Phase 3 revisits this.

Errors are RFC 7807 ProblemDetail, the same shape Kirana's backend returns, so
the frontend's Problem component shows either service's errors unchanged.
"""
import json
import logging
import uuid
from collections.abc import AsyncIterator, Iterator

import anyio
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException as StarletteHTTPException

from kirana_ai import actions, auth, chat, config, kb, memory, policy
from kirana_ai.db.models import DocType, Document
from kirana_ai.db.models import Message as MessageRow
from kirana_ai.errors import UpstreamUnavailable
from kirana_ai.llm import LLMError, get_adapter
from kirana_ai.schemas import (
    Approval,
    ApprovalDecision,
    MemoryOut,
    MemoryPatch,
    ChatReply,
    ChatRequest,
    Citation,
    KbDocument,
    KbUploadRequest,
    KbUploadTicket,
    MessageOut,
    Step,
    ThreadDetail,
    ThreadSummary,
    Usage,
)

log = logging.getLogger("kirana_ai.api")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Configuration errors fail the start, not the first request: a missing API
    # key or an unknown provider is found here, before any shopper waits on it.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    config.require_api_key()
    llm = get_adapter()
    log.info("LLM %s / %s, Qdrant %s, collection %s", llm.provider, llm.model,
             config.QDRANT_URL, config.COLLECTION_NAME)
    yield


app = FastAPI(title="Kirana AI", version="0.1.0", lifespan=lifespan)

# Phase 5: the caller is whoever Kirana's signed token says, verified on every request, and may
# do what the token's permissions (`scope`) allow. X-User-Id is not read at all: once a tool can
# read personal data, a header anyone can type is an identity anyone can claim.
def shopper(authorization: Annotated[str | None, Header()] = None) -> auth.Caller:
    return auth.from_header(authorization).require("chat")


def kb_admin(authorization: Annotated[str | None, Header()] = None) -> auth.Caller:
    return auth.from_header(authorization).require("kb:write")


Caller = Annotated[auth.Caller, Depends(shopper)]


def _turn_caller(caller: auth.Caller) -> policy.Caller:
    """What the agent gets: permissions for the policy, and credentials instead of the raw token."""
    return policy.Caller(user_id=caller.user_id, scopes=caller.scopes, credentials=auth.Credentials(caller.token))
KbAdmin = Annotated[auth.Caller, Depends(kb_admin)]


# --- routes ---------------------------------------------------------------------

@app.post("/v1/chat", response_model=ChatReply)
def post_chat(body: ChatRequest, caller: Caller, request: Request, response: Response, background: BackgroundTasks):
    if "text/event-stream" in request.headers.get("accept", ""):
        # Ownership is checked here, before the stream starts: once it has, the 200 is sent
        # and a 404 can no longer be returned.
        turn = chat.prepare(caller.user_id, body.message, thread_id=body.thread_id, caller=_turn_caller(caller))
        # Phase 7: the summary is written after the last byte was sent: the shopper never waits for it.
        return ClosingStreamingResponse(_closing(_sse(turn, request.url.path)), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                                 background=BackgroundTask(chat.after_turn, turn.thread_id))
    result = chat.send(caller.user_id, body.message, thread_id=body.thread_id, caller=_turn_caller(caller))
    background.add_task(chat.after_turn, result.thread_id)
    usage = Usage(**result.usage)
    # Mirrors X-Query-Count: the Requests panel shows what each call cost.
    response.headers["X-AI-LLM-Calls"] = str(usage.llm_calls)
    response.headers["X-AI-Input-Tokens"] = str(usage.input_tokens)
    response.headers["X-AI-Output-Tokens"] = str(usage.output_tokens)
    if usage.cost_usd is not None:
        response.headers["X-AI-Cost-USD"] = f"{usage.cost_usd:.6f}"
    return ChatReply(
        thread_id=result.thread_id,
        message_id=result.message_id,
        reply=result.reply,
        citations=[Citation(**c) for c in result.citations],
        steps=[Step(**s) for s in result.steps],
        usage=usage,
        product_ids=result.product_ids,
    )


class ClosingStreamingResponse(StreamingResponse):
    """
    A StreamingResponse that always closes its body iterator, however the stream ends.

    Found by experiment (Phase 3): with uvicorn (ASGI 2.4), Starlette does not listen for
    disconnects; it notices when the next send() fails, and lets that error escape its
    `async for` *without closing the iterator*. Our generators then stay suspended mid-turn:
    the LLM call in flight is never recorded, and the provider's HTTP stream stays open,
    until garbage collection, which the exception's reference cycles can delay indefinitely.
    """
    async def stream_response(self, send) -> None:
        try:
            await super().stream_response(send)
        finally:
            with anyio.CancelScope(shield=True):
                await self.body_iterator.aclose()


async def _closing(frames: Iterator[str]) -> AsyncIterator[str]:
    """
    Stream a sync generator from worker threads (AD16), and close it when we stop.

    Closing raises GeneratorExit down the chain, each layer closing the next explicitly:
    _sse -> chat.run -> agent -> llm.stream, which records the abandoned call -> the
    provider's stream, which releases its HTTP connection.
    """
    done = object()
    try:
        while (frame := await anyio.to_thread.run_sync(next, frames, done)) is not done:
            yield frame
    finally:
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(frames.close)


def _sse(turn: chat.PreparedTurn, path: str) -> Iterator[str]:
    """
    One chat turn as Server-Sent Events: start, status/step/token/reset as they happen,
    then citation(s) and done. A failure mid-stream becomes an `error` event carrying a
    ProblemDetail, because the HTTP status was already sent with the first byte.

    A plain (sync) generator: FastAPI iterates it in its worker-thread pool (AD16).
    """
    def frame(event: str, data) -> str:
        return f"event: {event}\ndata: {json.dumps(data, default=str, ensure_ascii=False)}\n\n"

    yield frame("start", {"thread_id": str(turn.thread_id)})
    events = chat.run(turn)
    try:
        for event in events:
            if event.kind != "done":
                yield frame(event.kind, event.data)
                continue
            r = event.data
            for c in r.citations:
                yield frame("citation", Citation(**c).model_dump())
            yield frame("done", {"thread_id": str(r.thread_id), "message_id": str(r.message_id),
                                 "reply": r.reply, "steps": r.steps, "product_ids": r.product_ids,
                                 "usage": Usage(**r.usage).model_dump(mode="json")})
    except (LLMError, UpstreamUnavailable, OperationalError, PoolTimeout) as exc:
        log.warning("Chat stream failed: %s", exc)
        yield frame("error", problem_body(path, 503, "Assistant unavailable",
                                          "The assistant could not answer right now. Please try again in a moment.",
                                          code="UPSTREAM_UNAVAILABLE"))
    except Exception:
        log.exception("Chat stream failed unexpectedly")
        yield frame("error", problem_body(path, 500, "Internal error", "Something went wrong on our side"))
    finally:
        events.close()      # explicitly: never leave closing to the garbage collector


@app.get("/v1/threads", response_model=list[ThreadSummary])
def get_threads(caller: Caller) -> list[ThreadSummary]:
    return [ThreadSummary(id=t.id, title=t.title, updated_at=t.updated_at)
            for t in chat.list_threads(caller.user_id)]


@app.get("/v1/threads/{thread_id}", response_model=ThreadDetail)
def get_thread(thread_id: uuid.UUID, caller: Caller) -> ThreadDetail:
    thread, messages = chat.get_thread(caller.user_id, thread_id)
    return ThreadDetail(id=thread.id, title=thread.title, created_at=thread.created_at,
                        updated_at=thread.updated_at, messages=[_message(m) for m in messages])


@app.delete("/v1/threads/{thread_id}", status_code=204)
def delete_thread(thread_id: uuid.UUID, caller: Caller) -> Response:
    chat.delete_thread(caller.user_id, thread_id)
    return Response(status_code=204)


# --- long-term memory (Phase 7 M4) ------------------------------------------------------------
# Only the shopper's own memories, ever. Saving happens through an approval card (remember_preference);
# here the shopper can see, pin and delete what is remembered.

@app.get("/v1/memories", response_model=list[MemoryOut])
def list_memories(caller: Caller) -> list[MemoryOut]:
    return [MemoryOut(**vars(m)) for m in memory.list_for(caller.user_id)]


@app.patch("/v1/memories/{memory_id}", response_model=MemoryOut)
def pin_memory(memory_id: uuid.UUID, body: MemoryPatch, caller: Caller) -> MemoryOut:
    return MemoryOut(**vars(memory.set_pinned(caller.user_id, memory_id, body.pinned)))


@app.delete("/v1/memories/{memory_id}", status_code=204)
def delete_memory(memory_id: uuid.UUID, caller: Caller) -> Response:
    memory.delete(caller.user_id, memory_id)
    return Response(status_code=204)


# --- approvals (Phase 6 M4) -----------------------------------------------------------------
# The shopper's click. The body says only confirm or reject: what runs is the server's stored copy.

@app.get("/v1/approvals/{approval_id}", response_model=Approval)
def get_approval(approval_id: uuid.UUID, caller: Caller) -> Approval:
    return Approval(**vars(actions.get(caller.user_id, approval_id)))


@app.post("/v1/approvals/{approval_id}", response_model=Approval)
def decide_approval(approval_id: uuid.UUID, body: ApprovalDecision, caller: Caller) -> Approval:
    view = actions.decide(caller.user_id, auth.Credentials(caller.token), approval_id, body.decision)
    return Approval(**vars(view))


# --- knowledge base (Phase 2) --------------------------------------------------------

@app.post("/v1/kb/documents/upload-url", response_model=KbUploadTicket)
def kb_upload_url(body: KbUploadRequest, request: Request, caller: KbAdmin):
    if body.size_bytes > config.KB_MAX_UPLOAD_BYTES:
        # Same answer the signed policy would give, but before the file leaves the browser.
        return problem(request, 400, "Validation failed", "One or more fields are invalid",
                       errors=[{"field": "size_bytes",
                                "message": f"must be at most {config.KB_MAX_UPLOAD_BYTES} bytes"}])
    _, ticket = kb.create_upload(body.title, DocType(body.doc_type), body.file_name,
                                 body.content_type, body.size_bytes,
                                 uploaded_by=f"user:{caller.user_id}")
    return KbUploadTicket(**ticket)


@app.get("/v1/kb/documents", response_model=list[KbDocument])
def kb_documents(_admin: KbAdmin) -> list[KbDocument]:
    return [_document(d) for d in kb.list_documents()]


@app.delete("/v1/kb/documents/{document_id}", status_code=204)
def kb_delete(document_id: uuid.UUID, _admin: KbAdmin) -> Response:
    kb.delete_document(document_id)
    return Response(status_code=204)


def _document(d: Document) -> KbDocument:
    return KbDocument(id=d.id, title=d.title, doc_type=d.doc_type.value, file_name=d.file_name,
                      content_type=d.content_type, size_bytes=d.size_bytes, status=d.status.value,
                      page_count=d.page_count, chunk_count=d.chunk_count, error=d.error,
                      uploaded_by=d.uploaded_by, created_at=d.created_at, updated_at=d.updated_at)


@app.get("/health")
def health() -> dict:
    """Liveness only: the process is up. Checking Qdrant or the LLM here comes with Phase 11."""
    return {"status": "ok"}


def _message(row: MessageRow) -> MessageOut:
    return MessageOut(id=row.id, role=row.role.value, content=row.content,
                      citations=[Citation(**c) for c in row.citations],
                      steps=[Step(**s) for s in row.tool_steps], product_ids=row.product_ids or [],
                      created_at=row.created_at)


# --- errors: everything becomes a ProblemDetail -----------------------------------

def problem_body(path: str, status: int, title: str, detail: str, **extra) -> dict:
    return {"type": "about:blank", "title": title, "status": status, "detail": detail,
            "instance": path, **extra}


def problem(request: Request, status: int, title: str, detail: str, **extra) -> JSONResponse:
    body = problem_body(request.url.path, status, title, detail, **extra)
    return JSONResponse(body, status_code=status, media_type="application/problem+json")


@app.exception_handler(RequestValidationError)
async def validation_failed(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [{"field": _field_name(e["loc"]), "message": e["msg"]} for e in exc.errors()]
    if any(e["type"] == "json_invalid" for e in exc.errors()):
        return problem(request, 400, "Malformed request",
                       "The request body is missing, is not valid JSON, or has a value of the wrong type")
    # 400, not FastAPI's default 422: the same status Kirana uses for invalid input.
    return problem(request, 400, "Validation failed", "One or more fields are invalid", errors=errors)


@app.exception_handler(auth.InvalidToken)
async def invalid_token(request: Request, exc: auth.InvalidToken) -> JSONResponse:
    # The reason is safe to return (it describes the caller's own token) and helps the demo.
    log.info("Rejected token on %s %s: %s", request.method, request.url.path, exc)
    response = problem(request, 401, "Unauthorized", str(exc), code="UNAUTHENTICATED")
    response.headers["WWW-Authenticate"] = 'Bearer error="invalid_token"'
    return response


@app.exception_handler(auth.Forbidden)
async def forbidden(request: Request, exc: auth.Forbidden) -> JSONResponse:
    return problem(request, 403, "Forbidden", str(exc), code="FORBIDDEN")


@app.exception_handler(memory.MemoryNotFound)
async def memory_not_found(request: Request, _exc: memory.MemoryNotFound) -> JSONResponse:
    return problem(request, 404, "Memory not found", "No such memory for this shopper")


@app.exception_handler(actions.ApprovalNotFound)
async def approval_not_found(request: Request, _exc: actions.ApprovalNotFound) -> JSONResponse:
    return problem(request, 404, "Approval not found", "No such approval for this shopper")


@app.exception_handler(actions.ApprovalTampered)
async def approval_tampered(request: Request, _exc: actions.ApprovalTampered) -> JSONResponse:
    return problem(request, 409, "Approval refused", "This request was changed after it was created, so it will not run",
                   code="APPROVAL_TAMPERED")


@app.exception_handler(chat.ThreadNotFound)
async def thread_not_found(request: Request, _exc: chat.ThreadNotFound) -> JSONResponse:
    # Same answer for "does not exist" and "belongs to someone else".
    return problem(request, 404, "Thread not found", "No such thread for this shopper")


@app.exception_handler(kb.DocumentNotFound)
async def document_not_found(request: Request, _exc: kb.DocumentNotFound) -> JSONResponse:
    return problem(request, 404, "Document not found", "No such knowledge-base document")


@app.exception_handler(LLMError)
async def llm_unavailable(request: Request, exc: LLMError) -> JSONResponse:
    # The provider's message goes to the log, never to the shopper: it can hold
    # request ids, quota details or fragments of the prompt.
    log.warning("LLM call failed: %s", exc)
    return problem(request, 503, "Assistant unavailable",
                   "The assistant could not answer right now. Please try again in a moment.",
                   code="UPSTREAM_UNAVAILABLE")


@app.exception_handler(UpstreamUnavailable)
async def upstream_unavailable(request: Request, exc: UpstreamUnavailable) -> JSONResponse:
    log.warning("Upstream unavailable: %s", exc)
    return problem(request, 503, "Assistant unavailable",
                   "The assistant could not answer right now. Please try again in a moment.",
                   code="UPSTREAM_UNAVAILABLE")


@app.exception_handler(OperationalError)
@app.exception_handler(PoolTimeout)
async def database_unavailable(request: Request, exc: Exception) -> JSONResponse:
    # Postgres unreachable, or every pooled connection busy: both mean "retry soon",
    # so 503, as Kirana does. The SQL and driver message stay in the log.
    log.warning("Database unavailable: %s", exc)
    return problem(request, 503, "Database busy",
                   "No database connection was available. Try again in a moment.",
                   code="UPSTREAM_UNAVAILABLE")


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    titles = {404: "Not found", 405: "Method not allowed"}
    return problem(request, exc.status_code, titles.get(exc.status_code, "Error"), str(exc.detail))


@app.exception_handler(Exception)
async def unexpected(request: Request, exc: Exception) -> JSONResponse:
    log.error("Unhandled exception", exc_info=exc)
    return problem(request, 500, "Internal error", "Something went wrong on our side")


def _field_name(loc: tuple) -> str:
    """('body', 'message') -> 'message'; ('header', 'X-User-Id') -> 'X-User-Id'."""
    return ".".join(str(p) for p in loc[1:]) or str(loc[0])
