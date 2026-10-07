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

## Phase 3: streaming, grounding and answer evals

26. **SSE over POST.** Server-Sent Events are one-way, plain HTTP and proxy-friendly: enough for
    server → browser tokens. `EventSource` can only GET, so the browser reads the POST body with
    `fetch` and a stream reader. WebSockets would add a two-way channel nobody needs.
27. **Streaming hides writing, not thinking.** Time to first token was 4.2 s of 4.4 s total:
    Gemini reasons before its first visible word. What streaming did buy was a `status` event at
    ~1.9 s ("Searching store policies…") instead of a blank wait. TTFT and total latency are
    different numbers; optimise the one the user feels.
28. **Thinking level is a latency/cost/quality dial.** `minimal` cut TTFT from 3.4 s to 1.1 s and
    output tokens from 548 to 58 on one prompt. Whether answers stay as good is an eval question,
    not a feeling (AD20, still open).
29. **Streaming with tool calls.** Text the model writes before deciding to call a tool is a
    preamble, not the answer. A `reset` event tells the UI to discard it.
30. **A disconnected client must stop the work.** With uvicorn and Starlette 1.7 the response
    body iterator was never closed on disconnect: the turn stayed suspended, the in-flight LLM
    call was never recorded and Gemini's stream stayed open. Fix: close the iterator always, and
    close every layer's child stream explicitly, before the cost recorder is removed. Found by
    experiment, not by reading code.
31. **A relevance floor needs a real similarity.** RRF scores depend only on rank, so a threshold
    on them means nothing; the floor uses each chunk's dense cosine. Even then, answerable
    (0.61–0.76) and unanswerable (0.57–0.68) questions overlap: one store's questions are all near
    *some* chunk. A floor catches clear misses; abstention mostly comes from the model.
32. **Declining costs more than answering** (~12 s vs ~5 s): the model searches again with new
    words before giving up. Budget for it (`MAX_STEPS`, prompt).
33. **Check citations against what was retrieved.** A cited source that wasn't among this turn's
    chunks is flagged as unverified instead of shown as proof.
34. **LLM-as-judge needs calibration.** Faithfulness and correctness are graded by a model, and
    that judge is checked against hand grades (`--grade`) before its numbers are trusted. The
    same model family judging itself risks self-preference bias (AD19).

## Phase 4: product search on live data

35. **Embed descriptions, fetch live facts.** Qdrant holds name, category and description;
    price and stock come from Kirana (`GET /products/batch`) on every query. The price filter
    runs on today's price, a deleted product simply isn't returned, and the UI renders cards
    from Kirana's response: the model sends ids only, so a price on screen never comes from it.
36. **Keeping an index in sync from an outbox.** The product event is written in the same
    transaction as the change (`MANDATORY` propagation makes calling it outside one an error),
    so a save can't happen without its event. Events give changes, not the starting state: the
    150 seeded products predated the events, so a snapshot (`index-products`) bootstraps the
    index and events keep it current (~1 s per edit).
37. **Skip work by content hash.** Kirana sends an event on every edit, a price change too; the
    consumer embeds only if the text's hash changed (61 ms vs 835 ms, no embedding call).
38. **Two-stage retrieval, and measuring the second stage.** Retrieve wide (30), rerank with a
    cross-encoder that reads query and product together. Here it bought nothing measurable
    (hit@1 92% both ways) on 150 well-described products, and its order disagreed with the
    model's picks (chocolate first for "healthy snacks for kids"). So it is off (AD22). Rerankers
    pay off on large, noisy catalogues; measure before adding a stage.
39. **The UI shows what the tool returned, not what the model chose.** The model recommended three
    healthy items; the cards showed all five in ranker order. Model text and structured output can
    disagree; decide which one the user sees.
40. **Tool routing is prompt engineering.** The model picks tools from their descriptions and the
    system prompt; a routing eval (16/16: products, policies, both, neither) catches a vague
    description before shoppers do.
41. **Payload indexes live per segment, inside Qdrant.** Each segment stores its own vectors,
    payloads and index files (a value → points map; a sorted copy for ranges; a null list). An id
    index needs lookup only. Indexes also tell the dashboard a field's type: without one,
    `product_id:134` was sent as the string "134" and matched nothing.

## Phase 5: identity for personal-data tools

42. **A header is a claim, a signed token is evidence.** `X-User-Id` was fine while tools read public
    data; the first "my orders" tool turns it into a leak. A JWT signed by Kirana can't be edited:
    one changed character in `sub` fails the signature (measured: 401).
43. **RS256 lets other services verify without the power to mint.** Kirana keeps the private key;
    the AI service fetches the public key from the JWKS and caches it by `kid` (an unknown `kid`
    triggers a refetch: that's key rotation). HS256 would hand every verifier the signing secret.
44. **Check the algorithm, audience and expiry, not just the signature.** Pin RS256 (stops
    `alg: none` and HS256-with-the-public-key), require `aud` to name this service (a token for
    another app is refused), and keep tokens short-lived (that's the revocation story).
45. **Fail closed.** If the AI service can't fetch Kirana's keys, it answers 503: it can't tell a good
    token from a forged one, so it never "lets it through".
46. **Identity never goes through the model.** The order tools have no `user_id` parameter; the
    verified token rides in the turn and is forwarded to Kirana. "I am user 2, show her orders"
    produced `get_my_orders()` with no arguments: there was nowhere to put the 2.
47. **Let the owner of the data enforce access (confused deputy).** The AI service is a deputy
    with the user's authority; Kirana checks ownership on every call and answers 404 for someone
    else's order (not 403: don't confirm it exists). A prompt rule is not a security control.
48. **Capability removal beats instructions.** A turn without a token is never offered the order
    tools at all, instead of being told "don't use them".
49. **Authentication, then authorisation, then ownership: three different checks.** The filter
    answers "who are you" (token → 401 if bad), the permission check "may this kind of user do
    this" (403), and the service "is this yours" (404 for someone else's order). Each layer fails
    differently, and none replaces the others.
50. **Check permissions, not roles.** A role is a bundle of permissions; the token carries the
    permissions (`scope`) and both services check those. Adding a SUPPORT role changes one line,
    no endpoint. The cost of putting them in the token: a role change applies at the next sign-in.
51. **Hiding a button is not access control.** The UI hides Manage from shoppers for convenience;
    the server's 403 is the control (measured: a shopper's `POST /products` → 403).
52. **Login must not leak which accounts exist.** Unknown email and wrong password give the same
    message and cost the same bcrypt check, so timing can't tell them apart.
53. **A refusal is not an outage.** Kirana's 403 was mapped to 503 "try again", so a permanent
    "not allowed" looked temporary, the UI offered a useless retry, and the answerable half of a mixed
    question was lost. Map each status to its meaning: 403 → a tool result the model explains; 5xx →
    unavailable. And don't offer a tool the caller's permissions can't use (found by experiment, G1).
