-- V5: Stage 6 transactional outbox.
-- An event is written here in the SAME transaction as the change it describes, so the two can
-- never disagree: no "order PAID but event lost", no "event sent for a rolled-back change".
-- A relay (OutboxRelay) reads unpublished rows in id order and publishes them to Kafka.

CREATE SEQUENCE outbox_seq INCREMENT BY 50;

CREATE TABLE outbox (
    id            BIGINT       PRIMARY KEY,
    event_id      UUID         NOT NULL UNIQUE,  -- consumers ignore an event_id they have seen
    topic         VARCHAR(100) NOT NULL,
    message_key   VARCHAR(200) NOT NULL,         -- Kafka key: same key -> same partition -> in order
    event_type    VARCHAR(100) NOT NULL,
    payload       TEXT         NOT NULL,         -- JSON
    created_at    TIMESTAMPTZ  NOT NULL,
    published_at  TIMESTAMPTZ,                   -- NULL = still to send
    attempts      INT          NOT NULL DEFAULT 0,
    last_error    VARCHAR(500)
);

-- The relay only ever looks at unpublished rows, oldest first.
CREATE INDEX idx_outbox_unpublished ON outbox (id) WHERE published_at IS NULL;
