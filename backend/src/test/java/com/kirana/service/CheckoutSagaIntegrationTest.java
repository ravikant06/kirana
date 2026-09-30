package com.kirana.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
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
import java.util.concurrent.atomic.AtomicReference;

import com.kirana.PostgresContainerConfig;
import com.kirana.RedisContainerConfig;
import com.kirana.dto.CheckoutResponse;
import com.kirana.dto.ProductRequest;
import com.kirana.dto.UserRequest;
import com.kirana.entity.OrderStatus;
import com.kirana.exception.ConflictException;
import com.kirana.exception.InvalidFieldException;
import com.kirana.payment.GatewayOrder;
import com.kirana.payment.GatewayStatus;
import com.kirana.payment.PaymentGateway;
import com.kirana.payment.PaymentGateways;
import com.kirana.payment.PaymentSession;
import com.kirana.payment.PaymentUnavailableException;
import io.minio.MinioClient;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.bean.override.mockito.MockitoBean;

/**
 * The checkout saga against a real Postgres and Redis, with a stub gateway whose answers each
 * test controls. Covers the happy path, forged signatures, idempotent steps, compensation,
 * the reconciler and expiry, and races between them.
 */
@SpringBootTest(properties = "kirana.payment.jobs-enabled=false")
@Import({PostgresContainerConfig.class, RedisContainerConfig.class})
class CheckoutSagaIntegrationTest {

    @MockitoBean MinioClient minio;
    @MockitoBean PaymentGateways gateways;

    @Autowired CheckoutSaga saga;
    @Autowired UserService users;
    @Autowired ProductService products;
    @Autowired InventoryService inventory;
    @Autowired CartService carts;
    @Autowired OrderService orders;

    private final StubGateway gateway = new StubGateway();

    @BeforeEach
    void stubGateway() {
        when(gateways.forCheckout(any())).thenReturn(gateway);
        when(gateways.of(anyString())).thenReturn(gateway);
    }

    @Test
    void aVerifiedPaymentMarksTheOrderPaid() {
        Fixture f = fixture(5);
        CheckoutResponse r = saga.start(f.user, "stub");
        assertThat(r.order().status()).isEqualTo(OrderStatus.CREATED);
        assertThat(r.payment().amountPaise()).isEqualTo(12010);
        assertThat(stock(f)).isEqualTo(4);

        var paid = saga.verifyPayment(f.user, r.order().id(), r.payment().gatewayOrderId(), "pay_1", StubGateway.GOOD);

        assertThat(paid.status()).isEqualTo(OrderStatus.PAID);
        // Verifying again is harmless (the browser retried, or the reconciler got there too).
        assertThat(saga.verifyPayment(f.user, r.order().id(), r.payment().gatewayOrderId(), "pay_1", StubGateway.GOOD).status())
                .isEqualTo(OrderStatus.PAID);
    }

    @Test
    void aForgedSignatureIsRejected() {
        Fixture f = fixture(5);
        CheckoutResponse r = saga.start(f.user, "stub");

        assertThatThrownBy(() -> saga.verifyPayment(f.user, r.order().id(), r.payment().gatewayOrderId(), "pay_1", "forged"))
                .isInstanceOf(InvalidFieldException.class);
        assertThat(orders.get(f.user, r.order().id()).status()).isEqualTo(OrderStatus.CREATED);
    }

    @Test
    void cancellingReturnsTheStockOnce() {
        Fixture f = fixture(5);
        CheckoutResponse r = saga.start(f.user, "stub");
        assertThat(stock(f)).isEqualTo(4);

        assertThat(saga.cancel(f.user, r.order().id()).status()).isEqualTo(OrderStatus.CANCELLED);
        assertThat(stock(f)).isEqualTo(5);
        assertThatThrownBy(() -> saga.cancel(f.user, r.order().id())).isInstanceOf(ConflictException.class);
        assertThat(stock(f)).isEqualTo(5);
    }

    @Test
    void manySimultaneousClosesReleaseTheStockExactlyOnce() throws Exception {
        Fixture f = fixture(5);
        long orderId = saga.start(f.user, "stub").order().id();
        gateway.status.set(new GatewayStatus(GatewayStatus.State.PENDING, null));

        // Shopper cancels, and the expiry job runs, 10 times each, all at once.
        List<Callable<Object>> calls = new ArrayList<>();
        for (int i = 0; i < 10; i++) {
            calls.add(() -> saga.cancel(f.user, orderId));
            calls.add(() -> {
                saga.expire(orderId);
                return null;
            });
        }
        together(calls);

        assertThat(stock(f)).isEqualTo(5); // released once, not up to 20 times
        assertThat(orders.get(f.user, orderId).status()).isIn(OrderStatus.CANCELLED, OrderStatus.FAILED);
    }

    @Test
    void theReconcilerSettlesAPaymentTheShopWasNeverToldAbout() {
        Fixture f = fixture(5);
        long orderId = saga.start(f.user, "stub").order().id();

        gateway.status.set(new GatewayStatus(GatewayStatus.State.PENDING, null));
        saga.reconcile(orderId);
        assertThat(orders.get(f.user, orderId).status()).isEqualTo(OrderStatus.CREATED);

        gateway.status.set(new GatewayStatus(GatewayStatus.State.PAID, "pay_lost"));
        saga.reconcile(orderId);
        assertThat(orders.get(f.user, orderId).status()).isEqualTo(OrderStatus.PAID);
    }

    @Test
    void expiryChecksTheGatewayBeforeReleasingStock() {
        Fixture f = fixture(5);
        long paidLate = saga.start(f.user, "stub").order().id();
        gateway.status.set(new GatewayStatus(GatewayStatus.State.PAID, "pay_late"));
        saga.expire(paidLate);
        assertThat(orders.get(f.user, paidLate).status()).isEqualTo(OrderStatus.PAID);

        carts.add(f.user, f.product, 1);
        long unpaid = saga.start(f.user, "stub").order().id();
        gateway.status.set(new GatewayStatus(GatewayStatus.State.PENDING, null));
        saga.expire(unpaid);
        assertThat(orders.get(f.user, unpaid).status()).isEqualTo(OrderStatus.FAILED);
        assertThat(stock(f)).isEqualTo(4); // the paid one keeps its unit; the unpaid one's came back
    }

    @Test
    void aPaymentAfterCancellationIsFlaggedForRefund() {
        Fixture f = fixture(5);
        CheckoutResponse r = saga.start(f.user, "stub");
        saga.cancel(f.user, r.order().id());

        assertThatThrownBy(() -> saga.verifyPayment(f.user, r.order().id(), r.payment().gatewayOrderId(), "pay_x", StubGateway.GOOD))
                .isInstanceOf(ConflictException.class)
                .hasMessageContaining("refund");
    }

    @Test
    void aGatewayOutageStillHoldsTheOrderForLaterPayment() {
        Fixture f = fixture(5);
        gateway.down = true;

        CheckoutResponse r = saga.start(f.user, "stub");

        assertThat(r.payment()).isNull();
        assertThat(r.paymentProblem()).contains("temporarily unavailable");
        assertThat(r.order().status()).isEqualTo(OrderStatus.CREATED);

        gateway.down = false;
        PaymentSession later = saga.requestPayment(f.user, r.order().id(), null);
        assertThat(later.gatewayOrderId()).isNotBlank();
    }

    @Test
    void rupeesBecomeExactPaise() {
        assertThat(CheckoutSaga.toPaise(120.10)).isEqualTo(12010);
        assertThat(CheckoutSaga.toPaise(0.1 + 0.2)).isEqualTo(30);  // 0.30000000000000004
        assertThat(CheckoutSaga.toPaise(99.995)).isEqualTo(10000);   // rounds half up
    }

    // ---------------------------------------------------------------- helpers

    record Fixture(long user, long product) {
    }

    private Fixture fixture(int stock) {
        long user = users.create(new UserRequest("Saga", "saga-" + System.nanoTime() + "@test.com")).id();
        long product = products.create(new ProductRequest("Saga tea " + System.nanoTime(), null, "120.10")).id();
        inventory.set(product, stock);
        carts.add(user, product, 1);
        return new Fixture(user, product);
    }

    private int stock(Fixture f) {
        return inventory.get(f.product).quantity();
    }

    private static void together(List<Callable<Object>> calls) throws Exception {
        ExecutorService pool = Executors.newFixedThreadPool(calls.size());
        CountDownLatch start = new CountDownLatch(1);
        List<Future<Object>> futures = new ArrayList<>();
        for (Callable<Object> c : calls) {
            futures.add(pool.submit(() -> {
                start.await();
                try {
                    return c.call();
                } catch (Exception e) {
                    return e; // losers of the race get ConflictException; that is expected
                }
            }));
        }
        start.countDown();
        for (Future<Object> f : futures) {
            f.get(30, TimeUnit.SECONDS);
        }
        pool.shutdown();
    }

    /** A gateway whose behaviour each test sets. Signatures are valid only if equal to GOOD. */
    static class StubGateway implements PaymentGateway {

        static final String GOOD = "good-signature";

        final AtomicReference<GatewayStatus> status = new AtomicReference<>(new GatewayStatus(GatewayStatus.State.PENDING, null));
        volatile boolean down;

        @Override public String id() { return "stub"; }
        @Override public String label() { return "Stub"; }
        @Override public boolean available() { return true; }

        @Override
        public GatewayOrder createOrder(long orderId, long amountPaise, String currency) {
            if (down) {
                throw new PaymentUnavailableException("stub is down", null);
            }
            return new GatewayOrder("gw_" + orderId, amountPaise, currency); // same id for the same order
        }

        @Override
        public GatewayStatus fetchStatus(String gatewayOrderId) {
            if (down) {
                throw new PaymentUnavailableException("stub is down", null);
            }
            return status.get();
        }

        @Override
        public boolean verifySignature(String gatewayOrderId, String paymentId, String signature) {
            return GOOD.equals(signature);
        }

        @Override
        public PaymentSession session(long orderId, GatewayOrder order) {
            return new PaymentSession("stub", orderId, order.gatewayOrderId(), order.amountPaise(), order.currency(), "key", null);
        }
    }
}
