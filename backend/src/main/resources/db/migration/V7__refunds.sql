-- V7: Stage 6d, refunds. One row per refunded payment; the row is the refund's state machine:
--   REQUESTED  we decided to refund; the gateway may or may not have a refund yet
--   PENDING    the gateway has the refund (gateway_refund_id) and is processing it
--   PROCESSED  money returned
--   FAILED     the gateway refused; a person must look (last_error says why)

CREATE SEQUENCE refunds_seq INCREMENT BY 50;

CREATE TABLE refunds (
    id                 BIGINT PRIMARY KEY,
    order_id           BIGINT           NOT NULL REFERENCES orders (id),
    payment_id         VARCHAR(100)     NOT NULL UNIQUE,  -- at most one refund per payment, enforced here
    provider           VARCHAR(20)      NOT NULL,
    amount             DOUBLE PRECISION NOT NULL,
    status             VARCHAR(20)      NOT NULL CHECK (status IN ('REQUESTED', 'PENDING', 'PROCESSED', 'FAILED')),
    gateway_refund_id  VARCHAR(100)     UNIQUE,
    attempts           INT              NOT NULL DEFAULT 0,
    last_error         VARCHAR(500),
    -- Lease: whoever claims the row may call the gateway until then; nobody else may meanwhile.
    claimed_until      TIMESTAMPTZ,
    created_at         TIMESTAMPTZ      NOT NULL,
    updated_at         TIMESTAMPTZ      NOT NULL,
    processed_at       TIMESTAMPTZ
);

CREATE INDEX idx_refunds_order ON refunds (order_id);
CREATE INDEX idx_refunds_open ON refunds (id) WHERE status IN ('REQUESTED', 'PENDING');
