package com.kirana.service;

import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

import com.kirana.entity.OutboxMessage;
import com.kirana.repository.OutboxRepository;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.json.JsonMapper;

/**
 * Writes one event to the outbox table (Stage 6), joining the caller's transaction. Every event
 * on every topic has the same envelope: { eventId, type, occurredAt, orderId, data }.
 */
@Component
public class OutboxWriter {

    private final OutboxRepository outbox;
    private final JsonMapper json;

    public OutboxWriter(OutboxRepository outbox, JsonMapper json) {
        this.outbox = outbox;
        this.json = json;
    }

    @Transactional(propagation = Propagation.REQUIRED)
    public void append(UUID eventId, String topic, long orderId, String type, Object data) {
        Map<String, Object> envelope = new LinkedHashMap<>();
        envelope.put("eventId", eventId.toString());
        envelope.put("type", type);
        envelope.put("occurredAt", Instant.now().toString());
        envelope.put("orderId", orderId);
        envelope.put("data", data);
        // saveAndFlush: a duplicate eventId fails here, inside the caller's code, not later at commit.
        outbox.saveAndFlush(new OutboxMessage(eventId, topic, Long.toString(orderId), type, json.writeValueAsString(envelope)));
    }
}
