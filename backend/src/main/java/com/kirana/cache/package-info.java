/**
 * Redis access for caching and the flash-sale gate (Stage 4). The Redis counterpart of the
 * repository and storage layers: no business rules here.
 *
 * Every call fails open: if Redis is down or slow (200 ms timeout), callers get "no answer"
 * and fall back to Postgres, which stays the source of truth. Nothing here holds a database
 * connection, so services call it outside transactions.
 */
package com.kirana.cache;
