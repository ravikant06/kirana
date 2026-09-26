package com.kirana.config;

import java.time.Duration;

import org.springframework.boot.context.properties.ConfigurationProperties;

/** Binds the kirana.storage.* block in application.yml. */
@ConfigurationProperties(prefix = "kirana.storage")
public record StorageProperties(
        String endpoint,
        String accessKey,
        String secretKey,
        String bucket,
        long maxUploadBytes,
        Duration uploadUrlTtl) {
}
