package com.kirana.resilience;

import java.time.Duration;
import java.util.List;
import java.util.function.Supplier;

import com.kirana.payment.PaymentUnavailableException;
import com.kirana.payment.RazorpayStyleGateway;
import io.github.resilience4j.bulkhead.Bulkhead;
import io.github.resilience4j.bulkhead.BulkheadConfig;
import io.github.resilience4j.bulkhead.BulkheadRegistry;
import io.github.resilience4j.circuitbreaker.CallNotPermittedException;
import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import io.github.resilience4j.circuitbreaker.CircuitBreakerConfig;
import io.github.resilience4j.circuitbreaker.CircuitBreakerConfig.SlidingWindowType;
import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry;
import io.github.resilience4j.core.IntervalFunction;
import io.github.resilience4j.retry.Retry;
import io.github.resilience4j.retry.RetryConfig;
import io.github.resilience4j.retry.RetryRegistry;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.dao.DataAccessException;
import org.springframework.data.redis.RedisConnectionFailureException;
import org.springframework.stereotype.Component;

/**
 * All of Stage 5's policies (D48–D51), applied with Resilience4j's functional API.
 *
 * Circuit breaker (per dependency): counts recent failures and slow calls. Past a threshold it
 * OPENS: calls fail instantly for a while instead of waiting on a dependency that is down,
 * which protects our threads and gives the dependency room to recover. Then HALF_OPEN: a few
 * trial calls decide whether to close again.
 *
 * Retry: only for calls that are safe to repeat (idempotent), with exponential backoff and
 * random jitter so many clients do not retry in lockstep (a retry storm). Retries sit OUTSIDE
 * the breaker, so each attempt is counted, and an open breaker is never retried.
 *
 * Bulkhead: caps concurrent checkouts, so a checkout pile-up (slow gateway, flash-sale rush)
 * cannot take every thread and connection away from browsing.
 *
 * kirana.resilience.enabled=false turns all of it off, for before/after measurements.
 */
@Component
public class Resilience {

    private static final Logger log = LoggerFactory.getLogger(Resilience.class);

    public static final String REDIS = "redis";
    public static final String MINIO = "minio";
    public static final String CHECKOUT = "checkout";

    public static String paymentBreaker(String provider) {
        return "payment-" + provider;
    }

    private final boolean enabled;
    private final CircuitBreakerRegistry breakers = CircuitBreakerRegistry.ofDefaults();
    private final RetryRegistry retries = RetryRegistry.ofDefaults();
    private final BulkheadRegistry bulkheads = BulkheadRegistry.ofDefaults();

    public Resilience(@Value("${kirana.resilience.enabled:true}") boolean enabled) {
        this.enabled = enabled;

        // Payment gateway: a slow gateway is as bad as a dead one (threads pile up), so slow
        // calls count as failures. Five of the last ten failing or slow opens it for 15 s.
        CircuitBreakerConfig payment = CircuitBreakerConfig.custom()
                .slidingWindowType(SlidingWindowType.COUNT_BASED).slidingWindowSize(10).minimumNumberOfCalls(5)
                .failureRateThreshold(50)
                .slowCallDurationThreshold(Duration.ofMillis(1500)).slowCallRateThreshold(50)
                .waitDurationInOpenState(Duration.ofSeconds(15)).permittedNumberOfCallsInHalfOpenState(2)
                .recordExceptions(PaymentUnavailableException.class)
                .ignoreExceptions(RazorpayStyleGateway.GatewayRejectedException.class) // 4xx: our bug, not their outage
                .build();
        for (String provider : List.of("mock", "razorpay", "stub")) {
            breakers.circuitBreaker(paymentBreaker(provider), payment);
        }
        // createOrder and fetchStatus are idempotent (our order id is the receipt), so retrying is
        // safe. 3 attempts, waits ~200 ms then ~400 ms, each randomised by +-50% (jitter).
        retries.retry("payment", RetryConfig.custom()
                .maxAttempts(3)
                .intervalFunction(IntervalFunction.ofExponentialRandomBackoff(Duration.ofMillis(200), 2.0, 0.5))
                .retryExceptions(PaymentUnavailableException.class)
                .build());

        // Redis: every call already gives up after 200 ms (fail open). Without a breaker, an
        // outage still costs 200 ms per call, on every request. With it, after 10 s of mostly
        // failures, calls skip Redis instantly for 5 s, then probe. No retry: we fail open anyway.
        breakers.circuitBreaker(REDIS, CircuitBreakerConfig.custom()
                .slidingWindowType(SlidingWindowType.TIME_BASED).slidingWindowSize(10).minimumNumberOfCalls(10)
                .failureRateThreshold(50)
                .slowCallDurationThreshold(Duration.ofMillis(150)).slowCallRateThreshold(80)
                .waitDurationInOpenState(Duration.ofSeconds(5)).permittedNumberOfCallsInHalfOpenState(3)
                .recordExceptions(DataAccessException.class)
                .build());

        // MinIO: stat and delete are idempotent, so one retry; the breaker stops a hung MinIO
        // from holding upload-confirm threads.
        breakers.circuitBreaker(MINIO, CircuitBreakerConfig.custom()
                .slidingWindowType(SlidingWindowType.COUNT_BASED).slidingWindowSize(10).minimumNumberOfCalls(4)
                .failureRateThreshold(50)
                .slowCallDurationThreshold(Duration.ofSeconds(2)).slowCallRateThreshold(50)
                .waitDurationInOpenState(Duration.ofSeconds(15)).permittedNumberOfCallsInHalfOpenState(2)
                .build());
        retries.retry(MINIO, RetryConfig.custom()
                .maxAttempts(2)
                .intervalFunction(IntervalFunction.ofExponentialRandomBackoff(Duration.ofMillis(200), 2.0, 0.5))
                .build());

        // Checkout: at most 20 at once; the 21st gets "busy, retry" immediately instead of
        // queueing. Browsing keeps the rest of Tomcat's 200 threads and the DB pool.
        bulkheads.bulkhead(CHECKOUT, BulkheadConfig.custom()
                .maxConcurrentCalls(20).maxWaitDuration(Duration.ZERO)
                .build());
    }

    public boolean enabled() {
        return enabled;
    }

    /** A payment gateway call: retry (idempotent calls only) around a per-provider breaker. */
    public <T> T payment(String provider, boolean idempotent, Supplier<T> call) {
        if (!enabled) {
            return call.get();
        }
        CircuitBreaker breaker = breakers.circuitBreaker(paymentBreaker(provider));
        Supplier<T> guarded = CircuitBreaker.decorateSupplier(breaker, call);
        if (idempotent) {
            guarded = Retry.decorateSupplier(retries.retry("payment"), guarded);
        }
        try {
            return guarded.get();
        } catch (CallNotPermittedException e) {
            throw new PaymentUnavailableException("The %s gateway is failing, so it is not being called for now (circuit open)"
                    .formatted(provider), e);
        }
    }

    /** A Redis call. An open breaker looks like "Redis unavailable", so callers' fail-open paths apply. */
    public <T> T redis(Supplier<T> call) {
        if (!enabled) {
            return call.get();
        }
        try {
            return breakers.circuitBreaker(REDIS).executeSupplier(call);
        } catch (CallNotPermittedException e) {
            throw new RedisConnectionFailureException("Redis circuit open, skipping Redis", e);
        }
    }

    public void redisRun(Runnable call) {
        redis(() -> {
            call.run();
            return null;
        });
    }

    /** A MinIO call; {@code idempotent} calls get one retry. Checked SDK exceptions are wrapped by the caller. */
    public <T> T minio(boolean idempotent, Supplier<T> call) {
        if (!enabled) {
            return call.get();
        }
        Supplier<T> guarded = CircuitBreaker.decorateSupplier(breakers.circuitBreaker(MINIO), call);
        if (idempotent) {
            guarded = Retry.decorateSupplier(retries.retry(MINIO), guarded);
        }
        return guarded.get();
    }

    /** Runs a checkout if one of the 20 slots is free; else BulkheadFullException (503). */
    public <T> T checkout(Supplier<T> call) {
        if (!enabled) {
            return call.get();
        }
        return bulkheads.bulkhead(CHECKOUT).executeSupplier(call);
    }

    public List<CircuitBreaker> allBreakers() {
        return breakers.getAllCircuitBreakers().stream()
                .filter(b -> !b.getName().equals(paymentBreaker("stub")))
                .toList();
    }

    public Bulkhead checkoutBulkhead() {
        return bulkheads.bulkhead(CHECKOUT);
    }

    /** For the chaos panel: close a breaker by hand after fixing a fault. */
    public void reset(String breakerName) {
        breakers.find(breakerName).ifPresent(b -> {
            b.reset();
            log.info("Circuit breaker {} reset by hand", breakerName);
        });
    }
}
