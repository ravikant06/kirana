# CLAUDE.md: kirana-ai

Context for AI coding sessions inside `kirana-ai/`. The repo-root `CLAUDE.md` still
applies for **roles and working method** (Ravi decides, predicts experiments and runs
everything; you write the code and explain it; options and trade-offs before any real
design choice). Its **stack, package and Java conventions, and stage rules do not apply
here**: this folder is a separate Python service on its own track.

## Read these first

- `docs/AI-PLAN.md`: the phases, requirements, what Kirana needs per phase, open decisions (AD*).
- `docs/ai-contract.md`: the API and events between Kirana and this service. Change it
  together with both sides.

## What this is

Kirana's AI assistant, as its own service. It began as a fresh copy of the engine from
`~/Workspace/rag-project` (GitHub: `rag-engine-from-scratch`), which stays untouched.
Engine decisions inherited from it (their D1–D14, in that repo's `DESIGN.md`):
character chunking with soft boundaries, metadata only in the payload, scope injected by
the server, filters applied during search, payload indexes, deterministic point ids,
the agent loop with a bounded budget, the LLM adapter pattern, hybrid BM25 + dense with RRF.

## Stack and layout

    kirana_ai/        the package (flat on purpose)
      llm/            adapter: Gemini, OpenAI, Anthropic behind one interface
      agent.py        agent loop + tool specs (takes text-only history)
      chat.py         one chat turn: thread, history, agent, messages (3 steps, no tx across the LLM)
      usage.py        CallRecorder: one ai.llm_calls row per LLM call; cost from pricing.yaml
      loader.py, chunker.py, embeddings.py, sparse.py, vector_store.py, filters.py
      api.py          FastAPI routes + ProblemDetail error handlers (uvicorn kirana_ai.api:app --port 8000)
      schemas.py      Pydantic DTOs for the API; never return db rows directly
      errors.py       UpstreamUnavailable (Qdrant / embeddings / empty KB) -> 503
      kb.py           KB documents API side: signed POST policy with pinned x-amz-meta-*, list, delete
      worker.py       ingest worker: Kafka kb.documents.v1 -> Qdrant; in-place retries, then <topic>-dlt
      reconcile.py    seed-kb, redrive (to the original partition), reindex (MinIO is the source of truth)
      kafka.py, storage.py   topics; kb-docs bucket + its MinIO -> Kafka rule
      cli.py          ask / chat / threads / kafka-setup / events / seed-kb / redrive / reindex
      db/             SQLAlchemy models + session_scope()
      config.py       every tunable, overridable by env / .env
    migrations/       Alembic (schema `ai`)
    pricing.yaml      USD per 1M tokens; missing = cost NULL (unknown, not free)
    kb/seed/          seed policies; `cli seed-kb` uploads them to MinIO (the only ingestion path)
    eval/             retrieval eval: golden sets, recall@k, MRR, bootstrap CIs
    tests/            pytest, offline only (FakeAdapter, no network)
    docs/             plan and contract

Python 3.11, venv in `.venv`, `pip install -e ".[dev]"`. Qdrant runs from Kirana's
`infra/docker-compose.yml` on host port **6335**. Collection `kirana_kb`.

## Conventions

- **No LLM or embedding SDK above its layer.** Chat goes through `kirana_ai.llm`
  (`get_adapter()`), embeddings through `embeddings.py`.
- **No frameworks that hide the mechanics** (no LangChain etc.). Hand-written loops, so
  every step can be traced and explained.
- **Scope and identity are injected by the server, never tool parameters.** The shopper's verified
  token rides in the turn (`PreparedTurn.user_token`), never in tool arguments, prompts or logs.
- **Embed descriptions, fetch live facts.** Price, stock and order status never go in Qdrant.
- **The AI service never reads Kirana's tables.** Kirana data comes over REST only.
- **No SystemExit in the request path** (it ends a server, not a request): raise LLMError or
  UpstreamUnavailable. One LLM adapter per turn; SDK clients are cached per process.
- Tests need no network and no API key. Postgres tests use Testcontainers (Docker), like
  Kirana's. Anything that needs Qdrant or an LLM is an eval or a manual run, not a test.
- **Database:** SQLAlchemy 2.0 ORM (AD2), everything in schema `ai`, role `kirana_ai`.
  Alembic owns the schema; migrations are hand-written SQL. `test_db.py` checks models
  and migrations agree. Relationships are `lazy="raise"`. Enums are VARCHAR + CHECK.
  Never hold a DB transaction open across an LLM call.
- Keep the module docstring style: say *why*, not just what.

## Current phase

**Phases 0–2 done.** Phase 2: knowledge base in MinIO (bucket `kb-docs`), uploads publish to
Kafka `kb.documents.v1` with our `x-amz-meta-*` metadata, the worker (group
`kirana-ai-ingest`) indexes them; dead letters in `kb.documents.v1-dlt`; Manage → Knowledge
base tab; page numbers on citations. **Phase 3 built:** SSE streaming (`stream()` in every
adapter, `agent.answer_stream`, `chat.prepare` + `chat.run`), relevance floor on dense cosine
(`RELEVANCE_FLOOR`, from `eval.run_floor`), answer evals (`eval.run_answers`, Gemini judge,
`--grade`), unverified citations flagged. Ravi's Phase 3 runs still open (baseline, grading, thinking level).
**Phase 4 built:** `search_products` tool (`products.py`: hybrid → live hydration from Kirana via `kirana.py`; the reranker is built but off, AD22), `kirana_products` index from `catalog.v1` events (`catalog.py`, group `kirana-ai-catalog`) plus `cli index-products`, product cards in chat, evals `eval.run_products` and `eval.run_routing`. Shared Kafka loop in `consumer.py`.
`docs/concepts-learned.md` covers Phases 0–4. Ravi runs the Phase 3 evals at the very end, with every phase's results. **Phase 5 built (slim):** `auth.py` verifies Kirana's RS256 token on every route (JWKS, `aud` kirana-ai); `X-User-Id` is gone; order tools `get_my_orders` / `get_order` forward the token, have no identity parameter, and are offered only to signed-in turns (`agent.tools_for`). Kirana: password login, roles as permission bundles in the token's `scope`, JWKS (D72, D73); the AI checks `chat` and `kb:write`. Next: Phase 6 planning with Ravi.

Only the current phase is in scope. Gaps listed in the plan for later phases (no auth, no
actions, no budgets or timeouts…) are deliberate: those phases fix them.
