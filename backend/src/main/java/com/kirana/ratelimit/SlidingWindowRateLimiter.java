package com.kirana.ratelimit;

import java.time.Duration;
import java.util.List;
import java.util.UUID;

import com.kirana.resilience.Resilience;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Component;

/**
 * Sliding window log: a sorted set of request timestamps; count those in the last window.
 * Exact, no boundary burst. Cost: memory grows with the limit (one entry per request), so it
 * suits small limits (logins, OTPs) better than high-volume APIs.
 */
@Component
public class SlidingWindowRateLimiter implements RateLimiter {

    // KEYS[1] = sorted set; ARGV = limit, windowMs, now, uniqueMember
    private static final DefaultRedisScript<List<Long>> SCRIPT = RedisScripts.script("""
            local limit = tonumber(ARGV[1]); local window = tonumber(ARGV[2]); local now = tonumber(ARGV[3])
            redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - window)
            local n = redis.call('ZCARD', KEYS[1])
            if n >= limit then
              local oldest = tonumber(redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')[2])
              return {0, 0, oldest + window - now}
            end
            redis.call('ZADD', KEYS[1], now, ARGV[4])
            redis.call('PEXPIRE', KEYS[1], window)
            return {1, limit - n - 1, 0}
            """);

    private final StringRedisTemplate redis;
    private final Resilience resilience;

    public SlidingWindowRateLimiter(StringRedisTemplate redis, Resilience resilience) {
        this.redis = redis;
        this.resilience = resilience;
    }

    @Override
    public Decision tryAcquire(String key, int limit, Duration window, long nowMillis) {
        return RedisScripts.run(redis, resilience, SCRIPT, key + ":sw", limit, window.toMillis(), nowMillis,
                nowMillis + "-" + UUID.randomUUID());
    }
}
