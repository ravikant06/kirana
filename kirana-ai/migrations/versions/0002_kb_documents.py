"""Knowledge-base documents: one row per uploaded file, tracking it from upload link to indexed.

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE ai.documents (
            id                 UUID          NOT NULL,
            title              VARCHAR(200)  NOT NULL,
            doc_type           VARCHAR(20)   NOT NULL,
            file_name          VARCHAR(255)  NOT NULL,
            object_key         VARCHAR(400)  NOT NULL,   -- {id}/{file_name} in bucket kb-docs
            content_type       VARCHAR(100)  NOT NULL,
            size_bytes         BIGINT,                   -- declared at upload-link time; the event's size from M3
            etag               VARCHAR(100),             -- from the upload event (M3)
            content_hash       VARCHAR(64),              -- of the extracted text: unchanged text is not re-embedded
            status             VARCHAR(20)   NOT NULL,
            page_count         INTEGER,
            chunk_count        INTEGER,
            error              TEXT,                     -- why it FAILED, in words an admin can act on
            uploaded_by        VARCHAR(100)  NOT NULL,
            upload_expires_at  TIMESTAMPTZ   NOT NULL,   -- after this, a pending upload can no longer happen
            created_at         TIMESTAMPTZ   NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ   NOT NULL DEFAULT now(),
            CONSTRAINT pk_documents PRIMARY KEY (id),
            CONSTRAINT uq_documents_object_key UNIQUE (object_key),
            CONSTRAINT ck_documents_doc_type CHECK (doc_type IN ('policy', 'faq', 'guide')),
            CONSTRAINT ck_documents_status CHECK (status IN
                ('pending', 'uploaded', 'indexing', 'ready', 'failed', 'deleting', 'deleted'))
        );
        CREATE INDEX ix_documents_updated_at ON ai.documents (updated_at);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE ai.documents;")
