package com.kirana.mapper;

import com.kirana.dto.OrderItemResponse;
import com.kirana.dto.OrderResponse;
import com.kirana.entity.Order;
import com.kirana.entity.OrderItem;

public final class OrderMapper {

    private OrderMapper() {
    }

    /** Touches order.getItems() and getRefunds(): lazy collections. Needs an open session (experiment 4). */
    public static OrderResponse toResponse(Order order) {
        return new OrderResponse(order.getId(), order.getStatus(), order.getTotal(), order.getCreatedAt(),
                order.getItems().stream().map(OrderMapper::toItem).toList(),
                order.getPaymentProvider(), order.getPaymentDueAt(), order.getPaidAt(), order.getClosedReason(),
                order.getLatePaymentId(),
                order.getRefunds().stream().map(r -> r.getStatus().name()).findFirst().orElse(null));
    }

    private static OrderItemResponse toItem(OrderItem item) {
        // getProduct().getId() reads the FK from the proxy; it does not load the product.
        return new OrderItemResponse(item.getProduct().getId(), item.getProductName(), item.getUnitPrice(),
                item.getQuantity(), item.getLineTotal());
    }
}
