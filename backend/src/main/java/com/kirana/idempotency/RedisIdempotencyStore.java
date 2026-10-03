package com.kirana.idempotency;

import java.time.Duration;
import java.util.List;
import java.util.function.Supplier;

import com.kirana.resilience.Resilience;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.dao.DataAccessException;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

/**
 * Stage 7d, for comparison only (kirana.idempotency.store=redis). One hash per key,
 * "idem:{user}:{key}", expiring after 24 h; claim is one Lua script, so check-and-claim is atomic.
 *
 * What it can't do, and why Postgres stays the default (D69):
 *   - reach(): Redis is not part of the database transaction. The recovery point is written
 *     after the commit; a crash between the commit and this write loses it, and a retry repeats
 *     the work. In Postgres they commit together.
 *   - durability: Kirana's Redis has no persistence and evicts keys under memory pressure
 *     (allkeys-lru, Stage 4). A restart or an eviction forgets keys: a retry then runs again.
 *   - availability: if Redis is down, nothing can be checked; requests are refused (503) rather
 *     than run unprotected. (The Stage 4 cache fails open; a safety check must fail closed.)
 */
@Component
@ConditionalOnProperty(name = "kirana.idempotency.store", havingValue = "redis")
public class RedisIdempotencyStore implements IdempotencyStore {

    private static final Logger log = LoggerFactory.getLogger(RedisIdempotencyStore.class);

    // KEYS[1] the key's hash. ARGV: endpoint, request hash, now (ms), lock until (ms), ttl (ms).
    @SuppressWarnings({"rawtypes", "unchecked"})
    private static final DefaultRedisScript<List> CLAIM = new DefaultRedisScript<>("""
            local h = redis.call('HGETALL', KEYS[1])
            if #h == 0 then
              redis.call('HSET', KEYS[1], 'endpoint', ARGV[1], 'hash', ARGV[2], 'status', 'IN_PROGRESS', 'locked_until', ARGV[4])
              redis.call('PEXPIRE', KEYS[1], ARGV[5])
              return {'CLAIMED', '', ''}
            end
            local f = {}
            for i = 1, #h, 2 do f[h[i]] = h[i + 1] end
            if f['hash'] ~= ARGV[2] then return {'MISMATCH', f['endpoint']} end
            if f['status'] == 'COMPLETED' then
              return {'COMPLETED', f['response_status'], f['response_body'], f['response_location'] or ''}
            end
            if tonumber(f['locked_until']) > tonumber(ARGV[3]) then return {'IN_PROGRESS'} end
            redis.call('HSET', KEYS[1], 'locked_until', ARGV[4])
            return {'CLAIMED', f['recovery_point'] or '', f['resource_id'] or ''}
            """, List.class);

    // Nothing committed (no recovery point): forget the key. Otherwise unlock it for a resume.
    private static final DefaultRedisScript<Long> FAIL = new DefaultRedisScript<>("""
            if redis.call('HGET', KEYS[1], 'status') == 'IN_PROGRESS' and not redis.call('HGET', KEYS[1], 'recovery_point') then
              return redis.call('DEL', KEYS[1])
            end
            redis.call('HSET', KEYS[1], 'locked_until', '0')
            return 0
            """, Long.class);

    private final StringRedisTemplate redis;
    private final Resilience resilience;
    private final Duration retention;

    public RedisIdempotencyStore(StringRedisTemplate redis, Resilience resilience,
                                 @Value("${kirana.idempotency.retention:24h}") Duration retention) {
        this.redis = redis;
        this.resilience = resilience;
        this.retention = retention;
    }

    @Override
    public Claim claim(long userId, String key, String endpoint, String requestHash, Duration lockFor) {
        long now = System.currentTimeMillis();
        @SuppressWarnings("unchecked")
        List<String> r = call(() -> redis.execute(CLAIM, List.of(redisKey(userId, key)), endpoint, requestHash,
                Long.toString(now), Long.toString(now + lockFor.toMillis()), Long.toString(retention.toMillis())));
        return switch (r.get(0)) {
            case "CLAIMED" -> new Claimed(blankToNull(r.get(1)), r.get(2).isEmpty() ? null : Long.valueOf(r.get(2)));
            case "COMPLETED" -> new Completed(Integer.parseInt(r.get(1)), r.get(2), blankToNull(r.get(3)));
            case "IN_PROGRESS" -> new InProgress();
            default -> new Mismatch(r.get(1));
        };
    }

    @Override
    public void reach(long userId, String key, String recoveryPoint, Long resourceId) {
        Runnable write = () -> redis.opsForHash().putAll(redisKey(userId, key), java.util.Map.of(
                "recovery_point", recoveryPoint, "resource_id", resourceId == null ? "" : resourceId.toString()));
        if (!TransactionSynchronizationManager.isSynchronizationActive()) {
            call(() -> {
                write.run();
                return null;
            });
            return;
        }
        // Only after the database commits (writing before could claim work that then rolls back).
        // THE WINDOW: if the process dies between that commit and this write, the key never learns
        // the work happened, and the next attempt does it again.
        TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
            @Override
            public void afterCommit() {
                try {
                    resilience.redisRun(write);
                } catch (DataAccessException e) {
                    log.error("Idempotency-Key {} (user {}): '{}' committed in Postgres but not recorded in Redis ({}). "
                            + "A retry with this key will repeat the work.", key, userId, recoveryPoint, e.getMessage());
                }
            }
        });
    }

    @Override
    public void complete(long userId, String key, int status, String body, String location) {
        call(() -> {
            redis.opsForHash().putAll(redisKey(userId, key), java.util.Map.of("status", "COMPLETED",
                    "response_status", Integer.toString(status), "response_body", body,
                    "response_location", location == null ? "" : location));
            return null;
        });
    }

    @Override
    public void fail(long userId, String key) {
        call(() -> redis.execute(FAIL, List.of(redisKey(userId, key))));
    }

    private <T> T call(Supplier<T> op) {
        try {
            return resilience.redis(op);
        } catch (DataAccessException e) {
            throw new IdempotencyUnavailableException(e); // fail closed: no protection, no request
        }
    }

    static String redisKey(long userId, String key) {
        return "idem:" + userId + ":" + key;
    }

    private static String blankToNull(String s) {
        return s == null || s.isEmpty() ? null : s;
    }
}
