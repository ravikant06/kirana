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
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from kirana_ai import config, embeddings, filters, sparse, trace, vector_store
from kirana_ai import products as products_mod
from kirana_ai.llm import Message, TextDelta, ToolResult, ToolSpec, get_adapter

MAX_STEPS = 5  # search rounds per question, before we force an answer

SYSTEM_INSTRUCTION = """You are the shopping assistant of Kirana, an online grocery store.
You help shoppers find products and answer questions about the store's policies
(returns, refunds, cancellations, delivery, payments, freshness).

You cannot see the catalogue or the store's documents directly. Use the tools.

Choosing a tool:
- `search_products` finds products to buy: recommendations, "do you have…",
  "something for…", price limits ("under ₹200"). It returns live price and stock.
- `search_docs` finds passages of the store's policies and FAQ that answer a
  specific question. It returns only its best few matches.
- `list_documents` enumerates the policy documents exactly. Use it when the
  question asks what documents or policies exist.
- A question can need both, e.g. "suggest snacks and tell me the delivery fee".

Products:
- The shopper sees the products you found as cards with photo, price and an
  add-to-cart button. Do not repeat every price: name the best picks and say
  briefly why they fit. Only state a price that the tool returned.
- If the tool says items were dropped for being out of stock or over the price
  limit, you may say so. Never recommend a product the tool did not return.
- Set `category` only when the shopper clearly asks for one; set `max_price`
  only when they give a limit.

Policies:
- Call a tool at least once before answering a policy question.
- Do not repeat a search you have already run with the same or near-identical
  arguments. If two searches have not helped, answer with what you have.
- Set filters only when the question clearly implies them. An unnecessary
  filter can hide the correct answer.
- If a filtered search returns nothing, search again with fewer filters
  before concluding the answer is absent.
- Answer ONLY from retrieved passages. Never invent policies, fees, time
  limits or amounts.
- Retrieved passages can be about a nearby topic without answering the question.
  Answer only if a passage actually states the answer; otherwise do not guess.
- If the documents do not contain the answer, say:
  "I couldn't find that in our store policies. Please contact support."
- Be brief and friendly. When you used policy documents, end your answer with a
  "Sources:" line listing their filenames. Product answers need no Sources line."""

# Declared once, in plain JSON Schema. Each adapter rewraps this in whatever
# envelope its provider expects — see kirana_ai/llm/*.py.
SEARCH_DOCS = ToolSpec(
    name="search_docs",
    description=(
        "Search Kirana's store policies and FAQ (returns, delivery, refunds, payments, "
        "freshness). Returns the most relevant passages with their source filenames. "
        "Not for finding products: use search_products for that."
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

SEARCH_PRODUCTS = ToolSpec(
    name="search_products",
    description=(
        "Find products in Kirana's catalogue by meaning: what the shopper wants, a use "
        "(\"for a diabetic breakfast\"), a diet, an occasion. Returns up to 5 in-stock "
        "products with their live price and stock. Use it for any question about what to buy."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What the shopper is looking for, in a few descriptive words.",
            },
            "category": {
                "type": "string",
                "enum": config.PRODUCT_CATEGORIES,
                "description": "Only products in this category. Set it only if the shopper asks for one.",
            },
            "max_price": {
                "type": "number",
                "description": "Highest price in rupees, if the shopper gives a limit (\"under ₹200\" -> 200).",
            },
        },
        "required": ["query"],
    },
)

TOOLS = [SEARCH_PRODUCTS, SEARCH_DOCS, LIST_DOCUMENTS]


def _run_list(args: dict, tenant_id: str | None) -> list[dict]:
    """Enumerate matching documents. tenant_id comes from us, as ever."""
    where = {k: v for k, v in args.items() if v}
    if trace.is_on():
        trace.section("TOOL list_documents")
        trace.kv("model filters", trace.compact_json(where) if where else "(none)")
        trace.kv("tenant injected", tenant_id)

    query_filter = filters.build_filter(tenant_id=tenant_id, **where)
    return vector_store.list_documents(vector_store.get_client(), query_filter)


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
    client = vector_store.get_client()
    query_vector = embeddings.embed_text(query)
    sparse_vector = sparse.encode_query(query) if config.HYBRID_SEARCH else None
    return vector_store.search(
        client, query_vector, top_k, query_filter=query_filter, sparse_vector=sparse_vector
    )


def _above_floor(chunks: list[dict]) -> tuple[list[dict], int]:
    """Drop chunks whose dense similarity is below the relevance floor. Returns (kept, dropped)."""
    floor = config.RELEVANCE_FLOOR
    kept = [c for c in chunks if c.get("similarity", 1.0) >= floor]
    return kept, len(chunks) - len(kept)


def _tool_payload(chunks: list[dict], dropped: int = 0) -> dict:
    """What the model sees back. Deliberately trimmed to what it needs to cite."""
    if not chunks and dropped:
        return {"results": [], "count": 0,
                "note": (f"{dropped} passage(s) were found but none was relevant enough to this "
                         "question. Do not answer from general knowledge: search again with "
                         "different words, or say you couldn't find it.")}
    return {
        "results": [
            {
                "source": c["source"],
                "title": c.get("title"),
                "heading": c.get("heading"),
                "page": c.get("page"),                 # PDFs only
                "score": round(c["score"], 3),
                "text": c["text"],
            }
            for c in chunks
        ],
        "count": len(chunks),
    }


def _run_products(args: dict) -> products_mod.ProductResults:
    if trace.is_on():
        trace.section("TOOL search_products")
        trace.kv("model arguments", trace.compact_json(args))
    max_price = args.get("max_price")
    return products_mod.search(args.get("query", ""), category=args.get("category") or None,
                               max_price=float(max_price) if max_price else None)


def _products_payload(r: products_mod.ProductResults) -> dict:
    """What the model sees: live facts for the products the UI will show as cards."""
    payload = {
        "products": [{k: p[k] for k in ("product_id", "name", "category", "price", "stock", "description")}
                     for p in r.products],
        "count": len(r.products),
    }
    dropped = {k: v for k, v in (("out_of_stock", r.out_of_stock), ("over_price", r.over_price)) if v}
    if dropped:
        payload["dropped"] = dropped
    if not r.products:
        payload["note"] = "No matching in-stock product. Say so; do not suggest products from memory."
    return payload


def _list_payload(documents: list[dict]) -> dict:
    """Enumeration is exhaustive, and the model is told so explicitly."""
    return {
        "documents": documents,
        "count": len(documents),
        "complete": True,  # every match is included, not a ranked subset
    }


@dataclass(frozen=True)
class AgentEvent:
    """
    What the agent loop reports while it runs (Phase 3 streaming):

        status  {"tool", "query", "where"}   a tool call is about to run
        step    {"tool", "query", "where", "count"}   it ran
        token   {"text"}                     a piece of answer text
        reset   {}                           text streamed so far was a preamble to tool
                                             calls, not the answer: discard it
        done    {"chunks", "answer", "steps"}   the final result
    """
    kind: str
    data: dict


def answer(
    question: str,
    history: Sequence[Message] = (),
    top_k: int = config.TOP_K,
    tenant_id: str | None = None,
    llm=None,
) -> tuple[list[dict], str, list[dict]]:
    """
    Agentic RAG. Returns (chunks_seen, final_answer, steps).

    The non-streaming view of answer_stream(): same loop, events consumed here.
    """
    for event in answer_stream(question, history, top_k, tenant_id, llm):
        if event.kind == "done":
            return event.data["chunks"], event.data["answer"], event.data["steps"]
    raise RuntimeError("agent loop ended without a result")


def answer_stream(
    question: str,
    history: Sequence[Message] = (),
    top_k: int = config.TOP_K,
    tenant_id: str | None = None,
    llm=None,
) -> Iterator[AgentEvent]:
    """
    The agent loop, yielding events as it goes, so the UI can show progress and stream text.

    `history` is the earlier turns of the conversation, as plain user and
    assistant text (AD11). The model is stateless: it knows about "the rice"
    in a follow-up only because the earlier turns are sent again, in full,
    on every call — which is why each turn costs more input tokens than the last.

    Every model call streams. Text is forwarded as it arrives; if that call then
    asks for tools, the text was only a preamble, and a `reset` tells the UI to drop it.

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
        reply = None
        streamed_text = False
        stream = llm.stream(messages, tools=TOOLS, system=SYSTEM_INSTRUCTION)
        try:
            for item in stream:
                if isinstance(item, TextDelta):
                    streamed_text = True
                    yield AgentEvent("token", {"text": item.text})
                else:
                    reply = item
        finally:
            stream.close()      # if we are closed mid-call, close the call now (records "abandoned")

        if not reply.wants_tools:
            answer_text = reply.text or "(empty response from model)"
            trace.section("DONE")
            trace.kv("tool calls run", len(steps))
            trace.kv("unique chunks seen", len(seen))
            yield AgentEvent("done", {"chunks": list(seen.values()), "answer": answer_text, "steps": steps})
            return

        if streamed_text:
            yield AgentEvent("reset", {})
        messages.append(Message.assistant(text=reply.text, tool_calls=reply.tool_calls))

        for call in reply.tool_calls:
            dropped = 0
            where = {k: v for k, v in call.arguments.items() if k != "query" and v}
            yield AgentEvent("status", {"tool": call.name, "query": call.arguments.get("query", ""),
                                        "where": where})
            product_ids: list[int] = []
            try:
                if call.name == SEARCH_PRODUCTS.name:
                    found = _run_products(call.arguments)
                    payload = _products_payload(found)
                    count = len(found.products)
                    product_ids = [p["product_id"] for p in found.products]
                elif call.name == LIST_DOCUMENTS.name:
                    documents = _run_list(call.arguments, tenant_id)
                    payload = _list_payload(documents)
                    count = len(documents)
                else:
                    chunks, dropped = _above_floor(_run_search(call.arguments, tenant_id, top_k))
                    for chunk in chunks:
                        seen.setdefault(chunk["chunk_id"], chunk)
                    payload = _tool_payload(chunks, dropped)
                    count = len(chunks)
            except ValueError as exc:
                # A filter the model made up. Tell it, so it can retry without it,
                # rather than failing the whole turn.
                payload = {"error": str(exc), "count": 0}
                count = 0

            step = {"tool": call.name, "query": call.arguments.get("query", ""), "where": where,
                    "count": count}
            if call.name == SEARCH_DOCS.name and dropped:
                step["below_floor"] = dropped
            if product_ids:
                # Ids only: the UI fetches price, stock and photo from Kirana itself, so a
                # price on screen can never come from the model (or from a stale index).
                step["product_ids"] = product_ids
                yield AgentEvent("products", {"product_ids": product_ids})
            steps.append(step)
            yield AgentEvent("step", step)
            messages.append(
                Message.tool(ToolResult(id=call.id, name=call.name, content=payload))
            )

    # Budget exhausted. Ask once more with no tools available, so the model has
    # to answer from what it already retrieved instead of searching forever.
    trace.section("BUDGET EXHAUSTED - forcing an answer (no tools offered)")
    final = None
    stream = llm.stream(
        messages,
        system=(
            SYSTEM_INSTRUCTION
            + "\n\nYou have no more searches left. Answer from the results you "
            "already have, or say you could not find it."
        ),
    )
    try:
        for item in stream:
            if isinstance(item, TextDelta):
                yield AgentEvent("token", {"text": item.text})
            else:
                final = item
    finally:
        stream.close()
    yield AgentEvent("done", {
        "chunks": list(seen.values()),
        "answer": final.text or "I couldn't find that in our store policies. Please contact support.",
        "steps": steps,
    })
