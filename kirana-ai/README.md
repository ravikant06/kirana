# kirana-ai

Kirana's AI shopping assistant, as its own Python service. Today it answers store-policy
questions (returns, delivery, refunds…) from a knowledge base, using an agent that picks
its own searches over hybrid (dense + BM25) retrieval in Qdrant.

The plan is in [docs/AI-PLAN.md](docs/AI-PLAN.md); the contract with Kirana is in
[docs/ai-contract.md](docs/ai-contract.md).

## Setup

```bash
# 1. Qdrant (with the rest of Kirana's infra)
cd infra && docker compose up -d qdrant        # dashboard: http://localhost:6335/dashboard

# 2. Python env
cd ../kirana-ai
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env                            # then set GEMINI_API_KEY

# 3. Database: schema `ai` + role `kirana_ai` (once), then the tables
docker exec -i kirana-postgres-1 psql -U kirana -d kirana < ../infra/seed/ai-schema.sql
alembic upgrade head

# 4. Knowledge base: topics + bucket (once), the ingest worker, the seed policies
python -m kirana_ai.cli kafka-setup             # topics, kb-docs bucket, MinIO -> Kafka rule
python -m kirana_ai.worker                      # keep running: Kafka consumer, indexes uploads
python -m kirana_ai.cli seed-kb                 # (other terminal) upload kb/seed/* to MinIO
# Admins upload more in Kirana: Manage -> Knowledge base
python -m kirana_ai.cli reindex --dry-run       # repair drift MinIO / ai.documents / Qdrant
python -m kirana_ai.cli redrive                 # replay the dead-letter topic once fixed
python -m kirana_ai.cli ask "Can I return opened rice?"
python -m kirana_ai.cli ask -t "..."            # trace every step
python -m kirana_ai.cli chat --user 7           # saved conversation, with history
python -m kirana_ai.cli threads --user 7        # that shopper's threads

# 5. The API (what Kirana's frontend calls, via the Vite proxy at /ai)
uvicorn kirana_ai.api:app --reload --port 8000   # docs: http://localhost:8000/docs

# 6. Measure and test
python -m eval.run_retrieval --compare          # dense vs hybrid recall@k
python -m eval.run_floor                        # pick RELEVANCE_FLOOR from data
python -m eval.run_answers --save-baseline      # answer quality, judged by Gemini (~$0.30)
python -m eval.run_answers --grade 20           # hand-grade; agreement with the judge
pytest                                          # no API key needed; DB tests need Docker
```

## Layout

| Path | What |
|---|---|
| `kirana_ai/llm/` | LLM adapter: Gemini, OpenAI, Anthropic behind one interface |
| `kirana_ai/agent.py` | Agent loop and tools (`search_docs`, `list_documents`) |
| `kirana_ai/{loader,chunker,embeddings,sparse,vector_store,filters}.py` | Ingestion and retrieval |
| `kirana_ai/chat.py` | One chat turn: thread, history, agent, saved messages |
| `kirana_ai/usage.py` | One `ai.llm_calls` row per LLM call, with cost from `pricing.yaml` |
| `kirana_ai/api.py`, `schemas.py` | FastAPI routes, Pydantic request/response models, ProblemDetail errors |
| `kirana_ai/kb.py` | Knowledge-base documents: signed upload policies, list, delete |
| `kirana_ai/worker.py` | Ingest worker: Kafka consumer, parse → chunk → embed → Qdrant, retries, dead letters |
| `kirana_ai/reconcile.py` | `seed-kb`, `redrive`, `reindex` |
| `kirana_ai/kafka.py`, `storage.py` | Topics; MinIO bucket and its event rule |
| `kirana_ai/cli.py` | `ask`, `chat`, `threads`, `kafka-setup`, `events`, `seed-kb`, `redrive`, `reindex` |
| `kirana_ai/db/` | SQLAlchemy models and sessions (schema `ai`) |
| `migrations/` | Alembic migrations (owns schema `ai`) |
| `kb/seed/` | Seed store policies (uploaded to MinIO by `seed-kb`) |
| `eval/` | Retrieval eval with golden sets |
| `tests/` | Tests: offline, plus Postgres via Testcontainers |

The engine started as a copy of [rag-engine-from-scratch](https://github.com/ravikant06/rag-engine-from-scratch).
