package com.kirana.controller;

import java.net.URI;
import java.util.List;

import com.kirana.dto.CheckoutRequest;
import com.kirana.dto.CheckoutResponse;
import com.kirana.dto.OrderResponse;
import com.kirana.dto.VerifyPaymentRequest;
import com.kirana.payment.PaymentSession;
import com.kirana.service.CheckoutSaga;
import com.kirana.service.OrderService;
import jakarta.validation.Valid;
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

    public OrderController(OrderService orders, CheckoutSaga checkout) {
        this.orders = orders;
        this.checkout = checkout;
    }

    /** Places the order (stock held) and starts payment. Body optional: {"paymentProvider": "mock"}. */
    @PostMapping
    public ResponseEntity<CheckoutResponse> place(@RequestHeader(Headers.USER_ID) Long userId,
                                                  @RequestBody(required = false) CheckoutRequest req) {
        CheckoutResponse result = checkout.start(userId, req == null ? null : req.paymentProvider());
        return ResponseEntity.created(URI.create("/orders/" + result.order().id())).body(result);
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
    public PaymentSession pay(@RequestHeader(Headers.USER_ID) Long userId, @PathVariable Long id,
                              @RequestBody(required = false) CheckoutRequest req) {
        return checkout.requestPayment(userId, id, req == null ? null : req.paymentProvider());
    }

    /** The browser reports a successful payment; it counts only if the signature is valid. */
    @PostMapping("/{id}/payment/verify")
    public OrderResponse verify(@RequestHeader(Headers.USER_ID) Long userId, @PathVariable Long id,
                                @Valid @RequestBody VerifyPaymentRequest req) {
        return checkout.verifyPayment(userId, id, req.gatewayOrderId(), req.paymentId(), req.signature());
    }

    @PostMapping("/{id}/cancel")
    public OrderResponse cancel(@RequestHeader(Headers.USER_ID) Long userId, @PathVariable Long id) {
        return checkout.cancel(userId, id);
    }
}
