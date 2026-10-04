package com.kirana.mapper;

import java.util.List;

import com.kirana.cache.ProductSnapshot;
import com.kirana.dto.ImageResponse;
import com.kirana.dto.ProductDetail;
import com.kirana.dto.ProductSummary;
import com.kirana.entity.Product;
import com.kirana.entity.ProductImage;

/** URLs are passed in: signing them is the storage layer's job, not the mapper's. */
public final class ProductMapper {

    private ProductMapper() {
    }

    public static ProductSummary toSummary(Product p, int stock, String thumbnailUrl) {
        return new ProductSummary(p.getId(), p.getName(), p.getPrice(), stock, thumbnailUrl, p.getCategory());
    }

    public static ProductDetail toDetail(Product p, int stock, List<ImageResponse> images) {
        return new ProductDetail(p.getId(), p.getName(), p.getDescription(), p.getPrice(), stock,
                images, p.getCreatedAt(), p.getUpdatedAt(), p.getVersion(), p.getCategory());
    }

    public static ProductDetail toDetail(ProductSnapshot p, int stock, List<ImageResponse> images) {
        return new ProductDetail(p.id(), p.name(), p.description(), p.price(), stock,
                images, p.createdAt(), p.updatedAt(), p.version(), p.category());
    }

    public static ProductSnapshot toSnapshot(Product p, List<ProductImage> activeImages) {
        return new ProductSnapshot(p.getId(), p.getName(), p.getDescription(), p.getPrice(), p.getCreatedAt(),
                p.getUpdatedAt(), p.getVersion(), activeImages.stream()
                .map(i -> new ProductSnapshot.Image(i.getId(), i.getObjectKey(), i.getContentType(), i.getSizeBytes(), i.getPosition()))
                .toList(), p.getCategory());
    }

    public static ImageResponse toImage(ProductSnapshot.Image img, String url) {
        return new ImageResponse(img.id(), url, img.contentType(), img.sizeBytes(), img.position());
    }

    public static ImageResponse toImage(ProductImage img, String url) {
        return new ImageResponse(img.getId(), url, img.getContentType(), img.getSizeBytes(), img.getPosition());
    }
}
