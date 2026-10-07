package com.kirana.controller;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.kirana.auth.AuthClients;
import com.kirana.auth.ForbiddenException;
import com.kirana.auth.TokenService;
import com.kirana.auth.UnauthenticatedException;
import com.kirana.service.UserService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

/** Token exchange errors are 4xx with a reason, never a 500 (found in the Phase 6 review). */
@WebMvcTest(AuthController.class)
class AuthControllerTest {

    private static final String BODY = """
            {"grant_type": "urn:ietf:params:oauth:grant-type:token-exchange", "subject_token": "x", "scope": "orders:read"}
            """;

    @Autowired
    MockMvc mvc;

    @MockitoBean
    TokenService tokens;

    @MockitoBean
    UserService users;

    @MockitoBean
    AuthClients clients;

    @Test
    void aBadSubjectTokenIs401NotA500() throws Exception {
        when(clients.authenticate(anyString())).thenReturn("kirana-ai");
        when(tokens.exchange(eq("x"), any(), eq("kirana-ai"))).thenThrow(new TokenService.InvalidTokenException("Token expired"));
        mvc.perform(post("/auth/token-exchange").header("Authorization", "Basic a2lyYW5hLWFpOng=")
                        .contentType(MediaType.APPLICATION_JSON).content(BODY))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("UNAUTHENTICATED"))
                .andExpect(jsonPath("$.detail").value("Token expired"));
    }

    @Test
    void aBadClientIs401() throws Exception {
        when(clients.authenticate(any())).thenThrow(new UnauthenticatedException("Client authentication failed"));
        mvc.perform(post("/auth/token-exchange").contentType(MediaType.APPLICATION_JSON).content(BODY))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void aScopeTheUserLacksIs403() throws Exception {
        when(clients.authenticate(anyString())).thenReturn("kirana-ai");
        when(tokens.exchange(any(), any(), any())).thenThrow(new ForbiddenException("not held"));
        mvc.perform(post("/auth/token-exchange").header("Authorization", "Basic a2lyYW5hLWFpOng=")
                        .contentType(MediaType.APPLICATION_JSON).content(BODY))
                .andExpect(status().isForbidden());
    }

    @Test
    void aWrongGrantTypeIs400() throws Exception {
        when(clients.authenticate(anyString())).thenReturn("kirana-ai");
        mvc.perform(post("/auth/token-exchange").header("Authorization", "Basic a2lyYW5hLWFpOng=")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(BODY.replace("urn:ietf:params:oauth:grant-type:token-exchange", "password")))
                .andExpect(status().isBadRequest());
    }
}
