package com.kirana.config;

import java.time.Duration;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Binds the kirana.storage.* block in application.yml.
 *
 * endpoint:    the address browsers use; signed upload and read URLs point here.
 * apiEndpoint: the address the backend itself calls (stat, delete). Usually the same; the
 *              chaos profile points it through Toxiproxy. Stage 10 (Docker) makes them differ
 *              for real: inside a container "localhost" is not MinIO.
 * region:      fixed, so signing URLs is pure local computation (no bucket-location lookup),
 *              and keeps working while MinIO is down.
 */
@ConfigurationProperties(prefix = "kirana.storage")
public record StorageProperties(
        String endpoint,
        String apiEndpoint,
        String region,
        String accessKey,
        String secretKey,
        String bucket,
        long maxUploadBytes,
        Duration uploadUrlTtl,
        Duration connectTimeout,
        Duration readTimeout) {

    public String apiEndpointOrDefault() {
        return apiEndpoint == null || apiEndpoint.isBlank() ? endpoint : apiEndpoint;
    }
}
