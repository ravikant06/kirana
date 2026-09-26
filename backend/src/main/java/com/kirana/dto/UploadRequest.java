package com.kirana.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.Size;

/**
 * sizeBytes is the client's claim and is NOT used to enforce the limit: the signed policy
 * does that inside MinIO. Rejecting on this number would only stop honest clients.
 */
public record UploadRequest(
        @NotBlank @Size(max = 255) String fileName,
        @NotBlank @Pattern(regexp = "image/[A-Za-z0-9.+-]+", message = "must be an image type, like image/png") String contentType,
        @NotNull @Positive Long sizeBytes) {
}
