package com.kirana.service;

import java.nio.charset.StandardCharsets;
import java.util.UUID;

import com.kirana.entity.Order;
import com.kirana.exception.NotFoundException;
import com.kirana.messaging.Topics;
import com.kirana.payment.PaymentGateway;
import com.kirana.payment.PaymentGateways;
import com.kirana.repository.OrderRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * Stage 6c: the gateway tells us about payments (webhooks), whether or not the shopper's browser
 * is still open. This class only checks and records; it does not change any order.
 *
 *   1. verify the signature over the raw body (anyone can POST to a public URL)
 *   2. find our order by the gateway order id
 *   3. write the event to the outbox, topic payments.v1, keyed by our order id
 *
 * The HTTP 200 goes back only after step 3 commits. So once the gateway hears "OK", the event
 * is durable; if we crash before that, the gateway retries. The real work (PAID, or a late
 * payment) happens in PaymentEventsListener, off Kafka.
 *
 * Gateways deliver webhooks at least once (retries, and sometimes duplicates). The outbox event
 * id is derived from the gateway's own event id, so a repeated delivery is recognised here and
 * written only once.
 */
@Service
public class PaymentWebhooks {

    private static final Logger log = LoggerFactory.getLogger(PaymentWebhooks.class);

    /** What a webhook delivery led to. All but INVALID_SIGNATURE answer 200, so the gateway stops retrying. */
    public enum Result { ACCEPTED, DUPLICATE, IGNORED, INVALID_SIGNATURE }

    /** The data part of a payments.v1 event. */
    public record PaymentEvent(String provider, String gatewayEventId, String gatewayOrderId, String paymentId,
                               String status) {
    }

    private final PaymentGateways gateways;
    private final OrderRepository orders;
    private final OutboxWriter outbox;
    private final TransactionTemplate tx;
    private final JsonMapper json;

    public PaymentWebhooks(PaymentGateways gateways, OrderRepository orders, OutboxWriter outbox,
                           TransactionTemplate tx, JsonMapper json) {
        this.gateways = gateways;
        this.orders = orders;
        this.outbox = outbox;
        this.tx = tx;
        this.json = json;
    }

    public Result receive(String provider, String body, String signature, String gatewayEventId) {
        PaymentGateway gateway = gateways.find(provider)
                .orElseThrow(() -> new NotFoundException("No payment provider '%s'".formatted(provider)));
        if (!gateway.verifyWebhook(body, signature)) {
            log.warn("Webhook from {} rejected: bad or missing signature", provider);
            return Result.INVALID_SIGNATURE;
        }
        JsonNode root = json.readTree(body);
        String event = root.path("event").asString();
        String type = switch (event) {
            case "payment.captured" -> "PaymentCaptured";
            case "payment.failed" -> "PaymentFailed";
            default -> null;
        };
        if (type == null) {
            log.info("Webhook from {}: {} ignored (not a payment event we use)", provider, event);
            return Result.IGNORED;
        }
        JsonNode payment = root.path("payload").path("payment").path("entity");
        String gatewayOrderId = payment.path("order_id").asString();
        Order order = orders.findByPaymentProviderAndGatewayOrderId(provider, gatewayOrderId).orElse(null);
        if (order == null) {
            log.info("Webhook from {}: {} for unknown gateway order {} ignored", provider, event, gatewayOrderId);
            return Result.IGNORED;
        }
        // The same gateway event always maps to the same outbox event id (a name-based UUID).
        String sourceId = gatewayEventId != null && !gatewayEventId.isBlank() ? gatewayEventId : body;
        UUID eventId = UUID.nameUUIDFromBytes((provider + ":" + sourceId).getBytes(StandardCharsets.UTF_8));
        PaymentEvent data = new PaymentEvent(provider, gatewayEventId, gatewayOrderId,
                payment.path("id").asString(), payment.path("status").asString());
        try {
            tx.executeWithoutResult(s -> outbox.append(eventId, Topics.PAYMENTS, order.getId(), type, data));
        } catch (DataIntegrityViolationException e) {
            // event_id is UNIQUE: this delivery was already recorded (a retry, or a duplicate in flight).
            log.info("Webhook from {}: {} for order {} already recorded ({}), duplicate ignored",
                    provider, event, order.getId(), gatewayEventId);
            return Result.DUPLICATE;
        }
        log.info("Webhook from {}: {} for order {} (payment {}) queued as {}",
                provider, event, order.getId(), data.paymentId(), eventId);
        return Result.ACCEPTED;
    }
}
