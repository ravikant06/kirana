package com.kirana.ratelimit;

import java.time.Duration;

/** Allow or refuse one request for {@code key}, given at most {@code limit} per {@code window}. */
public interface RateLimiter {

    Decision tryAcquire(String key, int limit, Duration window, long nowMillis);

    /**
     * @param remaining        requests still allowed right now, or -1 if unknown (Redis down)
     * @param retryAfterMillis when refused, how long until a request would be allowed
     */
    record Decision(boolean allowed, long remaining, long retryAfterMillis) {

        static Decision failOpen() {
            return new Decision(true, -1, 0);
        }
    }
}
