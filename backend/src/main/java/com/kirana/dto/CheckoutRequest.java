package com.kirana.dto;

/** Optional body of POST /orders. paymentProvider: "mock" or "razorpay"; default from config. */
public record CheckoutRequest(String paymentProvider) {
}
