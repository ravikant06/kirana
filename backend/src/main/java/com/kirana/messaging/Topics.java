package com.kirana.messaging;

/**
 * Every Kafka topic name in one place. The ".v1" is the message-format version: a breaking
 * change to a message gets a new topic, so old and new consumers can run side by side.
 */
public final class Topics {

    /** Order lifecycle events (placed, paid, closed). Key: the order id. */
    public static final String ORDERS = "orders.v1";

    private Topics() {
    }
}
