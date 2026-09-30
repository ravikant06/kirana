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
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from starlette.exceptions import HTTPException as StarletteHTTPException

from kirana_ai import chat, config
from kirana_ai.db.models import Message as MessageRow
from kirana_ai.errors import UpstreamUnavailable
from kirana_ai.llm import LLMError, get_adapter
from kirana_ai.schemas import (
    ChatReply,
    ChatRequest,
    Citation,
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

# X-User-Id identifies the shopper until login exists (AD12). Forgeable, exactly
# like Kirana's own use of it; acceptable while tools read only public data.
UserId = Annotated[int, Header(alias="X-User-Id", ge=1)]


# --- routes ---------------------------------------------------------------------

@app.post("/v1/chat", response_model=ChatReply)
def post_chat(body: ChatRequest, user_id: UserId, response: Response) -> ChatReply:
    result = chat.send(user_id, body.message, thread_id=body.thread_id)
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
    )


@app.get("/v1/threads", response_model=list[ThreadSummary])
def get_threads(user_id: UserId) -> list[ThreadSummary]:
    return [ThreadSummary(id=t.id, title=t.title, updated_at=t.updated_at)
            for t in chat.list_threads(user_id)]


@app.get("/v1/threads/{thread_id}", response_model=ThreadDetail)
def get_thread(thread_id: uuid.UUID, user_id: UserId) -> ThreadDetail:
    thread, messages = chat.get_thread(user_id, thread_id)
    return ThreadDetail(id=thread.id, title=thread.title, created_at=thread.created_at,
                        updated_at=thread.updated_at, messages=[_message(m) for m in messages])


@app.delete("/v1/threads/{thread_id}", status_code=204)
def delete_thread(thread_id: uuid.UUID, user_id: UserId) -> Response:
    chat.delete_thread(user_id, thread_id)
    return Response(status_code=204)


@app.get("/health")
def health() -> dict:
    """Liveness only: the process is up. Checking Qdrant or the LLM here comes with Phase 11."""
    return {"status": "ok"}


def _message(row: MessageRow) -> MessageOut:
    return MessageOut(id=row.id, role=row.role.value, content=row.content,
                      citations=[Citation(**c) for c in row.citations],
                      steps=[Step(**s) for s in row.tool_steps], created_at=row.created_at)


# --- errors: everything becomes a ProblemDetail -----------------------------------

def problem(request: Request, status: int, title: str, detail: str, **extra) -> JSONResponse:
    body = {"type": "about:blank", "title": title, "status": status, "detail": detail,
            "instance": request.url.path, **extra}
    return JSONResponse(body, status_code=status, media_type="application/problem+json")


@app.exception_handler(RequestValidationError)
async def validation_failed(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [{"field": _field_name(e["loc"]), "message": e["msg"]} for e in exc.errors()]
    if any(e["type"] == "json_invalid" for e in exc.errors()):
        return problem(request, 400, "Malformed request",
                       "The request body is missing, is not valid JSON, or has a value of the wrong type")
    # 400, not FastAPI's default 422: the same status Kirana uses for invalid input.
    return problem(request, 400, "Validation failed", "One or more fields are invalid", errors=errors)


@app.exception_handler(chat.ThreadNotFound)
async def thread_not_found(request: Request, _exc: chat.ThreadNotFound) -> JSONResponse:
    # Same answer for "does not exist" and "belongs to someone else".
    return problem(request, 404, "Thread not found", "No such thread for this shopper")


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
