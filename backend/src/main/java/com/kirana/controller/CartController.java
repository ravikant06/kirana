package com.kirana.controller;

import com.kirana.dto.AddCartItemRequest;
import com.kirana.dto.CartResponse;
import com.kirana.dto.UpdateCartItemRequest;
import com.kirana.service.CartService;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/cart")
public class CartController {

    private final CartService carts;

    public CartController(CartService carts) {
        this.carts = carts;
    }

    @GetMapping
    public CartResponse get(@RequestHeader(Headers.USER_ID) Long userId) {
        return carts.get(userId);
    }

    @PostMapping("/items")
    public CartResponse add(@RequestHeader(Headers.USER_ID) Long userId, @Valid @RequestBody AddCartItemRequest req) {
        return carts.add(userId, req.productId(), req.quantity());
    }

    @PutMapping("/items/{productId}")
    public CartResponse update(@RequestHeader(Headers.USER_ID) Long userId, @PathVariable Long productId,
                               @Valid @RequestBody UpdateCartItemRequest req) {
        return carts.update(userId, productId, req.quantity());
    }

    @DeleteMapping("/items/{productId}")
    public CartResponse remove(@RequestHeader(Headers.USER_ID) Long userId, @PathVariable Long productId) {
        return carts.remove(userId, productId);
    }
}
