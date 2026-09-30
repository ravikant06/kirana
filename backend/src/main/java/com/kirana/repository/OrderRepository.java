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

    /** Unpaid orders that have a gateway order and are older than :before (for the reconciler). */
    @Query("""
            select o.id from Order o
            where o.status = com.kirana.entity.OrderStatus.CREATED and o.gatewayOrderId is not null
              and o.createdAt < :before
            order by o.id
            """)
    List<Long> findUnsettledIds(Instant before, Limit limit);

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
