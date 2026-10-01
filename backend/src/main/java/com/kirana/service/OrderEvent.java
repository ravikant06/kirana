package com.kirana.service;

/**
 * Facts about orders. Written to the outbox with the change (Stage 6) and relayed to Kafka topic
 * orders.v1, keyed by order id, for services that react on their own (refunds, fulfilment).
 * Checkout itself does not depend on who listens.
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
