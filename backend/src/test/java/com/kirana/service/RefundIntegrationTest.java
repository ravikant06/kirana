package com.kirana.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.when;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

import com.kirana.PostgresContainerConfig;
import com.kirana.RedisContainerConfig;
import com.kirana.dto.ProductRequest;
import com.kirana.dto.UserRequest;
import com.kirana.entity.RefundStatus;
import com.kirana.payment.GatewayRefund;
import com.kirana.payment.PaymentGateways;
import com.kirana.repository.RefundRepository;
import com.kirana.service.CheckoutSagaIntegrationTest.StubGateway;
import io.minio.MinioClient;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.Import;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.bean.override.mockito.MockitoBean;

/**
 * Stage 6d: a late payment is refunded exactly once, whatever goes wrong: duplicates, a gateway
 * outage, a call that times out after the gateway refunded, and concurrent attempts.
 */
@SpringBootTest(properties = "kirana.payment.jobs-enabled=false")
@Import({PostgresContainerConfig.class, RedisContainerConfig.class})
class RefundIntegrationTest {

    @MockitoBean MinioClient minio;
    @MockitoBean PaymentGateways gateways;

    @Autowired RefundService refunds;
    @Autowired RefundRepository repo;
    @Autowired CheckoutSaga saga;
    @Autowired OrderService orders;
    @Autowired UserService users;
    @Autowired ProductService products;
    @Autowired InventoryService inventory;
    @Autowired CartService carts;
    @Autowired JdbcTemplate jdbc;

    private final StubGateway gateway = new StubGateway();

    @BeforeEach
    void stubGateway() {
        when(gateways.forCheckout(any())).thenReturn(gateway);
        when(gateways.of(anyString())).thenReturn(gateway);
    }

    @Test
    void aLatePaymentIsRefundedOnceAndTheOrderShowsIt() {
        Late l = latePayment("pay_r1");

        Long id = refunds.request(l.orderId, "pay_r1", "stub");
        assertThat(refunds.request(l.orderId, "pay_r1", "stub")).isNull(); // a second request: already recorded
        refunds.execute(id);
        refunds.execute(id); // already at the gateway: nothing to do

        assertThat(gateway.refundCalls.get()).isEqualTo(1);
        assertThat(status(id)).isEqualTo(RefundStatus.PENDING);
        assertThat(orders.get(l.userId, l.orderId).refundStatus()).isEqualTo("PENDING");

        assertThat(refunds.settle("pay_r1", "rfnd_pay_r1_1", GatewayRefund.State.PROCESSED)).isTrue();
        assertThat(refunds.settle("pay_r1", "rfnd_pay_r1_1", GatewayRefund.State.PROCESSED)).isFalse(); // webhook retried
        assertThat(orders.get(l.userId, l.orderId).refundStatus()).isEqualTo("PROCESSED");
    }

    @Test
    void aGatewayOutageLeavesItRequestedAndALaterAttemptRefunds() {
        Late l = latePayment("pay_r2");
        Long id = refunds.request(l.orderId, "pay_r2", "stub");
        gateway.down = true;

        refunds.execute(id);
        assertThat(status(id)).isEqualTo(RefundStatus.REQUESTED);
        assertThat(repo.findById(id).orElseThrow().getLastError()).contains("down");

        gateway.down = false;
        refunds.execute(id); // still within the lease: nobody may call the gateway yet
        assertThat(gateway.refundCalls.get()).isZero();

        leaseRunsOut(id);
        refunds.execute(id);
        assertThat(status(id)).isEqualTo(RefundStatus.PENDING);
        assertThat(gateway.refundCalls.get()).isEqualTo(1);
    }

    @Test
    void aTimeoutAfterTheGatewayRefundedDoesNotRefundTwice() {
        Late l = latePayment("pay_r3");
        Long id = refunds.request(l.orderId, "pay_r3", "stub");
        gateway.refundTimesOut = true;

        refunds.execute(id); // the gateway made the refund, but we heard a timeout
        assertThat(status(id)).isEqualTo(RefundStatus.REQUESTED);
        assertThat(gateway.refunds.get("pay_r3")).hasSize(1);

        gateway.refundTimesOut = false;
        leaseRunsOut(id);
        refunds.execute(id); // asks first, finds rfnd_pay_r3_1, adopts it

        assertThat(gateway.refunds.get("pay_r3")).hasSize(1); // still one refund: the customer is not paid twice
        assertThat(repo.findById(id).orElseThrow().getGatewayRefundId()).isEqualTo("rfnd_pay_r3_1");
        assertThat(status(id)).isEqualTo(RefundStatus.PENDING);
    }

    @Test
    void simultaneousAttemptsCallTheGatewayOnce() throws Exception {
        Late l = latePayment("pay_r4");
        Long id = refunds.request(l.orderId, "pay_r4", "stub");
        List<Callable<Object>> calls = new ArrayList<>();
        for (int i = 0; i < 10; i++) {
            calls.add(() -> {
                refunds.execute(id); // the consumer's fast path and the job, on several instances
                return null;
            });
        }
        together(calls);

        assertThat(gateway.refundCalls.get()).isEqualTo(1);
        assertThat(status(id)).isEqualTo(RefundStatus.PENDING);
    }

    // ---------------------------------------------------------------- helpers

    record Late(long userId, long orderId) {
    }

    /** An order that was cancelled and then paid anyway (6c: late payment recorded). */
    private Late latePayment(String paymentId) {
        long user = users.create(new UserRequest("Refund", "refund-" + System.nanoTime() + "@t.com")).id();
        long tea = products.create(new ProductRequest("Refund tea " + System.nanoTime(), null, "120.10")).id();
        inventory.set(tea, 5);
        carts.add(user, tea, 1);
        long orderId = saga.start(user, "stub").order().id();
        saga.cancel(user, orderId);
        assertThat(saga.applyPayment(orderId, paymentId)).isEqualTo(CheckoutSaga.PaymentResult.PAID_AFTER_CLOSE);
        return new Late(user, orderId);
    }

    private RefundStatus status(long id) {
        return repo.findById(id).orElseThrow().getStatus();
    }

    private void leaseRunsOut(long id) {
        jdbc.update("UPDATE refunds SET claimed_until = now() - interval '1 second' WHERE id = ?", id);
    }

    private static void together(List<Callable<Object>> calls) throws Exception {
        ExecutorService pool = Executors.newFixedThreadPool(calls.size());
        CountDownLatch start = new CountDownLatch(1);
        List<Future<Object>> futures = new ArrayList<>();
        for (Callable<Object> c : calls) {
            futures.add(pool.submit(() -> {
                start.await();
                return c.call();
            }));
        }
        start.countDown();
        for (Future<Object> f : futures) {
            f.get(30, TimeUnit.SECONDS);
        }
        pool.shutdown();
    }
}
