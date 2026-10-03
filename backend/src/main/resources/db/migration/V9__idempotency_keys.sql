-- V9: Stage 7, idempotency keys at the API edge (D69).
--
-- One row per (shopper, key): the first attempt claims it (IN_PROGRESS), and the finished
-- attempt stores its response (COMPLETED). A repeat with the same key gets that response back.
--
-- recovery_point / resource_id: how far the work got, written IN THE SAME TRANSACTION as that
-- work ("order_created" + the order id, together with the order). If the process dies before
-- the response is stored, the next attempt resumes from there instead of doing the work again.
-- locked_until: an IN_PROGRESS claim whose owner died stops blocking retries after this.

CREATE TABLE idempotency_keys (
    user_id            BIGINT       NOT NULL,
    idempotency_key    VARCHAR(255) NOT NULL,
    endpoint           VARCHAR(200) NOT NULL,   -- "POST /orders/42/cancel"
    request_hash       CHAR(64)     NOT NULL,   -- SHA-256 of endpoint + body: same key, different request -> 422
    status             VARCHAR(20)  NOT NULL CHECK (status IN ('IN_PROGRESS', 'COMPLETED')),
    recovery_point     VARCHAR(50),
    resource_id        BIGINT,
    locked_until       TIMESTAMPTZ  NOT NULL,
    response_status    INT,
    response_body      TEXT,
    response_location  VARCHAR(200),
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
    completed_at       TIMESTAMPTZ,
    PRIMARY KEY (user_id, idempotency_key)
);

-- For the 24-hour cleanup.
CREATE INDEX idx_idempotency_keys_created ON idempotency_keys (created_at);
