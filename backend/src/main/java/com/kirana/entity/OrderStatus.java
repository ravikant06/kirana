package com.kirana.entity;

/** Matches the CHECK constraint on orders.status. Only CREATED is used in Stage 1. */
public enum OrderStatus {
    CREATED,
    PAID,
    FAILED,
    CANCELLED
}
