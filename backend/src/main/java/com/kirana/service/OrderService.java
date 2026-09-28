package com.kirana.service;

import java.time.Instant;
import java.util.Comparator;
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
     *
     * R2: stock is taken with one atomic conditional UPDATE per line, so two buyers can never
     * both get the last unit, across any number of app instances. Concurrent buyers of one
     * product queue briefly on its row lock and still succeed while stock lasts.
     * Lines are processed in product-id order: carts (Tea, Rice) and (Rice, Tea) checking out
     * together would otherwise each lock one row and wait for the other (a deadlock).
     * Stock is taken after the order is built, so each row stays locked only until the commit.
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

        List<CartItem> byProductId = lines.stream()
                .sorted(Comparator.comparing((CartItem i) -> i.getProduct().getId()))
                .toList();

        Order order = new Order(user);
        byProductId.forEach(line -> order.addLine(line.getProduct(), line.getQuantity()));
        orders.save(order);

        Instant now = Instant.now();
        for (CartItem line : byProductId) {
            Product product = line.getProduct();
            if (inventory.decrementIfAvailable(product.getId(), line.getQuantity(), now) == 0) {
                int available = inventory.findById(product.getId()).map(Inventory::getQuantity).orElse(0);
                throw new OutOfStockException(product.getName(), available, line.getQuantity());
            }
        }

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
