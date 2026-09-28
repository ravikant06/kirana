package com.kirana.cache;

/**
 * What the cache did for the current request, reported as the X-Cache response header:
 * HIT (all lookups answered by Redis), MISS (at least one went to Postgres), BYPASS (Redis
 * unavailable). Per-thread, like QueryMetrics; requests that never touch the cache get none.
 */
public final class CacheStatus {

    public enum Result { HIT, MISS, BYPASS }

    private static final ThreadLocal<Result> CURRENT = new ThreadLocal<>();

    private CacheStatus() {
    }

    static void record(Result r) {
        Result now = CURRENT.get();
        // The worst outcome wins: BYPASS > MISS > HIT.
        if (now == null || r.ordinal() > now.ordinal()) {
            CURRENT.set(r);
        }
    }

    /** Returns the result for the finished request and clears it. */
    public static Result takeAndClear() {
        Result r = CURRENT.get();
        CURRENT.remove();
        return r;
    }
}
