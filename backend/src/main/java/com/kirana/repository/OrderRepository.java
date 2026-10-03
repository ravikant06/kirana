package com.kirana.repository;

import java.time.Instant;
import java.util.List;
import java.util.Optional;

import com.kirana.entity.Order;
import com.kirana.entity.OrderStatus;
import org.springframework.data.domain.Limit;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;

/**
 * Orders are always loaded with their lines in one query (JOIN FETCH), which removed the
 * Stage 1 N+1 (P4). Not usable with pagination: a fetch join on a collection makes Hibernate
 * page in memory. If order history is ever paged, fetch order IDs first, then their lines.
 * Order history also fetches refunds (6d) in the same query: still two statements in total.
 * Joining a List (items) and a Set (refunds) is allowed; two Lists ("bags") would not be.
 */
public interface OrderRepository extends JpaRepository<Order, Long> {

    // ---------------------------------------------------------------- Stage 5 state transitions
    // Each is one conditional UPDATE: it succeeds (returns 1) only if the order is still CREATED.
    // Whoever gets 1 owns the transition and its side effects (e.g. releasing stock); everyone
    // else gets 0 and does nothing. That is what makes the saga steps safe to run twice, and
    // safe when the verify call, the reconciler and the expiry job race each other.

    @Modifying
    @Query("""
            update Order o set o.paymentProvider = :provider, o.gatewayOrderId = :gatewayOrderId, o.updatedAt = :now
            where o.id = :id and o.status = com.kirana.entity.OrderStatus.CREATED and o.gatewayOrderId is null
            """)
    int attachGatewayOrder(Long id, String provider, String gatewayOrderId, Instant now);

    @Modifying
    @Query("""
            update Order o set o.status = com.kirana.entity.OrderStatus.PAID, o.paymentId = :paymentId,
                o.paidAt = :now, o.updatedAt = :now
            where o.id = :id and o.status = com.kirana.entity.OrderStatus.CREATED
            """)
    int markPaid(Long id, String paymentId, Instant now);

    @Modifying
    @Query("""
            update Order o set o.status = :to, o.closedReason = :reason, o.updatedAt = :now
            where o.id = :id and o.status = com.kirana.entity.OrderStatus.CREATED
            """)
    int close(Long id, OrderStatus to, String reason, Instant now);

    /**
     * Stage 6c: records a payment that arrived after the order closed. Once per order: the
     * caller that gets 1 raises the refund event; duplicates (webhook retries, the re-check job,
     * the browser) get 0.
     */
    @Modifying
    @Query("""
            update Order o set o.latePaymentId = :paymentId, o.updatedAt = :now
            where o.id = :id and o.latePaymentId is null
              and o.status in (com.kirana.entity.OrderStatus.CANCELLED, com.kirana.entity.OrderStatus.FAILED)
            """)
    int markLatePayment(Long id, String paymentId, Instant now);

    /** Stage 6c: the gateway does not know this order yet; keep holding it a little longer. */
    @Modifying
    @Query("""
            update Order o set o.paymentDueAt = :due, o.updatedAt = :now
            where o.id = :id and o.status = com.kirana.entity.OrderStatus.CREATED
            """)
    int postponeDue(Long id, Instant due, Instant now);

    Optional<Order> findByPaymentProviderAndGatewayOrderId(String paymentProvider, String gatewayOrderId);

    /**
     * Stage 6c safety net for lost webhooks: orders closed recently that had a gateway order and
     * no late payment recorded yet. The gateway is asked whether money arrived after all.
     */
    @Query("""
            select o.id from Order o
            where o.status in (com.kirana.entity.OrderStatus.CANCELLED, com.kirana.entity.OrderStatus.FAILED)
              and o.gatewayOrderId is not null and o.latePaymentId is null and o.updatedAt > :closedAfter
            order by o.id
            """)
    List<Long> findRecentlyClosedIds(Instant closedAfter, Limit limit);

    /** One gateway's unpaid orders that have a gateway order and are older than :before (reconciler). */
    @Query("""
            select o.id from Order o
            where o.status = com.kirana.entity.OrderStatus.CREATED and o.gatewayOrderId is not null
              and o.paymentProvider = :provider and o.createdAt < :before
            order by o.id
            """)
    List<Long> findUnsettledIds(String provider, Instant before, Limit limit);

    /** Unpaid orders whose payment window has passed (for the expiry job). */
    @Query("""
            select o.id from Order o
            where o.status = com.kirana.entity.OrderStatus.CREATED and o.paymentDueAt < :now
            order by o.paymentDueAt
            """)
    List<Long> findExpiredIds(Instant now, Limit limit);

    @Query("""
            select o from Order o
            left join fetch o.items
            left join fetch o.refunds
            where o.user.id = :userId
            order by o.createdAt desc, o.id desc
            """)
    List<Order> findWithItemsByUserId(Long userId);

    @Query("""
            select o from Order o
            left join fetch o.items
            where o.id = :id
            """)
    Optional<Order> findWithItemsById(Long id);

    @Query("""
            select o from Order o
            left join fetch o.items
            where o.id = :id and o.user.id = :userId
            """)
    Optional<Order> findWithItemsByIdAndUserId(Long id, Long userId);
}
