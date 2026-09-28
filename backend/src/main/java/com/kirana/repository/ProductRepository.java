package com.kirana.repository;

import java.util.Optional;

import com.kirana.entity.Product;
import jakarta.persistence.LockModeType;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;

/**
 * Soft delete (D8) is explicit: callers that want live products use the *DeletedAtIsNull
 * methods. Plain findById still sees deleted products, which old orders and carts need.
 */
public interface ProductRepository extends JpaRepository<Product, Long> {

    Optional<Product> findByIdAndDeletedAtIsNull(Long id);

    Page<Product> findAllByDeletedAtIsNull(Pageable pageable);

    /**
     * R6: a live product with its row locked (SELECT ... FOR UPDATE) until the transaction ends.
     * Used to serialize work on one product's images; other products are unaffected.
     */
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select p from Product p where p.id = :id and p.deletedAt is null")
    Optional<Product> lockLive(Long id);
}
