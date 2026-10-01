package com.kirana.service;

import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

import com.kirana.entity.OutboxMessage;
import com.kirana.messaging.Topics;
import com.kirana.repository.OutboxRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.json.JsonMapper;

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

    private final OutboxRepository outbox;
    private final JsonMapper json;

    public OutboxOrderEvents(OutboxRepository outbox, JsonMapper json) {
        this.outbox = outbox;
        this.json = json;
    }

    @Override
    @Transactional(propagation = Propagation.REQUIRED)
    public void publish(OrderEvent event) {
        UUID eventId = UUID.randomUUID();
        String type = event.getClass().getSimpleName();
        // The message body: a small envelope around the event's own fields.
        Map<String, Object> envelope = new LinkedHashMap<>();
        envelope.put("eventId", eventId.toString());
        envelope.put("type", type);
        envelope.put("occurredAt", Instant.now().toString());
        envelope.put("orderId", event.orderId());
        envelope.put("data", event);
        outbox.save(new OutboxMessage(eventId, Topics.ORDERS, Long.toString(event.orderId()), type,
                json.writeValueAsString(envelope)));
        if (event instanceof OrderEvent.PaymentAfterClose) {
            log.error("REFUND NEEDED (queued as {}): {}", eventId, event);
        } else {
            log.info("Outbox: {} for order {} queued as {}", type, event.orderId(), eventId);
        }
    }
}
