package com.kirana.payment;

/** One refund as the gateway sees it (Stage 6d). Gateways refund asynchronously: PENDING, then PROCESSED. */
public record GatewayRefund(String refundId, String paymentId, long amountPaise, State state) {

    public enum State { PENDING, PROCESSED, FAILED }

    public static State state(String gatewayStatus) {
        return switch (gatewayStatus) {
            case "processed" -> State.PROCESSED;
            case "failed" -> State.FAILED;
            default -> State.PENDING;
        };
    }
}
