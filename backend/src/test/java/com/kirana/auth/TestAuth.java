package com.kirana.auth;

import java.util.Set;
import java.util.stream.Collectors;

import com.kirana.entity.Role;
import org.springframework.test.web.servlet.request.RequestPostProcessor;

/**
 * Signs a MockMvc request in as a user, the way BearerTokenFilter would after a valid token.
 * Works in web slices too (no filter, no TokenService there). Token checks themselves are tested
 * in TokenServiceTest and BearerTokenFilterTest.
 */
public final class TestAuth {

    private TestAuth() {
    }

    public static RequestPostProcessor as(Object userId) {
        return as(userId, Role.SHOPPER);
    }

    public static RequestPostProcessor admin() {
        return as(1, Role.ADMIN);
    }

    public static RequestPostProcessor as(Object userId, Role role) {
        long id = Long.parseLong(String.valueOf(userId).trim());
        Set<String> scopes = role.permissions().stream().map(Permission::scope).collect(Collectors.toSet());
        return request -> {
            request.setAttribute(AuthUser.ATTRIBUTE, new AuthUser(id, role.name(), scopes));
            return request;
        };
    }
}
