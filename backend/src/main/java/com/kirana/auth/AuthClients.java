package com.kirana.auth;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Base64;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/**
 * The services allowed to exchange a user's token (Phase 6 M3). One today: kirana-ai, a
 * confidential client that proves itself with HTTP Basic (id + secret), so Kirana knows who the
 * actor is before it hands out a token that acts for a user.
 */
@Component
public class AuthClients {

    private final String aiClientId;
    private final byte[] aiClientSecret;

    public AuthClients(@Value("${kirana.auth.ai-client.id:kirana-ai}") String aiClientId,
                       @Value("${kirana.auth.ai-client.secret:kirana-ai-dev-secret}") String aiClientSecret) {
        this.aiClientId = aiClientId;
        this.aiClientSecret = aiClientSecret.getBytes(StandardCharsets.UTF_8);
    }

    /** The client id, or 401. Secrets are compared in constant time. */
    public String authenticate(String authorization) {
        if (authorization == null || !authorization.regionMatches(true, 0, "Basic ", 0, 6)) {
            throw new UnauthenticatedException("Client authentication required (HTTP Basic)");
        }
        String decoded;
        try {
            decoded = new String(Base64.getDecoder().decode(authorization.substring(6).trim()), StandardCharsets.UTF_8);
        } catch (IllegalArgumentException e) {
            throw new UnauthenticatedException("Client authentication failed");
        }
        int colon = decoded.indexOf(':');
        String id = colon < 0 ? decoded : decoded.substring(0, colon);
        byte[] secret = (colon < 0 ? "" : decoded.substring(colon + 1)).getBytes(StandardCharsets.UTF_8);
        if (!aiClientId.equals(id) || !MessageDigest.isEqual(aiClientSecret, secret)) {
            throw new UnauthenticatedException("Client authentication failed");
        }
        return id;
    }
}
