package com.kirana.storage;

import io.minio.MinioClient;

/**
 * A MinIO client used only to sign URLs for browsers, built with the public endpoint and a
 * fixed region. Signing is local HMAC work: it never calls MinIO, so it works during an outage.
 */
public record MinioSigner(MinioClient client) {
}
