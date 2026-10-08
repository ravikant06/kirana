"""Phase 7 M2-M4: short-term memory on threads (summary, facts) and long-term memory (ai.memories).

Revision ID: 0006
Revises: 0005
"""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE ai.threads ADD COLUMN summary          TEXT;                     -- older turns, compressed
        ALTER TABLE ai.threads ADD COLUMN summary_through  UUID;                     -- last message folded in
        ALTER TABLE ai.threads ADD COLUMN facts            JSONB NOT NULL DEFAULT '{}';  -- ids, written by code

        CREATE TABLE ai.memories (
            id             UUID          NOT NULL,                -- also the Qdrant point's memory_id
            user_id        BIGINT        NOT NULL,                -- every read filters on it
            text           VARCHAR(300)  NOT NULL,                -- "Vegetarian", "Family of 4"
            kind           VARCHAR(20)   NOT NULL,
            pinned         BOOLEAN       NOT NULL DEFAULT false,  -- core profile: always loaded
            status         VARCHAR(20)   NOT NULL,
            source_thread  UUID,                                  -- provenance: where the shopper said it
            approval_id    UUID,                                  -- the click that saved it
            confirmed_at   TIMESTAMPTZ   NOT NULL,
            expires_at     TIMESTAMPTZ,                           -- NULL = until deleted
            created_at     TIMESTAMPTZ   NOT NULL DEFAULT now(),
            updated_at     TIMESTAMPTZ   NOT NULL DEFAULT now(),
            CONSTRAINT pk_memories PRIMARY KEY (id),
            CONSTRAINT ck_memories_kind   CHECK (kind IN ('preference', 'fact')),
            CONSTRAINT ck_memories_status CHECK (status IN ('active', 'deleted'))
        );
        CREATE INDEX ix_memories_user_id_status ON ai.memories (user_id, status);
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE ai.memories;
        ALTER TABLE ai.threads DROP COLUMN facts;
        ALTER TABLE ai.threads DROP COLUMN summary_through;
        ALTER TABLE ai.threads DROP COLUMN summary;
    """)
