package com.kirana.dto;

public record CartItemResponse(
        Long productId,
        String productName,
        double unitPrice,
        int quantity,
        double lineTotal,
        String thumbnailUrl) {
}
