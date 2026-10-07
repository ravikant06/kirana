package com.kirana.auth;

import java.text.ParseException;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.Date;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.stream.Collectors;

import com.nimbusds.jose.JOSEException;
import com.nimbusds.jose.JWSAlgorithm;
import com.nimbusds.jose.JWSHeader;
import com.nimbusds.jose.crypto.RSASSASigner;
import com.nimbusds.jose.crypto.RSASSAVerifier;
import com.nimbusds.jose.jwk.JWKSet;
import com.nimbusds.jose.jwk.KeyUse;
import com.nimbusds.jose.jwk.RSAKey;
import com.nimbusds.jose.jwk.gen.RSAKeyGenerator;
import com.nimbusds.jwt.JWTClaimsSet;
import com.nimbusds.jwt.SignedJWT;
import com.kirana.entity.Role;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

/**
 * Issues and verifies access tokens. The RSA key pair is generated at startup and kept in
 * memory; the private half never leaves this class, the public half is the JWKS.
 */
@Service
public class TokenService {

    public static final String ISSUER = "kirana";
    /** Who may accept the token: Kirana's API and the AI service, which forwards it back here. */
    public static final List<String> AUDIENCE = List.of("kirana-api", "kirana-ai");
    private static final String THIS_AUDIENCE = "kirana-api";

    private final RSAKey key;
    private final Duration ttl;
    private final Clock clock;

    @Autowired
    public TokenService(@Value("${kirana.auth.token-ttl:1h}") Duration ttl) {
        this(generateKey(), ttl, Clock.systemUTC());
    }

    TokenService(RSAKey key, Duration ttl, Clock clock) {
        this.key = key;
        this.ttl = ttl;
        this.clock = clock;
    }

    public record Issued(String token, Instant expiresAt) {
    }

    public Issued issue(long userId, String name, Role role) {
        Instant now = clock.instant();
        Instant exp = now.plus(ttl);
        JWTClaimsSet claims = new JWTClaimsSet.Builder()
                .issuer(ISSUER)
                .subject(Long.toString(userId))
                .audience(AUDIENCE)
                .issueTime(Date.from(now))
                .expirationTime(Date.from(exp))
                .jwtID(UUID.randomUUID().toString())
                .claim("name", name)
                .claim("role", role.name())
                // OAuth's convention: permissions as one space-separated string. Both services check these.
                .claim("scope", role.permissions().stream().map(Permission::scope).collect(Collectors.joining(" ")))
                .build();
        // The kid tells a verifier which published key to use (and makes rotation possible).
        SignedJWT jwt = new SignedJWT(new JWSHeader.Builder(JWSAlgorithm.RS256).keyID(key.getKeyID()).build(), claims);
        try {
            jwt.sign(new RSASSASigner(key));
        } catch (JOSEException e) {
            throw new IllegalStateException("Could not sign a token", e);
        }
        return new Issued(jwt.serialize(), exp);
    }

    /**
     * The caller inside a valid token. Every check is explicit, in the order an attacker would
     * try them: the algorithm (no "none", no HS256 with the public key as secret), the key id,
     * the signature, then issuer, audience and expiry.
     */
    public AuthUser verify(String token) {
        try {
            SignedJWT jwt = SignedJWT.parse(token);
            if (!JWSAlgorithm.RS256.equals(jwt.getHeader().getAlgorithm())) {
                throw new InvalidTokenException("Unsupported algorithm " + jwt.getHeader().getAlgorithm());
            }
            if (!key.getKeyID().equals(jwt.getHeader().getKeyID())) {
                throw new InvalidTokenException("Unknown signing key");
            }
            if (!jwt.verify(new RSASSAVerifier(key.toRSAPublicKey()))) {
                throw new InvalidTokenException("Bad signature");
            }
            JWTClaimsSet claims = jwt.getJWTClaimsSet();
            if (!ISSUER.equals(claims.getIssuer())) {
                throw new InvalidTokenException("Wrong issuer");
            }
            if (claims.getAudience() == null || !claims.getAudience().contains(THIS_AUDIENCE)) {
                throw new InvalidTokenException("Token not meant for this service");
            }
            Date exp = claims.getExpirationTime();
            if (exp == null || !exp.toInstant().isAfter(clock.instant())) {
                throw new InvalidTokenException("Token expired");
            }
            String scope = claims.getStringClaim("scope");
            Set<String> scopes = scope == null || scope.isBlank() ? Set.of()
                    : new LinkedHashSet<>(List.of(scope.trim().split("\\s+")));
            return new AuthUser(Long.parseLong(claims.getSubject()), claims.getStringClaim("role"), Set.copyOf(scopes));
        } catch (ParseException | JOSEException | NumberFormatException e) {
            throw new InvalidTokenException("Malformed token");
        }
    }

    /** The public key only (toPublicJWK drops the private parts). */
    public Map<String, Object> jwks() {
        return new JWKSet(key.toPublicJWK()).toJSONObject();
    }

    private static RSAKey generateKey() {
        try {
            return new RSAKeyGenerator(2048)
                    .keyUse(KeyUse.SIGNATURE)
                    .algorithm(JWSAlgorithm.RS256)
                    .keyIDFromThumbprint(true)
                    .generate();
        } catch (JOSEException e) {
            throw new IllegalStateException("Could not generate the signing key", e);
        }
    }

    public static class InvalidTokenException extends RuntimeException {
        public InvalidTokenException(String message) {
            super(message);
        }
    }
}
