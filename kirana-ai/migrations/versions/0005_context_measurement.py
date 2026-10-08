"""Phase 7 M1: what each LLM call's prompt is made of, and how much of it came from the cache.

Revision ID: 0005
Revises: 0004
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE ai.llm_calls ADD COLUMN cached_tokens INTEGER;   -- of input_tokens, served from the prompt cache
        ALTER TABLE ai.llm_calls ADD COLUMN context       JSONB;     -- estimated input tokens per prompt block
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE ai.llm_calls DROP COLUMN context;
        ALTER TABLE ai.llm_calls DROP COLUMN cached_tokens;
    """)
