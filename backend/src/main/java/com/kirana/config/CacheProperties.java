package com.kirana.config;

import java.time.Duration;

import org.springframework.boot.context.properties.ConfigurationProperties;

/** Binds kirana.cache.* (Stage 4). */
@ConfigurationProperties(prefix = "kirana.cache")
public record CacheProperties(
        Duration productTtl,
        Duration listTtl,
        Duration negativeTtl,
        Duration lockTtl,
        Duration lockWait) {
}
