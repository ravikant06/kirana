package com.kirana.messaging;

/**
 * Every Kafka topic name in one place. The ".v1" is the message-format version: a breaking
 * change to a message gets a new topic, so old and new consumers can run side by side.
 */
public final class Topics {

    /** Order lifecycle events (placed, paid, closed). Key: the order id. */
    public static final String ORDERS = "orders.v1";

    /** What payment gateways told us (webhooks): captured, failed. Key: our order id. */
    public static final String PAYMENTS = "payments.v1";

    private Topics() {
    }
}
