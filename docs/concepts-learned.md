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
