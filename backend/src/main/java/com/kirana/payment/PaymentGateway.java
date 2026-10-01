package com.kirana.payment;

/**
 * What checkout needs from a payment gateway. The browser pays (card data never reaches our
 * server); the server creates the gateway order, verifies the signed result, and can ask the
 * gateway for the truth later (reconciliation).
 */
public interface PaymentGateway {

    /** "mock" or "razorpay"; stored on the order. */
    String id();

    String label();

    /** False when not configured (e.g. Razorpay keys missing). */
    boolean available();

    /**
     * Creates the gateway's order for our order. Idempotent: our order id is sent as the
     * receipt, so asking twice for the same order returns the same gateway order.
     *
     * @throws PaymentUnavailableException on timeouts, connection errors and 5xx (transient)
     */
    GatewayOrder createOrder(long orderId, long amountPaise, String currency);

    /** What the gateway knows about this gateway order right now. Used by the reconciler. */
    GatewayStatus fetchStatus(String gatewayOrderId);

    /**
     * True only if the signature proves the gateway said "paid": HMAC-SHA256 of
     * "gatewayOrderId|paymentId" with our key secret. A browser cannot forge it.
     */
    boolean verifySignature(String gatewayOrderId, String paymentId, String signature);

    /**
     * True only if this webhook body was signed by the gateway: HMAC-SHA256 of the raw body with
     * the webhook secret. False when no webhook secret is configured.
     */
    boolean verifyWebhook(String body, String signature);

    /** What the browser needs to open this gateway's checkout. */
    PaymentSession session(long orderId, GatewayOrder order);
}
