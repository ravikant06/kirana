package com.kirana.dto;

import jakarta.validation.constraints.NotBlank;

/** What the gateway's checkout hands the browser after a successful payment. */
public record VerifyPaymentRequest(@NotBlank String gatewayOrderId, @NotBlank String paymentId, @NotBlank String signature) {
}
