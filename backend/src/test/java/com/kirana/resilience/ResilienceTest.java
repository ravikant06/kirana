package com.kirana.resilience;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import com.kirana.payment.PaymentUnavailableException;
import io.github.resilience4j.bulkhead.BulkheadFullException;
import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import org.junit.jupiter.api.Test;
import org.springframework.data.redis.RedisConnectionFailureException;

/** Stage 5 policies in isolation: no Spring, no network. */
class ResilienceTest {

    private final Resilience resilience = new Resilience(true);

    @Test
    void aFailingGatewayOpensTheBreakerThenCallsFailFastWithoutReachingIt() {
        AtomicInteger calls = new AtomicInteger();
        for (int i = 0; i < 5; i++) { // minimum 5 calls, 50% failure threshold
            assertThatThrownBy(() -> resilience.payment("mock", false, () -> {
                calls.incrementAndGet();
                throw new PaymentUnavailableException("down", null);
            })).isInstanceOf(PaymentUnavailableException.class);
        }
        assertThat(breaker("payment-mock").getState()).isEqualTo(CircuitBreaker.State.OPEN);

        assertThatThrownBy(() -> resilience.payment("mock", false, () -> {
            calls.incrementAndGet();
            return "never";
        })).isInstanceOf(PaymentUnavailableException.class).hasMessageContaining("circuit open");
        assertThat(calls.get()).isEqualTo(5); // the 6th never reached the gateway
    }

    @Test
    void idempotentCallsAreRetriedWithBackoffUntilTheyWork() {
        AtomicInteger calls = new AtomicInteger();
        long start = System.nanoTime();

        String result = resilience.payment("mock", true, () -> {
            if (calls.incrementAndGet() < 3) {
                throw new PaymentUnavailableException("blip", null);
            }
            return "ok";
        });

        long waitedMs = (System.nanoTime() - start) / 1_000_000;
        assertThat(result).isEqualTo("ok");
        assertThat(calls.get()).isEqualTo(3);
        assertThat(waitedMs).isGreaterThanOrEqualTo(100 + 200); // ~200 ms then ~400 ms, each +-50%
    }

    @Test
    void nonIdempotentCallsAreNotRetried() {
        AtomicInteger calls = new AtomicInteger();
        assertThatThrownBy(() -> resilience.payment("razorpay", false, () -> {
            calls.incrementAndGet();
            throw new PaymentUnavailableException("blip", null);
        })).isInstanceOf(PaymentUnavailableException.class);
        assertThat(calls.get()).isEqualTo(1);
    }

    @Test
    void anOpenRedisBreakerLooksLikeRedisBeingDownSoCallersFailOpen() {
        for (int i = 0; i < 10; i++) {
            assertThatThrownBy(() -> resilience.redis(() -> {
                throw new RedisConnectionFailureException("refused");
            })).isInstanceOf(RedisConnectionFailureException.class);
        }
        AtomicInteger reached = new AtomicInteger();

        assertThatThrownBy(() -> resilience.redis(() -> reached.incrementAndGet()))
                .isInstanceOf(RedisConnectionFailureException.class) // a DataAccessException: existing fail-open paths catch it
                .hasMessageContaining("circuit open");
        assertThat(reached.get()).isZero();
    }

    @Test
    void theTwentyFirstConcurrentCheckoutIsRejectedImmediately() throws Exception {
        CountDownLatch release = new CountDownLatch(1);
        CountDownLatch inside = new CountDownLatch(20);
        ExecutorService pool = Executors.newFixedThreadPool(20);
        List<Future<String>> running = new ArrayList<>();
        for (int i = 0; i < 20; i++) {
            running.add(pool.submit(() -> resilience.checkout(() -> {
                inside.countDown();
                await(release);
                return "done";
            })));
        }
        assertThat(inside.await(5, TimeUnit.SECONDS)).isTrue();

        long start = System.nanoTime();
        assertThatThrownBy(() -> resilience.checkout(() -> "21st")).isInstanceOf(BulkheadFullException.class);
        assertThat((System.nanoTime() - start) / 1_000_000).isLessThan(100); // no queueing

        release.countDown();
        for (Future<String> f : running) {
            assertThat(f.get(5, TimeUnit.SECONDS)).isEqualTo("done");
        }
        pool.shutdown();
    }

    @Test
    void switchedOffEverythingPassesStraightThrough() {
        Resilience off = new Resilience(false);
        AtomicInteger calls = new AtomicInteger();
        assertThatThrownBy(() -> off.payment("mock", true, () -> {
            calls.incrementAndGet();
            throw new PaymentUnavailableException("down", null);
        })).isInstanceOf(PaymentUnavailableException.class);
        assertThat(calls.get()).isEqualTo(1); // no retry
    }

    private CircuitBreaker breaker(String name) {
        return resilience.allBreakers().stream().filter(b -> b.getName().equals(name)).findFirst().orElseThrow();
    }

    private static void await(CountDownLatch latch) {
        try {
            latch.await(10, TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }
}
