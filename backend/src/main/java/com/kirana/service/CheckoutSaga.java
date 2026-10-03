package com.kirana.service;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Instant;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

import com.kirana.cache.FlashSaleCounter;
import com.kirana.config.FulfilmentProperties;
import com.kirana.config.PaymentProperties;
import com.kirana.dto.CheckoutResponse;
import com.kirana.dto.OrderResponse;
import com.kirana.entity.Order;
import com.kirana.entity.OrderItem;
import com.kirana.entity.OrderStatus;
import com.kirana.exception.ConflictException;
import com.kirana.exception.InvalidFieldException;
import com.kirana.exception.NotFoundException;
import com.kirana.mapper.OrderMapper;
import com.kirana.payment.GatewayOrder;
import com.kirana.payment.GatewayStatus;
import com.kirana.payment.PaymentGateway;
import com.kirana.payment.PaymentGateways;
import com.kirana.payment.PaymentSession;
import com.kirana.payment.PaymentUnavailableException;
import com.kirana.repository.InventoryRepository;
import com.kirana.repository.OrderRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * Checkout as a saga (D46). Each step is its own short transaction and is safe to run twice:
 *
 *   1. reserve       stock taken, order CREATED ("awaiting payment")     OrderService.place
 *   2. request       gateway order created (no transaction held)         requestPayment
 *   3a. confirm      CREATED -> PAID                                     confirmPayment
 *   3b. close        CREATED -> CANCELLED | FAILED, and release stock    cancel / expire
 *   4. late payment  money arrived after 3b: recorded once, refund event  applyPayment (Stage 6c)
 *
 * Every move out of CREATED is one conditional UPDATE (OrderRepository): whoever changes the
 * row owns the step's side effects; a duplicate or late caller changes nothing. That is what
 * lets the shopper's verify call, the reconciler and the expiry job race safely today, and
 * what lets Kafka consumers (at-least-once) call the same methods in Stage 6.
 *
 * Stage 6c: payments now reach us three ways, all ending in applyPayment: the browser (verify),
 * the gateway's webhook (via Kafka, PaymentEventsListener) and polling (reconcile, recheckClosed).
 */
@Service
public class CheckoutSaga {

    private static final Logger log = LoggerFactory.getLogger(CheckoutSaga.class);

    private final OrderService orderService;
    private final OrderRepository orders;
    private final InventoryRepository inventory;
    private final FlashSaleCounter flashSale;
    private final PaymentGateways gateways;
    private final OrderEvents events;
    private final TransactionTemplate tx;
    private final PaymentProperties props;
    private final FulfilmentService fulfilment;
    private final FulfilmentProperties fulfilmentProps;

    public CheckoutSaga(OrderService orderService, OrderRepository orders, InventoryRepository inventory,
                        FlashSaleCounter flashSale, PaymentGateways gateways, OrderEvents events,
                        TransactionTemplate tx, PaymentProperties props, FulfilmentService fulfilment,
                        FulfilmentProperties fulfilmentProps) {
        this.fulfilment = fulfilment;
        this.fulfilmentProps = fulfilmentProps;
        this.orderService = orderService;
        this.orders = orders;
        this.inventory = inventory;
        this.flashSale = flashSale;
        this.gateways = gateways;
        this.events = events;
        this.tx = tx;
        this.props = props;
    }

    /** Steps 1 and 2. A gateway outage does not lose the order: it stays CREATED and can be paid later. */
    public CheckoutResponse start(Long userId, String provider) {
        PaymentGateway gateway = gateways.forCheckout(provider); // validate before taking any stock
        OrderResponse order = orderService.place(userId); // publishes OrderPlaced inside TX1 (Stage 6)
        try {
            PaymentSession session = request(order.id(), gateway);
            return new CheckoutResponse(reload(order.id()), session, null);
        } catch (PaymentUnavailableException e) {
            log.warn("Order {} placed, but {} is unavailable: {}", order.id(), gateway.label(), e.getMessage());
            return new CheckoutResponse(reload(order.id()), null,
                    "Payment is temporarily unavailable. Your items are held until %s; try paying again from Orders."
                            .formatted(order.paymentDueAt()));
        }
    }

    /** Step 2 again: "Pay now" on an unpaid order. Idempotent: returns the same gateway order. */
    public PaymentSession requestPayment(Long userId, Long orderId, String provider) {
        Order order = requireOwned(userId, orderId);
        requireAwaitingPayment(order);
        PaymentGateway gateway = order.getPaymentProvider() != null
                ? gateways.of(order.getPaymentProvider())
                : gateways.forCheckout(provider);
        return request(orderId, gateway);
    }

    /** Step 3a, from the browser: trust the result only if the gateway's signature checks out. */
    public OrderResponse verifyPayment(Long userId, Long orderId, String gatewayOrderId, String paymentId, String signature) {
        Order order = requireOwned(userId, orderId);
        if (order.getGatewayOrderId() == null || !order.getGatewayOrderId().equals(gatewayOrderId)) {
            throw new InvalidFieldException("gatewayOrderId", "does not belong to order " + orderId);
        }
        PaymentGateway gateway = gateways.of(order.getPaymentProvider());
        if (!gateway.verifySignature(gatewayOrderId, paymentId, signature)) {
            throw new InvalidFieldException("signature", "is not a valid payment signature");
        }
        confirmPayment(orderId, paymentId);
        return reload(orderId);
    }

    /** Step 3b by the shopper. */
    public OrderResponse cancel(Long userId, Long orderId) {
        requireAwaitingPayment(requireOwned(userId, orderId));
        close(orderId, OrderStatus.CANCELLED, "Cancelled by the shopper");
        return reload(orderId);
    }

    // ---------------------------------------------------------------- used by PaymentJobs

    /** Ask the gateway what happened to an unpaid order; settle it if it was paid. */
    public void reconcile(Long orderId) {
        Order order = orders.findById(orderId).orElse(null);
        if (order == null || order.getStatus() != OrderStatus.CREATED || order.getGatewayOrderId() == null) {
            return;
        }
        GatewayStatus status = gateways.of(order.getPaymentProvider()).fetchStatus(order.getGatewayOrderId());
        if (status.state() == GatewayStatus.State.PAID) {
            log.info("Reconciler: order {} was paid at the gateway ({})", orderId, status.paymentId());
            confirmPayment(orderId, status.paymentId());
        }
    }

    /**
     * The payment window passed. Check the gateway one last time, then release the stock.
     *
     * PENDING means the gateway knows the order and has no captured payment: safe to close (and
     * if money still arrives later, recheckClosed / the webhook catch it). UNKNOWN means the
     * gateway could not tell us anything about it, which is not the same as "not paid" (G3):
     * hold the order a little longer and ask again, up to unknown-grace; only then close it.
     */
    public void expire(Long orderId) {
        Order order = orders.findById(orderId).orElse(null);
        if (order == null || order.getStatus() != OrderStatus.CREATED) {
            return;
        }
        if (order.getGatewayOrderId() != null) {
            GatewayStatus status = gateways.of(order.getPaymentProvider()).fetchStatus(order.getGatewayOrderId());
            if (status.state() == GatewayStatus.State.PAID) {
                confirmPayment(orderId, status.paymentId());
                return;
            }
            if (status.state() == GatewayStatus.State.UNKNOWN) {
                Instant now = Instant.now();
                Instant giveUpAt = order.getCreatedAt().plus(props.window()).plus(props.unknownGrace());
                if (now.isBefore(giveUpAt)) {
                    // Postponing the due time also moves the order to the back of the expiry queue,
                    // so a pile of UNKNOWN orders cannot starve the others.
                    Instant from = now.isAfter(order.getPaymentDueAt()) ? now : order.getPaymentDueAt();
                    Instant retryAt = min(from.plusSeconds(300), giveUpAt);
                    tx.executeWithoutResult(s -> orders.postponeDue(orderId, retryAt, now));
                    log.warn("Expiry: {} has no record of order {} ({}); holding it until {} instead of closing",
                            order.getPaymentProvider(), orderId, order.getGatewayOrderId(), retryAt);
                    return;
                }
                log.error("Expiry: {} still has no record of order {} after {}; closing it. "
                                + "The re-check job keeps watching for a late payment for {}.",
                        order.getPaymentProvider(), orderId, props.unknownGrace(), props.recheckClosedFor());
            }
        }
        close(orderId, OrderStatus.FAILED, "Not paid within " + props.window().toMinutes() + " minutes");
    }

    /**
     * Stage 6c safety net (G1) for when no webhook arrives: a recently closed order is asked
     * about once more. Money that arrived after the close becomes a late payment (refund).
     */
    public void recheckClosed(Long orderId) {
        Order order = orders.findById(orderId).orElse(null);
        if (order == null || order.getGatewayOrderId() == null || order.getLatePaymentId() != null
                || (order.getStatus() != OrderStatus.CANCELLED && order.getStatus() != OrderStatus.FAILED)) {
            return;
        }
        GatewayStatus status = gateways.of(order.getPaymentProvider()).fetchStatus(order.getGatewayOrderId());
        if (status.state() == GatewayStatus.State.PAID) {
            log.warn("Re-check: order {} was {} but {} captured payment {}",
                    orderId, order.getStatus(), order.getPaymentProvider(), status.paymentId());
            applyPayment(orderId, status.paymentId());
        }
    }

    // ---------------------------------------------------------------- the steps themselves

    /**
     * An order gets exactly one gateway order, and every payment attempt uses it.
     *
     * Do not rely on the gateway to de-duplicate: real Razorpay creates a NEW order for the same
     * receipt (tested), so asking again on "Pay now" would hand the browser an order we never
     * stored, and verify would reject the payment. So: reuse the attached one if there is one;
     * otherwise create one and attach it, and if a concurrent request attached a different one
     * first, use theirs (ours is left unused and expires at the gateway, unpaid).
     */
    private PaymentSession request(Long orderId, PaymentGateway gateway) {
        Order order = orders.findById(orderId).orElseThrow();
        long paise = toPaise(order.getTotal());
        if (order.getGatewayOrderId() != null) {
            return gateway.session(orderId, new GatewayOrder(order.getGatewayOrderId(), paise, props.currency()));
        }
        // Outside any transaction: the gateway call can take seconds and must not hold a connection.
        GatewayOrder created = gateway.createOrder(orderId, paise, props.currency());
        tx.executeWithoutResult(s -> orders.attachGatewayOrder(orderId, gateway.id(), created.gatewayOrderId(), Instant.now()));
        String attached = orders.findById(orderId).orElseThrow().getGatewayOrderId();
        if (attached != null && !attached.equals(created.gatewayOrderId())) {
            log.info("Order {}: another request attached gateway order {} first; {} stays unused",
                    orderId, attached, created.gatewayOrderId());
            return gateway.session(orderId, new GatewayOrder(attached, paise, props.currency()));
        }
        return gateway.session(orderId, created);
    }

    /** What applyPayment did with a captured payment. */
    public enum PaymentResult {
        /** CREATED -> PAID by this call. */
        PAID,
        /** Someone else (browser, webhook, reconciler) confirmed it first; nothing to do. */
        ALREADY_PAID,
        /** The order had already closed: recorded as a late payment, refund event raised once. */
        PAID_AFTER_CLOSE
    }

    /**
     * Step 3a or 4 for a payment the gateway captured, whoever reports it. Never throws for a
     * closed order, so a Kafka consumer can call it inside its own transaction. Every branch is
     * a conditional UPDATE, so reporting the same payment any number of times is harmless.
     */
    public PaymentResult applyPayment(Long orderId, String paymentId) {
        return tx.execute(s -> {
            Instant now = Instant.now();
            if (orders.markPaid(orderId, paymentId, now) == 1) {
                Order o = orders.findById(orderId).orElseThrow();
                events.publish(new OrderEvent.OrderPaid(orderId, o.getPaymentProvider(), paymentId));
                if (fulfilmentProps.naive()) {
                    shipRightAfterCommit(orderId);
                }
                return PaymentResult.PAID;
            }
            Order order = orders.findById(orderId).orElseThrow();
            if (order.getStatus() == OrderStatus.PAID) {
                return PaymentResult.ALREADY_PAID;
            }
            if (orders.markLatePayment(orderId, paymentId, now) == 1) {
                events.publish(new OrderEvent.PaymentAfterClose(orderId, order.getPaymentProvider(), paymentId));
            }
            return PaymentResult.PAID_AFTER_CLOSE;
        });
    }

    /**
     * Stage 6e, mode "naive": THE DUAL WRITE, kept to reproduce its problems. After the PAID
     * commit, call the warehouse directly, in whatever thread confirmed the payment (often the
     * shopper's verify request). If the warehouse is down or slow, or the app dies right after the
     * commit, the order is paid and never shipped, and nothing will ever try again. Mode "events"
     * replaces this with FulfilmentListener, a consumer of OrderPaid.
     */
    private void shipRightAfterCommit(Long orderId) {
        TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
            @Override
            public void afterCommit() {
                try {
                    fulfilment.send(orderId);
                } catch (RuntimeException e) {
                    log.error("Fulfilment (naive): order {} is PAID but the warehouse call failed ({}). "
                            + "Nothing will retry it: this order will never ship.", orderId, e.getMessage());
                }
            }
        });
    }

    /** applyPayment for callers that answer a person (the shopper's verify): a closed order is an error. */
    void confirmPayment(Long orderId, String paymentId) {
        if (applyPayment(orderId, paymentId) == PaymentResult.PAID_AFTER_CLOSE) {
            Order order = orders.findById(orderId).orElseThrow();
            throw new ConflictException("Order already closed",
                    "Order %d was %s before this payment arrived. A refund is needed.".formatted(orderId, order.getStatus()));
        }
    }

    /** CREATED -> CANCELLED/FAILED, once; only the caller that closes it releases the stock. */
    private void close(Long orderId, OrderStatus to, String reason) {
        tx.executeWithoutResult(s -> {
            if (orders.close(orderId, to, reason, Instant.now()) == 0) {
                return; // someone else settled it first
            }
            Order order = orders.findWithItemsById(orderId).orElseThrow();
            Map<Long, Integer> byProduct = order.getItems().stream()
                    .sorted(Comparator.comparing((OrderItem i) -> i.getProduct().getId())) // lock order: no deadlocks
                    .collect(Collectors.toMap(i -> i.getProduct().getId(), OrderItem::getQuantity,
                            Integer::sum, java.util.LinkedHashMap::new));
            Instant now = Instant.now();
            byProduct.forEach((productId, qty) -> inventory.adjustIfValid(productId, qty, now));
            // The flash-sale gate is Redis, outside this transaction: hand units back only after commit.
            TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
                @Override
                public void afterCommit() {
                    byProduct.forEach(flashSale::giveBack);
                }
            });
            events.publish(new OrderEvent.OrderClosed(orderId, to.name(), reason));
        });
    }

    // ---------------------------------------------------------------- helpers

    private static Instant min(Instant a, Instant b) {
        return a.isBefore(b) ? a : b;
    }

    private Order requireOwned(Long userId, Long orderId) {
        return orders.findWithItemsByIdAndUserId(orderId, userId)
                .orElseThrow(() -> new NotFoundException("Order %d not found".formatted(orderId)));
    }

    private static void requireAwaitingPayment(Order order) {
        if (order.getStatus() != OrderStatus.CREATED) {
            throw new ConflictException("Order not awaiting payment",
                    "Order %d is %s".formatted(order.getId(), order.getStatus()));
        }
    }

    private OrderResponse reload(Long orderId) {
        return tx.execute(s -> OrderMapper.toResponse(orders.findWithItemsById(orderId).orElseThrow()));
    }

    /**
     * Gateways take integer paise. Our totals are double (D3), and 120.10 * 100 in floating point
     * is 12009.999..., so convert through the decimal string with explicit rounding.
     */
    static long toPaise(double rupees) {
        return BigDecimal.valueOf(rupees).setScale(2, RoundingMode.HALF_UP).movePointRight(2).longValueExact();
    }

    List<PaymentGateway> gateways() {
        return gateways.all();
    }
}
