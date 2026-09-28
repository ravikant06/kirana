package com.kirana.cache;

import java.time.Duration;
import java.util.List;
import java.util.OptionalLong;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataAccessException;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Component;

/**
 * The flash-sale gate's stock counter in Redis (key flash:stock:{productId}), D44.
 * Redis decides who gets a unit, in memory, so losers are turned away without a database
 * transaction. Postgres keeps the Stage 3 atomic UPDATE as the final guard, so the counter can
 * only ever be more cautious than the database, never oversell.
 *
 * If the key is missing (sale not armed, Redis restarted, key evicted) or Redis is down, the
 * gate steps aside and checkout goes straight to Postgres: slower under load, still correct.
 */
@Component
public class FlashSaleCounter {

    private static final Logger log = LoggerFactory.getLogger(FlashSaleCounter.class);

    /** Long enough for any sale; an armed sale that is never stopped cleans itself up. */
    private static final Duration ARMED_FOR = Duration.ofHours(24);

    // Take :qty units only if that many are left; one atomic step, no overshoot below zero.
    // Returns the new count, -1 when too few are left, -2 when the sale is not armed.
    private static final DefaultRedisScript<Long> TAKE = new DefaultRedisScript<>("""
            local left = redis.call('GET', KEYS[1])
            if not left then return -2 end
            if tonumber(left) < tonumber(ARGV[1]) then return -1 end
            return redis.call('DECRBY', KEYS[1], ARGV[1])
            """, Long.class);

    // Give units back only while the sale is still armed (a stopped sale stays stopped).
    private static final DefaultRedisScript<Long> GIVE_BACK = new DefaultRedisScript<>("""
            if redis.call('EXISTS', KEYS[1]) == 0 then return -2 end
            return redis.call('INCRBY', KEYS[1], ARGV[1])
            """, Long.class);

    public enum Take { TAKEN, SOLD_OUT, NOT_ARMED }

    private final StringRedisTemplate redis;

    public FlashSaleCounter(StringRedisTemplate redis) {
        this.redis = redis;
    }

    public void arm(Long productId, int units) {
        redis.opsForValue().set(CacheKeys.flashStock(productId), Integer.toString(units), ARMED_FOR);
    }

    public void disarm(Long productId) {
        redis.delete(CacheKeys.flashStock(productId));
    }

    /** Units left at the gate, or empty when no sale is armed. */
    public OptionalLong remaining(Long productId) {
        String v = redis.opsForValue().get(CacheKeys.flashStock(productId));
        return v == null ? OptionalLong.empty() : OptionalLong.of(Long.parseLong(v));
    }

    public Take take(Long productId, int qty) {
        try {
            Long r = redis.execute(TAKE, List.of(CacheKeys.flashStock(productId)), Integer.toString(qty));
            if (r == null || r == -2) {
                return Take.NOT_ARMED;
            }
            return r == -1 ? Take.SOLD_OUT : Take.TAKEN;
        } catch (DataAccessException e) {
            log.warn("Flash-sale gate unavailable, checkout goes to Postgres: {}", e.getMessage());
            return Take.NOT_ARMED;
        }
    }

    /** Compensation: the database refused after the gate said yes. */
    public void giveBack(Long productId, int qty) {
        try {
            redis.execute(GIVE_BACK, List.of(CacheKeys.flashStock(productId)), Integer.toString(qty));
        } catch (DataAccessException e) {
            // The counter now shows fewer units than the database has: the gate is too strict,
            // never too loose. Re-arming the sale resyncs it from Postgres.
            log.warn("Could not return {} units of product {} to the flash-sale gate: {}", qty, productId, e.getMessage());
        }
    }
}
