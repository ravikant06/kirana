"""
The HTTP API against a real Postgres, with a FakeAdapter and stubbed search.

What matters here is the contract: status codes, ProblemDetail bodies, the
ownership rule, and the usage headers the Requests panel reads.
"""
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from conftest import FakeAdapter
from kirana_ai import agent, api, chat
from kirana_ai.errors import UpstreamUnavailable
from kirana_ai.llm import LLMError, LLMResponse, ToolCall, Usage

CHUNK = {"chunk_id": "policy-returns-0", "doc_id": "policy-returns", "source": "policy-returns.md",
         "title": "Returns Policy", "heading": "Return windows", "chunk_index": 0,
         "text": "Packaged staples: 7 days from delivery", "score": 0.8}

SEARCH = LLMResponse(tool_calls=(ToolCall(id="1", name="search_docs",
                                          arguments={"query": "return rice", "doc_type": "policy"}),),
                     usage=Usage(800, 20))
ANSWER = LLMResponse(text="Unopened rice: within 7 days.\nSources: policy-returns.md",
                     usage=Usage(1500, 60))


@pytest.fixture
def llm_script(monkeypatch):
    """Each chat turn gets a fresh FakeAdapter, replaying the next script in the list."""
    scripts: list[list] = []
    monkeypatch.setattr(chat, "get_adapter", lambda: FakeAdapter(scripts.pop(0)))
    return scripts


@pytest.fixture
def client(ai_db, llm_script, monkeypatch):
    monkeypatch.setattr(agent, "_run_search", lambda args, tenant, k: [CHUNK])
    # No lifespan: it checks the real API key, which tests do not need.
    return TestClient(api.app, raise_server_exceptions=False)


def _chat(client, user=7, **body):
    return client.post("/v1/chat", json={"message": "Can I return rice?", **body},
                       headers={"X-User-Id": str(user)})


def test_chat_answers_and_reports_usage(client, llm_script):
    llm_script.append([SEARCH, ANSWER])

    r = _chat(client)

    assert r.status_code == 200
    body = r.json()
    assert body["reply"].startswith("Unopened rice")
    assert body["citations"] == [{"source": "policy-returns.md", "doc_id": "policy-returns",
                                  "title": "Returns Policy", "pages": []}]
    assert body["steps"] == [{"tool": "search_docs", "query": "return rice",
                              "where": {"doc_type": "policy"}, "count": 1, "below_floor": 0}]
    assert body["usage"]["llm_calls"] == 2 and body["usage"]["input_tokens"] == 2300
    assert r.headers["X-AI-LLM-Calls"] == "2"
    assert r.headers["X-AI-Input-Tokens"] == "2300"
    assert r.headers["X-AI-Output-Tokens"] == "80"


def test_cost_is_a_decimal_string_and_header(client, llm_script, monkeypatch):
    from decimal import Decimal
    from kirana_ai import usage
    monkeypatch.setattr(usage, "load_prices",
                        lambda: {"fake-model": usage.Price(Decimal("1.50"), Decimal("9.00"))})
    llm_script.append([SEARCH, ANSWER])

    r = _chat(client)

    assert r.json()["usage"]["cost_usd"] == "0.004170"   # string, never a float
    assert r.headers["X-AI-Cost-USD"] == "0.004170"


def test_follow_up_continues_the_thread(client, llm_script):
    llm_script += [[SEARCH, ANSWER], [LLMResponse(text="Sealed: 7 days.", usage=Usage(10, 5))]]
    thread_id = _chat(client).json()["thread_id"]

    r = _chat(client, thread_id=thread_id, message="and if it's sealed?")

    assert r.status_code == 200 and r.json()["thread_id"] == thread_id
    detail = client.get(f"/v1/threads/{thread_id}", headers={"X-User-Id": "7"}).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant", "user", "assistant"]
    assert detail["messages"][1]["citations"][0]["source"] == "policy-returns.md"


def test_missing_user_header_is_a_400_problem(client):
    r = client.post("/v1/chat", json={"message": "hi"})

    assert r.status_code == 400
    assert r.headers["content-type"] == "application/problem+json"
    problem = r.json()
    assert problem["title"] == "Validation failed" and problem["instance"] == "/v1/chat"
    assert problem["errors"][0]["field"] == "X-User-Id"


@pytest.mark.parametrize("body, field", [
    ({"message": "   "}, "message"),
    ({"message": "x" * 2001}, "message"),
    ({"message": "hi", "thread_id": "not-a-uuid"}, "thread_id"),
    ({"message": "hi", "user_id": 8}, "user_id"),      # unknown fields are rejected, not ignored
])
def test_invalid_body_lists_the_field(client, body, field):
    r = client.post("/v1/chat", json=body, headers={"X-User-Id": "7"})

    assert r.status_code == 400
    assert [e["field"] for e in r.json()["errors"]] == [field]


def test_malformed_json_is_a_400(client):
    r = client.post("/v1/chat", content="{not json", headers={"X-User-Id": "7",
                                                               "Content-Type": "application/json"})
    assert r.status_code == 400 and r.json()["title"] == "Malformed request"


def test_someone_elses_thread_is_404_everywhere(client, llm_script):
    llm_script.append([ANSWER])
    thread_id = _chat(client, user=7).json()["thread_id"]
    other = {"X-User-Id": "8"}

    assert client.get(f"/v1/threads/{thread_id}", headers=other).status_code == 404
    assert client.delete(f"/v1/threads/{thread_id}", headers=other).status_code == 404
    r = client.post("/v1/chat", json={"message": "x", "thread_id": thread_id}, headers=other)
    assert r.status_code == 404 and r.json()["title"] == "Thread not found"
    # ...and it is still there for its owner.
    assert client.get(f"/v1/threads/{thread_id}", headers={"X-User-Id": "7"}).status_code == 200


def test_llm_failure_is_503_without_the_provider_message(client, llm_script):
    llm_script.append([LLMError("Gemini call failed: 429 quota exceeded for project 12345")])

    r = _chat(client)

    assert r.status_code == 503
    problem = r.json()
    assert problem["code"] == "UPSTREAM_UNAVAILABLE"
    assert "12345" not in r.text and "quota" not in r.text


def test_qdrant_down_is_503(client, llm_script, monkeypatch):
    def down(args, tenant, k):
        raise UpstreamUnavailable("qdrant", "connection refused")
    monkeypatch.setattr(agent, "_run_search", down)
    llm_script.append([SEARCH])

    r = _chat(client)

    assert r.status_code == 503 and r.json()["code"] == "UPSTREAM_UNAVAILABLE"


def test_database_down_is_503_database_busy(client, monkeypatch):
    from sqlalchemy.exc import OperationalError

    def db_down(*args, **kwargs):
        raise OperationalError("SELECT ...", {}, Exception("connection refused"))
    monkeypatch.setattr(chat, "send", db_down)

    r = _chat(client)

    assert r.status_code == 503
    assert r.json()["title"] == "Database busy"
    assert "SELECT" not in r.text and "refused" not in r.text   # no SQL or driver detail


def test_missing_collection_says_how_to_build_it():
    import httpx
    from qdrant_client.http.exceptions import UnexpectedResponse
    from kirana_ai import vector_store

    err = vector_store._unavailable(UnexpectedResponse(404, "Not Found", b"", httpx.Headers()))

    assert err.service == "knowledge base" and "reindex" in str(err)


def test_list_and_delete_threads(client, llm_script):
    llm_script += [[ANSWER], [ANSWER]]
    first = _chat(client, message="first").json()["thread_id"]
    second = _chat(client, message="second").json()["thread_id"]
    me = {"X-User-Id": "7"}

    listed = client.get("/v1/threads", headers=me).json()
    assert [t["id"] for t in listed] == [second, first]
    assert listed[0]["title"] == "second"

    assert client.delete(f"/v1/threads/{first}", headers=me).status_code == 204
    assert client.get(f"/v1/threads/{first}", headers=me).status_code == 404
    assert [t["id"] for t in client.get("/v1/threads", headers=me).json()] == [second]


def test_unknown_thread_and_route_are_problems(client):
    me = {"X-User-Id": "7"}
    assert client.get(f"/v1/threads/{uuid.uuid4()}", headers=me).status_code == 404
    r = client.get("/v1/nope", headers=me)
    assert r.status_code == 404 and r.headers["content-type"] == "application/problem+json"


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


# --- streaming (Phase 3 M1) ---------------------------------------------------------

def _sse_events(r) -> list[tuple[str, dict]]:
    """Parse a text/event-stream body into (event, data) pairs."""
    events = []
    for frame in r.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in frame.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def _stream(client, user=7, **body):
    return client.post("/v1/chat", json={"message": "Can I return rice?", **body},
                       headers={"X-User-Id": str(user), "Accept": "text/event-stream"})


def test_stream_sends_status_tokens_citations_then_done(client, llm_script):
    llm_script.append([SEARCH, ANSWER])

    r = _stream(client)

    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = _sse_events(r)
    kinds = [k for k, _ in events]
    assert kinds[0] == "start" and kinds[-1] == "done"
    assert kinds.index("status") < kinds.index("step") < kinds.index("token") < kinds.index("citation")
    status = next(d for k, d in events if k == "status")
    assert status == {"tool": "search_docs", "query": "return rice", "where": {"doc_type": "policy"}}
    text = "".join(d["text"] for k, d in events if k == "token")
    done = events[-1][1]
    assert text == done["reply"] == ANSWER.text
    assert done["usage"]["llm_calls"] == 2 and done["usage"]["first_token_ms"] is not None
    # and it was saved, exactly like the JSON path
    detail = client.get(f"/v1/threads/{done['thread_id']}", headers={"X-User-Id": "7"}).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]


def test_stream_rejects_a_foreign_thread_before_streaming(client, llm_script):
    llm_script.append([ANSWER])
    thread_id = _chat(client, user=7).json()["thread_id"]

    r = _stream(client, user=8, thread_id=thread_id)

    assert r.status_code == 404                       # a normal 404, not a 200 with an error event
    assert r.headers["content-type"] == "application/problem+json"


def test_failure_mid_stream_becomes_an_error_event(client, llm_script):
    llm_script.append([SEARCH, LLMError("Gemini call failed: 503 project 12345")])

    r = _stream(client)

    assert r.status_code == 200                       # already sent when the failure happened
    kind, problem = _sse_events(r)[-1]
    assert kind == "error" and problem["status"] == 503 and problem["code"] == "UPSTREAM_UNAVAILABLE"
    assert "12345" not in r.text


def test_preamble_text_before_tool_calls_is_reset(client, llm_script):
    preamble = LLMResponse(text="Let me check.", tool_calls=SEARCH.tool_calls, usage=Usage(10, 5))
    llm_script.append([preamble, ANSWER])

    kinds = [k for k, _ in _sse_events(_stream(client))]

    assert kinds.index("reset") < kinds.index("status")


@pytest.mark.anyio
async def test_streaming_response_closes_its_iterator_when_the_client_is_gone():
    """Starlette lets a failed send escape without closing the iterator; ours always closes it."""
    closed = []

    async def frames():
        try:
            for i in range(10):
                yield f"frame {i}\n\n"
        finally:
            closed.append(True)

    sent = []

    async def send(message):
        if message["type"] == "http.response.body" and sent:
            raise OSError("client disconnected")      # what uvicorn raises after a hang-up
        sent.append(message)

    response = api.ClosingStreamingResponse(frames(), media_type="text/event-stream")
    with pytest.raises(OSError):
        await response.stream_response(send)
    assert closed == [True]


def test_closing_a_turn_mid_answer_records_the_abandoned_call(ai_db, llm_script):
    """The recorder must still be listening when the in-flight LLM call is closed."""
    from sqlalchemy import text as sql
    from kirana_ai.llm import TextDelta

    class SlowStreamer(FakeAdapter):
        def _stream(self, messages, *, tools=(), system=None):
            reply = self._replies.pop(0)
            if reply.wants_tools:
                yield reply
                return
            yield TextDelta("Unopened rice ")
            yield TextDelta("can be returned…")      # the client leaves before the rest
            yield reply

    llm = SlowStreamer([SEARCH, ANSWER])
    turn = chat.prepare(7, "Can I return rice?")
    events = chat.run(turn, llm=llm)
    for event in events:
        if event.kind == "token":
            break
    events.close()                                    # what ClosingStreamingResponse triggers

    with ai_db.connect() as conn:
        rows = conn.execute(sql("SELECT status, error FROM llm_calls ORDER BY id")).all()
        answers = conn.execute(sql("SELECT count(*) FROM messages WHERE role = 'assistant'")).scalar()
    assert [tuple(r) for r in rows] == [("ok", None), ("error", "stream abandoned by the client")]
    assert answers == 0                               # a half answer is never saved
