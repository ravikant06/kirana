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
      cli.py          ingest / ask / chat, until the HTTP API exists
      db/             SQLAlchemy models + session_scope()
      config.py       every tunable, overridable by env / .env
    migrations/       Alembic (schema `ai`)
    pricing.yaml      USD per 1M tokens; missing = cost NULL (unknown, not free)
    kb/seed/          seed knowledge base (Phase 0–1; MinIO from Phase 2)
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
- **Scope and identity are injected by the server, never tool parameters.**
- **Embed descriptions, fetch live facts.** Price, stock and order status never go in Qdrant.
- **The AI service never reads Kirana's tables.** Kirana data comes over REST only.
- Tests need no network and no API key. Postgres tests use Testcontainers (Docker), like
  Kirana's. Anything that needs Qdrant or an LLM is an eval or a manual run, not a test.
- **Database:** SQLAlchemy 2.0 ORM (AD2), everything in schema `ai`, role `kirana_ai`.
  Alembic owns the schema; migrations are hand-written SQL. `test_db.py` checks models
  and migrations agree. Relationships are `lazy="raise"`. Enums are VARCHAR + CHECK.
  Never hold a DB transaction open across an LLM call.
- Keep the module docstring style: say *why*, not just what.

## Current phase

**Phase 0: done** (engine copied, Kirana seed corpus, Qdrant in compose, CLI, retrieval
eval, offline tests). **Next: Phase 1** (FastAPI chat API + chat panel in Kirana's
frontend). Phase 1 decisions are settled: AD2 (SQLAlchemy + Alembic), AD11 (text-only history), AD12 (X-User-Id owns threads).

Only the current phase is in scope. Gaps listed in the plan (no deletes, no relevance
floor, no streaming…) are deliberate: later phases fix them.
