package com.kirana.dto;

import java.time.Instant;

public record InventoryResponse(Long productId, int quantity, Instant updatedAt) {
}
