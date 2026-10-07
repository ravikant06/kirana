package com.kirana.dto;

import java.time.Instant;
import java.util.List;

/** permissions: what this user may do, for the UI to show or hide things. The servers still check. */
public record LoginResponse(String accessToken, String tokenType, Instant expiresAt, UserResponse user,
                            List<String> permissions) {
}
