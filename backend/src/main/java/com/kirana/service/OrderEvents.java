package com.kirana.service;

/**
 * Where order events go. The seam Stage 6 plugs into: replace the implementation with
 * "insert into an outbox table in the same transaction, relay to Kafka", and nothing that
 * publishes has to change.
 */
public interface OrderEvents {

    void publish(OrderEvent event);
}
