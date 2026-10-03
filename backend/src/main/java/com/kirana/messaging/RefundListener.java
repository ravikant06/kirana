package com.kirana.messaging;

import java.util.UUID;

import com.kirana.service.RefundService;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.kafka.support.Acknowledgment;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * Stage 6d: the refund consumer. Its own group, "kirana-refunds", so it reads orders.v1
 * independently of anyone else (and, being new, from the oldest record: earlier late payments
 * get refunded too). It only cares about PaymentAfterClose.
 *
 *   1. one transaction: inbox row + REQUESTED refund row        (database only, fast)
 *   2. acknowledge: the offset moves on; the refund can no longer be lost, it is a row now
 *   3. try the gateway once, right away                         (RefundJobs retries if this fails)
 *
 * The gateway call is deliberately after the acknowledgement: a slow or failing gateway must not
 * hold up the partition, and Kafka's retries (KafkaConfig) would repeat a non-idempotent call.
 */
@Component
@ConditionalOnProperty(name = "kirana.kafka.enabled", havingValue = "true")
public class RefundListener {

    static final String GROUP = "kirana-refunds";

    private static final Logger log = LoggerFactory.getLogger(RefundListener.class);

    private final RefundService refunds;
    private final Inbox inbox;
    private final TransactionTemplate tx;
    private final JsonMapper json;

    public RefundListener(RefundService refunds, Inbox inbox, TransactionTemplate tx, JsonMapper json) {
        this.refunds = refunds;
        this.inbox = inbox;
        this.tx = tx;
        this.json = json;
    }

    @KafkaListener(id = "refunds-listener", topics = Topics.ORDERS, groupId = GROUP)
    public void onMessage(ConsumerRecord<String, String> record, Acknowledgment ack) {
        JsonNode event = json.readTree(record.value());
        String type = event.path("type").asString();
        if (!"PaymentAfterClose".equals(type)) {
            ack.acknowledge(); // not for us: just move on
            return;
        }
        UUID eventId = UUID.fromString(event.path("eventId").asString());
        long orderId = event.path("orderId").asLong();
        JsonNode data = event.path("data");

        Long refundId = tx.execute(s -> inbox.firstDelivery(GROUP, eventId)
                ? refunds.request(orderId, data.path("paymentId").asString(), data.path("provider").asString())
                : null);
        ack.acknowledge();
        log.info("orders.v1 p{}@{}: PaymentAfterClose for order {} -> {}", record.partition(), record.offset(), orderId,
                refundId == null ? "duplicate or already recorded, skipped" : "refund " + refundId + " REQUESTED");
        if (refundId != null) {
            refunds.execute(refundId);
        }
    }
}
