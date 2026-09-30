/**
 * Stage 5 resilience: how Kirana behaves when a dependency is slow or down.
 * Every policy (timeouts live in each client's config; retries, circuit breakers and the
 * checkout bulkhead live here) is declared in one place, in code, with the reasoning next to it.
 */
package com.kirana.resilience;
