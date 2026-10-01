-- V6: Stage 6c, late-payment detection.

-- A payment that reached the gateway after the order was CANCELLED or FAILED. Set once, by a
-- conditional UPDATE (... WHERE late_payment_id IS NULL), so however many paths notice the same
-- late payment (webhook, re-check job, browser), the refund event is raised exactly once.
ALTER TABLE orders ADD COLUMN late_payment_id VARCHAR(100);

-- Consumer-side de-duplication (the "inbox"). Kafka delivers at least once, so a consumer can see
-- the same event twice. It records each event id in the same transaction as the work it does;
-- a second delivery finds the row and is skipped. One row per (consumer, event).
CREATE TABLE processed_events (
    consumer      VARCHAR(100) NOT NULL,
    event_id      UUID         NOT NULL,
    processed_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (consumer, event_id)
);
