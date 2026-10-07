package com.kirana.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

/** RFC 8693 field names, so any OAuth client library can talk to it. */
public record TokenExchangeRequest(
        @NotBlank String grant_type,
        @NotBlank @Size(max = 4096) String subject_token,
        @NotBlank @Size(max = 500) String scope) {

    public static final String GRANT = "urn:ietf:params:oauth:grant-type:token-exchange";
}
