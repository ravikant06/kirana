package com.kirana.controller;

import com.kirana.idempotency.IdempotentRequests;
import com.kirana.dto.AddCartItemRequest;
import com.kirana.dto.CartResponse;
import com.kirana.dto.UpdateCartItemRequest;
import com.kirana.service.CartService;
import jakarta.validation.Valid;
import org.springframework.http.ResponseEntity;
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
    private final IdempotentRequests idempotency;

    public CartController(CartService carts, IdempotentRequests idempotency) {
        this.carts = carts;
        this.idempotency = idempotency;
    }

    @GetMapping
    public CartResponse get(@RequestHeader(Headers.USER_ID) Long userId) {
        return carts.get(userId);
    }

    /** Stage 7: adds to the quantity, so a retry must not run twice: Idempotency-Key required. */
    @PostMapping("/items")
    public ResponseEntity<?> add(@RequestHeader(Headers.USER_ID) Long userId,
                                 @RequestHeader(name = IdempotentRequests.HEADER, required = false) String key,
                                 @Valid @RequestBody AddCartItemRequest req) {
        return idempotency.execute(userId, key, "POST /cart/items", req,
                () -> carts.add(userId, req.productId(), req.quantity()));
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
