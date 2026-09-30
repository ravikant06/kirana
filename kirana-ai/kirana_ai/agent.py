"""
Agentic retrieval: the model chooses its own searches.

Retrieval is exposed to the model as tools. The model fills in the filter
arguments as part of the same call it answers with, and can search again
with different filters if the first attempt comes back empty.

This module is the Client in the Adapter pattern: it talks only to
kirana_ai.llm.LLMAdapter and holds no provider-specific code, so the same
loop runs against Gemini, OpenAI or Anthropic.

Two deliberate design choices:

  - `tenant_id` is NOT a tool parameter. It is injected server-side from
    config. A model that could choose its own scope is a data leak waiting
    to happen; from Phase 5 the same rule covers the user's identity.

  - Every tool call is recorded in the returned steps. A wrongly-inferred
    filter silently hides the right answer, so the filters must be visible
    to whoever is reading the output.
"""
from collections.abc import Sequence

from kirana_ai import config, embeddings, filters, sparse, trace, vector_store
from kirana_ai.llm import Message, ToolResult, ToolSpec, get_adapter

MAX_STEPS = 5  # search rounds per question, before we force an answer

SYSTEM_INSTRUCTION = """You are the shopping assistant of Kirana, an online grocery store.
You answer questions about the store's policies: returns, refunds, cancellations,
delivery, payments and freshness.

You cannot see the store's documents directly. Use the tools to retrieve them.

Choosing a tool:
- `search_docs` finds passages that answer a specific question. It returns only
  its best few matches and can never tell you whether more exist.
- `list_documents` enumerates the knowledge base exactly. Use it when the
  question asks what documents or policies exist.

Guidelines:
- Call a tool at least once before answering a policy question.
- Do not repeat a search you have already run with the same or near-identical
  arguments. If two searches have not helped, answer with what you have.
- Set filters only when the question clearly implies them. An unnecessary
  filter can hide the correct answer.
- If a filtered search returns nothing, search again with fewer filters
  before concluding the answer is absent.
- Answer ONLY from retrieved passages. Never invent policies, fees, time
  limits or amounts.
- If the documents do not contain the answer, say:
  "I couldn't find that in our store policies. Please contact support."
- Be brief and friendly. End your answer with a "Sources:" line listing the
  filenames you used."""

# Declared once, in plain JSON Schema. Each adapter rewraps this in whatever
# envelope its provider expects — see kirana_ai/llm/*.py.
SEARCH_DOCS = ToolSpec(
    name="search_docs",
    description=(
        "Search Kirana's store policies and FAQ. "
        "Returns the most relevant passages with their source filenames."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "What to search for, as a natural-language phrase. Rephrase "
                    "the shopper's question into the wording you would expect a "
                    "store policy to use."
                ),
            },
            "doc_type": {
                "type": "string",
                "enum": config.DOC_TYPES,
                "description": "Restrict to one class of document.",
            },
            "source": {
                "type": "string",
                "description": "Restrict to one file, e.g. 'policy-returns.md'.",
            },
        },
        "required": ["query"],
    },
)

LIST_DOCUMENTS = ToolSpec(
    name="list_documents",
    description=(
        "List the documents in the knowledge base, optionally filtered by type. "
        "Returns every match with its title — not a ranked subset — so it "
        "answers 'what policies do you have' exactly."
    ),
    parameters={
        "type": "object",
        "properties": {
            "doc_type": {
                "type": "string",
                "enum": config.DOC_TYPES,
                "description": "Only documents of this class.",
            },
        },
    },
)

TOOLS = [SEARCH_DOCS, LIST_DOCUMENTS]


def _client_or_die():
    client = vector_store.get_client()
    if not client.collection_exists(config.COLLECTION_NAME):
        raise SystemExit("Collection is empty. Run `python -m kirana_ai.cli ingest` first.")
    return client


def _run_list(args: dict, tenant_id: str | None) -> list[dict]:
    """Enumerate matching documents. tenant_id comes from us, as ever."""
    where = {k: v for k, v in args.items() if v}
    if trace.is_on():
        trace.section("TOOL list_documents")
        trace.kv("model filters", trace.compact_json(where) if where else "(none)")
        trace.kv("tenant injected", tenant_id)

    query_filter = filters.build_filter(tenant_id=tenant_id, **where)
    return vector_store.list_documents(_client_or_die(), query_filter)


def _run_search(args: dict, tenant_id: str | None, top_k: int) -> list[dict]:
    """Execute one search. tenant_id comes from us, never from the model."""
    query = args.get("query", "")
    where = {k: v for k, v in args.items() if k != "query" and v}

    if trace.is_on():
        trace.section("TOOL search_docs")
        trace.kv("query", repr(query))
        trace.kv("model filters", trace.compact_json(where) if where else "(none)")
        trace.kv("tenant injected", tenant_id)

    query_filter = filters.build_filter(tenant_id=tenant_id, **where)
    client = _client_or_die()
    query_vector = embeddings.embed_text(query)
    sparse_vector = sparse.encode_query(query) if config.HYBRID_SEARCH else None
    return vector_store.search(
        client, query_vector, top_k, query_filter=query_filter, sparse_vector=sparse_vector
    )


def _tool_payload(chunks: list[dict]) -> dict:
    """What the model sees back. Deliberately trimmed to what it needs to cite."""
    return {
        "results": [
            {
                "source": c["source"],
                "heading": c.get("heading"),
                "score": round(c["score"], 3),
                "text": c["text"],
            }
            for c in chunks
        ],
        "count": len(chunks),
    }


def _list_payload(documents: list[dict]) -> dict:
    """Enumeration is exhaustive, and the model is told so explicitly."""
    return {
        "documents": documents,
        "count": len(documents),
        "complete": True,  # every match is included, not a ranked subset
    }


def answer(
    question: str,
    history: Sequence[Message] = (),
    top_k: int = config.TOP_K,
    tenant_id: str | None = None,
    llm=None,
) -> tuple[list[dict], str, list[dict]]:
    """
    Agentic RAG. Returns (chunks_seen, final_answer, steps).

    `history` is the earlier turns of the conversation, as plain user and
    assistant text (AD11). The model is stateless: it knows about "the rice"
    in a follow-up only because the earlier turns are sent again, in full,
    on every call — which is why each turn costs more input tokens than the last.

    `llm` is injected for testability and provider choice; it defaults to
    whatever config.LLM_PROVIDER selects.
    """
    llm = llm or get_adapter()
    tenant_id = tenant_id or config.TENANT_ID

    trace.reset()
    trace.section("QUERY RECEIVED")
    trace.kv("question", repr(question))
    trace.kv("history", f"{len(history)} earlier message(s)")
    trace.kv("tenant", tenant_id)
    trace.kv("top_k", top_k)
    trace.kv("provider", getattr(llm, "provider", "?"))
    trace.kv("max search rounds", MAX_STEPS)

    messages: list[Message] = [*history, Message.user(question)]
    seen: dict[str, dict] = {}   # chunk_id -> chunk, deduped across searches
    steps: list[dict] = []

    for _ in range(MAX_STEPS):
        reply = llm.complete(messages, tools=TOOLS, system=SYSTEM_INSTRUCTION)

        if not reply.wants_tools:
            answer_text = reply.text or "(empty response from model)"
            trace.section("DONE")
            trace.kv("tool calls run", len(steps))
            trace.kv("unique chunks seen", len(seen))
            return list(seen.values()), answer_text, steps

        messages.append(Message.assistant(text=reply.text, tool_calls=reply.tool_calls))

        for call in reply.tool_calls:
            try:
                if call.name == LIST_DOCUMENTS.name:
                    documents = _run_list(call.arguments, tenant_id)
                    payload = _list_payload(documents)
                    count = len(documents)
                else:
                    chunks = _run_search(call.arguments, tenant_id, top_k)
                    for chunk in chunks:
                        seen.setdefault(chunk["chunk_id"], chunk)
                    payload = _tool_payload(chunks)
                    count = len(chunks)
            except ValueError as exc:
                # A filter the model made up. Tell it, so it can retry without it,
                # rather than failing the whole turn.
                payload = {"error": str(exc), "count": 0}
                count = 0

            steps.append(
                {
                    "tool": call.name,
                    "query": call.arguments.get("query", ""),
                    "where": {k: v for k, v in call.arguments.items() if k != "query" and v},
                    "count": count,
                }
            )
            messages.append(
                Message.tool(ToolResult(id=call.id, name=call.name, content=payload))
            )

    # Budget exhausted. Ask once more with no tools available, so the model has
    # to answer from what it already retrieved instead of searching forever.
    trace.section("BUDGET EXHAUSTED - forcing an answer (no tools offered)")
    final = llm.complete(
        messages,
        system=(
            SYSTEM_INSTRUCTION
            + "\n\nYou have no more searches left. Answer from the results you "
            "already have, or say you could not find it."
        ),
    )
    return (
        list(seen.values()),
        final.text or "I couldn't find that in our store policies. Please contact support.",
        steps,
    )
