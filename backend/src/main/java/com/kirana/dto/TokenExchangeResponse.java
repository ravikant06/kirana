package com.kirana.dto;

public record TokenExchangeResponse(String access_token, String issued_token_type, String token_type,
                                    long expires_in, String scope) {
}
