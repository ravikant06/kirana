package com.kirana.service;

import java.util.UUID;

import com.kirana.messaging.Topics;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

/**
 * Stage 6 implementation of the OrderEvents seam (replaces Stage 5's in-memory one; CheckoutSaga
 * did not change). publish() INSERTs the event into the outbox table, joining the caller's
 * transaction: the event commits if and only if the order change commits. The relay sends it
 * to Kafka afterwards, as many times as needed.
 *
 * Called outside a transaction (PaymentAfterClose), it opens its own: there is no order change
 * to be atomic with, but the event still must not be lost.
 */
@Component
public class OutboxOrderEvents implements OrderEvents {

    private static final Logger log = LoggerFactory.getLogger(OutboxOrderEvents.class);

    private final OutboxWriter outbox;

    public OutboxOrderEvents(OutboxWriter outbox) {
        this.outbox = outbox;
    }

    @Override
    @Transactional(propagation = Propagation.REQUIRED)
    public void publish(OrderEvent event) {
        UUID eventId = UUID.randomUUID();
        String type = event.getClass().getSimpleName();
        outbox.append(eventId, Topics.ORDERS, event.orderId(), type, event);
        if (event instanceof OrderEvent.PaymentAfterClose) {
            log.error("REFUND NEEDED (queued as {}): {}", eventId, event);
        } else {
            log.info("Outbox: {} for order {} queued as {}", type, event.orderId(), eventId);
        }
    }
}
