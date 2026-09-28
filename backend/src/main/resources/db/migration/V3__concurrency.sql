-- V3: Stage 3 concurrency fixes.

-- R3: optimistic locking for product edits. The client sends back the version it loaded;
-- a save based on an older version is rejected with 409. A constant default makes this an
-- instant, metadata-only change in Postgres 11+, even on a large table.
ALTER TABLE products ADD COLUMN version BIGINT NOT NULL DEFAULT 0;

-- R6: last line of defence for image order. Confirm locks the product row before choosing
-- the next position; this index makes a duplicate impossible even if that code is wrong.
CREATE UNIQUE INDEX uq_product_images_active_position
    ON product_images (product_id, position) WHERE status = 'ACTIVE';
