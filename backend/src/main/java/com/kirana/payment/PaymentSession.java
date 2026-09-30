package com.kirana.payment;

/**
 * Sent to the browser so it can pay. keyId is public by design (Razorpay's checkout needs it);
 * the key secret never leaves the server. checkoutUrl is set for the mock's hosted page.
 */
public record PaymentSession(
        String provider,
        long orderId,
        String gatewayOrderId,
        long amountPaise,
        String currency,
        String keyId,
        String checkoutUrl) {
}
