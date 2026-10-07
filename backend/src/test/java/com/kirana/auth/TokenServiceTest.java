package com.kirana.auth;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.Base64;
import java.util.Date;
import java.util.List;

import com.nimbusds.jose.JWSAlgorithm;
import com.nimbusds.jose.JWSHeader;
import com.nimbusds.jose.crypto.MACSigner;
import com.nimbusds.jose.crypto.RSASSASigner;
import com.nimbusds.jose.jwk.RSAKey;
import com.nimbusds.jose.jwk.gen.RSAKeyGenerator;
import com.nimbusds.jwt.JWTClaimsSet;
import com.nimbusds.jwt.SignedJWT;
import com.kirana.entity.Role;
import org.junit.jupiter.api.Test;

/** The attacks a token check must stop, one per test. */
class TokenServiceTest {

    private static final Instant NOW = Instant.parse("2026-10-04T10:00:00Z");
    private final RSAKey key = rsa("k1");
    private final TokenService tokens = new TokenService(key, Duration.ofHours(1), Clock.fixed(NOW, ZoneOffset.UTC));

    @Test
    void validTokenGivesItsUserRoleAndPermissions() {
        AuthUser user = tokens.verify(tokens.issue(7, "Puja", Role.SHOPPER).token());
        assertThat(user.id()).isEqualTo(7);
        assertThat(user.role()).isEqualTo("SHOPPER");
        assertThat(user.scopes()).containsExactlyInAnyOrder("orders:read", "orders:write", "cart:read", "cart:write", "chat");
        assertThat(user.can(Permission.CATALOG_WRITE)).isFalse();
        assertThat(user.actor()).isNull();
    }

    @Test
    void adminTokenCarriesEveryPermission() {
        AuthUser admin = tokens.verify(tokens.issue(1, "Ravi", Role.ADMIN).token());
        assertThat(admin.scopes()).contains("catalog:write", "kb:write", "system", "users:read");
    }

    @Test
    void editedScopeBreaksTheSignature() {
        String[] parts = tokens.issue(7, "Puja", Role.SHOPPER).token().split("\\.");
        String payload = new String(Base64.getUrlDecoder().decode(parts[1])).replace("\"scope\":\"", "\"scope\":\"catalog:write ");
        String forged = parts[0] + "." + Base64.getUrlEncoder().withoutPadding().encodeToString(payload.getBytes()) + "." + parts[2];
        assertThatThrownBy(() -> tokens.verify(forged)).hasMessage("Bad signature");
    }

    @Test
    void editedSubjectBreaksTheSignature() {
        String[] parts = tokens.issue(1, "Ravi", Role.ADMIN).token().split("\\.");
        String payload = new String(Base64.getUrlDecoder().decode(parts[1])).replace("\"sub\":\"1\"", "\"sub\":\"2\"");
        String forged = parts[0] + "." + Base64.getUrlEncoder().withoutPadding().encodeToString(payload.getBytes()) + "." + parts[2];
        assertThatThrownBy(() -> tokens.verify(forged)).hasMessage("Bad signature");
    }

    @Test
    void algNoneIsRejected() {
        String[] parts = tokens.issue(1, "Ravi", Role.ADMIN).token().split("\\.");
        String none = Base64.getUrlEncoder().withoutPadding().encodeToString("{\"alg\":\"none\"}".getBytes());
        assertThatThrownBy(() -> tokens.verify(none + "." + parts[1] + ".")).isInstanceOf(TokenService.InvalidTokenException.class);
    }

    @Test
    void hs256SignedWithAnyKeyIsRejected() throws Exception {
        SignedJWT jwt = new SignedJWT(new JWSHeader.Builder(JWSAlgorithm.HS256).keyID("k1").build(), claims(1, "kirana-api", NOW.plusSeconds(60)));
        jwt.sign(new MACSigner(new byte[32]));
        assertThatThrownBy(() -> tokens.verify(jwt.serialize())).hasMessageStartingWith("Unsupported algorithm");
    }

    @Test
    void anotherKeyIsRejected() throws Exception {
        SignedJWT jwt = new SignedJWT(new JWSHeader.Builder(JWSAlgorithm.RS256).keyID("k1").build(), claims(1, "kirana-api", NOW.plusSeconds(60)));
        jwt.sign(new RSASSASigner(rsa("k1")));       // same kid, different private key
        assertThatThrownBy(() -> tokens.verify(jwt.serialize())).hasMessage("Bad signature");
    }

    @Test
    void wrongAudienceIsRejected() throws Exception {
        SignedJWT jwt = new SignedJWT(new JWSHeader.Builder(JWSAlgorithm.RS256).keyID("k1").build(), claims(1, "some-other-app", NOW.plusSeconds(60)));
        jwt.sign(new RSASSASigner(key));
        assertThatThrownBy(() -> tokens.verify(jwt.serialize())).hasMessage("Token not meant for this service");
    }

    @Test
    void expiredTokenIsRejected() {
        String token = tokens.issue(1, "Ravi", Role.ADMIN).token();
        TokenService later = new TokenService(key, Duration.ofHours(1), Clock.fixed(NOW.plus(Duration.ofHours(2)), ZoneOffset.UTC));
        assertThatThrownBy(() -> later.verify(token)).hasMessage("Token expired");
    }

    @Test
    void jwksHasNoPrivateParts() {
        @SuppressWarnings("unchecked")
        var k = ((List<java.util.Map<String, Object>>) tokens.jwks().get("keys")).get(0);
        assertThat(k).containsKeys("kty", "n", "e", "kid").doesNotContainKeys("d", "p", "q");
    }

    // --- token exchange (Phase 6 M3) -------------------------------------------------------

    @Test
    void exchangeNarrowsScopesAndMarksTheActor() {
        String user = tokens.issue(7, "Puja", Role.SHOPPER).token();
        TokenService.Issued narrow = tokens.exchange(user, java.util.Set.of("orders:read"), "kirana-ai");
        AuthUser seen = tokens.verify(narrow.token());
        assertThat(seen.id()).isEqualTo(7);
        assertThat(seen.scopes()).containsExactly("orders:read");
        assertThat(seen.actor()).isEqualTo("kirana-ai");
        assertThat(narrow.expiresAt()).isEqualTo(NOW.plus(Duration.ofMinutes(5)));
    }

    @Test
    void aWriteScopeLivesTwoMinutes() {
        String user = tokens.issue(7, "Puja", Role.SHOPPER).token();
        assertThat(tokens.exchange(user, java.util.Set.of("orders:write"), "kirana-ai").expiresAt())
                .isEqualTo(NOW.plus(Duration.ofMinutes(2)));
    }

    @Test
    void youCanOnlyNarrowNeverWiden() {
        String user = tokens.issue(7, "Puja", Role.SHOPPER).token();
        assertThatThrownBy(() -> tokens.exchange(user, java.util.Set.of("orders:read", "catalog:write"), "kirana-ai"))
                .isInstanceOf(ForbiddenException.class);
    }

    @Test
    void anExchangedTokenCantBeExchangedAgain() {
        String user = tokens.issue(7, "Puja", Role.SHOPPER).token();
        String narrow = tokens.exchange(user, java.util.Set.of("orders:read", "orders:write"), "kirana-ai").token();
        assertThatThrownBy(() -> tokens.exchange(narrow, java.util.Set.of("orders:read"), "kirana-ai"))
                .isInstanceOf(TokenService.InvalidTokenException.class);
    }

    @Test
    void aTokenNotIssuedForTheClientCantBeExchanged() {
        String user = tokens.issue(7, "Puja", Role.SHOPPER).token();
        assertThatThrownBy(() -> tokens.exchange(user, java.util.Set.of("orders:read"), "some-other-app"))
                .hasMessageContaining("not issued for client");
    }

    @Test
    void theNarrowTokenIsOnlyForKiranaApi() throws Exception {
        String user = tokens.issue(7, "Puja", Role.SHOPPER).token();
        SignedJWT narrow = SignedJWT.parse(tokens.exchange(user, java.util.Set.of("orders:read"), "kirana-ai").token());
        assertThat(narrow.getJWTClaimsSet().getAudience()).containsExactly("kirana-api");   // the AI service would refuse it
    }

    private static JWTClaimsSet claims(long sub, String aud, Instant exp) {
        return new JWTClaimsSet.Builder().issuer("kirana").subject(Long.toString(sub)).audience(aud)
                .expirationTime(Date.from(exp)).build();
    }

    private static RSAKey rsa(String kid) {
        try {
            return new RSAKeyGenerator(2048).keyID(kid).generate();
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }
}
