package com.kirana.messaging;

import java.util.UUID;

import com.kirana.payment.GatewayRefund;
import com.kirana.service.CheckoutSaga;
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
 * Kirana's first Kafka consumer (Stage 6c): applies what gateways reported (payments.v1).
 *
 * Consumer group "kirana-payments": Kafka remembers how far this group has read (its committed
 * offset per partition). With 3 partitions, up to 3 instances of the app share the work.
 *
 * One record at a time:
 *   1. in ONE transaction: record the event id in the inbox, then apply the payment
 *   2. after the commit, acknowledge (commit the offset)
 *
 * If the app dies between 1 and 2, Kafka delivers the record again; the inbox row makes the
 * second delivery a no-op. If step 1 throws, nothing commits and nothing is acknowledged: the
 * error handler (KafkaConfig) retries it a few times, then logs it and moves on (dead-letter
 * topic in 6f; until then the re-check job is the safety net).
 */
@Component
@ConditionalOnProperty(name = "kirana.kafka.enabled", havingValue = "true")
public class PaymentEventsListener {

    static final String GROUP = "kirana-payments";

    private static final Logger log = LoggerFactory.getLogger(PaymentEventsListener.class);

    private final CheckoutSaga saga;
    private final RefundService refunds;
    private final Inbox inbox;
    private final TransactionTemplate tx;
    private final JsonMapper json;

    public PaymentEventsListener(CheckoutSaga saga, RefundService refunds, Inbox inbox, TransactionTemplate tx,
                                 JsonMapper json) {
        this.saga = saga;
        this.refunds = refunds;
        this.inbox = inbox;
        this.tx = tx;
        this.json = json;
    }

    @KafkaListener(id = "payments-listener", topics = Topics.PAYMENTS, groupId = GROUP)
    public void onMessage(ConsumerRecord<String, String> record, Acknowledgment ack) {
        JsonNode event = json.readTree(record.value());
        UUID eventId = UUID.fromString(event.path("eventId").asString());
        String type = event.path("type").asString();
        long orderId = event.path("orderId").asLong();
        String paymentId = event.path("data").path("paymentId").asString();

        String outcome = tx.execute(s -> {
            if (!inbox.firstDelivery(GROUP, eventId)) {
                return "duplicate delivery, skipped";
            }
            return switch (type) {
                case "PaymentCaptured" -> switch (saga.applyPayment(orderId, paymentId)) {
                    case PAID -> "order PAID";
                    case ALREADY_PAID -> "already paid (browser or reconciler was first)";
                    case PAID_AFTER_CLOSE -> "order was closed: late payment, refund needed";
                };
                case "PaymentFailed" -> "payment attempt failed; order stays awaiting payment";
                case "RefundProcessed", "RefundFailed" -> {
                    var state = type.equals("RefundProcessed") ? GatewayRefund.State.PROCESSED : GatewayRefund.State.FAILED;
                    boolean changed = refunds.settle(paymentId, event.path("data").path("refundId").asString(), state);
                    yield changed ? "refund " + state : "refund already settled";
                }
                default -> "unknown type, ignored";
            };
        });
        ack.acknowledge();
        log.info("payments.v1 p{}@{}: {} for order {} -> {}", record.partition(), record.offset(), type, orderId, outcome);
    }
}
