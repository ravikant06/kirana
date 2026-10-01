package com.kirana.dto;

import java.time.Instant;
import java.util.List;

import com.kirana.entity.OrderStatus;

/**
 * status CREATED means "awaiting payment" (stock held until paymentDueAt).
 * closedReason is set when an order ends CANCELLED or FAILED.
 * latePaymentId is set when money arrived after the order closed (a refund is due, Stage 6).
 */
public record OrderResponse(
        Long id,
        OrderStatus status,
        double total,
        Instant createdAt,
        List<OrderItemResponse> items,
        String paymentProvider,
        Instant paymentDueAt,
        Instant paidAt,
        String closedReason,
        String latePaymentId) {
}
