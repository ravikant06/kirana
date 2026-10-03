package com.kirana.service;

import java.time.Duration;
import java.time.Instant;

import com.kirana.config.PaymentProperties;
import com.kirana.payment.PaymentGateway;
import com.kirana.payment.PaymentGateways;
import com.kirana.payment.PaymentUnavailableException;
import com.kirana.repository.OrderRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.data.domain.Limit;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * Background settlement of unpaid orders (D47):
 *  - reconciler: orders unpaid for more than reconcile-after are checked with the gateway, which
 *    catches "paid, but the browser never told us" (closed tab, lost network, timeout).
 *  - expiry: orders past their payment window are closed and their stock released.
 *  - re-check (Stage 6c): recently closed orders are asked about once more, which catches money
 *    that arrived after the close when the webhook never came (G1).
 *
 * Known gap (Stage 8): with several app instances, every instance runs these jobs. The
 * conditional transitions keep that correct (only one settles each order), but the work is
 * duplicated. A distributed lock or SELECT ... FOR UPDATE SKIP LOCKED fixes that later.
 */
@Component
@ConditionalOnProperty(name = "kirana.payment.jobs-enabled", havingValue = "true", matchIfMissing = true)
public class PaymentJobs {

    private static final Logger log = LoggerFactory.getLogger(PaymentJobs.class);
    private static final Limit BATCH = Limit.of(50);

    private final OrderRepository orders;
    private final CheckoutSaga saga;
    private final PaymentGateways gateways;
    private final PaymentProperties props;

    public PaymentJobs(OrderRepository orders, CheckoutSaga saga, PaymentGateways gateways, PaymentProperties props) {
        this.orders = orders;
        this.gateways = gateways;
        this.saga = saga;
        this.props = props;
    }

    /** Per gateway: one that sends us webhooks needs only a slow safety net (6c). */
    @Scheduled(fixedDelay = 30_000, initialDelay = 30_000)
    public void reconcile() {
        for (PaymentGateway g : gateways.all()) {
            Duration after = g.receivesWebhooks() ? props.reconcileAfter() : props.reconcileAfterWithoutWebhooks();
            for (Long id : orders.findUnsettledIds(g.id(), Instant.now().minus(after), BATCH)) {
                settle("reconcile", id, () -> saga.reconcile(id));
            }
        }
    }

    @Scheduled(fixedDelay = 30_000, initialDelay = 45_000)
    public void expire() {
        for (Long id : orders.findExpiredIds(Instant.now(), BATCH)) {
            settle("expire", id, () -> saga.expire(id));
        }
    }

    @Scheduled(fixedDelay = 120_000, initialDelay = 60_000)
    public void recheckClosed() {
        for (Long id : orders.findRecentlyClosedIds(Instant.now().minus(props.recheckClosedFor()), BATCH)) {
            settle("re-check", id, () -> saga.recheckClosed(id));
        }
    }

    private void settle(String job, Long orderId, Runnable step) {
        try {
            step.run();
        } catch (PaymentUnavailableException e) {
            log.warn("{}: order {} left for the next run, gateway unavailable: {}", job, orderId, e.getMessage());
        } catch (RuntimeException e) {
            log.error("{}: order {} failed", job, orderId, e);
        }
    }
}
