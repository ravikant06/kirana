package com.kirana.ratelimit;

import java.time.Duration;
import java.util.List;

import org.springframework.context.annotation.Primary;
import com.kirana.resilience.Resilience;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Component;

/**
 * Token bucket, the one used for traffic (D43). The bucket holds up to {@code limit} tokens and
 * refills at limit/window. Each request takes one. Allows a short burst (a double click, a
 * few quick adds) but caps the sustained rate. Two numbers per key, whatever the limit.
 */
@Primary
@Component
public class TokenBucketRateLimiter implements RateLimiter {

    // KEYS[1] = hash {tokens, ts}; ARGV = capacity, refillPerMs, now
    private static final DefaultRedisScript<List<Long>> SCRIPT = RedisScripts.script("""
            local cap = tonumber(ARGV[1]); local rate = tonumber(ARGV[2]); local now = tonumber(ARGV[3])
            local b = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
            local tokens = tonumber(b[1]) or cap
            local ts = tonumber(b[2]) or now
            tokens = math.min(cap, tokens + math.max(0, now - ts) * rate)
            local allowed = 0
            local retry = 0
            if tokens >= 1 then
              tokens = tokens - 1
              allowed = 1
            else
              retry = math.ceil((1 - tokens) / rate)
            end
            redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'ts', tostring(now))
            redis.call('PEXPIRE', KEYS[1], math.ceil(cap / rate))
            return {allowed, math.floor(tokens), retry}
            """);

    private final StringRedisTemplate redis;
    private final Resilience resilience;

    public TokenBucketRateLimiter(StringRedisTemplate redis, Resilience resilience) {
        this.redis = redis;
        this.resilience = resilience;
    }

    @Override
    public Decision tryAcquire(String key, int limit, Duration window, long nowMillis) {
        double refillPerMs = (double) limit / window.toMillis();
        return RedisScripts.run(redis, resilience, SCRIPT, key + ":tb", limit, refillPerMs, nowMillis);
    }
}
