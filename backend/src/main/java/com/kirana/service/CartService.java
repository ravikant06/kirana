package com.kirana.service;

import java.util.List;
import java.util.Map;

import com.kirana.dto.CartItemResponse;
import com.kirana.dto.CartLimits;
import com.kirana.dto.CartResponse;
import com.kirana.entity.Cart;
import com.kirana.entity.CartItem;
import com.kirana.exception.InvalidFieldException;
import com.kirana.exception.NotFoundException;
import com.kirana.mapper.CartMapper;
import com.kirana.repository.CartRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * Stock is not checked here: a cart is a wish list, and stock can change before checkout.
 * Lines whose product was deleted are hidden, so the total shown is the total charged.
 */
@Service
public class CartService {

    private final CartRepository carts;
    private final UserService users;
    private final ProductService products;
    private final ImageService images;

    public CartService(CartRepository carts, UserService users, ProductService products, ImageService images) {
        this.carts = carts;
        this.users = users;
        this.products = products;
        this.images = images;
    }

    /** Reading never creates a cart. A user with no cart sees an empty one. */
    @Transactional(readOnly = true)
    public CartResponse get(Long userId) {
        users.require(userId);
        return carts.findWithItemsByUserId(userId).map(this::toResponse).orElseGet(CartService::empty);
    }

    /**
     * Adds to the existing quantity if the product is already in the cart.
     * R4/R5: two statements that each decide in the database, no read-then-write in Java:
     * create the cart if absent, then insert the line or add to it. A double click, or two
     * tabs, now add twice, and a shopper's first two clicks no longer collide on the cart.
     */
    @Transactional
    public CartResponse add(Long userId, Long productId, int quantity) {
        users.require(userId);
        products.requireLive(productId);
        carts.createIfAbsent(userId);
        Long cartId = carts.findIdByUserId(userId)
                .orElseThrow(() -> new IllegalStateException("Cart missing right after creation for user " + userId));
        if (carts.addOrIncrement(cartId, productId, quantity, CartLimits.MAX_QUANTITY) == 0) {
            throw new InvalidFieldException("quantity", "at most %d of one product per cart".formatted(CartLimits.MAX_QUANTITY));
        }
        return carts.findWithItemsByUserId(userId).map(this::toResponse)
                .orElseThrow(() -> new IllegalStateException("Cart missing for user " + userId));
    }

    @Transactional
    public CartResponse update(Long userId, Long productId, int quantity) {
        users.require(userId);
        Cart cart = carts.findWithItemsByUserId(userId).orElseThrow(() -> notInCart(productId));
        CartItem item = cart.findItem(productId).orElseThrow(() -> notInCart(productId));
        item.setQuantity(quantity);
        return toResponse(cart);
    }

    /** Idempotent: removing a product that is not in the cart just returns the cart. */
    @Transactional
    public CartResponse remove(Long userId, Long productId) {
        users.require(userId);
        return carts.findWithItemsByUserId(userId)
                .map(cart -> {
                    cart.findItem(productId).ifPresent(cart::removeItem);
                    return toResponse(cart);
                })
                .orElseGet(CartService::empty);
    }

    private CartResponse toResponse(Cart cart) {
        List<CartItem> live = cart.getItems().stream().filter(i -> !i.getProduct().isDeleted()).toList();
        Map<Long, String> thumbnails = images.thumbnailUrls(live.stream().map(i -> i.getProduct().getId()).toList());
        List<CartItemResponse> items = live.stream()
                .map(i -> CartMapper.toItem(i, thumbnails.get(i.getProduct().getId())))
                .toList();
        return CartMapper.toResponse(items);
    }

    private static CartResponse empty() {
        return new CartResponse(List.of(), 0);
    }

    private static NotFoundException notInCart(Long productId) {
        return new NotFoundException("Product %d is not in your cart".formatted(productId));
    }
}
