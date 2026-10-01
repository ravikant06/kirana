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
    @Autowired org.springframework.jdbc.core.JdbcTemplate jdbc;

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
    void anUnknownGatewayAnswerHoldsTheOrderInsteadOfClosingIt() {
        // G3: "the gateway has no record" is not "not paid" (e.g. payment-mock restarted and lost
        // its memory). Expiry keeps the stock held and asks again later.
        Fixture f = fixture(5);
        long orderId = saga.start(f.user, "stub").order().id();
        var due = orders.get(f.user, orderId).paymentDueAt();
        gateway.status.set(new GatewayStatus(GatewayStatus.State.UNKNOWN, null));

        saga.expire(orderId);

        var after = orders.get(f.user, orderId);
        assertThat(after.status()).isEqualTo(OrderStatus.CREATED);
        assertThat(after.paymentDueAt()).isAfter(due); // asked again later, not closed
        assertThat(stock(f)).isEqualTo(4);
    }

    @Test
    void aLatePaymentIsRecordedOnceHoweverManyWaysItIsReported() {
        // G1: money arrives after the order closed. The browser, the webhook consumer and the
        // re-check job may all notice it; the refund event must be raised exactly once.
        Fixture f = fixture(5);
        CheckoutResponse r = saga.start(f.user, "stub");
        long orderId = r.order().id();
        saga.cancel(f.user, orderId);
        gateway.status.set(new GatewayStatus(GatewayStatus.State.PAID, "pay_late"));

        assertThat(saga.applyPayment(orderId, "pay_late")).isEqualTo(CheckoutSaga.PaymentResult.PAID_AFTER_CLOSE);
        assertThat(saga.applyPayment(orderId, "pay_late")).isEqualTo(CheckoutSaga.PaymentResult.PAID_AFTER_CLOSE);
        saga.recheckClosed(orderId);
        assertThatThrownBy(() -> saga.verifyPayment(f.user, orderId, r.payment().gatewayOrderId(), "pay_late", StubGateway.GOOD))
                .isInstanceOf(ConflictException.class);

        var order = orders.get(f.user, orderId);
        assertThat(order.status()).isEqualTo(OrderStatus.CANCELLED); // never flips to PAID
        assertThat(order.latePaymentId()).isEqualTo("pay_late");
        assertThat(jdbc.queryForObject("SELECT count(*) FROM outbox WHERE message_key = ? AND event_type = 'PaymentAfterClose'",
                Long.class, Long.toString(orderId))).isEqualTo(1);
        assertThat(stock(f)).isEqualTo(5); // the stock released on cancel stays released
    }

    @Test
    void theReCheckFindsMoneyThatArrivedAfterExpiry() {
        Fixture f = fixture(5);
        long orderId = saga.start(f.user, "stub").order().id();
        gateway.status.set(new GatewayStatus(GatewayStatus.State.PENDING, null));
        saga.expire(orderId);
        assertThat(orders.get(f.user, orderId).status()).isEqualTo(OrderStatus.FAILED);

        saga.recheckClosed(orderId); // still nothing at the gateway
        assertThat(orders.get(f.user, orderId).latePaymentId()).isNull();

        gateway.status.set(new GatewayStatus(GatewayStatus.State.PAID, "pay_after_expiry")); // shopper paid in the old tab
        saga.recheckClosed(orderId);
        assertThat(orders.get(f.user, orderId).latePaymentId()).isEqualTo("pay_after_expiry");
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
    void payNowReusesTheAttachedGatewayOrderEvenIfTheGatewayWouldMakeANewOne() {
        // Real Razorpay creates a new order for a repeated receipt; the stub now does the same.
        gateway.newIdEachCall = true;
        Fixture f = fixture(5);
        CheckoutResponse first = saga.start(f.user, "stub");

        PaymentSession again = saga.requestPayment(f.user, first.order().id(), null);

        assertThat(again.gatewayOrderId()).isEqualTo(first.payment().gatewayOrderId());
        assertThat(gateway.created.get()).isEqualTo(1); // "Pay now" did not create a second gateway order
        assertThat(saga.verifyPayment(f.user, first.order().id(), again.gatewayOrderId(), "pay_1", StubGateway.GOOD).status())
                .isEqualTo(OrderStatus.PAID);
    }

    @Test
    void twoSimultaneousCheckoutsOfOneCartPlaceOneOrderAndSayWhy() throws Exception {
        Fixture f = fixture(5);
        ExecutorService pool = Executors.newFixedThreadPool(2);
        CountDownLatch start = new CountDownLatch(1);
        List<Future<Object>> tabs = new ArrayList<>();
        for (int i = 0; i < 2; i++) {
            tabs.add(pool.submit(() -> {
                start.await();
                try {
                    return saga.start(f.user, "stub");
                } catch (Exception e) {
                    return e;
                }
            }));
        }
        start.countDown();
        List<Object> results = new ArrayList<>();
        for (Future<Object> t : tabs) {
            results.add(t.get(30, TimeUnit.SECONDS));
        }
        pool.shutdown();

        assertThat(results).filteredOn(r -> r instanceof CheckoutResponse).hasSize(1);
        Object loser = results.stream().filter(r -> !(r instanceof CheckoutResponse)).findFirst().orElseThrow();
        assertThat(loser).isInstanceOf(ConflictException.class);
        // Racing: "already in progress". Arriving just after the first committed: "Cart is empty".
        assertThat(((ConflictException) loser).getTitle()).isIn("Checkout already in progress", "Cart is empty");
        assertThat(stock(f)).isEqualTo(4);
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
        final java.util.concurrent.atomic.AtomicInteger created = new java.util.concurrent.atomic.AtomicInteger();
        volatile boolean down;
        volatile boolean newIdEachCall; // behave like real Razorpay: no de-duplication by receipt

        @Override public String id() { return "stub"; }
        @Override public String label() { return "Stub"; }
        @Override public boolean available() { return true; }

        @Override
        public GatewayOrder createOrder(long orderId, long amountPaise, String currency) {
            if (down) {
                throw new PaymentUnavailableException("stub is down", null);
            }
            int n = created.incrementAndGet();
            String id = newIdEachCall ? "gw_" + orderId + "_" + n : "gw_" + orderId; // mock-style: same id per order
            return new GatewayOrder(id, amountPaise, currency);
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
        public boolean verifyWebhook(String body, String signature) {
            return GOOD.equals(signature);
        }

        @Override
        public PaymentSession session(long orderId, GatewayOrder order) {
            return new PaymentSession("stub", orderId, order.gatewayOrderId(), order.amountPaise(), order.currency(), "key", null);
        }
    }
}
