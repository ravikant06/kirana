package com.kirana.dto;

import java.util.List;

public record CartResponse(List<CartItemResponse> items, double total) {
}
