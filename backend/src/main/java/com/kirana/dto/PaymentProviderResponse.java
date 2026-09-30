package com.kirana.dto;

public record PaymentProviderResponse(String id, String label, boolean available, boolean isDefault) {
}
