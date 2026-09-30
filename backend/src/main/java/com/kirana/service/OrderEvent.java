package com.kirana.service;

/**
 * Facts about orders, published after the change commits. Stage 5 only logs them; in Stage 6
 * they go to Kafka (through an outbox) for services that react on their own: email, loyalty,
 * analytics, shipping. Checkout itself does not depend on who listens.
 */
public sealed interface OrderEvent {

    long orderId();

    record OrderPlaced(long orderId, long userId, double total) implements OrderEvent {
    }

    record OrderPaid(long orderId, String provider, String paymentId) implements OrderEvent {
    }

    record OrderClosed(long orderId, String status, String reason) implements OrderEvent {
    }

    /** Money arrived for an order that was already cancelled or expired: someone must refund. */
    record PaymentAfterClose(long orderId, String provider, String paymentId) implements OrderEvent {
    }
}
