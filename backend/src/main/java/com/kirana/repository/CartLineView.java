package com.kirana.repository;

/** A cart line as the flash-sale gate needs it: one light query, no entities. */
public record CartLineView(Long productId, String productName, int quantity) {
}
