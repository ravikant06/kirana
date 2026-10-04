#!/usr/bin/env bash
# Wipe the generated test data and keep a small, realistic base (AI track, Phase 4 prep).
#
#   infra/seed/reset-demo-data.sh --yes
#
# Deletes: every order (and its lines, refunds), every cart, idempotency keys, outbox and
# processed-event rows, every product (with stock rows and image rows, and the image files in
# MinIO), every user except 1 (Ravi) and 2 (Puja), AI chat threads of deleted users, and all
# Redis keys (cache, rate limits, flash-sale gates: all rebuildable by design).
# Keeps: users 1 and 2, the schema, Flyway history, AI llm_calls (cost history), KB documents.
#
# Afterwards: restart the backend (it holds pre-allocated id blocks of the old sequences),
# then load the catalog: python3 infra/seed/catalog/seed_catalog.py
#
# Back up first if the data matters:
#   docker exec kirana-postgres-1 pg_dump -U kirana -d kirana -Fc > infra/data/backups/before-reset.dump
set -euo pipefail

if [[ "${1:-}" != "--yes" ]]; then
  echo "This deletes all orders, carts, products and users except 1 and 2. Re-run with --yes." >&2
  exit 1
fi

psql() { docker exec -i kirana-postgres-1 psql -U kirana -d kirana -v ON_ERROR_STOP=1 "$@"; }

echo "== before"
psql -tA -c "SELECT 'users ' || count(*) FROM users UNION ALL SELECT 'products ' || count(*) FROM products
             UNION ALL SELECT 'orders ' || count(*) FROM orders UNION ALL SELECT 'order_items ' || count(*) FROM order_items"

echo "== deleting (one transaction)"
psql <<'SQL'
BEGIN;
-- TRUNCATE, not DELETE: 3 million order lines go in milliseconds, with no per-row work.
-- All tables in one statement, so their foreign keys are satisfied together.
TRUNCATE order_items, refunds, orders, cart_items, carts, idempotency_keys,
         outbox, processed_events, product_images, inventory, products;
DELETE FROM users WHERE id NOT IN (1, 2);
-- AI chats belonged to users by id only (no foreign key across services): clean them up too.
DELETE FROM ai.threads WHERE user_id NOT IN (1, 2);

-- Small, readable ids again. Increment stays 50 (Hibernate's pooled optimizer);
-- users restart above the kept ids 1 and 2.
ALTER SEQUENCE products_seq       RESTART WITH 1;
ALTER SEQUENCE product_images_seq RESTART WITH 1;
ALTER SEQUENCE orders_seq         RESTART WITH 1;
ALTER SEQUENCE order_items_seq    RESTART WITH 1;
ALTER SEQUENCE carts_seq          RESTART WITH 1;
ALTER SEQUENCE cart_items_seq     RESTART WITH 1;
ALTER SEQUENCE refunds_seq        RESTART WITH 1;
ALTER SEQUENCE outbox_seq         RESTART WITH 1;
ALTER SEQUENCE users_seq          RESTART WITH 101;
COMMIT;
SQL
psql -q -c "VACUUM (ANALYZE) users"

echo "== product images in MinIO"
docker exec kirana-minio-1 sh -c 'mc alias set local http://localhost:9000 minioadmin minioadmin >/dev/null \
  && mc rm --recursive --force local/product-images >/dev/null 2>&1; mc du local/product-images'

echo "== Redis (cache, rate limits, flash-sale gates)"
docker exec kirana-redis-1 redis-cli FLUSHDB

echo "== after"
psql -tA -c "SELECT 'users ' || count(*) FROM users UNION ALL SELECT 'products ' || count(*) FROM products
             UNION ALL SELECT 'orders ' || count(*) FROM orders"
echo "Done. Restart the backend, then: python3 infra/seed/catalog/seed_catalog.py"
