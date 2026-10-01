package com.kirana.messaging;

import java.util.UUID;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

/**
 * Consumer-side de-duplication (Stage 6c), table processed_events. Call it inside the same
 * transaction as the consumer's work: the row and the work commit together, or neither does.
 */
@Component
public class Inbox {

    private final JdbcTemplate jdbc;

    public Inbox(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    /** True the first time this consumer sees this event; false for any redelivery. */
    public boolean firstDelivery(String consumer, UUID eventId) {
        return jdbc.update("INSERT INTO processed_events (consumer, event_id) VALUES (?, ?) ON CONFLICT DO NOTHING",
                consumer, eventId) == 1;
    }
}
