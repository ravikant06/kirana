package com.kirana.dto;

public record ProductSummary(Long id, String name, double price, int stock, String thumbnailUrl, String category) {
}
