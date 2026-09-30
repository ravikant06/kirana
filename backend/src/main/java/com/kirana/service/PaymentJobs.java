package com.kirana.service;

import java.time.Instant;

import com.kirana.config.PaymentProperties;
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
    private final PaymentProperties props;

    public PaymentJobs(OrderRepository orders, CheckoutSaga saga, PaymentProperties props) {
        this.orders = orders;
        this.saga = saga;
        this.props = props;
    }

    @Scheduled(fixedDelay = 30_000, initialDelay = 30_000)
    public void reconcile() {
        for (Long id : orders.findUnsettledIds(Instant.now().minus(props.reconcileAfter()), BATCH)) {
            settle("reconcile", id, () -> saga.reconcile(id));
        }
    }

    @Scheduled(fixedDelay = 30_000, initialDelay = 45_000)
    public void expire() {
        for (Long id : orders.findExpiredIds(Instant.now(), BATCH)) {
            settle("expire", id, () -> saga.expire(id));
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
