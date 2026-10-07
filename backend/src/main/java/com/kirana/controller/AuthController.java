package com.kirana.controller;

import java.time.Duration;
import java.time.Instant;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;

import com.kirana.auth.AuthClients;
import com.kirana.auth.CurrentUser;
import com.kirana.auth.Permission;
import com.kirana.auth.TokenService;
import com.kirana.dto.LoginRequest;
import com.kirana.dto.LoginResponse;
import com.kirana.dto.TokenExchangeRequest;
import com.kirana.dto.TokenExchangeResponse;
import com.kirana.exception.InvalidFieldException;
import com.kirana.dto.UserResponse;
import com.kirana.entity.User;
import com.kirana.mapper.UserMapper;
import com.kirana.service.UserService;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestController;

/** Sign-in (AI Phase 5): email + password → an RS256 token carrying the user's role and permissions. */
@RestController
public class AuthController {

    private final TokenService tokens;
    private final UserService users;
    private final AuthClients clients;

    public AuthController(TokenService tokens, UserService users, AuthClients clients) {
        this.tokens = tokens;
        this.users = users;
        this.clients = clients;
    }

    /** 401 "Invalid email or password" for both an unknown email and a wrong password. */
    @PostMapping("/auth/login")
    public LoginResponse login(@Valid @RequestBody LoginRequest req) {
        User user = users.authenticate(req.email(), req.password());
        TokenService.Issued issued = tokens.issue(user.getId(), user.getName(), user.getRole());
        List<String> permissions = user.getRole().permissions().stream().map(Permission::scope).toList();
        return new LoginResponse(issued.token(), "Bearer", issued.expiresAt(), UserMapper.toResponse(user), permissions);
    }

    /** Who the token says you are (401 without one). */
    @GetMapping("/auth/me")
    public UserResponse me(@CurrentUser Long userId) {
        return UserMapper.toResponse(users.require(userId));
    }

    /**
     * Token exchange (Phase 6 M3): the AI service, authenticated as a client, trades the shopper's
     * token for a short-lived one with only the scopes a tool needs, marked act = kirana-ai.
     * 401 bad client or subject token · 403 a scope the user doesn't hold.
     */
    @PostMapping("/auth/token-exchange")
    public TokenExchangeResponse exchange(@RequestHeader(name = "Authorization", required = false) String clientAuth,
                                          @Valid @RequestBody TokenExchangeRequest req) {
        String client = clients.authenticate(clientAuth);
        if (!TokenExchangeRequest.GRANT.equals(req.grant_type())) {
            throw new InvalidFieldException("grant_type", "must be " + TokenExchangeRequest.GRANT);
        }
        var scopes = new LinkedHashSet<>(List.of(req.scope().trim().split("\\s+")));
        TokenService.Issued issued = tokens.exchange(req.subject_token(), scopes, client);
        return new TokenExchangeResponse(issued.token(), "urn:ietf:params:oauth:token-type:access_token", "Bearer",
                Duration.between(Instant.now(), issued.expiresAt()).toSeconds(), String.join(" ", scopes));
    }

    /** The public key that verifies our tokens. The AI service fetches and caches it by kid. */
    @GetMapping("/.well-known/jwks.json")
    public Map<String, Object> jwks() {
        return tokens.jwks();
    }
}
