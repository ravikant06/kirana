package com.kirana.dto;

import com.kirana.payment.PaymentSession;

/**
 * POST /orders answer: the order (CREATED, stock held), and either a payment session to open the
 * gateway's checkout, or paymentProblem when the gateway was unavailable (pay later from Orders).
 */
public record CheckoutResponse(OrderResponse order, PaymentSession payment, String paymentProblem) {
}
