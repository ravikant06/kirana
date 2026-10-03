package com.kirana.messaging;

import static org.assertj.core.api.Assertions.assertThat;

import java.nio.charset.StandardCharsets;
import java.sql.Timestamp;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.function.BooleanSupplier;

import com.kirana.KafkaContainerConfig;
import com.kirana.PostgresContainerConfig;
import com.kirana.RedisContainerConfig;
import io.minio.MinioClient;
import org.apache.kafka.clients.consumer.ConsumerConfig;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.clients.consumer.KafkaConsumer;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.Import;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.kafka.core.KafkaAdmin;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.support.KafkaHeaders;
import org.springframework.test.context.bean.override.mockito.MockitoBean;

/**
 * Stage 6f gaps: records a consumer can't process land in a dead-letter topic (not dropped), an
 * unknown event type is never acknowledged as done, dead letters can be re-driven, lag is
 * reported, and old outbox/inbox rows are cleaned up.
 */
@SpringBootTest(properties = {"kirana.kafka.enabled=true", "kirana.outbox.relay-enabled=false",
        "kirana.payment.jobs-enabled=false"})
@Import({PostgresContainerConfig.class, RedisContainerConfig.class, KafkaContainerConfig.class})
class DeadLetterIntegrationTest {

    @MockitoBean MinioClient minio;

    @Autowired KafkaTemplate<String, String> kafka;
    @Autowired KafkaAdmin admin;
    @Autowired JdbcTemplate jdbc;
    @Autowired DeadLetters deadLetters;
    @Autowired KafkaStatus status;

    @Test
    void aPoisonMessageGoesToTheDeadLetterTopicWithItsError() throws Exception {
        String key = "poison-" + System.nanoTime();
        kafka.send(Topics.PAYMENTS, key, "this is not JSON").get();

        ConsumerRecord<String, String> dead = awaitDeadLetter(Topics.PAYMENTS_DLT, key);
        assertThat(dead.value()).isEqualTo("this is not JSON");
        assertThat(header(dead, KafkaHeaders.DLT_ORIGINAL_TOPIC)).isEqualTo(Topics.PAYMENTS);
        assertThat(header(dead, KafkaHeaders.DLT_EXCEPTION_FQCN)).contains("ListenerExecutionFailedException");
        assertThat(header(dead, KafkaHeaders.DLT_ORIGINAL_CONSUMER_GROUP)).isEqualTo(PaymentEventsListener.GROUP);
    }

    @Test
    void anUnknownEventTypeIsDeadLetteredNotAcknowledgedAsDone() throws Exception {
        UUID eventId = UUID.randomUUID();
        String key = "unknown-" + System.nanoTime();
        kafka.send(Topics.PAYMENTS, key, envelope(eventId, "PaymentDisputed")).get();

        ConsumerRecord<String, String> dead = awaitDeadLetter(Topics.PAYMENTS_DLT, key);
        assertThat(header(dead, KafkaHeaders.DLT_EXCEPTION_MESSAGE)).contains("Unknown event type 'PaymentDisputed'");
        // The consumer's transaction rolled back: the inbox doesn't claim it was processed.
        assertThat(processed(eventId)).isZero();
    }

    @Test
    void reDrivingSendsDeadLettersBackToTheirTopicOnce() throws Exception {
        // A dead letter whose cause has been "fixed": a valid event the consumer knows.
        UUID eventId = UUID.randomUUID();
        ProducerRecord<String, String> dead = new ProducerRecord<>(Topics.PAYMENTS_DLT, "redrive-" + eventId,
                envelope(eventId, "PaymentFailed"));
        dead.headers().add(KafkaHeaders.DLT_ORIGINAL_TOPIC, Topics.PAYMENTS.getBytes(StandardCharsets.UTF_8));
        kafka.send(dead).get();

        assertThat(deadLetters.redrive(Topics.PAYMENTS_DLT)).isGreaterThanOrEqualTo(1);
        await(() -> processed(eventId) == 1); // PaymentEventsListener processed it this time

        // Re-driving again doesn't send it a second time: the re-drive group's offset has moved past it.
        // (Other tests' dead letters may be re-driven here and fail again: those are new dead letters.)
        deadLetters.redrive(Topics.PAYMENTS_DLT);
        assertThat(count(Topics.PAYMENTS, "redrive-" + eventId)).isEqualTo(1);
    }

    @Test
    void theLabSeesEveryConsumerGroupAndItsLag() {
        KafkaStatus.Snapshot s = status.snapshot();
        assertThat(s.reachable()).isTrue();
        assertThat(s.groups()).extracting(KafkaStatus.Group::name)
                .contains(PaymentEventsListener.GROUP, RefundListener.GROUP, FulfilmentListener.GROUP);
        assertThat(s.groups()).allSatisfy(g -> assertThat(g.lag()).isGreaterThanOrEqualTo(0));
        assertThat(s.deadLetters()).extracting(KafkaStatus.DeadLetterTopic::name)
                .containsExactlyInAnyOrder(Topics.ORDERS_DLT, Topics.PAYMENTS_DLT);
    }

    @Test
    void cleanupDeletesOnlyOldPublishedRowsAndOldInboxRows() {
        Instant old = Instant.now().minus(Duration.ofDays(8));
        long base = 900_000_000L + (System.nanoTime() % 1_000_000) * 3; // ids outside Hibernate's sequence ranges
        insertOutbox(base, old, old);            // published 8 days ago: deleted
        insertOutbox(base + 1, old, null);       // never published: kept, whatever its age
        insertOutbox(base + 2, Instant.now(), Instant.now()); // published just now: kept
        UUID oldEvent = UUID.randomUUID();
        UUID newEvent = UUID.randomUUID();
        jdbc.update("INSERT INTO processed_events (consumer, event_id, processed_at) VALUES ('test', ?, ?)",
                oldEvent, Timestamp.from(old));
        jdbc.update("INSERT INTO processed_events (consumer, event_id) VALUES ('test', ?)", newEvent);

        new MessagingCleanup(jdbc, Duration.ofDays(7)).run();

        assertThat(jdbc.queryForList("SELECT id FROM outbox WHERE id BETWEEN ? AND ? ORDER BY id", Long.class, base, base + 2))
                .containsExactly(base + 1, base + 2);
        assertThat(processed(oldEvent)).isZero();
        assertThat(processed(newEvent)).isEqualTo(1);
    }

    // ---------------------------------------------------------------- helpers

    private static String envelope(UUID eventId, String type) {
        return """
                {"eventId":"%s","type":"%s","occurredAt":"2026-10-03T00:00:00Z","orderId":-1,\
                "data":{"provider":"mock","paymentId":"pay_test"}}""".formatted(eventId, type);
    }

    private long processed(UUID eventId) {
        return jdbc.queryForObject("SELECT count(*) FROM processed_events WHERE event_id = ?", Long.class, eventId);
    }

    private void insertOutbox(long id, Instant created, Instant published) {
        jdbc.update("""
                INSERT INTO outbox (id, event_id, topic, message_key, event_type, payload, created_at, published_at)
                VALUES (?, ?, 'orders.v1', 'k', 'Test', '{}', ?, ?)
                """, id, UUID.randomUUID(), Timestamp.from(created), published == null ? null : Timestamp.from(published));
    }

    private ConsumerRecord<String, String> awaitDeadLetter(String topic, String key) {
        Map<String, Object> config = Map.of(
                ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG, admin.getConfigurationProperties().get("bootstrap.servers"),
                ConsumerConfig.GROUP_ID_CONFIG, "dlt-test-" + System.nanoTime(),
                ConsumerConfig.AUTO_OFFSET_RESET_CONFIG, "earliest",
                ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class,
                ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
        try (KafkaConsumer<String, String> consumer = new KafkaConsumer<>(config)) {
            consumer.subscribe(List.of(topic));
            long deadline = System.currentTimeMillis() + 20_000;
            while (System.currentTimeMillis() < deadline) {
                for (ConsumerRecord<String, String> r : consumer.poll(Duration.ofMillis(500))) {
                    if (key.equals(r.key())) {
                        return r;
                    }
                }
            }
        }
        throw new AssertionError("no dead letter with key " + key + " in " + topic);
    }

    /** How many records with this key the topic holds (reads until 2 s pass with nothing new). */
    private int count(String topic, String key) {
        Map<String, Object> config = Map.of(
                ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG, admin.getConfigurationProperties().get("bootstrap.servers"),
                ConsumerConfig.GROUP_ID_CONFIG, "dlt-count-" + System.nanoTime(),
                ConsumerConfig.AUTO_OFFSET_RESET_CONFIG, "earliest",
                ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class,
                ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
        int n = 0;
        try (KafkaConsumer<String, String> consumer = new KafkaConsumer<>(config)) {
            consumer.subscribe(List.of(topic));
            long quietUntil = System.currentTimeMillis() + 5_000;
            while (System.currentTimeMillis() < quietUntil) {
                var records = consumer.poll(Duration.ofMillis(500));
                if (!records.isEmpty()) {
                    quietUntil = System.currentTimeMillis() + 2_000;
                }
                for (ConsumerRecord<String, String> r : records) {
                    n += key.equals(r.key()) ? 1 : 0;
                }
            }
        }
        return n;
    }

    private static String header(ConsumerRecord<String, String> r, String key) {
        var h = r.headers().lastHeader(key);
        return h == null ? null : new String(h.value(), StandardCharsets.UTF_8);
    }

    private static void await(BooleanSupplier condition) {
        long deadline = System.currentTimeMillis() + 20_000;
        while (!condition.getAsBoolean()) {
            if (System.currentTimeMillis() > deadline) {
                throw new AssertionError("condition not met within 20 s");
            }
            try {
                Thread.sleep(200);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new AssertionError(e);
            }
        }
    }
}
