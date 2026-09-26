# Design decisions

One entry per decision: what we chose, why, and what it costs.
New decisions go at the bottom. A reversed decision stays here, marked as superseded.

## Stage 1

**D1. Inventory is its own table, keyed by product ID.**
Stock changes on every order; product details change rarely. Separating them keeps
stock locks from blocking product edits (Stage 3) and lets product data be cached
without stock invalidating it constantly (Stage 4).
Inventory is created in the same transaction as its product.

**D2. Order lines snapshot product name and price.**
An order records what was true at purchase time. Renames and price changes must not
rewrite history.

**D3. Money is `double` / `DOUBLE PRECISION`.**
Chosen for simplicity during learning. Known cost: binary floating point cannot
represent most decimal amounts exactly, so totals can drift. Revisit when it bites.

**D4. Cart is stored in the database: `carts` header plus `cart_items`.**
Works across devices. The header holds cart-level data (future coupons, discounts)
without repeating it per line. One cart per user for life; checkout deletes the items.
Cart lines hold no price: the cart shows the current price.

**D5. Orders use header–detail: `orders` plus `order_items`.**
Cart lines and order lines look alike but have opposite lifecycles (mutable and
short-lived vs immutable and kept for years). Order lines are copied at checkout,
never referenced from the cart. A table (not jsonb) keeps per-product queries,
foreign keys, and indexes simple.

**D6. IDs come from Postgres sequences.**
One database hands out every ID, so many app instances cannot collide. Globally
unique IDs across databases become a question only with sharding (Stage 12).
Sequences increment by 50 to match Hibernate's pooled allocation.

**D7. Browsers upload images directly to MinIO using a presigned POST policy.**
Image bytes never pass through the backend. Because the backend never sees the
bytes, the storage layer enforces the rules: the signed policy fixes the object key,
requires an `image/*` content type, caps the size, and expires. Three steps:
request policy, upload, confirm. Images stay `PENDING` until confirmed.
Known gap: abandoned `PENDING` rows need a cleanup job (see Stage 8).

**D8. Products are soft-deleted (`deleted_at`).**
Past orders keep a valid product reference. Deleted products return 404 and are
left out of listings.

**D9. `GET /products/{id}/inventory` returns 404 for a missing or deleted product.**
"Does not exist" and "exists with zero stock" are different answers.

**D10. Packages are organized by layer** (`controller`, `service`, `repository`, ...).
Simple and familiar. Expected cost: nothing stops cross-domain coupling, which we
will measure when splitting services in Stage 9.

**D11. One repository for frontend, backend, and infrastructure.**

**D12. Flyway owns the schema; Hibernate only validates it.**
Schema changes are explicit, versioned SQL. `ddl-auto=validate` makes the app refuse
to start if entities and tables disagree.

**D13. Local object storage is the `pgsty/minio` community fork.**
Upstream MinIO stopped publishing images and was archived in 2026. The backend talks
the S3 API, so switching servers later is a configuration change.

**D14. Price is accepted as text and validated as a decimal, then stored as `double`.** (Ravi)
Rules: a plain number, greater than 0, at most 2 decimal places, at most 10,000,000.
Parsed with `BigDecimal` so "12.345" is rejected exactly; a JSON number is accepted too.
Cost: a custom validator instead of a one-line annotation.

**D15. Email uniqueness is enforced only by the database index.** (Ravi)
The service inserts and translates a violation of `uq_users_email_lower` into 409.
No "does it exist?" query first: that check-then-act is racy. Cost: the code has to
recognise the constraint by name.

**D16. Soft-deleted products are filtered by explicit repository methods.** (Ravi)
`findByIdAndDeletedAtIsNull`, not a global `@SQLRestriction`. Visible at each call site;
old orders and carts can still load a deleted product. Cost: a new query can forget it.

**D17. The backend does not enforce the upload size; the signed policy does.**
`sizeBytes` in the upload request is the client's claim and is only checked to be positive.
Rejecting on it would stop honest clients and never a dishonest one. Confirm records the
real size from `statObject`, and confirming an active image again just returns it.

**D18. Deleting an image removes the row first, then the object.**
If the object delete fails, the result is an invisible orphan file (costs storage), never a
row pointing at a missing file (a broken image). Orphans join the Stage 8 cleanup job.

**D19. Cart rules.**
Stock is not checked when adding (it can change before checkout). Lines of deleted products
are hidden, skipped at checkout and cleared by it, so the total shown is the total charged.
At most 99 units per line. `GET /cart` never creates a cart. Removing a line is idempotent.

**D20. Another user's order is 404, not 403.** Do not confirm that the ID exists.

**D21. Product listing: newest first, size clamped to 1–100, three queries per page.**
Products, then stock and thumbnails for the whole page in one query each (no N+1).
The deliberate N+1 is kept for `GET /orders` (experiment 5).

## Parked

- Inventory reservation (on hand vs reserved) for async payment and flash sales, Stages 6–7.
- Category hierarchy, only if a feature needs it.
- Stale `PENDING` image cleanup job, Stage 8.
