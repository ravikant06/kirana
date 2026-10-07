package com.kirana.auth;

import java.util.Set;

import jakarta.servlet.http.HttpServletRequest;

/**
 * The verified caller, taken from the token alone (no database read per request). The flip side:
 * a role change takes effect at the next sign-in, at most one token lifetime later.
 */
public record AuthUser(long id, String role, Set<String> scopes) {

    /** Request attribute the token filter sets; nothing else may set it. */
    public static final String ATTRIBUTE = AuthUser.class.getName();

    public boolean can(Permission permission) {
        return scopes.contains(permission.scope());
    }

    public static AuthUser of(HttpServletRequest request) {
        return (AuthUser) request.getAttribute(ATTRIBUTE);
    }
}
