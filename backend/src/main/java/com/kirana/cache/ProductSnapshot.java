package com.kirana.cache;

import java.time.Instant;
import java.util.List;

/**
 * What is cached for a product detail page (key product:v1:{id}).
 * Deliberately NOT included: stock (changes on every order; read fresh by primary key) and
 * image URLs (signed URLs expire after 1 h, longer than a cached entry may live). Images are
 * cached as object keys and signed per response.
 */
public record ProductSnapshot(
        Long id,
        String name,
        String description,
        double price,
        Instant createdAt,
        Instant updatedAt,
        long version,
        List<Image> images) {

    public record Image(Long id, String objectKey, String contentType, Long sizeBytes, int position) {
    }
}
