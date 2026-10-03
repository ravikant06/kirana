package com.kirana.messaging;

import com.kirana.service.FulfilmentService;
import com.kirana.warehouse.WarehouseUnavailableException;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnExpression;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.kafka.support.Acknowledgment;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * Stage 6e: sends paid orders to the warehouse. Group "kirana-fulfilment" on orders.v1, OrderPaid
 * only. Replaces the naive dual write (CheckoutSaga, mode "naive").
 *
 * Unlike the refund consumer (6d), this one calls the remote system BEFORE acknowledging, and
 * when the warehouse is down it keeps retrying the same record, holding up the partition
 * (KafkaConfig.fulfilmentListenerFactory: back-off 1 s doubling to 30 s, no limit). Why that is
 * the right call here:
 *   - the warehouse call is idempotent (Idempotency-Key), so repeating it is safe
 *   - a warehouse outage affects every OrderPaid alike: skipping ahead would not ship anything
 *     else, it would only lose this one
 *   - the order is already PAID and its event is durable in Kafka: waiting costs time, not data
 * The backlog shows up as consumer lag (Kafka UI). A 4xx from the warehouse is different: retrying
 * can never help, so that record is logged and skipped (dead-letter topic in 6f).
 */
@Component
@ConditionalOnExpression("${kirana.kafka.enabled:false} and '${kirana.fulfilment.mode:events}' == 'events'")
public class FulfilmentListener {

    static final String GROUP = "kirana-fulfilment";

    private static final Logger log = LoggerFactory.getLogger(FulfilmentListener.class);

    private final FulfilmentService fulfilment;
    private final JsonMapper json;

    public FulfilmentListener(FulfilmentService fulfilment, JsonMapper json) {
        this.fulfilment = fulfilment;
        this.json = json;
    }

    @KafkaListener(id = "fulfilment-listener", topics = Topics.ORDERS, groupId = GROUP,
            containerFactory = "fulfilmentListenerFactory")
    public void onMessage(ConsumerRecord<String, String> record, Acknowledgment ack) {
        JsonNode event = json.readTree(record.value());
        if (!"OrderPaid".equals(event.path("type").asString())) {
            ack.acknowledge();
            return;
        }
        long orderId = event.path("orderId").asLong();
        String shipmentId;
        try {
            shipmentId = fulfilment.send(orderId);
        } catch (WarehouseUnavailableException e) {
            log.warn("orders.v1 p{}@{}: OrderPaid for order {} -> warehouse unavailable ({}); retrying, partition waits",
                    record.partition(), record.offset(), orderId, e.getMessage());
            throw e; // not acknowledged: the error handler backs off and redelivers this record
        }
        ack.acknowledge();
        log.info("orders.v1 p{}@{}: OrderPaid for order {} -> {}", record.partition(), record.offset(), orderId,
                shipmentId == null ? "nothing to do (already sent, or not paid)" : "sent to warehouse as " + shipmentId);
    }
}
