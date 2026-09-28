/**
 * Rate limiting in Redis (Stage 4). Each algorithm is one Lua script, so the read-decide-write
 * happens atomically inside Redis: two app instances can never both let the last request in
 * (the Stage 3 race, one level up). Time is passed in by the caller, which keeps tests exact;
 * with many instances, clock skew between them is a known cost (Redis TIME would avoid it).
 *
 * The limiter fails open: if Redis is down, requests are allowed.
 */
package com.kirana.ratelimit;
