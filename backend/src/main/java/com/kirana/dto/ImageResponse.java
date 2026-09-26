package com.kirana.dto;

public record ImageResponse(Long id, String url, String contentType, Long sizeBytes, int position) {
}
