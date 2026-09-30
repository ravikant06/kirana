package com.kirana.config;

import java.time.Duration;

import org.springframework.boot.context.properties.ConfigurationProperties;

/** Binds kirana.payment.* (Stage 5). */
@ConfigurationProperties(prefix = "kirana.payment")
public record PaymentProperties(
        String defaultProvider,
        String currency,
        Duration window,
        Duration reconcileAfter,
        Http http,
        Gateway mock,
        Gateway razorpay) {

    /** Client-side timeouts for every gateway call. */
    public record Http(Duration connectTimeout, Duration readTimeout) {
    }

    /**
     * apiUrl: where the backend calls the gateway. checkoutUrl: where the browser opens its
     * checkout (only the mock hosts one; Razorpay's comes from its checkout.js).
     */
    public record Gateway(String apiUrl, String checkoutUrl, String keyId, String keySecret) {

        public boolean configured() {
            return keyId != null && !keyId.isBlank() && keySecret != null && !keySecret.isBlank();
        }
    }
}
