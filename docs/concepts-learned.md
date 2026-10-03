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
20. **Saga and compensation.** A business operation across systems that cannot share one
   transaction becomes steps, each with an undo. Make every step idempotent (conditional
   state transitions), so retries and duplicate events are harmless, and let only the step
   that wins the transition run its side effects.
21. **Unknown is not failed.** A timeout does not tell you whether the other side acted.
   Record the uncertainty, reconcile with the source of truth later, and handle the
   late surprise (paid after cancel → refund). Never trust a client's "success" without a
   signature the server can verify.
22. **Timeouts, retries, backoff and jitter.** Every remote call needs a timeout chosen from
   the dependency's real latency. Retry only idempotent calls, a few times, with exponential
   backoff plus jitter so clients do not retry in lockstep; retries sit outside the breaker.
23. **Circuit breaker.** Closed → open when recent calls fail or are slow → half-open trials →
   closed. Open means failing in microseconds instead of waiting, which saves threads and
   gives the dependency room to recover. Slow counts as failure.
24. **Bulkhead and graceful degradation.** Cap the resources one feature can take so its
   trouble cannot sink the others, and decide per page what still works when each
   dependency is down.
25. **Webhooks (push) vs polling (pull), and using both.** The gateway pushes events even when
   the shopper's tab is closed; senders retry until they get a 2xx, so a webhook is
   at-least-once and must be verified (HMAC over the raw body) and de-duplicated (by the
   sender's event id). Answer 2xx only after the event is durable. Keep polling as the safety
   net for webhooks that never arrive.
26. **Idempotent consumers: three layers.** De-dupe at the door (unique event id), in the
   consumer (an inbox table written in the same transaction as the work, offset committed
   after), and in the state change itself (conditional updates). The last one alone is enough
   when the effect is naturally idempotent; the inbox covers effects that are not.
27. **Consumer groups own partitions.** A crashed consumer still owns its partitions until the
   broker's session timeout (45 s by default) expires; only then does a rebalance hand them to
   someone else. Events are not lost meanwhile, only late.
28. **Calling a non-idempotent API from an at-least-once system.** Record the intent first (a
   row with a unique business key), acknowledge, then act. Before acting, claim a lease so only
   one worker acts at a time, and ask the other side whether it already happened (a timeout
   doesn't mean it failed). Never retry the action blindly; retry "ask, then act".
29. **A new consumer group replays the log.** It starts from the oldest record, so a consumer
   added later still sees every past event, which is how the refund consumer refunded late
   payments from before it existed. Kafka is a log, not a queue.
30. **Mixed versions in one consumer group.** During a rolling deploy, old instances own some
   partitions and acknowledge event types they don't know; those events are lost to the group.
   Deploy consumers before the producers of new event types, and keep a polling safety net.
31. **The dual write, seen live.** Calling another system right after your commit couples your
   latency to theirs (verify took 2 s), loses the call on any failure (nothing retries), and
   can leave the two systems disagreeing (the warehouse shipped, Kirana didn't know). An event
   in the outbox, consumed with retries against an idempotent API, fixes all three, and a new
   consumer group can even replay history to repair what the naive version lost.
32. **Blocking retry vs record-and-retry-later.** If the dependency's failure hits every message
   alike and the call is idempotent, retry the same record and let the partition wait (lag is
   the signal). If failures are per message, or the call isn't idempotent, record the intent,
   move on, and retry from a job. Never retry an error that can't succeed (4xx): that's a
   poison message.
33. **Dead-letter topics.** A record that can't be processed is moved aside with its error, so
   the partition keeps flowing and nothing is lost. Retry only what can succeed (timeouts),
   dead-letter at once what can't (bad JSON, unknown type, 4xx). Re-drive after the fix, and
   remember that a re-driven record is a new delivery to every consumer of that topic.
34. **Lag is the health signal of a consumer.** End offset minus committed offset: growing lag
   means the consumer is slower than the producer, stuck on a record, or down.
35. **Session timeout is the failure-detection trade-off.** Shorter means faster takeover after
   a crash, but more false rebalances on pauses. A graceful shutdown leaves the group at once.
36. **Outboxes and inboxes need retention.** Keep inbox rows as long as a duplicate can still
   arrive (the log's retention), published outbox rows as long as they help debugging.
37. **Idempotency vs an idempotency key.** Idempotency is a property (twice = once). A key is one
   way to get it when the request itself carries no identity: the client names the action, the
   server stores "key → response" and replays it. Internal flows get the same property from ids
   they already have (event id, payment id, order id) and conditional updates.
38. **The IETF Idempotency-Key rules.** Missing key 400; same key + same request finished →
   replay; still running → 409 + Retry-After; same key + different request → 422. Clients make a
   new key per action, reuse it only to retry, and retry only when the outcome is unknown.
39. **Recovery points (atomic phases).** Storing the response after the work leaves a crash
   window. Write "I got this far" in the same transaction as each step's work; a retry whose
   first attempt died resumes from there instead of repeating it.
40. **Exactly-once effect = at-least-once delivery + idempotent effect.** Kafka's idempotent
   producer and transactions only cover Kafka-to-Kafka. When the effect is in a database or
   another service, make the effect idempotent. And keep the idempotency record where the effect
   is (same transaction), which is why the keys stay in Postgres, not Redis.
