package com.kirana.messaging;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import com.kirana.KafkaContainerConfig;
import com.kirana.PostgresContainerConfig;
import com.kirana.RedisContainerConfig;
import com.kirana.dto.ProductRequest;
import com.kirana.dto.UserRequest;
import com.kirana.exception.OutOfStockException;
import com.kirana.service.CartService;
import com.kirana.service.CheckoutSaga;
import com.kirana.service.InventoryService;
import com.kirana.service.OrderService;
import com.kirana.service.ProductService;
import com.kirana.service.UserService;
import io.minio.MinioClient;
import org.apache.kafka.clients.consumer.ConsumerConfig;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.clients.consumer.KafkaConsumer;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.Import;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.kafka.core.KafkaAdmin;
import org.springframework.test.context.bean.override.mockito.MockitoBean;

/**
 * Stage 6b: events are written with the change they describe (same transaction), and the relay
 * delivers them to Kafka in order. The relay's schedule is off here; the test drives it.
 */
@SpringBootTest(properties = {"kirana.kafka.enabled=true", "kirana.outbox.relay-enabled=false",
        "kirana.payment.jobs-enabled=false"})
@Import({PostgresContainerConfig.class, RedisContainerConfig.class, KafkaContainerConfig.class})
class OutboxIntegrationTest {

    @MockitoBean MinioClient minio;

    @Autowired OrderService orders;
    @Autowired CheckoutSaga saga;
    @Autowired UserService users;
    @Autowired ProductService products;
    @Autowired InventoryService inventory;
    @Autowired CartService carts;
    @Autowired OutboxRelay relay;
    @Autowired JdbcTemplate jdbc;
    @Autowired KafkaAdmin admin;

    @Test
    void anEventExistsOnlyIfItsChangeCommitted() {
        long user = users.create(new UserRequest("Outbox", "outbox-" + System.nanoTime() + "@t.com")).id();
        long soldOut = products.create(new ProductRequest("Sold out tea", null, "10")).id(); // stock 0
        carts.add(user, soldOut, 1);
        long before = count();

        assertThatThrownBy(() -> orders.place(user)).isInstanceOf(OutOfStockException.class);

        assertThat(count()).isEqualTo(before); // TX1 rolled back, so its OrderPlaced never existed

        long tea = products.create(new ProductRequest("In stock tea", null, "10")).id();
        inventory.set(tea, 5);
        carts.remove(user, soldOut);
        carts.add(user, tea, 1);
        long orderId = orders.place(user).id();

        assertThat(typesFor(orderId)).containsExactly("OrderPlaced"); // committed together with the order
    }

    @Test
    void theRelayPublishesAnOrdersEventsInOrderWithTheirIds() throws Exception {
        long user = users.create(new UserRequest("Relay", "relay-" + System.nanoTime() + "@t.com")).id();
        long tea = products.create(new ProductRequest("Relay tea", null, "10")).id();
        inventory.set(tea, 5);
        carts.add(user, tea, 1);
        long orderId = orders.place(user).id();
        saga.cancel(user, orderId);
        assertThat(typesFor(orderId)).containsExactly("OrderPlaced", "OrderClosed");

        while (relay.publishBatch() > 0) {
            // drain everything waiting, including other tests' events
        }

        List<Map<String, Object>> rows = jdbc.queryForList(
                "SELECT event_id::text AS id, published_at FROM outbox WHERE message_key = ? ORDER BY outbox.id", Long.toString(orderId));
        assertThat(rows).allSatisfy(r -> assertThat(r.get("published_at")).isNotNull());

        List<String[]> received = consume(Long.toString(orderId), 2);
        List<String> types = received.stream().map(r -> r[0]).toList();
        List<String> ids = received.stream().map(r -> r[1]).toList();
        assertThat(types).containsExactly("OrderPlaced", "OrderClosed");
        assertThat(ids).containsExactly(String.valueOf(rows.get(0).get("id")), String.valueOf(rows.get(1).get("id")));
    }

    private long count() {
        return jdbc.queryForObject("SELECT count(*) FROM outbox", Long.class);
    }

    private List<String> typesFor(long orderId) {
        return jdbc.queryForList("SELECT event_type FROM outbox WHERE message_key = ? ORDER BY id", String.class,
                Long.toString(orderId));
    }

    /** Reads orders.v1 from the beginning; returns {event-type, event-id} for the given key. */
    private List<String[]> consume(String key, int expected) {
        Map<String, Object> config = Map.of(
                ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG, admin.getConfigurationProperties().get("bootstrap.servers"),
                ConsumerConfig.GROUP_ID_CONFIG, "outbox-test-" + System.nanoTime(),
                ConsumerConfig.AUTO_OFFSET_RESET_CONFIG, "earliest",
                ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class,
                ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
        List<String[]> out = new ArrayList<>();
        try (KafkaConsumer<String, String> consumer = new KafkaConsumer<>(config)) {
            consumer.subscribe(List.of(Topics.ORDERS));
            long deadline = System.currentTimeMillis() + 15_000;
            while (out.size() < expected && System.currentTimeMillis() < deadline) {
                for (ConsumerRecord<String, String> r : consumer.poll(Duration.ofMillis(500))) {
                    if (key.equals(r.key())) {
                        out.add(new String[] {
                                new String(r.headers().lastHeader("event-type").value(), StandardCharsets.UTF_8),
                                new String(r.headers().lastHeader("event-id").value(), StandardCharsets.UTF_8)});
                    }
                }
            }
        }
        return out;
    }
}
