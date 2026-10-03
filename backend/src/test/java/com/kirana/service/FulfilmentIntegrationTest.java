package com.kirana.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.atLeast;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.util.function.BooleanSupplier;

import com.kirana.KafkaContainerConfig;
import com.kirana.PostgresContainerConfig;
import com.kirana.RedisContainerConfig;
import com.kirana.dto.ProductRequest;
import com.kirana.dto.UserRequest;
import com.kirana.messaging.OutboxRelay;
import com.kirana.messaging.Topics;
import com.kirana.payment.PaymentGateways;
import com.kirana.service.CheckoutSagaIntegrationTest.StubGateway;
import com.kirana.warehouse.Shipment;
import com.kirana.warehouse.WarehouseClient;
import com.kirana.warehouse.WarehouseUnavailableException;
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
 * Stage 6e on real Postgres and Kafka: a paid order reaches the warehouse through OrderPaid, a
 * warehouse outage is waited out (not skipped), and a redelivered OrderPaid ships nothing twice.
 */
@SpringBootTest(properties = {"kirana.kafka.enabled=true", "kirana.outbox.relay-enabled=false",
        "kirana.payment.jobs-enabled=false"})
@Import({PostgresContainerConfig.class, RedisContainerConfig.class, KafkaContainerConfig.class})
class FulfilmentIntegrationTest {

    @MockitoBean MinioClient minio;
    @MockitoBean PaymentGateways gateways;
    @MockitoBean WarehouseClient warehouse;

    @Autowired CheckoutSaga saga;
    @Autowired OrderService orders;
    @Autowired UserService users;
    @Autowired ProductService products;
    @Autowired InventoryService inventory;
    @Autowired CartService carts;
    @Autowired OutboxRelay relay;
    @Autowired JdbcTemplate jdbc;
    @Autowired KafkaTemplate<String, String> kafka;

    private final StubGateway gateway = new StubGateway();

    @BeforeEach
    void stubs() {
        when(gateways.forCheckout(any())).thenReturn(gateway);
        when(gateways.of(anyString())).thenReturn(gateway);
    }

    @Test
    void aPaidOrderIsSentToTheWarehouseOnceEvenIfOrderPaidArrivesTwice() {
        when(warehouse.ship(anyLong(), any())).thenAnswer(i -> new Shipment("shp_" + i.getArgument(0), "accepted", false));
        long[] o = paidOrder();
        drainRelay();
        await(() -> ("shp_" + o[1]).equals(orders.get(o[0], o[1]).shipmentId()));

        // At-least-once: OrderPaid delivered again. The order already has its shipment: no second call.
        String orderPaid = jdbc.queryForObject("SELECT payload FROM outbox WHERE message_key = ? AND event_type = 'OrderPaid'",
                String.class, Long.toString(o[1]));
        kafka.send(Topics.ORDERS, Long.toString(o[1]), orderPaid);
        long[] marker = paidOrder(); // processed after the duplicate if on the same partition; either way, give it time
        drainRelay();
        await(() -> orders.get(marker[0], marker[1]).shipmentId() != null);

        verify(warehouse, times(1)).ship(eq(o[1]), any());
    }

    @Test
    void aWarehouseOutageIsWaitedOutNotSkipped() {
        when(warehouse.ship(anyLong(), any()))
                .thenThrow(new WarehouseUnavailableException("down", null))
                .thenThrow(new WarehouseUnavailableException("still down", null))
                .thenAnswer(i -> new Shipment("shp_late_" + i.getArgument(0), "accepted", false));
        long[] o = paidOrder();
        assertThat(orders.get(o[0], o[1]).shipmentId()).isNull(); // payment never waits for the warehouse

        drainRelay();
        await(() -> orders.get(o[0], o[1]).shipmentId() != null); // ~1 s + 2 s of back-off

        verify(warehouse, atLeast(3)).ship(eq(o[1]), any());
    }

    // ---------------------------------------------------------------- helpers

    /** {userId, orderId} of an order paid through the saga (OrderPaid is in the outbox). */
    private long[] paidOrder() {
        long user = users.create(new UserRequest("Ship", "ship-" + System.nanoTime() + "@t.com")).id();
        long tea = products.create(new ProductRequest("Ship tea " + System.nanoTime(), null, "10")).id();
        inventory.set(tea, 5);
        carts.add(user, tea, 1);
        long orderId = saga.start(user, "stub").order().id();
        saga.applyPayment(orderId, "pay_ship_" + orderId);
        return new long[] {user, orderId};
    }

    private void drainRelay() {
        while (relay.publishBatch() > 0) {
            // everything waiting, including other tests' events
        }
    }

    private static void await(BooleanSupplier condition) {
        long deadline = System.currentTimeMillis() + 30_000;
        while (!condition.getAsBoolean()) {
            if (System.currentTimeMillis() > deadline) {
                throw new AssertionError("condition not met within 30 s");
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
