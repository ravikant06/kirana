"""Phase 7: the product cards an answer shows (the products it names), stored with the message.

Revision ID: 0007
Revises: 0006
"""
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ai.messages ADD COLUMN product_ids JSONB NOT NULL DEFAULT '[]';")


def downgrade() -> None:
    op.execute("ALTER TABLE ai.messages DROP COLUMN product_ids;")
