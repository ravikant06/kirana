package com.kirana.repository;

import java.util.Optional;

import com.kirana.entity.Cart;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;

public interface CartRepository extends JpaRepository<Cart, Long> {

    /** Cart, its lines and their products in one query. */
    @Query("""
            select distinct c from Cart c
            left join fetch c.items i
            left join fetch i.product
            where c.user.id = :userId
            """)
    Optional<Cart> findWithItemsByUserId(Long userId);
}
