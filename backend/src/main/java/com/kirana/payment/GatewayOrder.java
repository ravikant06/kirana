package com.kirana.payment;

public record GatewayOrder(String gatewayOrderId, long amountPaise, String currency) {
}
