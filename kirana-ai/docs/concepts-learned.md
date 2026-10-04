# AI engineering concepts learned

The AI track's counterpart of Kirana's `docs/concepts-learned.md`: each entry is something
built, measured or broken in `kirana-ai/`, stated the way you would explain it in an interview.

## Phase 0: from a RAG prototype to a service

1. **Corpus-specific vs engine code.** Chunking, embeddings, hybrid retrieval and the agent
   loop carried over unchanged; the prompt, document types, filters and eval set did not.
   An eval set belongs to its corpus: the engineering baseline meant nothing for store policies.
2. **Hybrid retrieval.** Dense vectors match meaning; BM25 (sparse) matches exact tokens like
   "₹49", "NPOP" or "1800-000-0000". Running both and fusing the ranked lists with RRF fixes
   the identifier queries dense search misses. Measure it (recall@k with confidence intervals)
   before believing it.
3. **Scope is injected, never chosen by the model.** `tenant_id` is applied by the server on
   every search and is not a tool parameter, so no prompt can widen it. The same rule covers
   user identity from Phase 5.

## Phase 1: the chat behind an API

4. **LLMs are stateless.** A follow-up ("and if it's sealed?") works only because earlier turns
   are resent on every call. Each turn therefore costs more input tokens than the last
   (measured: +194 tokens for one prior exchange with text-only history).
5. **Text-only history (AD11).** Resending only questions and final answers, not tool calls or
   retrieved chunks, keeps threads cheap and provider-neutral (no stored thought signatures).
   The cost: a follow-up searches again because the earlier chunks are gone.
6. **Tokens are the unit of cost and latency, and thinking tokens are billed but invisible.**
   One answer was ~150 visible tokens but 535 billed output tokens; at 6× the input price, the
   hidden reasoning cost more than the whole context. Count `thoughts_token_count` as output.
7. **Record every LLM call where it happens.** A listener on the adapter's Template Method
   writes one `llm_calls` row per call, in its own transaction, success or failure. Cost rows
   have no foreign keys because a failed turn has no answer to point at but was still billed.
   Money is `NUMERIC`, never float; an unknown price is `NULL`, never 0.
8. **Not every API reports usage.** Gemini's embedding endpoint returns no token counts, so
   embedding cost needs estimating or reconciling with the billing export.
9. **Never hold a database transaction across an LLM call.** A turn is three steps: short
   transaction (thread, history, question) → agent with no transaction → short transaction
   (answer). Holding one across a 4-second LLM wait pins a pooled connection per chat.
10. **Shared mutable state per request.** Listeners live on the adapter instance, so a shared
    adapter would record one turn's calls into another's costs. One adapter per turn, with the
    expensive SDK client cached per process.
11. **No `SystemExit` in a server.** It ends the process, not the request. Upstream failures
    are exceptions mapped to 503 ProblemDetails, with the provider's message logged, never
    returned (it can carry project ids or quota details).
12. **Blocking LLM calls in FastAPI.** Plain `def` routes run in a 40-thread pool, so a slow
    chat never freezes the event loop, but the pool caps concurrent chats. The real ceiling is
    usually the provider's rate limit and your budget, not threads.
13. **Rendering model output safely.** Markdown is turned into React elements, never inserted as
    HTML, so text the model echoes from documents can never become markup (prompt injection, Phase 8).

## Phase 2: an event-driven knowledge base

14. **Storage events.** MinIO, like S3, publishes "object created / removed" to Kafka. The upload
    is the event: no confirm call, and uploads that bypass the API (an admin's `mc cp`) still
    trigger indexing.
15. **Event-carried state via object metadata.** The signed POST policy pins `x-amz-meta-*`
    fields (document id, title, type), and MinIO copies them into the event. Changing any field
    after signing gets a 403, so the event carries what the server signed, not what a client
    chose. Delete events carry no metadata, so the document id lives in the object key.
16. **Real events differ from the docs.** Checked against a real event: the object key is
    URL-encoded inside the event, metadata keys arrive canonicalised (`X-Amz-Meta-Title`), and
    browser uploads are `ObjectCreated:Post` while SDK uploads are `:Put`. Read one before
    writing the consumer.
17. **Keys give per-entity order, within one partitioner.** Same key → same partition → in
    order, so a document's delete can't overtake its upload. But MinIO's client and librdkafka
    hash keys differently: a re-driven message produced by key landed on another partition.
    Re-drive back to the original partition.
18. **At-least-once with idempotent consumers.** Commit the offset only after the work. A
    crash replays the message, which is safe because point ids are deterministic, a document's
    old chunks are deleted before new ones are written, unchanged text (content hash) is
    skipped, and deleting something already gone is a no-op.
19. **Transient vs permanent failures.** Retry only what retrying can fix (a down embedding
    API, Qdrant, Postgres). A scanned PDF with no text fails immediately with the reason.
    Retry in place, not via retry topics, when per-entity order matters.
20. **Dead letters must be confirmed before the commit.** `flush()` returning doesn't prove
    delivery: errors arrive only via the delivery callback. Committing after an unconfirmed
    dead-letter write loses the event from both topics.
21. **Reconciliation is the safety net.** Events can still be lost before Kafka has them, or
    mishandled by a bug. The index is derived data, so a `reindex` that compares the source
    (MinIO) with the database and Qdrant, and repairs every difference, is what makes it
    correct in the end. It also removed the Phase 0 chunks no file backed.
22. **One ingestion path.** Seed documents go through the same upload → event → worker path
    as admin uploads. Two paths drift; one path gets tested by every use.
23. **Separate consumer groups per job.** Documents and (Phase 4) products get their own groups,
    so a slow 40-page PDF never delays product updates, lag is visible per job, and each scales alone.
24. **Durable producers at the edge.** MinIO's `queue_dir` keeps events on disk while Kafka is
    down and sends them when it returns; without it, uploads during an outage would never be indexed.
25. **PDF parsing keeps page boundaries.** Pages are joined with a paragraph break and each page's
    start offset recorded, so every chunk knows its page and citations can say "p. 2".
