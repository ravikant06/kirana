package com.kirana.controller;

import java.net.URI;
import java.util.List;

import com.kirana.idempotency.IdempotentRequests;
import com.kirana.dto.CheckoutRequest;
import com.kirana.dto.CheckoutResponse;
import com.kirana.dto.OrderResponse;
import com.kirana.dto.VerifyPaymentRequest;
import com.kirana.payment.PaymentSession;
import com.kirana.resilience.Resilience;
import com.kirana.service.CheckoutSaga;
import com.kirana.service.OrderService;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/orders")
public class OrderController {

    private final OrderService orders;
    private final CheckoutSaga checkout;
    private final Resilience resilience;
    private final IdempotentRequests idempotency;

    public OrderController(OrderService orders, CheckoutSaga checkout, Resilience resilience,
                           IdempotentRequests idempotency) {
        this.idempotency = idempotency;
        this.orders = orders;
        this.checkout = checkout;
        this.resilience = resilience;
    }

    /**
     * Places the order (stock held) and starts payment. Body optional: {"paymentProvider": "mock"}.
     * Stage 7: Idempotency-Key required; a retry gets the same order back (201, same body).
     */
    @PostMapping
    public ResponseEntity<?> place(@RequestHeader(Headers.USER_ID) Long userId,
                                   @RequestHeader(name = IdempotentRequests.HEADER, required = false) String key,
                                   @RequestBody(required = false) CheckoutRequest req) {
        // A replay is answered before the bulkhead: it needs no checkout slot.
        // Stage 5 bulkhead: at most 20 checkouts at once; the 21st gets 503 "Checkout busy" at once.
        return idempotency.execute(userId, key, "POST /orders", req, HttpStatus.CREATED,
                (CheckoutResponse r) -> URI.create("/orders/" + r.order().id()),
                () -> resilience.checkout(() -> checkout.start(userId, req == null ? null : req.paymentProvider())));
    }

    @GetMapping
    public List<OrderResponse> list(@RequestHeader(Headers.USER_ID) Long userId) {
        return orders.list(userId);
    }

    @GetMapping("/{id}")
    public OrderResponse get(@RequestHeader(Headers.USER_ID) Long userId, @PathVariable Long id) {
        return orders.get(userId, id);
    }

    /** "Pay now" for an order awaiting payment. Returns the same gateway order if one exists. */
    @PostMapping("/{id}/payment")
    public ResponseEntity<?> pay(@RequestHeader(Headers.USER_ID) Long userId, @PathVariable Long id,
                                 @RequestHeader(name = IdempotentRequests.HEADER, required = false) String key,
                                 @RequestBody(required = false) CheckoutRequest req) {
        return idempotency.execute(userId, key, "POST /orders/" + id + "/payment", req,
                () -> checkout.requestPayment(userId, id, req == null ? null : req.paymentProvider()));
    }

    /** The browser reports a successful payment; it counts only if the signature is valid. */
    @PostMapping("/{id}/payment/verify")
    public OrderResponse verify(@RequestHeader(Headers.USER_ID) Long userId, @PathVariable Long id,
                                @Valid @RequestBody VerifyPaymentRequest req) {
        return checkout.verifyPayment(userId, id, req.gatewayOrderId(), req.paymentId(), req.signature());
    }

    /** Stage 7: a retry gets the first answer (200, cancelled), not 409 "not awaiting payment". */
    @PostMapping("/{id}/cancel")
    public ResponseEntity<?> cancel(@RequestHeader(Headers.USER_ID) Long userId, @PathVariable Long id,
                                    @RequestHeader(name = IdempotentRequests.HEADER, required = false) String key) {
        return idempotency.execute(userId, key, "POST /orders/" + id + "/cancel", null,
                () -> checkout.cancel(userId, id));
    }
}
