package com.kirana.service;

/**
 * Where order events go. Stage 5 logged them in memory; Stage 6 (OutboxOrderEvents) writes them
 * to the outbox table in the caller's transaction, and OutboxRelay sends them to Kafka.
 * Publishers did not change. Call it inside the transaction that makes the change.
 */
public interface OrderEvents {

    void publish(OrderEvent event);
}
