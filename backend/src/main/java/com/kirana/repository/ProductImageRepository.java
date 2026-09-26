package com.kirana.repository;

import java.util.Collection;
import java.util.List;
import java.util.Optional;

import com.kirana.entity.ImageStatus;
import com.kirana.entity.ProductImage;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;

public interface ProductImageRepository extends JpaRepository<ProductImage, Long> {

    Optional<ProductImage> findByIdAndProductId(Long id, Long productId);

    List<ProductImage> findByProductIdAndStatusOrderByPositionAscIdAsc(Long productId, ImageStatus status);

    /** Images for a whole page of products in one query, to build thumbnails without N+1. */
    List<ProductImage> findByProductIdInAndStatusOrderByPositionAscIdAsc(Collection<Long> productIds, ImageStatus status);

    @Query("select coalesce(max(i.position), -1) from ProductImage i where i.product.id = :productId and i.status = :status")
    int findMaxPosition(Long productId, ImageStatus status);
}
