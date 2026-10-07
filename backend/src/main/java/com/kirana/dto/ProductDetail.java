package com.kirana.dto;

import java.time.Instant;
import java.util.List;

public record ProductDetail(
        Long id,
        String name,
        String description,
        double price,
        int stock,
        List<ImageResponse> images,
        Instant createdAt,
        Instant updatedAt,
        long version,
        String category) {
}
