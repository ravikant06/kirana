package com.kirana.messaging;

import java.time.Instant;
import java.util.List;
import java.util.concurrent.TimeUnit;

import org.apache.kafka.clients.producer.ProducerRecord;
import org.apache.kafka.clients.producer.RecordMetadata;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * Publishes outbox rows to Kafka (Stage 6 "polling publisher").
 *
 * Every half second: lock the oldest unpublished rows (FOR UPDATE SKIP LOCKED, so two app
 * instances never take the same rows), send each to Kafka and wait for the broker's
 * acknowledgement, then mark it published. If a send fails, stop there: later events (perhaps
 * of the same order) wait, so per-order order is kept. They are retried on the next tick.
 *
 * Delivery is at-least-once: if the app dies after Kafka acknowledged a send but before the
 * row is marked, that event is sent again after restart. Consumers dedupe by eventId (6c).
 *
 * Trade-off: the rows stay locked while we wait for Kafka (bounded by the producer's 5–10 s
 * timeouts), which breaks Stage 2f's "no network call inside a transaction" rule on purpose:
 * it runs on a scheduler thread with one connection, never on request threads. At larger scale
 * the relay claims rows with a lease and publishes outside the transaction, or is replaced by
 * change data capture (Debezium reading Postgres's write-ahead log).
 */
@Component
@ConditionalOnProperty(name = "kirana.kafka.enabled", havingValue = "true")
public class OutboxRelay {

    private static final Logger log = LoggerFactory.getLogger(OutboxRelay.class);

    private record Row(long id, String eventId, String topic, String key, String type, String payload) {
    }

    private final JdbcTemplate jdbc;
    private final KafkaTemplate<String, String> kafka;
    private final TransactionTemplate tx;
    private final int batchSize;
    private final boolean scheduled;

    public OutboxRelay(JdbcTemplate jdbc, KafkaTemplate<String, String> kafka, TransactionTemplate tx,
                       @Value("${kirana.outbox.batch-size:100}") int batchSize,
                       @Value("${kirana.outbox.relay-enabled:true}") boolean scheduled) {
        this.jdbc = jdbc;
        this.kafka = kafka;
        this.tx = tx;
        this.batchSize = batchSize;
        this.scheduled = scheduled;
    }

    @Scheduled(fixedDelayString = "${kirana.outbox.relay-interval-ms:500}", initialDelay = 2000)
    public void tick() {
        if (scheduled) {
            publishBatch();
        }
    }

    /** Publishes up to one batch; returns how many were sent. Public so tests can drive it. */
    public int publishBatch() {
        Integer sent = tx.execute(status -> {
            List<Row> rows = jdbc.query("""
                    SELECT id, event_id, topic, message_key, event_type, payload
                    FROM outbox
                    WHERE published_at IS NULL
                    ORDER BY id
                    LIMIT ?
                    FOR UPDATE SKIP LOCKED
                    """, (rs, i) -> new Row(rs.getLong("id"), rs.getString("event_id"), rs.getString("topic"),
                    rs.getString("message_key"), rs.getString("event_type"), rs.getString("payload")), batchSize);
            int ok = 0;
            for (Row r : rows) {
                try {
                    ProducerRecord<String, String> record = new ProducerRecord<>(r.topic(), r.key(), r.payload());
                    record.headers().add("event-id", r.eventId().getBytes());
                    record.headers().add("event-type", r.type().getBytes());
                    // Wait for the broker's acknowledgement; it says where the record was stored.
                    RecordMetadata stored = kafka.send(record).get(10, TimeUnit.SECONDS).getRecordMetadata();
                    jdbc.update("UPDATE outbox SET published_at = ?, attempts = attempts + 1, last_error = NULL WHERE id = ?",
                            java.sql.Timestamp.from(Instant.now()), r.id());
                    ok++;
                    log.info("Outbox relay: {} for order {} -> {} partition {} offset {} (event {})", r.type(), r.key(),
                            stored.topic(), stored.partition(), stored.offset(), r.eventId());
                } catch (Exception e) {
                    String reason = e.getClass().getSimpleName() + ": " + String.valueOf(e.getMessage());
                    jdbc.update("UPDATE outbox SET attempts = attempts + 1, last_error = ? WHERE id = ?",
                            reason.length() > 500 ? reason.substring(0, 500) : reason, r.id());
                    log.warn("Outbox relay: {} {} not published yet ({}); {} later rows wait", r.type(), r.eventId(),
                            reason, rows.size() - ok - 1);
                    break;
                }
            }
            return ok;
        });
        if (sent != null && sent > 0) {
            log.info("Outbox relay: published {} event(s)", sent);
        }
        return sent == null ? 0 : sent;
    }
}
