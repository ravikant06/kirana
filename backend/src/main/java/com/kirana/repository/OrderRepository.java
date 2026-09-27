package com.kirana.repository;

import java.util.List;
import java.util.Optional;

import com.kirana.entity.Order;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;

/**
 * Orders are always loaded with their lines in one query (JOIN FETCH), which removed the
 * Stage 1 N+1 (P4). Not usable with pagination: a fetch join on a collection makes Hibernate
 * page in memory. If order history is ever paged, fetch order IDs first, then their lines.
 */
public interface OrderRepository extends JpaRepository<Order, Long> {

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
            where o.id = :id and o.user.id = :userId
            """)
    Optional<Order> findWithItemsByIdAndUserId(Long id, Long userId);
}
