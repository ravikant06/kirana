package com.kirana.dto;

import java.time.Instant;
import java.util.Map;

public record UploadTicket(
        Long imageId,
        String objectKey,
        Instant expiresAt,
        String uploadUrl,
        Map<String, String> formFields) {
}
