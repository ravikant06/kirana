package com.kirana.config;

import java.time.Duration;

import org.springframework.boot.context.properties.ConfigurationProperties;

/** Binds kirana.rate-limit.* (Stage 4): one policy per protected group of endpoints. */
@ConfigurationProperties(prefix = "kirana.rate-limit")
public record RateLimitProperties(Policy orders, Policy cart) {

    /** At most {@code limit} requests per {@code window}, per shopper. */
    public record Policy(int limit, Duration window) {
    }
}
