package com.kirana.service;

import java.time.Duration;
import java.time.Instant;
import java.util.List;

import com.kirana.entity.Order;
import com.kirana.entity.Refund;
import com.kirana.entity.RefundStatus;
import com.kirana.payment.GatewayRefund;
import com.kirana.payment.PaymentGateway;
import com.kirana.payment.PaymentGateways;
import com.kirana.payment.PaymentUnavailableException;
import com.kirana.payment.RazorpayStyleGateway;
import com.kirana.repository.OrderRepository;
import com.kirana.repository.RefundRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * Stage 6d: returning money for a late payment (G2), exactly once.
 *
 * The gateway's refund call is NOT idempotent: two calls can make two refunds. And a call that
 * times out may still have worked. So refunding is split in two:
 *
 *   request()  record the intent: one REQUESTED row per payment (payment_id is UNIQUE).
 *              Database only; runs in the Kafka consumer's transaction.
 *   execute()  claim the row (a lease, so no one else calls the gateway meanwhile), then
 *                1. ask the gateway which refunds this payment already has  <- the timeout case
 *                2. only if none: create one
 *              Gateway down or timing out: the row stays REQUESTED; RefundJobs tries again
 *              after the lease runs out, and step 1 finds a refund that "failed" but worked.
 *
 * The gateway finishes refunds later (PENDING -> PROCESSED). It tells us by webhook (payments.v1,
 * PaymentEventsListener -> settle), and RefundJobs polls PENDING refunds in case it doesn't.
 */
@Service
public class RefundService {

    private static final Logger log = LoggerFactory.getLogger(RefundService.class);

    /** Longer than the slowest gateway attempt (Stage 5 timeouts and retries), so a lease never runs out mid-call. */
    static final Duration LEASE = Duration.ofSeconds(30);

    private final RefundRepository refunds;
    private final OrderRepository orders;
    private final PaymentGateways gateways;
    private final TransactionTemplate tx;

    public RefundService(RefundRepository refunds, OrderRepository orders, PaymentGateways gateways, TransactionTemplate tx) {
        this.refunds = refunds;
        this.orders = orders;
        this.gateways = gateways;
        this.tx = tx;
    }

    /** Records that this payment must be refunded. Returns the refund id, or null if it already was. */
    @Transactional(propagation = Propagation.REQUIRED)
    public Long request(long orderId, String paymentId, String provider) {
        if (refunds.existsByPaymentId(paymentId)) {
            log.info("Refund for payment {} (order {}) already recorded", paymentId, orderId);
            return null;
        }
        Order order = orders.findById(orderId).orElseThrow();
        Refund refund = refunds.saveAndFlush(new Refund(order, paymentId, provider, order.getTotal()));
        log.info("Refund {}: REQUESTED for order {} payment {} ({})", refund.getId(), orderId, paymentId, order.getTotal());
        return refund.getId();
    }

    /** Calls the gateway for a REQUESTED refund, if no one else is. Never throws for gateway trouble. */
    public void execute(long refundId) {
        Instant now = Instant.now();
        Integer claimed = tx.execute(s -> refunds.claim(refundId, now, now.plus(LEASE)));
        if (claimed == null || claimed == 0) {
            return; // not REQUESTED any more, or another thread/instance holds the lease
        }
        Refund refund = refunds.findById(refundId).orElseThrow();
        PaymentGateway gateway = gateways.of(refund.getProvider());
        try {
            // 1. Ask first. A previous attempt may have refunded and then timed out before telling us.
            List<GatewayRefund> existing = gateway.fetchRefunds(refund.getPaymentId()).stream()
                    .filter(r -> r.state() != GatewayRefund.State.FAILED)
                    .toList();
            GatewayRefund atGateway;
            if (!existing.isEmpty()) {
                atGateway = existing.get(0);
                log.warn("Refund {}: the gateway already has {} for payment {} (an earlier attempt worked); not refunding again",
                        refundId, atGateway.refundId(), refund.getPaymentId());
            } else {
                // 2. Only now create it. Not retried automatically (see ResilientPaymentGateway).
                atGateway = gateway.refund(refund.getPaymentId(), CheckoutSaga.toPaise(refund.getAmount()),
                        "kirana-refund-" + refundId);
                log.info("Refund {}: gateway created {} for payment {}", refundId, atGateway.refundId(), refund.getPaymentId());
            }
            RefundStatus to = atGateway.state() == GatewayRefund.State.PROCESSED ? RefundStatus.PROCESSED : RefundStatus.PENDING;
            Instant at = Instant.now();
            tx.executeWithoutResult(s -> refunds.markAtGateway(refundId, atGateway.refundId(), to, at,
                    to == RefundStatus.PROCESSED ? at : null));
        } catch (PaymentUnavailableException e) {
            // Includes "timed out": the refund may or may not exist. Step 1 of the next attempt finds out.
            tx.executeWithoutResult(s -> refunds.recordError(refundId, e.getMessage(), Instant.now()));
            log.warn("Refund {}: gateway unavailable ({}); will ask again after {} s", refundId, e.getMessage(), LEASE.toSeconds());
        } catch (RazorpayStyleGateway.GatewayRejectedException e) {
            tx.executeWithoutResult(s -> refunds.markFailed(refundId, e.getMessage(), Instant.now()));
            log.error("Refund {}: the gateway refused it; a person must look: {}", refundId, e.getMessage());
        }
    }

    /** The gateway's final word about a payment's refund (webhook or polling). Idempotent. */
    @Transactional(propagation = Propagation.REQUIRED)
    public boolean settle(String paymentId, String gatewayRefundId, GatewayRefund.State state) {
        RefundStatus to = switch (state) {
            case PROCESSED -> RefundStatus.PROCESSED;
            case FAILED -> RefundStatus.FAILED;
            case PENDING -> null;
        };
        if (to == null) {
            return false;
        }
        Instant now = Instant.now();
        boolean changed = refunds.settle(paymentId, gatewayRefundId, to, now,
                to == RefundStatus.PROCESSED ? now : null) == 1;
        if (changed) {
            log.info("Refund for payment {}: {} at the gateway ({})", paymentId, to, gatewayRefundId);
        }
        return changed;
    }

    /** Safety net for a lost refund webhook: ask the gateway how a PENDING refund is doing. */
    public void poll(long refundId) {
        Refund refund = refunds.findById(refundId).orElse(null);
        if (refund == null || refund.getStatus() != RefundStatus.PENDING) {
            return;
        }
        gateways.of(refund.getProvider()).fetchRefunds(refund.getPaymentId()).stream()
                .filter(r -> r.refundId().equals(refund.getGatewayRefundId()))
                .findFirst()
                .ifPresent(r -> tx.executeWithoutResult(s -> settle(refund.getPaymentId(), r.refundId(), r.state())));
    }
}
