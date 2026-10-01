package com.kirana.messaging;

import static org.assertj.core.api.Assertions.assertThat;

import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;

import com.kirana.KafkaContainerConfig;
import com.kirana.PostgresContainerConfig;
import com.kirana.RedisContainerConfig;
import io.minio.MinioClient;
import org.apache.kafka.clients.consumer.ConsumerConfig;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.clients.consumer.KafkaConsumer;
import org.apache.kafka.clients.producer.RecordMetadata;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.Import;
import org.springframework.kafka.core.KafkaAdmin;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.test.context.bean.override.mockito.MockitoBean;

/** Stage 6a: the app creates its topic, publishes, and a plain consumer reads the records back. */
@SpringBootTest(properties = {"kirana.kafka.enabled=true", "kirana.payment.jobs-enabled=false"})
@Import({PostgresContainerConfig.class, RedisContainerConfig.class, KafkaContainerConfig.class})
class KafkaSmokeIntegrationTest {

    @MockitoBean MinioClient minio;

    @Autowired KafkaTemplate<String, String> kafka;
    @Autowired KafkaAdmin admin;
    @Autowired KafkaStatus status;

    @Test
    void theAppCreatesItsTopicWithThreePartitions() {
        KafkaStatus.Snapshot s = status.snapshot();
        assertThat(s.reachable()).isTrue();
        assertThat(s.topics()).contains(new KafkaStatus.Topic(Topics.ORDERS, 3));
    }

    @Test
    void recordsWithTheSameKeyGoToTheSamePartitionAndComeBackInOrder() throws Exception {
        String key = "order-" + System.nanoTime();
        List<Integer> partitions = new ArrayList<>();
        for (int i = 1; i <= 3; i++) {
            RecordMetadata meta = kafka.send(Topics.ORDERS, key, "event-" + i).get().getRecordMetadata();
            partitions.add(meta.partition());
        }
        assertThat(Set.copyOf(partitions)).hasSize(1); // same key -> same partition, every time

        Map<String, Object> config = Map.of(
                ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG, admin.getConfigurationProperties().get("bootstrap.servers"),
                ConsumerConfig.GROUP_ID_CONFIG, "smoke-" + System.nanoTime(),
                ConsumerConfig.AUTO_OFFSET_RESET_CONFIG, "earliest",
                ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class,
                ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
        List<String> received = new ArrayList<>();
        try (KafkaConsumer<String, String> consumer = new KafkaConsumer<>(config)) {
            consumer.subscribe(List.of(Topics.ORDERS));
            long deadline = System.currentTimeMillis() + 15_000;
            while (received.size() < 3 && System.currentTimeMillis() < deadline) {
                for (ConsumerRecord<String, String> r : consumer.poll(Duration.ofMillis(500))) {
                    if (key.equals(r.key())) {
                        received.add(r.value());
                    }
                }
            }
        }
        assertThat(received).containsExactly("event-1", "event-2", "event-3"); // per-key order kept
    }
}
