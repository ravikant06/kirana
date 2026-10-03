package com.kirana.messaging;

import java.time.Duration;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;

import com.kirana.exception.ConflictException;
import com.kirana.exception.NotFoundException;
import org.apache.kafka.clients.consumer.ConsumerConfig;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.clients.consumer.KafkaConsumer;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.apache.kafka.common.TopicPartition;
import org.apache.kafka.common.header.Header;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.kafka.core.KafkaAdmin;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.support.KafkaHeaders;
import org.springframework.stereotype.Component;

/**
 * Stage 6f: re-drive. After the cause of the dead letters is fixed (a bug deployed, the warehouse
 * data corrected), copy them back to their original topic so the consumers try again.
 *
 * Progress is kept as the offsets of a consumer group of its own, "kirana-dlt-redrive": a dead
 * letter is re-driven once, and "waiting" in the lab is that group's lag. A re-driven record
 * reaches EVERY consumer group of the original topic, not only the one that failed; that is
 * safe because every Kirana consumer is idempotent (inbox, conditional updates, idempotency keys).
 *
 * One record can have several dead letters: orders.v1 has two consumer groups, and a record both
 * fail on is dead-lettered by each. Re-driving both copies would put it back twice (and each copy
 * reaches both groups again), so a re-drive sends each original record (topic, partition, offset)
 * back only once. The clean alternative, a retry topic per consumer group, is noted for later.
 */
@Component
public class DeadLetters {

    static final String REDRIVE_GROUP = "kirana-dlt-redrive";
    static final List<String> TOPICS = List.of(Topics.ORDERS_DLT, Topics.PAYMENTS_DLT);

    private static final Logger log = LoggerFactory.getLogger(DeadLetters.class);

    private final KafkaAdmin admin;
    private final KafkaTemplate<String, String> kafka;

    private final boolean enabled;

    public DeadLetters(KafkaAdmin admin, KafkaTemplate<String, String> kafka,
                       @Value("${kirana.kafka.enabled:false}") boolean enabled) {
        this.enabled = enabled;
        this.admin = admin;
        this.kafka = kafka;
    }

    /** Re-drives everything waiting in one dead-letter topic; returns how many records. */
    public int redrive(String deadLetterTopic) {
        if (!enabled) {
            throw new ConflictException("Kafka is off", "kirana.kafka.enabled is false");
        }
        if (!TOPICS.contains(deadLetterTopic)) {
            throw new NotFoundException("No dead-letter topic '%s'".formatted(deadLetterTopic));
        }
        Map<String, Object> config = new HashMap<>();
        config.put(ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG, admin.getConfigurationProperties().get("bootstrap.servers"));
        config.put(ConsumerConfig.GROUP_ID_CONFIG, REDRIVE_GROUP);
        config.put(ConsumerConfig.ENABLE_AUTO_COMMIT_CONFIG, false);
        config.put(ConsumerConfig.AUTO_OFFSET_RESET_CONFIG, "earliest");
        config.put(ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
        config.put(ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
        int sent = 0;
        java.util.Set<String> originals = new java.util.HashSet<>();
        try (KafkaConsumer<String, String> consumer = new KafkaConsumer<>(config)) {
            // assign(), not subscribe(): no group rebalance to wait for; offsets are still the group's.
            List<TopicPartition> partitions = consumer.partitionsFor(deadLetterTopic).stream()
                    .map(p -> new TopicPartition(deadLetterTopic, p.partition())).toList();
            consumer.assign(partitions);
            Map<TopicPartition, Long> end = consumer.endOffsets(partitions);
            long deadline = System.currentTimeMillis() + 10_000;
            while (!caughtUp(consumer, end) && System.currentTimeMillis() < deadline) {
                for (ConsumerRecord<String, String> r : consumer.poll(Duration.ofMillis(500))) {
                    String original = header(r, KafkaHeaders.DLT_ORIGINAL_TOPIC);
                    String origin = original + "/" + number(r, KafkaHeaders.DLT_ORIGINAL_PARTITION)
                            + "@" + number(r, KafkaHeaders.DLT_ORIGINAL_OFFSET);
                    if (!originals.add(origin)) {
                        log.info("Dead letter {} p{}@{} skipped: {} was already re-driven (another group's copy)",
                                deadLetterTopic, r.partition(), r.offset(), origin);
                        continue;
                    }
                    ProducerRecord<String, String> copy = new ProducerRecord<>(original, r.key(), r.value());
                    for (Header h : r.headers()) {
                        if (!h.key().startsWith("kafka_dlt-")) {
                            copy.headers().add(h); // keep event-id and event-type
                        }
                    }
                    copy.headers().add("redriven-from", deadLetterTopic.getBytes());
                    kafka.send(copy).get(10, TimeUnit.SECONDS);
                    sent++;
                    log.info("Dead letter {} p{}@{} re-driven to {} (key {}; it failed with: {})", deadLetterTopic,
                            r.partition(), r.offset(), original, r.key(), header(r, KafkaHeaders.DLT_EXCEPTION_MESSAGE));
                }
                consumer.commitSync();
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("re-drive interrupted", e);
        } catch (java.util.concurrent.ExecutionException | java.util.concurrent.TimeoutException e) {
            throw new IllegalStateException("re-drive failed after " + sent + " record(s): " + e.getMessage(), e);
        }
        return sent;
    }

    private static boolean caughtUp(KafkaConsumer<String, String> consumer, Map<TopicPartition, Long> end) {
        return end.entrySet().stream().allMatch(e -> consumer.position(e.getKey()) >= e.getValue());
    }

    /** Spring writes the original partition (int) and offset (long) as big-endian bytes. */
    private static String number(ConsumerRecord<String, String> r, String key) {
        Header h = r.headers().lastHeader(key);
        if (h == null) {
            return "?";
        }
        java.nio.ByteBuffer b = java.nio.ByteBuffer.wrap(h.value());
        return String.valueOf(h.value().length == 4 ? b.getInt() : b.getLong());
    }

    private static String header(ConsumerRecord<String, String> r, String key) {
        Header h = r.headers().lastHeader(key);
        return h == null ? null : new String(h.value());
    }
}
