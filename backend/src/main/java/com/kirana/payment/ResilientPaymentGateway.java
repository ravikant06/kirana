package com.kirana.payment;

import com.kirana.resilience.Resilience;

/**
 * Decorator: the same gateway, with Stage 5's retry and circuit breaker around its network calls.
 * The saga never knows the difference. createOrder and fetchStatus are idempotent (keyed by our
 * order id), so both may be retried. Signature checks are local and pass straight through.
 */
public class ResilientPaymentGateway implements PaymentGateway {

    private final PaymentGateway delegate;
    private final Resilience resilience;

    public ResilientPaymentGateway(PaymentGateway delegate, Resilience resilience) {
        this.delegate = delegate;
        this.resilience = resilience;
    }

    @Override
    public String id() {
        return delegate.id();
    }

    @Override
    public String label() {
        return delegate.label();
    }

    @Override
    public boolean available() {
        return delegate.available();
    }

    @Override
    public GatewayOrder createOrder(long orderId, long amountPaise, String currency) {
        return resilience.payment(id(), true, () -> delegate.createOrder(orderId, amountPaise, currency));
    }

    @Override
    public GatewayStatus fetchStatus(String gatewayOrderId) {
        return resilience.payment(id(), true, () -> delegate.fetchStatus(gatewayOrderId));
    }

    @Override
    public boolean verifySignature(String gatewayOrderId, String paymentId, String signature) {
        return delegate.verifySignature(gatewayOrderId, paymentId, signature);
    }

    @Override
    public boolean verifyWebhook(String body, String signature) {
        return delegate.verifyWebhook(body, signature);
    }

    @Override
    public PaymentSession session(long orderId, GatewayOrder order) {
        return delegate.session(orderId, order);
    }
}
