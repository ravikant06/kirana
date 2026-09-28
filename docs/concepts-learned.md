# Engineering concepts learned

1. **Snapshotting values on order lines.** An order records what was true at
   purchase time (price, name), so it must not reference data that can change later.
2. **Header–detail modeling.** Facts about a whole record go in one table; per-line
   facts go in another. Tables with the same columns can mean different things, so
   model by lifecycle and meaning, not by shape.
3. **Client-side validation is for user experience, not enforcement.** When the
   backend never sees the bytes, the storage layer has to enforce the rules.
4. **The transaction is the unit of rollback.** Inside one `@Transactional` method, an
   unchecked exception undoes every write made so far. Without it, each repository call
   runs and commits in its own small transaction, so a failure halfway leaves the earlier
   writes in place. Entities loaded outside a transaction are detached: changing them
   (like `cart.clear()`) silently writes nothing.
5. **Rollback rules.** `@Transactional` rolls back by default only on unchecked exceptions
   (`RuntimeException`, `Error`). A checked exception commits the partial work unless
   `rollbackFor` says otherwise.
6. **Proxies and self-invocation.** `@Transactional` works through a Spring proxy. A call
   through `this.` skips the proxy, so no transaction starts.
7. **Lazy loading and open-in-view.** Lazy collections need an open session. With
   open-in-view off, map to DTOs inside the transaction, or fetch what you need up front.
8. **N+1 queries.** One query for N parents plus one per parent for children. Fix with
   `JOIN FETCH` or an entity graph; for paged results, use batch fetching or two queries.
9. **Isolation levels in Postgres.** MVCC: READ COMMITTED takes a fresh snapshot per
   statement, REPEATABLE READ one per transaction. Postgres never allows dirty reads,
   and its REPEATABLE READ also prevents phantoms. Lost updates are silent at READ
   COMMITTED and become serialization errors (retry needed) at REPEATABLE READ.
10. **The persistence context is an identity map.** One Java object per row per
   transaction, keyed by entity type and id. `find` answers from it without SQL; entity
   queries send SQL but keep the existing object; scalar queries and `refresh` see new data.
11. **Composite and partial indexes.** Column order matters: equality column first, then
   the sort columns. A partial index (`WHERE deleted_at IS NULL`) holds only the rows the
   query can return. An index does not help a query that must read most of the table.
12. **Keep slow I/O out of transactions.** A transaction holds a pooled connection for its
   whole duration; a slow network call inside it can exhaust the pool and fail unrelated
   requests. Short transactions plus a fail-fast pool timeout contain the damage.
13. **Atomic conditional update.** Put the rule in the WHERE and compute from the current
   value (`quantity = quantity - :q WHERE quantity >= :q`). A second writer waits for the
   row lock, then Postgres re-checks the WHERE on the newest row; 0 rows = rule failed.
   Correct across instances, no retries; a hot row still serialises writers.
14. **Optimistic vs pessimistic.** Optimistic (`@Version`) detects conflicts at write time
   and needs a retry or a 409; it is the only tool when the conflict spans requests (a
   stale form), and collapses under contention (~N²/2 attempts). Pessimistic
   (`FOR UPDATE`) makes others wait, holding connections; use it for short read-then-decide
   work, with timeouts. JVM locks never protect shared database state.
15. **Lock ordering and lock duration.** Lock rows in one global order (product id) to avoid
   deadlocks, and take locks as late as possible: a row lock lasts until commit.
16. **Cache-aside and invalidation.** Read cache, else load and store. Evict after commit,
   not before, or a concurrent reader re-caches the old row. TTL is the safety net. Cache
   what changes rarely; read fast-changing values (stock) fresh; never cache what expires
   sooner than the entry (signed URLs).
17. **Stampede and penetration.** Stampede: a hot key expires and every request rebuilds it
   at once; fix with single-flight locks and TTL jitter. Penetration: requests for keys
   that never exist always miss; fix with negative caching or a Bloom filter.
18. **Rate-limit algorithms.** Fixed window is cheap but allows 2x bursts at the boundary;
   sliding window log is exact but stores every request; token bucket allows a bounded
   burst then a steady rate with two numbers per key. Do it atomically (Lua) in Redis.
19. **Gate in front of the database.** Decide winners in Redis (atomic Lua), let the
   database confirm them with its own guard, compensate when the database refuses, and
   make the gate only ever stricter than the source of truth. Fail open when it is down.

