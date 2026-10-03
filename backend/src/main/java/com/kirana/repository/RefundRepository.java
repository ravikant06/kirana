package com.kirana.repository;

import java.time.Instant;
import java.util.List;

import com.kirana.entity.Refund;
import com.kirana.entity.RefundStatus;
import org.springframework.data.domain.Limit;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;

/**
 * Stage 6d. Like orders, every state change is one conditional UPDATE: the caller that gets 1
 * owns the step. claim() adds a lease, because the step it guards (calling the gateway) is not
 * idempotent and must never run twice at the same time.
 */
public interface RefundRepository extends JpaRepository<Refund, Long> {

    boolean existsByPaymentId(String paymentId);

    /** Take the right to call the gateway for this refund until :until. 0 = someone else has it. */
    @Modifying
    @Query("""
            update Refund r set r.claimedUntil = :until, r.attempts = r.attempts + 1, r.updatedAt = :now
            where r.id = :id and r.status = com.kirana.entity.RefundStatus.REQUESTED
              and (r.claimedUntil is null or r.claimedUntil < :now)
            """)
    int claim(Long id, Instant now, Instant until);

    /** The gateway has the refund now (we created it, or found it). */
    @Modifying
    @Query("""
            update Refund r set r.status = :to, r.gatewayRefundId = :gatewayRefundId, r.claimedUntil = null,
                r.lastError = null, r.updatedAt = :now, r.processedAt = :processedAt
            where r.id = :id and r.status = com.kirana.entity.RefundStatus.REQUESTED
            """)
    int markAtGateway(Long id, String gatewayRefundId, RefundStatus to, Instant now, Instant processedAt);

    /**
     * The gateway's final word (webhook or polling), found by payment id: it can arrive before we
     * learned the gateway's refund id (our create call timed out but worked).
     */
    @Modifying
    @Query("""
            update Refund r set r.status = :to, r.gatewayRefundId = coalesce(r.gatewayRefundId, :gatewayRefundId),
                r.claimedUntil = null, r.updatedAt = :now, r.processedAt = :processedAt
            where r.paymentId = :paymentId
              and r.status in (com.kirana.entity.RefundStatus.REQUESTED, com.kirana.entity.RefundStatus.PENDING)
            """)
    int settle(String paymentId, String gatewayRefundId, RefundStatus to, Instant now, Instant processedAt);

    /** A failed attempt that may be retried: the lease runs out and the job tries again. */
    @Modifying
    @Query("update Refund r set r.lastError = :error, r.updatedAt = :now where r.id = :id")
    int recordError(Long id, String error, Instant now);

    @Modifying
    @Query("""
            update Refund r set r.status = com.kirana.entity.RefundStatus.FAILED, r.lastError = :error,
                r.claimedUntil = null, r.updatedAt = :now
            where r.id = :id and r.status = com.kirana.entity.RefundStatus.REQUESTED
            """)
    int markFailed(Long id, String error, Instant now);

    /** REQUESTED refunds nobody is working on (for RefundJobs). */
    @Query("""
            select r.id from Refund r
            where r.status = com.kirana.entity.RefundStatus.REQUESTED and (r.claimedUntil is null or r.claimedUntil < :now)
            order by r.id
            """)
    List<Long> findClaimableIds(Instant now, Limit limit);

    /** PENDING refunds not settled for a while: ask the gateway (lost webhook safety net). */
    @Query("""
            select r.id from Refund r
            where r.status = com.kirana.entity.RefundStatus.PENDING and r.updatedAt < :before
            order by r.id
            """)
    List<Long> findStalePendingIds(Instant before, Limit limit);
}
