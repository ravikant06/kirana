package com.kirana.repository;

import java.util.List;
import java.util.Optional;

import com.kirana.entity.Order;
import org.springframework.data.jpa.repository.JpaRepository;

/** Plain derived queries: items are NOT fetched. That is deliberate (Stage 1 experiments 4 and 5). */
public interface OrderRepository extends JpaRepository<Order, Long> {

    List<Order> findByUserIdOrderByCreatedAtDescIdDesc(Long userId);

    Optional<Order> findByIdAndUserId(Long id, Long userId);
}
