package com.kirana.ratelimit;

import java.time.Duration;
import java.util.List;

import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Component;

/**
 * Fixed window: one counter per clock-aligned window (e.g. 10:00:00–10:00:59).
 * Cheapest (one INCR). Flaw: a burst at the end of one window plus the start of the next lets
 * through 2 x limit within moments. Shown in RateLimiterIntegrationTest; not used for traffic.
 */
@Component
public class FixedWindowRateLimiter implements RateLimiter {

    // KEYS[1] = counter for this window; ARGV = limit, windowMs, msUntilWindowEnds
    private static final DefaultRedisScript<List<Long>> SCRIPT = RedisScripts.script("""
            local n = redis.call('INCR', KEYS[1])
            if n == 1 then redis.call('PEXPIRE', KEYS[1], ARGV[2]) end
            local limit = tonumber(ARGV[1])
            if n > limit then return {0, 0, tonumber(ARGV[3])} end
            return {1, limit - n, 0}
            """);

    private final StringRedisTemplate redis;

    public FixedWindowRateLimiter(StringRedisTemplate redis) {
        this.redis = redis;
    }

    @Override
    public Decision tryAcquire(String key, int limit, Duration window, long nowMillis) {
        long w = window.toMillis();
        long windowIndex = nowMillis / w;
        long untilEnd = (windowIndex + 1) * w - nowMillis;
        return RedisScripts.run(redis, SCRIPT, key + ":fw:" + windowIndex, limit, w, untilEnd);
    }
}
