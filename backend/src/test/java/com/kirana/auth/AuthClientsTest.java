package com.kirana.auth;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.nio.charset.StandardCharsets;
import java.util.Base64;

import org.junit.jupiter.api.Test;

class AuthClientsTest {

    private final AuthClients clients = new AuthClients("kirana-ai", "s3cret");

    private static String basic(String idAndSecret) {
        return "Basic " + Base64.getEncoder().encodeToString(idAndSecret.getBytes(StandardCharsets.UTF_8));
    }

    @Test
    void theRightSecretNamesTheClient() {
        assertThat(clients.authenticate(basic("kirana-ai:s3cret"))).isEqualTo("kirana-ai");
    }

    @Test
    void wrongOrMissingCredentialsAre401() {
        assertThatThrownBy(() -> clients.authenticate(basic("kirana-ai:nope"))).isInstanceOf(UnauthenticatedException.class);
        assertThatThrownBy(() -> clients.authenticate(null)).isInstanceOf(UnauthenticatedException.class);
        assertThatThrownBy(() -> clients.authenticate("Bearer x")).isInstanceOf(UnauthenticatedException.class);
    }
}
