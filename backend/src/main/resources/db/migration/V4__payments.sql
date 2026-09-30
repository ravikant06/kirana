-- V4: Stage 5 payments. An order is now a small state machine:
--   CREATED (stock held, awaiting payment) -> PAID
--                                          -> CANCELLED (shopper cancelled)  + stock released
--                                          -> FAILED    (payment window expired) + stock released
-- Every move out of CREATED is an atomic "UPDATE ... WHERE status = 'CREATED'", so the verify
-- call, the reconciler and the expiry job can race without settling an order twice.

ALTER TABLE orders
    ADD COLUMN payment_provider  VARCHAR(20),              -- 'mock' | 'razorpay'
    ADD COLUMN gateway_order_id  VARCHAR(100) UNIQUE,      -- the gateway's id for this order
    ADD COLUMN payment_id        VARCHAR(100),             -- the gateway's id for the successful payment
    ADD COLUMN payment_due_at    TIMESTAMPTZ,              -- after this, the expiry job releases the stock
    ADD COLUMN paid_at           TIMESTAMPTZ,
    ADD COLUMN closed_reason     VARCHAR(200);             -- why an order ended CANCELLED or FAILED

-- Orders placed before Stage 5 are left as they are: CREATED with no due date, never expired.

-- The reconciler and expiry job only ever look at unpaid orders: a small partial index.
CREATE INDEX idx_orders_awaiting_payment ON orders (payment_due_at) WHERE status = 'CREATED';
