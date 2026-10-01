package com.kirana.controller;

import com.kirana.service.PaymentWebhooks;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestController;

/**
 * Called by payment gateways, not by the shopper (Stage 6c). The body is taken as the raw string:
 * the signature is over the exact bytes sent, and re-serialising parsed JSON would change them.
 */
@RestController
public class WebhookController {

    private final PaymentWebhooks webhooks;

    public WebhookController(PaymentWebhooks webhooks) {
        this.webhooks = webhooks;
    }

    @PostMapping("/webhooks/payment/{provider}")
    public ResponseEntity<Void> payment(@PathVariable String provider, @RequestBody String body,
                                        @RequestHeader(name = "X-Razorpay-Signature", required = false) String signature,
                                        @RequestHeader(name = "X-Razorpay-Event-Id", required = false) String eventId) {
        return switch (webhooks.receive(provider, body, signature, eventId)) {
            case INVALID_SIGNATURE -> ResponseEntity.badRequest().build();
            // A duplicate or an event we do not use still gets 200, or the gateway retries it for a day.
            case ACCEPTED, DUPLICATE, IGNORED -> ResponseEntity.ok().build();
        };
    }
}
