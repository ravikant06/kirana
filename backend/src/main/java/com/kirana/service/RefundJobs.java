package com.kirana.service;

import java.time.Instant;

import com.kirana.payment.PaymentUnavailableException;
import com.kirana.repository.RefundRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.data.domain.Limit;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * Stage 6d. The refund consumer tries the gateway once, right away. This job finishes what that
 * could not: REQUESTED refunds whose attempt failed (gateway down, timed out) are tried again
 * once their lease runs out, and PENDING refunds whose webhook never came are polled.
 */
@Component
@ConditionalOnProperty(name = "kirana.payment.jobs-enabled", havingValue = "true", matchIfMissing = true)
public class RefundJobs {

    private static final Logger log = LoggerFactory.getLogger(RefundJobs.class);
    private static final Limit BATCH = Limit.of(50);

    private final RefundRepository refunds;
    private final RefundService service;

    public RefundJobs(RefundRepository refunds, RefundService service) {
        this.refunds = refunds;
        this.service = service;
    }

    @Scheduled(fixedDelay = 15_000, initialDelay = 20_000)
    public void retryRequested() {
        for (Long id : refunds.findClaimableIds(Instant.now(), BATCH)) {
            log.info("Refund job: trying refund {} again", id);
            service.execute(id);
        }
    }

    @Scheduled(fixedDelay = 60_000, initialDelay = 40_000)
    public void pollPending() {
        for (Long id : refunds.findStalePendingIds(Instant.now().minusSeconds(60), BATCH)) {
            try {
                service.poll(id);
            } catch (PaymentUnavailableException e) {
                log.warn("Refund job: refund {} not checked, gateway unavailable: {}", id, e.getMessage());
            }
        }
    }
}
