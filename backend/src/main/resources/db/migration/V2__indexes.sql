-- V2: Stage 2 indexes. Each one answers a problem measured in the 2a baseline
-- (docs/perf/2a-baseline.json, "Kirana Query Ledger").
--
-- Plain CREATE INDEX blocks writes to the table while it builds (a few seconds here).
-- On a large production table you would use CREATE INDEX CONCURRENTLY, which cannot run
-- inside a transaction, so Flyway would need that migration to run non-transactionally.

-- P1 + P2, order history: WHERE user_id = ? ORDER BY created_at DESC, id DESC.
-- user_id first finds one shopper's rows; created_at DESC, id DESC stores them already in
-- the order the API returns, so the plan needs no sort step.
CREATE INDEX idx_orders_user_created ON orders (user_id, created_at DESC, id DESC);

-- P1, images of a page of products (thumbnails) and of one product (detail page).
CREATE INDEX idx_product_images_product ON product_images (product_id);

-- P3, product listing: WHERE deleted_at IS NULL ORDER BY created_at DESC, id DESC.
-- Partial: only live products are in the index, so it is smaller and the filter is free.
-- Page 1 reads 12 index entries. Deep OFFSET pages still walk every entry before them.
CREATE INDEX idx_products_live_created ON products (created_at DESC, id DESC) WHERE deleted_at IS NULL;

-- Deliberately NOT indexed: order_items.product_id and cart_items.product_id. No query
-- filters on them and products are never hard-deleted, so an index would only slow inserts.
-- order_items.order_id and cart_items.cart_id are already covered: they lead the unique
-- constraints (order_id, product_id) and (cart_id, product_id).
