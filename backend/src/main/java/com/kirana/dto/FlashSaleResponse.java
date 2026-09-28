package com.kirana.dto;

/** remaining: units left at the gate, or null when no sale is armed. */
public record FlashSaleResponse(Long productId, boolean active, Long remaining) {
}
