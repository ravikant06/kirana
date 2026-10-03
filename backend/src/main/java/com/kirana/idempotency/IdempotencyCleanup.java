package com.kirana.idempotency;

import java.sql.Timestamp;
import java.time.Duration;
import java.time.Instant;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnExpression;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * Keys are kept 24 h (kirana.idempotency.retention): long enough for any client retry, after
 * which the same key is treated as new. Hourly, in batches. (The Redis store expires keys itself.)
 */
@Component
@ConditionalOnExpression("'${kirana.idempotency.store:postgres}' == 'postgres' and ${kirana.idempotency.cleanup-enabled:true}")
public class IdempotencyCleanup {

    private static final Logger log = LoggerFactory.getLogger(IdempotencyCleanup.class);
    private static final int BATCH = 5_000;

    private final JdbcTemplate jdbc;
    private final Duration retention;

    public IdempotencyCleanup(JdbcTemplate jdbc, @Value("${kirana.idempotency.retention:24h}") Duration retention) {
        this.jdbc = jdbc;
        this.retention = retention;
    }

    @Scheduled(fixedDelay = 3_600_000, initialDelay = 90_000)
    public int run() {
        Timestamp before = Timestamp.from(Instant.now().minus(retention));
        int total = 0;
        int n;
        do {
            n = jdbc.update("""
                    DELETE FROM idempotency_keys WHERE (user_id, idempotency_key) IN (
                        SELECT user_id, idempotency_key FROM idempotency_keys WHERE created_at < ? LIMIT ?)
                    """, before, BATCH);
            total += n;
        } while (n == BATCH);
        if (total > 0) {
            log.info("Idempotency cleanup: deleted {} key(s) older than {}", total, retention);
        }
        return total;
    }
}
