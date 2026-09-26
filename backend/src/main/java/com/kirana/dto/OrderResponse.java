package com.kirana.dto;

import java.time.Instant;
import java.util.List;

import com.kirana.entity.OrderStatus;

public record OrderResponse(Long id, OrderStatus status, double total, Instant createdAt, List<OrderItemResponse> items) {
}
