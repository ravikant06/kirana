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

    /**
     * Stage 6f: dead-letter topics. A record a consumer cannot process (after its retries, or at
     * once if retrying cannot help) is copied here with the error in its headers, instead of
     * being dropped. Same partition count as the source: a dead letter keeps its partition.
     */
    public static final String DLT_SUFFIX = "-dlt";
    public static final String ORDERS_DLT = ORDERS + DLT_SUFFIX;
    public static final String PAYMENTS_DLT = PAYMENTS + DLT_SUFFIX;

    private Topics() {
    }
}
