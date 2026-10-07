"""Phase 6 M4: actions waiting for the shopper's confirmation.

Revision ID: 0004
Revises: 0003
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE ai.pending_actions (
            id          UUID          NOT NULL,             -- also Kirana's Idempotency-Key for the action
            thread_id   UUID,
            user_id     BIGINT        NOT NULL,
            tool        VARCHAR(60)   NOT NULL,
            arguments   JSONB         NOT NULL,             -- exactly what will run
            summary     VARCHAR(300)  NOT NULL,             -- the card's title, built by the server
            lines       JSONB         NOT NULL DEFAULT '[]',
            seal        VARCHAR(64)   NOT NULL,             -- HMAC over id, user, tool, arguments
            status      VARCHAR(20)   NOT NULL,
            message     TEXT,                               -- what the shopper was told
            result      JSONB,                              -- Kirana's answer
            expires_at  TIMESTAMPTZ   NOT NULL,
            created_at  TIMESTAMPTZ   NOT NULL DEFAULT now(),
            decided_at  TIMESTAMPTZ,
            CONSTRAINT pk_pending_actions PRIMARY KEY (id),
            CONSTRAINT ck_pending_actions_status CHECK (status IN
                ('pending', 'executing', 'done', 'failed', 'rejected', 'expired'))
        );
        CREATE INDEX ix_pending_actions_user_id_created_at ON ai.pending_actions (user_id, created_at);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE ai.pending_actions;")
