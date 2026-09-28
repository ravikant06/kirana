package com.kirana.cache;

import java.time.Duration;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.ThreadLocalRandom;
import java.util.function.Supplier;

import com.kirana.cache.CacheStatus.Result;
import com.kirana.config.CacheProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataAccessException;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import tools.jackson.core.type.TypeReference;
import tools.jackson.databind.json.JsonMapper;

/**
 * Cache-aside over Redis: read the cache; on a miss load from Postgres and store the result.
 * Written out by hand (not @Cacheable) so every step is visible, and so it can do three things
 * @Cacheable cannot do well:
 *
 *   1. Stampede protection (single flight): on a miss, one request takes a short Redis lock and
 *      rebuilds; the others wait briefly for its result instead of all hitting Postgres.
 *   2. Penetration protection: a loader returning null ("does not exist") is cached too, for a
 *      short negative TTL, so bots asking for missing IDs stop reaching Postgres.
 *   3. TTL jitter (+-20%): keys cached at the same moment do not all expire at the same moment.
 *
 * Fails open: any Redis error means "no answer" and the loader runs, as if there were no cache.
 */
@Component
public class CacheAside {

    private static final Logger log = LoggerFactory.getLogger(CacheAside.class);

    /** Stored for "the loader found nothing". Not valid JSON, so it cannot clash with a value. */
    private static final String NOT_FOUND = "~none";

    /** Delete the lock only if we still own it, in one step (a plain DEL could remove another request's lock). */
    private static final DefaultRedisScript<Long> UNLOCK = new DefaultRedisScript<>("""
            if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
            return 0
            """, Long.class);

    private final StringRedisTemplate redis;
    private final JsonMapper json;
    private final CacheProperties props;
    private volatile long lastWarning;

    public CacheAside(StringRedisTemplate redis, JsonMapper json, CacheProperties props) {
        this.redis = redis;
        this.json = json;
        this.props = props;
    }

    private enum State { HIT, MISS, DOWN }

    private record Read<T>(State state, T value) {
    }

    /**
     * The value for {@code key}: from Redis if present, else from {@code loader} (then stored).
     * The loader may return null for "does not exist"; that answer is cached with the negative TTL.
     */
    public <T> T getOrLoad(String key, Duration ttl, TypeReference<T> type, Supplier<T> loader) {
        Read<T> first = read(key, type);
        if (first.state() == State.HIT) {
            CacheStatus.record(Result.HIT);
            return first.value();
        }
        if (first.state() == State.DOWN) {
            CacheStatus.record(Result.BYPASS);
            return loader.get();
        }
        CacheStatus.record(Result.MISS);

        String token = tryLock(key);
        if (token != null) {
            try {
                T value = loader.get();
                write(key, value, ttl);
                return value;
            } finally {
                unlock(key, token);
            }
        }

        // Another request is rebuilding this key. Wait a little for its result.
        long deadline = System.nanoTime() + props.lockWait().toNanos();
        while (System.nanoTime() < deadline) {
            sleep(20);
            Read<T> again = read(key, type);
            if (again.state() == State.HIT) {
                return again.value();
            }
            if (again.state() == State.DOWN) {
                break;
            }
        }
        // The rebuilder is slow or failed: load it ourselves rather than make the user wait longer.
        return loader.get();
    }

    public void evict(String... keys) {
        try {
            redis.delete(List.of(keys));
        } catch (DataAccessException e) {
            // The TTL is the safety net: the stale entry expires on its own.
            warn("evict", e);
        }
    }

    /**
     * Evict once the current transaction has committed. Evicting before commit lets a concurrent
     * reader load the old row from Postgres and put it straight back into the cache.
     */
    public void evictAfterCommit(String... keys) {
        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
                @Override
                public void afterCommit() {
                    evict(keys);
                }
            });
        } else {
            evict(keys);
        }
    }

    private <T> Read<T> read(String key, TypeReference<T> type) {
        String raw;
        try {
            raw = redis.opsForValue().get(key);
        } catch (DataAccessException e) {
            warn("read", e);
            return new Read<>(State.DOWN, null);
        }
        if (raw == null) {
            return new Read<>(State.MISS, null);
        }
        if (NOT_FOUND.equals(raw)) {
            return new Read<>(State.HIT, null);
        }
        try {
            return new Read<>(State.HIT, json.readValue(raw, type));
        } catch (RuntimeException e) {
            // Shape changed since it was cached (a deploy). Treat as a miss; the rebuild overwrites it.
            log.warn("Unreadable cache entry {}: {}", key, e.getMessage());
            return new Read<>(State.MISS, null);
        }
    }

    private void write(String key, Object value, Duration ttl) {
        try {
            if (value == null) {
                redis.opsForValue().set(key, NOT_FOUND, props.negativeTtl());
            } else {
                redis.opsForValue().set(key, json.writeValueAsString(value), jitter(ttl));
            }
        } catch (DataAccessException e) {
            warn("write", e);
        }
    }

    private String tryLock(String key) {
        String token = UUID.randomUUID().toString();
        try {
            Boolean ok = redis.opsForValue().setIfAbsent("lock:" + key, token, props.lockTtl());
            return Boolean.TRUE.equals(ok) ? token : null;
        } catch (DataAccessException e) {
            warn("lock", e);
            return token; // no Redis, no coordination: just load
        }
    }

    private void unlock(String key, String token) {
        try {
            redis.execute(UNLOCK, List.of("lock:" + key), token);
        } catch (DataAccessException e) {
            warn("unlock", e); // the lock TTL releases it
        }
    }

    private static Duration jitter(Duration ttl) {
        double factor = 0.8 + ThreadLocalRandom.current().nextDouble() * 0.4;
        return Duration.ofMillis((long) (ttl.toMillis() * factor));
    }

    private static void sleep(long millis) {
        try {
            Thread.sleep(millis);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    /** One warning per 10 s, so a Redis outage does not flood the log. */
    private void warn(String op, Exception e) {
        long now = System.currentTimeMillis();
        if (now - lastWarning > 10_000) {
            lastWarning = now;
            log.warn("Redis {} failed, serving from Postgres: {}", op, e.getMessage());
        }
    }
}
