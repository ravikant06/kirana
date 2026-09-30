package com.kirana.payment;

/**
 * The gateway's view of one order. paymentId is set when state is PAID.
 * PENDING: nothing captured yet (not paid, or paid attempts all failed). UNKNOWN: gateway has
 * no record (e.g. the mock restarted), so nothing can be concluded.
 */
public record GatewayStatus(State state, String paymentId) {

    public enum State { PAID, PENDING, UNKNOWN }
}
