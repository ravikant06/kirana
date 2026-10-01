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

## Stage 2

**D22. Per-request SQL metrics in response headers.** `X-Query-Count` and `X-DB-Time-Ms`,
from a JDK-proxy wrapper around the DataSource (`com.kirana.diagnostics`, a new package
for measurement only) and a filter. Shown in the UI Requests panel. Cost: responses are
buffered so headers can follow the body; on/off with `kirana.diagnostics.query-metrics`.

**D23. The Stage 2 dataset lives in the everyday `kirana` database.** (Ravi)
About 1M orders and 3M lines from `infra/seed/seed.sql`, so the UI shows the real effect.
Cost: resetting to a small database means `docker compose down -v`.

**D24. `pg_stat_statements` is preloaded in the compose Postgres.** Per-statement call
counts and total time. Cost: a little overhead on every statement; fine locally.

**D25. V2 indexes, each tied to a measured query.** (Ravi)
`orders (user_id, created_at DESC, id DESC)`, `product_images (product_id)`, and a partial
`products (created_at DESC, id DESC) WHERE deleted_at IS NULL`. Not indexed:
`order_items.product_id`, `cart_items.product_id` (no query uses them; they would only
slow inserts). Cost: plain `CREATE INDEX` blocks writes while it builds; production would
use `CONCURRENTLY` in a non-transactional migration.

**D26. Product listing keeps OFFSET pagination and the exact count.** (Ravi)
Page numbers in the UI need OFFSET. Known cost, measured: the last page still walks every
index entry before it (25 ms), and `count(*)` still reads all live products (8 ms) because
counting 98% of a table is a full scan whichever path Postgres takes.

**D27. `GET /users` is a bounded search: `?q=&limit=` (default 20, max 50), newest first.** (Ravi)
Substring match on name or email, with LIKE wildcards escaped. The shopper dropdown became
a search box. Cost: a leading `%` cannot use a B-tree index, so it scans users (fine at 50k).

**D28. Order history and one order load their lines with `JOIN FETCH`.** (Ravi)
One statement instead of 1 + N. Cost: cannot be paginated; switch to "IDs first, then
lines" if order history is ever paged.

**D29. `ProductService.list()` runs at REPEATABLE READ, read-only.** (Ravi)
Page, count, stock and thumbnails come from one snapshot (fixes P7). Free in Postgres for
read-only transactions.

**D30. Lost updates (P8) stay until Stage 3.** (Ravi)
`InventoryService.adjust`, `OrderService.place` and `CartService.add` read, compute in Java
and write back. Demonstrated by `infra/perf/isolation_demo.py` (100 concurrent +1 → 9).

**D31. No storage call runs inside a database transaction.** (Ravi)
Image request and confirm use `TransactionTemplate`: short DB step, storage call with no
connection held, short DB step. Cost: confirm is no longer one atomic unit; a concurrent
confirm is handled by re-checking the status in the last step.

**D32. Pool: 10 connections, 3 s connection timeout, 503 "Database busy" when exhausted.** (Ravi)
Fails fast instead of a 30 s hang and a 500. Pool size can be overridden with `DB_POOL_SIZE`.

## Stage 3

Rule used for every fix: atomic when the database can decide from one row; optimistic when
the conflict spans requests; pessimistic when code must read, then decide, and traffic is low.

**D33. Stock adjustment is one atomic statement (R1).** (Ravi)
`quantity = quantity + :delta WHERE quantity + :delta >= 0`; 0 rows → 409. Concurrent
adjustments all apply (100 concurrent +1 → 100; was 9). `set` stays last-writer-wins by design.

**D34. Checkout takes stock with an atomic conditional decrement per line (R2).** (Ravi)
`quantity = quantity - :q WHERE quantity >= :q`, lines in product-id order (no deadlocks),
after the order is built (short lock hold). Not optimistic: under contention buyers would
fail while stock remains. Cost: bypasses the persistence context; a hot product still
serialises on its row lock, which flash-sale designs move off the row (later stages).

**D35. Product edits use optimistic locking with a client-held version (R3).** (Ravi)
`products.version` (V3), returned in `ProductDetail`, required in PUT. Stale → 409
"Product changed", never retried automatically: a retry would re-apply the stale form.

**D36. Cart add is an atomic upsert; cart creation is insert-if-absent (R4, R5).** (Ravi)
`INSERT ... ON CONFLICT DO UPDATE SET quantity = quantity + excluded.quantity` (max 99), and
`INSERT INTO carts ... ON CONFLICT (user_id) DO NOTHING`. Cost: native SQL; nextval() runs
even when nothing is inserted, so IDs have gaps.

**D37. Image confirm locks the product row; a unique index guards positions (R6).** (Ravi)
`SELECT ... FOR UPDATE` on the product before choosing the next position, plus
`UNIQUE (product_id, position) WHERE status = 'ACTIVE'`. Pessimistic because the choice
needs a read (max position) and a subquery takes no lock; traffic is tiny.

**D38. Stage 3 Java-lock experiments were explained, not run.** (Ravi)
`synchronized`/`ReentrantLock` fail across instances and release before the proxy commits;
covered in discussion. Concurrency tests use `ExecutorService` + `CountDownLatch`.

## Stage 4

**D39. Redis 8 in compose, host port 6380, no persistence, allkeys-lru at 256 MB.** (Ravi)
Everything in Redis can be rebuilt from Postgres. Port 6380 because a local Redis on 6379
silently captured the app's traffic during the build. `valkey/valkey:8` is a drop-in swap.

**D40. Explicit cache-aside for product details (`product:v1:{id}`, 10 min ± 20%).** (Ravi)
Hand-written with `StringRedisTemplate`, not `@Cacheable`. Cached: product fields and image
object keys. Not cached: stock (one fresh PK read) and signed URLs (they expire in 1 h).
Evicted after commit on create/update/delete and image confirm/delete. Single-flight lock
on a miss (stampede), "not found" cached 60 s (penetration). Reads run outside transactions.

**D41. Product list pages are cached whole for 30 s, TTL-only.** (Ravi)
One new product shifts every page, so precise eviction is impractical. A new or edited
product, and list stock, can be 30 s stale; the product page and checkout are always fresh.

**D42. Redis failures fail open.** (Ravi)
200 ms command timeout; any error means "no answer": serve from Postgres, allow the request,
skip the flash-sale gate. `X-Cache: BYPASS` shows it. The shop stays up without Redis.

**D43. Token-bucket rate limits per shopper on writes.** (Ravi)
`POST /orders` 5 per minute, cart writes 20 per 10 s, as Lua scripts (atomic across
instances). 429 with `Retry-After`. Fixed and sliding window are kept only as comparisons.

**D44. Flash-sale gate: Redis decides winners, Postgres confirms them.** (Ravi)
Admin arms a sale per product (`POST /products/{id}/flash-sale`), copying DB stock to
`flash:stock:{id}`. Checkout takes units with an atomic Lua check-and-decrement before any
transaction; refused buyers cost one cart query. The Stage 3 conditional UPDATE stays the
final guard, and units are given back if the database refuses. The gate can only be stricter
than the database (restocks need a re-arm), never looser.

## Stage 5

**D45. Payments go through a `PaymentGateway` port; one Razorpay-style client serves both
Razorpay test mode and payment-mock.** (Ravi) The mock imitates Razorpay's Orders API, hosts a
checkout page and can be made slow, down, flaky or hanging. Razorpay keys live in
`backend/.env` (git-ignored). The browser pays; the server verifies the HMAC-SHA256
signature in constant time, so a forged "success" is rejected.

**D46. Checkout is a saga; an order is a state machine.** (Ravi) `CREATED` means "awaiting
payment, stock held" for 10 minutes; then `PAID`, `CANCELLED` (shopper) or `FAILED`
(window expired). Every move out of `CREATED` is one conditional UPDATE, so each step is
idempotent and concurrent settlers cannot double-apply. Only the caller that closes an
order releases its stock. A declined payment does not close the order: the shopper may
retry until it expires. (Refines the Stage 5 plan's "declined → FAILED".)

**D47. Unknown is not failed: a reconciler and an expiry job settle unpaid orders.** Every
30 s, orders unpaid for over a minute are checked with the gateway; expired ones are checked
once more, then released. A payment arriving after an order closed is flagged
(`PaymentAfterClose`) for a refund. Gap: with many instances each runs the jobs (Stage 8).

**D48. Order events go through an `OrderEvents` seam, in-process after commit for now.**
Stage 6 swaps the implementation for an outbox relayed to Kafka. Orchestration vs
choreography for the Kafka saga is decided in Stage 6 (hybrid recommended).

**D49. Explicit timeouts on every remote call.** Payment connect 1 s / read 2 s, MinIO
connect 1 s / read 5 s (SDK default: 5 minutes), Redis 200 ms, DB pool 3 s. URL signing
uses a separate MinIO client with a fixed region, so it needs no network. Measured cost: a
gateway slower than 2 s now fails ("pay later") where it used to succeed slowly. Set the
timeout from the dependency's measured p99, not a guess.

**D50. Retries only for idempotent calls, with exponential backoff and jitter, outside the
breaker.** Payment `createOrder`/`fetchStatus` (keyed by our order id): 3 attempts,
~200 ms then ~400 ms, ±50%. MinIO stat/delete: 2 attempts. Redis: none (fail open).

**D51. Circuit breakers per dependency (Resilience4j functional API).** Payment per provider
(slow calls count as failures; 5 of 10 opens it for 15 s), Redis (time-based; an open breaker
looks like "Redis down" so fail-open paths skip the 200 ms wait), MinIO. States are visible in
`/system/status` and the Resilience lab.

**D52. Checkout bulkhead: 20 concurrent checkouts, the 21st gets 503 "Checkout busy".**
Protects browsing from a checkout pile-up. `kirana.resilience.enabled=false` turns D50–D52 off
for measurements.

**D53. Chaos tooling is dev-only.** Toxiproxy in compose; the `chaos` profile routes the
backend's Redis, MinIO and payment traffic through it. The Resilience lab (Manage) drives
payment-mock's mode and Toxiproxy faults and resets breakers.

**D54. Money becomes paise with `BigDecimal` at the gateway boundary.** `double` (D3) stays
inside Kirana for now; `120.10 × 100` in floating point is `12009.99…`, so the conversion goes
through the decimal string with HALF_UP rounding.

**D55. One gateway order per Kirana order, stored and reused; never rely on the gateway to
de-duplicate.** Tested: real Razorpay creates a new order for a repeated receipt (the mock
does not), so "Pay now" re-creating it handed the browser an order we never stored and verify
rejected a real payment. Now `request()` reuses the attached gateway order; if two requests
race, the one attached first wins and the other's is left unused (it expires unpaid at the
gateway). A concurrent duplicate checkout of the same cart (two tabs) is rolled back by
Hibernate's row-count check on the cart-line delete and now answers 409 "Checkout already in
progress". Both are stopgaps: Stage 7 adds a client idempotency key that returns the first
order instead.

## Stage 6

**D56. Kafka: one Apache Kafka 4.1 broker in KRaft mode, plus Kafka UI.** (Ravi) Topics are
created by the backend (`NewTopic` beans), never automatically by the broker. `orders.v1` has 3
partitions, keyed by order id. Producer: `acks=all`, idempotent, 10 s delivery timeout. Spring
for Apache Kafka with explicit settings. `kirana.kafka.enabled` switches Kafka off (most tests).
Cost: one broker means replication factor 1 (production uses 3).

**D57. Order events go through a transactional outbox, relayed to Kafka by a polling publisher.**
(Ravi) `OutboxOrderEvents` inserts the event in the caller's transaction; `OutboxRelay` sends
waiting rows every 0.5 s (`FOR UPDATE SKIP LOCKED`, stop at the first failure to keep per-order
order), keyed by order id, with `event-id`/`event-type` headers. At-least-once: consumers
dedupe by `eventId`. Scheduler pool raised to 3 threads. Costs: the relay waits on Kafka inside a
transaction (scheduler thread only); published rows are not cleaned up yet.

**D58. Gateway webhooks enter through the outbox onto `payments.v1`; a Kafka consumer applies
them.** (6c) The endpoint verifies the HMAC signature over the raw body, finds the order, writes
one outbox row (topic `payments.v1`, key = our order id) and answers 200 only after that
commits. The outbox event id is a name-based UUID of the gateway's event id, so a redelivered
webhook hits the unique constraint and is recorded once. `PaymentEventsListener` (group
`kirana-payments`, manual offset commit after the database commit, 3 retries 1 s apart then
skip) applies them through `CheckoutSaga.applyPayment`. Consumer de-duplication uses an inbox
table, `processed_events (consumer, event_id)`, written in the same transaction as the work.
Costs: one more hop (~0.5 s relay latency); a skipped poison event is only logged (DLQ in 6f);
for these payment events the inbox is belt-and-braces, since the conditional updates are already
idempotent. It's there for consumers whose effects aren't idempotent (6d, 6e).

**D59. A late payment is recorded once on the order (`late_payment_id`), never flips it to
PAID, and raises `PaymentAfterClose` once.** (6c) Conditional `UPDATE … WHERE late_payment_id IS
NULL AND status IN (CANCELLED, FAILED)`. The browser, the webhook consumer and a new re-check job
(closed orders with a gateway order, checked every 2 min for `recheck-closed-for` = 30 min) can
all report it; only the first raises the refund event. The stock released on close is not taken
back. Cost: a second payment for the same closed order is not recorded (rare; the refund
consumer in 6d can look up all payments of the gateway order).

**D60. "Unknown" from the gateway does not close an order until a grace period passes.** (6c,
G3) Expiry on UNKNOWN postpones `payment_due_at` by 5 minutes (which also moves the order to the
back of the expiry queue, so these can't starve other orders) until `created_at + window +
unknown-grace` (1 h), then closes it with an error log; the re-check job keeps watching for 30
min after that. PENDING still closes immediately. Cost: the stock stays held up to an hour
longer when the gateway has lost the order.

## Parked

- Inventory reservation (on hand vs reserved) for async payment and flash sales, Stages 6–7.
- Category hierarchy, only if a feature needs it.
- Stale `PENDING` image cleanup job, Stage 8.
