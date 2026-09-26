package com.kirana.repository;

import java.util.Optional;

import com.kirana.entity.Product;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;

/**
 * Soft delete (D8) is explicit: callers that want live products use the *DeletedAtIsNull
 * methods. Plain findById still sees deleted products, which old orders and carts need.
 */
public interface ProductRepository extends JpaRepository<Product, Long> {

    Optional<Product> findByIdAndDeletedAtIsNull(Long id);

    Page<Product> findAllByDeletedAtIsNull(Pageable pageable);
}
