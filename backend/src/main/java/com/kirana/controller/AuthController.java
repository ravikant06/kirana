package com.kirana.controller;

import java.util.List;
import java.util.Map;

import com.kirana.auth.CurrentUser;
import com.kirana.auth.Permission;
import com.kirana.auth.TokenService;
import com.kirana.dto.LoginRequest;
import com.kirana.dto.LoginResponse;
import com.kirana.dto.UserResponse;
import com.kirana.entity.User;
import com.kirana.mapper.UserMapper;
import com.kirana.service.UserService;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

/** Sign-in (AI Phase 5): email + password → an RS256 token carrying the user's role and permissions. */
@RestController
public class AuthController {

    private final TokenService tokens;
    private final UserService users;

    public AuthController(TokenService tokens, UserService users) {
        this.tokens = tokens;
        this.users = users;
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

    /** The public key that verifies our tokens. The AI service fetches and caches it by kid. */
    @GetMapping("/.well-known/jwks.json")
    public Map<String, Object> jwks() {
        return tokens.jwks();
    }
}
