package com.kirana.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.when;

import java.util.Optional;
import java.util.function.BooleanSupplier;

import com.kirana.KafkaContainerConfig;
import com.kirana.PostgresContainerConfig;
import com.kirana.RedisContainerConfig;
import com.kirana.dto.ProductRequest;
import com.kirana.dto.UserRequest;
import com.kirana.entity.OrderStatus;
import com.kirana.messaging.Inbox;
import com.kirana.messaging.OutboxRelay;
import com.kirana.messaging.Topics;
import com.kirana.payment.PaymentGateways;
import com.kirana.service.CheckoutSagaIntegrationTest.StubGateway;
import com.kirana.service.PaymentWebhooks.Result;
import io.minio.MinioClient;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.Import;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.test.context.bean.override.mockito.MockitoBean;

/**
 * Stage 6c end to end, on real Postgres and Kafka: webhook -> outbox -> relay -> payments.v1 ->
 * PaymentEventsListener -> order. The relay's schedule is off; the test drives it.
 */
@SpringBootTest(properties = {"kirana.kafka.enabled=true", "kirana.outbox.relay-enabled=false",
        "kirana.payment.jobs-enabled=false"})
@Import({PostgresContainerConfig.class, RedisContainerConfig.class, KafkaContainerConfig.class})
class PaymentWebhookIntegrationTest {

    @MockitoBean MinioClient minio;
    @MockitoBean PaymentGateways gateways;

    @Autowired PaymentWebhooks webhooks;
    @Autowired CheckoutSaga saga;
    @Autowired OrderService orders;
    @Autowired UserService users;
    @Autowired ProductService products;
    @Autowired InventoryService inventory;
    @Autowired CartService carts;
    @Autowired OutboxRelay relay;
    @Autowired Inbox inbox;
    @Autowired JdbcTemplate jdbc;
    @Autowired KafkaTemplate<String, String> kafka;

    private final StubGateway gateway = new StubGateway();

    @BeforeEach
    void stubGateway() {
        when(gateways.forCheckout(any())).thenReturn(gateway);
        when(gateways.of(anyString())).thenReturn(gateway);
        when(gateways.find("stub")).thenReturn(Optional.of(gateway));
    }

    @Test
    void aWebhookPaysTheOrderWithoutTheBrowser() {
        long[] o = newOrder();
        String body = captured("pay_w1", "gw_" + o[1]);

        assertThat(webhooks.receive("stub", body, "forged", "evt_a")).isEqualTo(Result.INVALID_SIGNATURE);
        assertThat(webhooks.receive("stub", body, StubGateway.GOOD, "evt_a")).isEqualTo(Result.ACCEPTED);
        assertThat(webhooks.receive("stub", body, StubGateway.GOOD, "evt_a")).isEqualTo(Result.DUPLICATE); // gateway retried
        assertThat(eventsFor(o[1], "PaymentCaptured")).isEqualTo(1);

        drainRelay();
        await(() -> orders.get(o[0], o[1]).status() == OrderStatus.PAID);
        assertThat(orders.get(o[0], o[1]).paidAt()).isNotNull();
    }

    @Test
    void aWebhookForAClosedOrderRaisesOneRefundEvenWhenKafkaRedelivers() {
        long[] o = newOrder();
        saga.cancel(o[0], o[1]); // shopper gave up, then the payment went through anyway
        String body = captured("pay_w2", "gw_" + o[1]);
        assertThat(webhooks.receive("stub", body, StubGateway.GOOD, "evt_b")).isEqualTo(Result.ACCEPTED);
        drainRelay();
        await(() -> "pay_w2".equals(orders.get(o[0], o[1]).latePaymentId()));

        // At-least-once: the same record arrives again (e.g. the app died before committing its offset).
        String record = jdbc.queryForObject("SELECT payload FROM outbox WHERE message_key = ? AND event_type = 'PaymentCaptured'",
                String.class, Long.toString(o[1]));
        kafka.send(Topics.PAYMENTS, Long.toString(o[1]), record);
        // A marker behind it on the same partition: once the marker is processed, so is the duplicate.
        String marker = record.replaceFirst("\"eventId\":\"[^\"]+\"", "\"eventId\":\"" + java.util.UUID.randomUUID() + "\"")
                .replace("PaymentCaptured", "PaymentFailed");
        kafka.send(Topics.PAYMENTS, Long.toString(o[1]), marker);
        String markerId = marker.replaceFirst(".*\"eventId\":\"([^\"]+)\".*", "$1");
        await(() -> jdbc.queryForObject("SELECT count(*) FROM processed_events WHERE event_id = ?::uuid", Long.class, markerId) == 1);

        assertThat(orders.get(o[0], o[1]).status()).isEqualTo(OrderStatus.CANCELLED);
        assertThat(eventsFor(o[1], "PaymentAfterClose")).isEqualTo(1);
    }

    @Test
    void aLatePaymentWebhookEndsInAProcessedRefund() {
        // 6c + 6d end to end: webhook -> PaymentAfterClose on orders.v1 -> RefundListener -> gateway
        // refund (PENDING) -> refund.processed webhook -> payments.v1 -> PROCESSED.
        long[] o = newOrder();
        saga.cancel(o[0], o[1]);
        webhooks.receive("stub", captured("pay_w3", "gw_" + o[1]), StubGateway.GOOD, "evt_c");
        drainRelay(); // PaymentCaptured -> listener -> PaymentAfterClose into the outbox
        await(() -> eventsFor(o[1], "PaymentAfterClose") == 1);
        drainRelay(); // PaymentAfterClose -> orders.v1 -> RefundListener
        await(() -> "PENDING".equals(orders.get(o[0], o[1]).refundStatus()));
        assertThat(gateway.refunds.get("pay_w3")).hasSize(1);

        String refundId = gateway.refunds.get("pay_w3").get(0).refundId();
        String processed = """
                {"entity":"event","event":"refund.processed","payload":{"payment":{"entity":\
                {"id":"pay_w3","order_id":"gw_%d","status":"captured"}},"refund":{"entity":\
                {"id":"%s","payment_id":"pay_w3","status":"processed","amount":1000}}}}""".formatted(o[1], refundId);
        assertThat(webhooks.receive("stub", processed, StubGateway.GOOD, "evt_d")).isEqualTo(Result.ACCEPTED);
        drainRelay();
        await(() -> "PROCESSED".equals(orders.get(o[0], o[1]).refundStatus()));
    }

    @Test
    void theInboxSaysFirstOnlyOnce() {
        var id = java.util.UUID.randomUUID();
        assertThat(inbox.firstDelivery("test", id)).isTrue();
        assertThat(inbox.firstDelivery("test", id)).isFalse();
        assertThat(inbox.firstDelivery("another-consumer", id)).isTrue(); // each consumer group dedupes for itself
    }

    // ---------------------------------------------------------------- helpers

    /** {userId, orderId} of a fresh CREATED order attached to gateway order "gw_{orderId}". */
    private long[] newOrder() {
        long user = users.create(new UserRequest("Hook", "hook-" + System.nanoTime() + "@t.com")).id();
        long tea = products.create(new ProductRequest("Hook tea " + System.nanoTime(), null, "10")).id();
        inventory.set(tea, 5);
        carts.add(user, tea, 1);
        return new long[] {user, saga.start(user, "stub").order().id()};
    }

    private static String captured(String paymentId, String gatewayOrderId) {
        return """
                {"entity":"event","event":"payment.captured","payload":{"payment":{"entity":\
                {"id":"%s","order_id":"%s","status":"captured","amount":1000}}}}""".formatted(paymentId, gatewayOrderId);
    }

    private long eventsFor(long orderId, String type) {
        return jdbc.queryForObject("SELECT count(*) FROM outbox WHERE message_key = ? AND event_type = ?",
                Long.class, Long.toString(orderId), type);
    }

    private void drainRelay() {
        while (relay.publishBatch() > 0) {
            // everything waiting, including other tests' events
        }
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
