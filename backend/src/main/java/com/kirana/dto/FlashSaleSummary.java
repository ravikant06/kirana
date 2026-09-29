package com.kirana.dto;

/** One active flash sale, for the shop's strip. remaining = units left at the gate. */
public record FlashSaleSummary(Long productId, String name, double price, long remaining) {
}
