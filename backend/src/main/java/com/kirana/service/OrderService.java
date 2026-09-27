package com.kirana.service;

import java.util.List;

import com.kirana.dto.OrderResponse;
import com.kirana.entity.Cart;
import com.kirana.entity.CartItem;
import com.kirana.entity.Inventory;
import com.kirana.entity.Order;
import com.kirana.entity.Product;
import com.kirana.entity.User;
import com.kirana.exception.ConflictException;
import com.kirana.exception.NotFoundException;
import com.kirana.exception.OutOfStockException;
import com.kirana.mapper.OrderMapper;
import com.kirana.repository.CartRepository;
import com.kirana.repository.InventoryRepository;
import com.kirana.repository.OrderRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class OrderService {

    private final OrderRepository orders;
    private final CartRepository carts;
    private final InventoryRepository inventory;
    private final UserService users;

    public OrderService(OrderRepository orders, CartRepository carts, InventoryRepository inventory, UserService users) {
        this.orders = orders;
        this.carts = carts;
        this.inventory = inventory;
        this.users = users;
    }

    /**
     * Checkout: turn the cart into an order, subtract stock, empty the cart. All in one
     * transaction, so an out-of-stock line undoes the stock already subtracted for earlier lines.
     */
    @Transactional
    public OrderResponse place(Long userId) {
        User user = users.require(userId);
        Cart cart = carts.findWithItemsByUserId(userId).orElse(null);
        List<CartItem> lines = cart == null ? List.of()
                : cart.getItems().stream().filter(i -> !i.getProduct().isDeleted()).toList();
        if (lines.isEmpty()) {
            throw new ConflictException("Cart is empty", "Add something to your cart before placing an order");
        }

        Order order = new Order(user);
        for (CartItem line : lines) {
            Product product = line.getProduct();

            // NAIVE: Stage 3 will break this.
            // Read stock, check it, subtract, write. Nothing stops another checkout from reading
            // the same stock between our read and our write, so two buyers can both get the last unit.
            Inventory stock = inventory.findById(product.getId())
                    .orElseThrow(() -> new IllegalStateException("No inventory row for product " + product.getId()));
            if (stock.getQuantity() < line.getQuantity()) {
                throw new OutOfStockException(product.getName(), stock.getQuantity(), line.getQuantity());
            }
            stock.setQuantity(stock.getQuantity() - line.getQuantity());
            // Redundant inside a transaction (dirty checking writes it at commit). Kept explicit so
            // experiment 1 "without @Transactional" really writes each line's stock as it goes.
            inventory.save(stock);

            order.addLine(product, line.getQuantity());
        }

        orders.save(order);
        cart.clear(); // D4: checkout deletes the lines, keeps the cart row
        orders.flush();
        return OrderMapper.toResponse(order);
    }

    /** Two statements whatever the number of orders: the user check, then orders with their lines. */
    @Transactional(readOnly = true)
    public List<OrderResponse> list(Long userId) {
        users.require(userId);
        return orders.findWithItemsByUserId(userId).stream().map(OrderMapper::toResponse).toList();
    }

    /** Lines are fetched with the order, so mapping no longer depends on lazy loading. */
    @Transactional(readOnly = true)
    public OrderResponse get(Long userId, Long orderId) {
        users.require(userId);
        // Someone else's order is "not found", not "forbidden": don't confirm it exists.
        return orders.findWithItemsByIdAndUserId(orderId, userId)
                .map(OrderMapper::toResponse)
                .orElseThrow(() -> new NotFoundException("Order %d not found".formatted(orderId)));
    }
}
