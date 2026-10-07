# Session handoff: AI track (kirana-ai)

Written 2026-10-04 to start a fresh Claude session (updated later the same day). Read this first, then `CLAUDE.md` (repo
root), `kirana-ai/CLAUDE.md`, `kirana-ai/docs/AI-PLAN.md`, and `kirana-ai/docs/ai-contract.md`.

Suggested first prompt for the new session:

> Read kirana-ai/docs/session-handoff.md and the files it lists. Then tell me where we are
> and what's pending.

---

## 1. Who does what (unchanged from the root CLAUDE.md)

- **Ravi** is a senior backend engineer learning **AI engineering for interviews**. He makes
  the design decisions and runs everything locally. Don't ask him to write code.
- **Claude** writes all the code and explains the design in plain language. Before any real
  design choice, give the options and trade-offs, then ask. Challenge his decisions when you
  disagree.
- Before an experiment, ask Ravi to predict the outcome. Afterwards, explain what happened.
  His preference (memory): **explain experiments whose outcome is already known, without running
  them, and keep the pace fast.**
- After each milestone: what to run, what output to expect, the design points, and 1–2
  senior-level interview questions.
- **Commit or push only when Ravi says so.** Never commit `.claude/`. Never print secrets
  (`kirana-ai/.env` holds `GEMINI_API_KEY`).
- **Never touch `~/Workspace/rag-project`.** kirana-ai began as a fresh copy of its engine.
- The session email is only for identifying Ravi. Never send it to an external service. (A
  slip once put it in a Wikimedia User-Agent; the seed script's UA has no email now.)

## 2. The two tracks

| Track | Where | Status |
|---|---|---|
| Kirana system-design stages (Java backend) | root `CLAUDE.md`, `docs/` | Stages 1–7 done. **Stage 8 (distributed locking) is next, handled in other sessions.** |
| AI assistant (Python service) | `kirana-ai/`, own `CLAUDE.md` and `docs/AI-PLAN.md` | Phases 0–4 built and committed. **Next: Phase 5** (waiting for AD7) |

The tracks run in parallel. The Kirana-side changes each AI phase needs (AI-PLAN §5) are
pre-approved and don't count as "jumping ahead".

## 3. Architecture in one screen

- **kirana-ai** is a separate service in the same repo (`ravikant06/kirana`). FastAPI app
  `kirana_ai.api:app` on **:8000**. The frontend reaches it through the Vite `/ai` proxy.
- **Shared infra** (`infra/docker-compose.yml`):
  - **Postgres:** schema `ai` and role `kirana_ai`. `PUBLIC` usage on `public` is revoked, so the AI service can't read Kirana's tables.
  - **MinIO:** bucket `kb-docs` for knowledge-base files, `product-images` for Kirana.
  - **Redis**, **Kafka** (:9094), **Qdrant** (:6335/6336, volume `qdrant-data`).
- **LLM:** Gemini through an adapter layer (Gemini, OpenAI and Anthropic behind one interface).
  Template Method for tracing and cost records. Pricing is in `pricing.yaml` (Gemini 3.5 Flash
  costs $1.50 per 1M input tokens and $9.00 per 1M output). Thinking tokens are billed as output.
- **Retrieval:** hybrid search, combining dense Gemini embeddings with hand-written BM25 sparse
  vectors, fused in Qdrant with RRF. Point ids are deterministic (uuid5), and the tenant is
  injected by the server.
- **Chat:** a turn has 3 steps (load → agent → save). No DB transaction stays open across an LLM
  call. History is text-only (AD11). Every LLM call becomes one row in `ai.llm_calls`, with cost
  stored as NUMERIC.
- **Knowledge-base ingestion (Phase 2):**
  1. The browser uploads to MinIO with a signed POST policy that pins the key, Content-Type, size and `x-amz-meta-*` fields.
  2. A MinIO bucket notification sends an event to Kafka `kb.documents.v1`.
  3. The worker (group `kirana-ai-ingest`) indexes the file into Qdrant `kirana_kb`.
  4. Commits are manual (at-least-once): retry in place, then send to `kb.documents.v1-dlt`.
  5. `reconcile.py` handles seed-kb, redrive (back to the *original partition*, using `produce_confirmed` with `on_delivery`) and reindex. MinIO is the source of truth.
- **Product search (Phase 4):**
  - Kirana's outbox publishes `catalog.v1` with the envelope `{eventId,type,occurredAt,productId,data{name,description,category}}`.
  - `catalog.py` (group `kirana-ai-catalog`) indexes products into Qdrant `kirana_products`, skipping a product whose text hash hasn't changed.
  - `products.py` runs: hybrid retrieve 30 → **live hydration** from Kirana `GET /products/batch` → drop out-of-stock and over-price → top 5. The cross-encoder rerank is built but off (`RERANK_ENABLED=false`, AD22).
  - Qdrant payload indexes on `kirana_products`: `tenant_id`, `doc_id`, `category` (keyword) and `product_id` (integer, lookup only). In the dashboard, `product_id:134` works only because of that index: the UI types a value from the index schema. Values with spaces don't work there (it splits on spaces); use the Console tab (http://localhost:6335/dashboard → Console).
  - The model sends only product ids. The UI renders the cards from Kirana's live data.
- **SSE streaming (Phase 3):** `Accept: text/event-stream` on `POST /v1/chat`. Events: `start`, `status`, `step`, `token`, `reset`, `citation`, `products`, `done`, `error`. With Starlette 1.7 and uvicorn, the body iterator is never closed on disconnect. The workaround:
  - `ClosingStreamingResponse` plus the `_closing()` wrapper;
  - an explicit `events.close()` in `_sse` and in `chat.run`, which must run *before* the recorder is removed;
  - the agent and the Gemini adapter closing their streams.

  Verified: client hang-ups are recorded as "stream abandoned by the client".

## 4. What each phase delivered

- **Phase 0:** copy the engine into `kirana-ai/`, run on fresh Qdrant.
- **Phase 1:** chat API, threads and messages in Postgres (SQLAlchemy 2.0 + hand-written Alembic SQL), cost recording, ProblemDetail errors in Kirana's shape, chat dock UI. *Wrap-up still open:* UI experiments and `concepts-learned.md`.
- **Phase 2:** KB in MinIO → Kafka → worker → Qdrant; DLT and redrive; Manage → Knowledge base tab; page numbers on citations.
- **Phase 3 (built):** SSE streaming in every adapter, a relevance floor on dense cosine (`RELEVANCE_FLOOR=0.60`, from `eval.run_floor`), answer evals (`eval.run_answers` with a Gemini judge and `--grade` calibration), unverified citations flagged. `GEMINI_THINKING_LEVEL`: setting it to minimal cut time to first token from 3.4 s to 1.1 s.
- **Phase 4 (built, committed `3395f99`):**
  - **Backend:** `category` on products (DTOs, entity, mapper, cache keys bumped to `v2`); `ProductEvents` (MANDATORY propagation) written through the generic `OutboxWriter.append`; topics `catalog.v1` and `catalog.v1-dlt`; `GET /products/batch` (1–50 ids, request order kept, deleted products skipped). 114 backend tests pass.
  - **AI service:** `search_products` tool; `catalog.py`; `products.py`; `kirana.py` (httpx client for Kirana); CLI `index-products`; evals `eval.run_products` (25 golden cases) and `eval.run_routing`. 95 Python tests pass.
  - **Frontend:** `ProductCards.jsx` (live fetch; Add to cart sends an Idempotency-Key); category in Shop, ProductDetail and Manage.

## 5. Measured results (good interview material)

- Streaming hides the *writing* time, not the *thinking* time.
- The similarity-floor distributions overlap: answerable questions score 0.61–0.76, unanswerable ones 0.57–0.68. A floor can't separate them perfectly, so 0.60 is a trade-off.
- Declining to answer costs more than answering (~12 s vs ~5 s): the agent keeps searching before it gives up.
- The reranker gave **no measurable gain**. Hybrid alone: hit@1 92%, recall@5 96%, MRR 0.93. Reranked: 92%, 100%, 0.95, within noise. It also caused a mismatch between the order in the model's text and the order of the cards (Milk Chocolate ranked first for "healthy snacks").
- Tool routing: 16/16.
- Catalog sync: a description edit was reindexed in 835 ms; a price-only edit was skipped as "unchanged" in 61 ms.
- Qdrant `kirana_products` has 150 points, matching the 150 live products. The initial load came from the `index-products` REST snapshot, not Kafka. Only 2 events (edits to product 85) are on `catalog.v1`.

## 6. Demo data (reset done)

- `infra/seed/reset-demo-data.sh --yes` truncated 1.0 M orders, 3.0 M order items, 100k products and 50k users in 1.9 s. It kept users 1 (Ravi) and 2 (Puja), restarted the sequences (users restart at 101), emptied MinIO `product-images` and flushed Redis. Backup: `infra/data/backups/kirana-before-cleanup.dump` (gitignored).
- `infra/seed/catalog/seed_catalog.py`, through Kirana's own API, created:
  - 18 shoppers with Indian names, 20 users in total;
  - 150 products in `products.json` (12 categories, 3 out of stock);
  - photos from **Wikimedia Commons** (Ravi accepted this instead of Google). Credits are in `credits.json`. Re-run with `--images-only` to backfill photos; the script handles 429 with Retry-After.

## 7. Running everything: `infra/infra.py` (new, tested)

```
python3 infra/infra.py status [name|group]   # state, real health check, port, pid, uptime
python3 infra/infra.py restart <name|group>  # docker: rebuild image + recreate; host: kill + start
python3 infra/infra.py restart-all           # compose up --build --force-recreate, then hosts in order
python3 infra/infra.py start|stop <name|group>
python3 infra/infra.py logs <name> -n 100    # host servers log to infra/data/logs/<name>.log
```

- **Docker servers:** postgres, minio, redis, kafka, kafka-ui, qdrant, toxiproxy, payment-mock, warehouse-mock.
- **Host servers:** backend, frontend, ai-api, ai-worker, ai-catalog.
- **Groups:** infra, mocks, docker, kirana, kirana-ai, host, all.
- **Restart order for host servers:** backend → ai-api → ai-worker → ai-catalog → frontend.
- `restart-all` has **not** been run yet.

**Gotcha:** the shell's `$JAVA_HOME` points to JDK 18. The backend needs Java 21 or newer.
`infra.py` uses `$JAVA_HOME` only if it's at least Java 21; otherwise it falls back to Homebrew's
openjdk 25. For manual Maven runs, set
`JAVA_HOME=/opt/homebrew/Cellar/openjdk/25.0.1/libexec/openjdk.jdk/Contents/Home`.

## 8. Git state

- Branch `ai-phase-3`. Latest commit: `120052b AI Phase 3: streaming, relevance floor, answer evals` (not pushed).
- Commits on top: `3395f99` (Phase 4 + seed/reset scripts + `infra/infra.py`, reranker off), `ff5e697` (`product_id` index; each collection gets only its own indexes), then the docs update. None pushed. Never commit `.claude/` or `infra/__pycache__/`.
- Earlier phases were merged to `main` with `git checkout main && git merge --ff-only <branch> && git push`, run when Ravi asked.

## 9. Open decisions (Ravi's to make)

| Id | Question | Recommendation |
|---|---|---|
| AD20 | Gemini thinking level | Pending Ravi's thinking-level comparison run |
| **AD7** | Login style for Phase 5 (JWT) | **Dev login** (pick a user, get a real RS256 JWT); passwords later. Ravi is deciding |
| AD10 | Default LLM | Gemini for now (it's the only API key) |

## 10. Pending work, in order

1. Phases 3–5 are merged to `main` (local; push only when Ravi says). Work continues on `ai-phase-6`.
2. **Phase 6 is planned and approved** (AI-PLAN §4, AD28–AD32). Next: **M1 tool policy layer**, then
   M2 security eval, M3 narrowed tokens, M4 cancel with approval, M5 cart with approval, M6 attacks.
   One milestone at a time, reported to Ravi before the next.
3. **Deferred by Ravi to the very end:** the Phase 3 runs (answer-eval baseline, `--grade`
   calibration, thinking-level comparison → AD20).

## 11. Known gotchas

- Alembic: `search_path=ai` makes `ai` the default schema. `env.py` and the drift test set `dialect.default_schema_name="public"`.
- Kafka AdminClient: keep a reference to the client until its futures resolve.
- MinIO events: the object key is URL-encoded, metadata keys are canonical (`X-Amz-Meta-*`), and delete events carry no metadata.
- MinIO and librdkafka use different partitioners, so a redrive must target the original partition (taken from a header).
- `flush()` isn't proof of delivery. Use `produce_confirmed` (the `on_delivery` callback).
- Deleting a KB document always removes the MinIO object. The worker ignores events for deleted documents.
- Tests are offline: FakeAdapter for the LLM, Testcontainers for Postgres. Anything that needs Qdrant or an LLM is an eval, not a test.
- `kirana-ai`: Python 3.11, `.venv`, `pip install -e ".[dev]"`, `pytest`.
