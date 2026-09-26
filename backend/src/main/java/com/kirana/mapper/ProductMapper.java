package com.kirana.mapper;

import java.util.List;

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
        return new ProductSummary(p.getId(), p.getName(), p.getPrice(), stock, thumbnailUrl);
    }

    public static ProductDetail toDetail(Product p, int stock, List<ImageResponse> images) {
        return new ProductDetail(p.getId(), p.getName(), p.getDescription(), p.getPrice(), stock,
                images, p.getCreatedAt(), p.getUpdatedAt());
    }

    public static ImageResponse toImage(ProductImage img, String url) {
        return new ImageResponse(img.getId(), url, img.getContentType(), img.getSizeBytes(), img.getPosition());
    }
}
