package com.kirana.service;

import java.time.Duration;
import java.time.Instant;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import com.kirana.cache.FlashSaleCounter;
import com.kirana.config.PaymentProperties;
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
import com.kirana.repository.CartLineView;
import com.kirana.repository.CartRepository;
import com.kirana.repository.InventoryRepository;
import com.kirana.repository.OrderRepository;
import org.springframework.dao.OptimisticLockingFailureException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionTemplate;

@Service
public class OrderService {

    private final OrderRepository orders;
    private final CartRepository carts;
    private final InventoryRepository inventory;
    private final UserService users;
    private final FlashSaleCounter flashSale;
    private final TransactionTemplate tx;
    private final Duration paymentWindow;
    private final OrderEvents events;

    public OrderService(OrderRepository orders, CartRepository carts, InventoryRepository inventory, UserService users,
                        FlashSaleCounter flashSale, TransactionTemplate tx, PaymentProperties payment,
                        OrderEvents events) {
        this.paymentWindow = payment.window();
        this.events = events;
        this.orders = orders;
        this.carts = carts;
        this.inventory = inventory;
        this.users = users;
        this.flashSale = flashSale;
        this.tx = tx;
    }

    /**
     * Checkout, in two phases (D44):
     *
     * 1. Flash-sale gate, before any transaction. For products with an armed sale, Redis hands
     *    out units atomically. A buyer who gets none is refused here, after one light cart query
     *    and one Redis call, without opening a transaction or queueing on the stock row.
     * 2. The database checkout (placeInDb), in one transaction. If it fails for any reason,
     *    units taken at the gate are given back (compensation), because Redis is not part of
     *    the database transaction and a rollback cannot undo it.
     *
     * Not @Transactional itself: Redis calls must not hold a database connection (Stage 2f).
     */
    public OrderResponse place(Long userId) {
        Map<Long, Integer> taken = new LinkedHashMap<>();
        try {
            for (CartLineView line : carts.findLiveLines(userId)) {
                switch (flashSale.take(line.productId(), line.quantity())) {
                    case TAKEN -> taken.put(line.productId(), line.quantity());
                    case SOLD_OUT -> throw new OutOfStockException(line.productName(),
                            (int) flashSale.remaining(line.productId()).orElse(0), line.quantity());
                    case NOT_ARMED -> { } // no sale, or Redis down: Postgres decides, as in Stage 3
                }
            }
            try {
                return tx.execute(status -> placeInDb(userId, taken));
            } catch (OptimisticLockingFailureException e) {
                // Two checkouts of the same cart at once (two tabs, a double click): both read the
                // cart, the first commits and deletes its lines, the second's DELETE finds 0 rows
                // and Hibernate rolls it back. Correct outcome (one order), so just say what it was.
                // A real idempotency key (Stage 7) would instead return the first order.
                throw new ConflictException("Checkout already in progress",
                        "This cart is already being checked out (another tab or a double click). "
                                + "Check your orders before trying again.");
            }
        } catch (RuntimeException e) {
            taken.forEach(flashSale::giveBack);
            throw e;
        }
    }

    /**
     * Turn the cart into an order, subtract stock, empty the cart. All in one transaction, so an
     * out-of-stock line undoes the stock already subtracted for earlier lines.
     *
     * R2: stock is taken with one atomic conditional UPDATE per line, so two buyers can never
     * both get the last unit, across any number of app instances. Concurrent buyers of one
     * product queue briefly on its row lock and still succeed while stock lasts.
     * Lines are processed in product-id order: carts (Tea, Rice) and (Rice, Tea) checking out
     * together would otherwise each lock one row and wait for the other (a deadlock).
     * Stock is taken after the order is built, so each row stays locked only until the commit.
     */
    private OrderResponse placeInDb(Long userId, Map<Long, Integer> takenAtGate) {
        User user = users.require(userId);
        Cart cart = carts.findWithItemsByUserId(userId).orElse(null);
        List<CartItem> lines = cart == null ? List.of()
                : cart.getItems().stream().filter(i -> !i.getProduct().isDeleted()).toList();
        if (lines.isEmpty()) {
            throw new ConflictException("Cart is empty", "Add something to your cart before placing an order");
        }

        // The gate counted the cart as it was a moment ago. If it changed since (another tab),
        // stop: the units taken would not match what is being bought.
        takenAtGate.forEach((productId, qty) -> {
            boolean same = lines.stream().anyMatch(l -> l.getProduct().getId().equals(productId) && l.getQuantity() == qty);
            if (!same) {
                throw new ConflictException("Cart changed", "Your cart changed during checkout. Please try again.");
            }
        });

        List<CartItem> byProductId = lines.stream()
                .sorted(Comparator.comparing((CartItem i) -> i.getProduct().getId()))
                .toList();

        // Stage 5: stock is held until paymentDueAt; unpaid orders are then released (PaymentJobs).
        Order order = new Order(user, Instant.now().plus(paymentWindow));
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
        // Inside TX1: the outbox row commits together with the order, or not at all (Stage 6).
        events.publish(new OrderEvent.OrderPlaced(order.getId(), userId, order.getTotal()));
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
