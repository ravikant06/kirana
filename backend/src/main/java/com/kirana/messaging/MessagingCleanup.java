package com.kirana.messaging;

import java.sql.Timestamp;
import java.time.Duration;
import java.time.Instant;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * Stage 6f: the outbox and the inbox only grow. Every hour, delete what is no longer needed:
 *   - outbox rows published more than `retention` ago (never unpublished ones)
 *   - processed_events rows older than `retention`
 *
 * Why 7 days for the inbox: it must remember an event for as long as Kafka might deliver it
 * again. Kafka keeps records 7 days by default, so a consumer group rewound to the start can
 * replay at most 7 days. Older duplicates can't arrive.
 *
 * Deletes go in batches of 5,000, each its own short statement, so a big backlog never holds one
 * long transaction or locks the relay out.
 */
@Component
@ConditionalOnProperty(name = "kirana.messaging.cleanup-enabled", havingValue = "true", matchIfMissing = true)
public class MessagingCleanup {

    private static final Logger log = LoggerFactory.getLogger(MessagingCleanup.class);
    private static final int BATCH = 5_000;

    private final JdbcTemplate jdbc;
    private final Duration retention;

    public MessagingCleanup(JdbcTemplate jdbc, @Value("${kirana.messaging.retention:7d}") Duration retention) {
        this.jdbc = jdbc;
        this.retention = retention;
    }

    @Scheduled(fixedDelay = 3_600_000, initialDelay = 60_000)
    public void run() {
        Timestamp before = Timestamp.from(Instant.now().minus(retention));
        int outbox = deleteInBatches("""
                DELETE FROM outbox WHERE id IN (
                    SELECT id FROM outbox WHERE published_at IS NOT NULL AND published_at < ? LIMIT ?)
                """, before);
        int inbox = deleteInBatches("""
                DELETE FROM processed_events WHERE (consumer, event_id) IN (
                    SELECT consumer, event_id FROM processed_events WHERE processed_at < ? LIMIT ?)
                """, before);
        if (outbox + inbox > 0) {
            log.info("Messaging cleanup: deleted {} published outbox row(s) and {} processed_events row(s) older than {}",
                    outbox, inbox, retention);
        }
    }

    int deleteInBatches(String sql, Timestamp before) {
        int total = 0;
        int n;
        do {
            n = jdbc.update(sql, before, BATCH);
            total += n;
        } while (n == BATCH);
        return total;
    }
}
