package com.kirana.mapper;

import java.util.List;

import com.kirana.dto.CartItemResponse;
import com.kirana.dto.CartResponse;
import com.kirana.entity.CartItem;
import com.kirana.entity.Product;

public final class CartMapper {

    private CartMapper() {
    }

    /** Prices are read from the product now, not stored on the line (D4). */
    public static CartItemResponse toItem(CartItem item, String thumbnailUrl) {
        Product p = item.getProduct();
        return new CartItemResponse(p.getId(), p.getName(), p.getPrice(), item.getQuantity(),
                p.getPrice() * item.getQuantity(), thumbnailUrl);
    }

    public static CartResponse toResponse(List<CartItemResponse> items) {
        double total = items.stream().mapToDouble(CartItemResponse::lineTotal).sum();
        return new CartResponse(items, total);
    }
}
