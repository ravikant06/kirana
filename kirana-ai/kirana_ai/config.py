"""
Central configuration. Every tunable value lives here and can be overridden
with an environment variable (or a .env file in the kirana-ai folder).
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the kirana-ai folder so commands work from anywhere.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# --- Gemini ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "gemini-embedding-001")
# gemini-embedding-001 supports 768 / 1536 / 3072 dims (Matryoshka). 768 is
# plenty for a small corpus and keeps Qdrant small and fast.
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "768"))
GENERATION_MODEL = os.getenv("GENERATION_MODEL", "gemini-3.5-flash")

# Which LLM adapter to use: gemini | openai | anthropic (see kirana_ai/llm/).
# Swapping this also means setting a matching GENERATION_MODEL and API key.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini")

# Gemini "thinking": minimal | low | medium | high, or unset for the model's default.
# Thinking happens *before* the first visible token, so it sets time-to-first-token,
# and it is billed as output. Streaming hides writing time, never thinking time.
GEMINI_THINKING_LEVEL = os.getenv("GEMINI_THINKING_LEVEL") or None

# --- Qdrant ---
# Host port 6335, not 6333: the standalone rag-project container owns 6333,
# and both can run side by side (same reason Kirana's Redis is on 6380).
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6335")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "kirana_kb")

# --- Postgres (schema `ai`, role `kirana_ai`; see infra/seed/ai-schema.sql) ---
# Same Postgres instance as Kirana, but our own login role, which can reach only
# schema `ai`. Kirana's tables are off limits by permission, not by convention.
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+psycopg://kirana_ai:kirana_ai@localhost:5432/kirana"
)
# Log every SQL statement, like Kirana's SQL logging. The ORM writes the SQL for
# us, so this is how we keep seeing what it actually runs.
SQL_ECHO = os.getenv("SQL_ECHO", "false").lower() in {"1", "true", "yes"}

# --- Kafka (Kirana's broker; the laptop listener is 9094, containers use kafka:9092) ---
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "localhost:9094")
KB_TOPIC = os.getenv("KB_TOPIC", "kb.documents.v1")
KB_DLT = KB_TOPIC + "-dlt"                 # Kirana's convention: <topic>-dlt
KAFKA_PARTITIONS = int(os.getenv("KAFKA_PARTITIONS", "3"))

# --- Kirana's REST API (the AI service reads Kirana data only through it) ---
KIRANA_API_URL = os.getenv("KIRANA_API_URL", "http://localhost:8080")
KIRANA_TIMEOUT_SECONDS = float(os.getenv("KIRANA_TIMEOUT_SECONDS", "5"))
# Phase 5: shoppers sign in with Kirana's RS256 tokens; we verify them with its public keys.
KIRANA_JWKS_URL = os.getenv("KIRANA_JWKS_URL", KIRANA_API_URL + "/.well-known/jwks.json")
TOKEN_ISSUER = os.getenv("TOKEN_ISSUER", "kirana")
TOKEN_AUDIENCE = os.getenv("TOKEN_AUDIENCE", "kirana-ai")
# Phase 6: how the AI service authenticates to Kirana's token exchange (a confidential client).
AI_CLIENT_ID = os.getenv("AI_CLIENT_ID", "kirana-ai")
AI_CLIENT_SECRET = os.getenv("AI_CLIENT_SECRET", "kirana-ai-dev-secret")
# Phase 6 M4: pending actions expire, and are sealed with an HMAC so a changed row is refused.
APPROVAL_TTL_SECONDS = int(os.getenv("APPROVAL_TTL_SECONDS", "300"))
APPROVAL_HMAC_KEY = os.getenv("APPROVAL_HMAC_KEY", "kirana-ai-dev-approval-key")

# --- Product search (Phase 4) ---
PRODUCTS_COLLECTION = os.getenv("PRODUCTS_COLLECTION", "kirana_products")
CATALOG_TOPIC = os.getenv("CATALOG_TOPIC", "catalog.v1")   # produced by Kirana's outbox
CATALOG_DLT = CATALOG_TOPIC + "-dlt"
# Cross-encoder reranker, run locally (fastembed, ONNX). Off by default (AD22): on 150 products it
# gained nothing measurable over hybrid order and disagreed with the model's picks.
# `eval.run_products` still compares both.
RERANK_ENABLED = os.getenv("RERANK_ENABLED", "false").lower() in {"1", "true", "yes"}
RERANK_MODEL = os.getenv("RERANK_MODEL", "Xenova/ms-marco-MiniLM-L-6-v2")
# The categories Kirana's admin form offers (frontend/src/api.js CATEGORIES): the tool's enum.
PRODUCT_CATEGORIES = [
    "Fruits & Vegetables", "Dairy & Eggs", "Bakery", "Staples", "Oils & Ghee", "Spices & Masalas",
    "Snacks", "Beverages", "Breakfast & Cereals", "Frozen", "Personal Care", "Household",
]

# --- MinIO (knowledge-base documents) ---
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
KB_BUCKET = os.getenv("KB_BUCKET", "kb-docs")
# The notification target configured on the MinIO server (infra/docker-compose.yml, id "KB").
KB_EVENTS_ARN = os.getenv("KB_EVENTS_ARN", "arn:minio:sqs::KB:kafka")
# Where the *browser* sends uploads. Signed URLs must use a host the browser can resolve.
MINIO_PUBLIC_URL = os.getenv("MINIO_PUBLIC_URL", "http://localhost:9000")
MINIO_REGION = "us-east-1"   # set explicitly, so signing never makes a network call to ask
KB_MAX_UPLOAD_BYTES = int(os.getenv("KB_MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))
KB_UPLOAD_LINK_MINUTES = int(os.getenv("KB_UPLOAD_LINK_MINUTES", "10"))

# --- Tenancy ---
# Kirana is single-tenant, but every chunk is still stamped and every search
# still scoped. The rule "scope is injected by the server, never chosen by the
# model" is the same one user identity follows from Phase 5.
TENANT_ID = os.getenv("TENANT_ID", "kirana")

# --- Knowledge base ---
# Phase 0-1: a local folder. Phase 2 replaces it with the MinIO bucket.
KB_DIR = PROJECT_ROOT / "kb" / "seed"

# Filename prefix -> doc_type. First match wins; anything unmatched is "guide".
DOC_TYPE_RULES = [
    ("policy", "policy"),
    ("faq", "faq"),
]
DEFAULT_DOC_TYPE = "guide"
DOC_TYPES = ["policy", "faq", "guide"]

# --- Retrieval mode ---
# Hybrid runs dense and BM25 side by side and fuses the two ranked lists.
HYBRID_SEARCH = os.getenv("HYBRID_SEARCH", "true").lower() in {"1", "true", "yes"}

# BM25 weighting. K1 controls how fast repeated terms stop helping; B controls
# how much a chunk's length is penalised. 1.5 / 0.75 are the standard defaults.
BM25_K1 = float(os.getenv("BM25_K1", "1.5"))
BM25_B = float(os.getenv("BM25_B", "0.75"))

# --- RAG knobs ---
# Conversation turns (user + assistant pairs) resent to the model on each turn.
# 0 turns history off, for the "is it really stateless?" experiment.
HISTORY_TURNS = int(os.getenv("HISTORY_TURNS", "10"))
TOP_K = int(os.getenv("TOP_K", "4"))
# Chunks whose dense cosine similarity to the query is below this are dropped before the
# model sees them; if none survive, the model is told nothing relevant was found and must
# abstain. 0 turns it off. Tuned by `python -m eval.run_floor` (Phase 3 M2), not guessed.
# The sweep showed answerable (0.61-0.76) and unanswerable (0.57-0.68) questions overlap:
# in one store's corpus every store question is near *some* chunk. So the floor is only a
# safety net for clear misses (0.60 keeps every answerable question); abstention mostly comes
# from the model, and from a reranker in Phase 4.
RELEVANCE_FLOOR = float(os.getenv("RELEVANCE_FLOOR", "0.60"))                     # how many chunks to retrieve
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "800"))         # characters per chunk
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "150"))   # characters shared between neighbours


def require_api_key() -> str:
    """Fail fast with a clear message instead of a cryptic 401 later."""
    if not GEMINI_API_KEY:
        raise SystemExit(
            "GEMINI_API_KEY is not set. Add it to kirana-ai/.env (see .env.example) "
            "or export it in your shell."
        )
    return GEMINI_API_KEY
