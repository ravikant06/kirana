package com.kirana.config;

import java.time.Duration;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Binds kirana.fulfilment.* (Stage 6e).
 *
 * mode: "events" (an OrderPaid consumer sends paid orders to the warehouse) or "naive" (the
 * dual write: the payment step calls the warehouse itself, right after its commit). Naive exists
 * only to reproduce the problem the events mode solves.
 */
@ConfigurationProperties(prefix = "kirana.fulfilment")
public record FulfilmentProperties(String mode, String warehouseUrl, String apiKey, Duration connectTimeout,
                                   Duration readTimeout) {

    public boolean naive() {
        return "naive".equals(mode);
    }
}
