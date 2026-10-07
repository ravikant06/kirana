package com.kirana.auth;

import java.io.IOException;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Authentication only: "Authorization: Bearer <jwt>" becomes the request's {@link AuthUser}.
 * Whether that caller may use the endpoint is decided later ({@link PermissionInterceptor}).
 *
 * No token: the request continues anonymous, so public endpoints (products, login) work and
 * protected ones answer 401. A token that fails verification is 401 here, never anonymous: a
 * client that sent a bad token must hear about it, not be silently downgraded.
 *
 * Registered first in the chain by {@code AuthConfig}. There is no other way to say who you are:
 * the X-User-Id header of Stages 1-7 is gone.
 */
public class BearerTokenFilter extends OncePerRequestFilter {

    private static final Logger log = LoggerFactory.getLogger(BearerTokenFilter.class);

    private final TokenService tokens;

    public BearerTokenFilter(TokenService tokens) {
        this.tokens = tokens;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        String auth = request.getHeader("Authorization");
        if (auth != null && auth.regionMatches(true, 0, "Bearer ", 0, 7)) {
            try {
                AuthUser user = tokens.verify(auth.substring(7).trim());
                if (user.actor() != null) {
                    // Delegated: "user 2, via kirana-ai". Every such request is visible in the log.
                    log.info("{} acting for user {}: {} {} scope={}", user.actor(), user.id(),
                            request.getMethod(), request.getRequestURI(), user.scopes());
                }
                request.setAttribute(AuthUser.ATTRIBUTE, user);
            } catch (TokenService.InvalidTokenException e) {
                log.info("Rejected token on {} {}: {}", request.getMethod(), request.getRequestURI(), e.getMessage());
                unauthorized(request, response, e.getMessage());
                return;
            }
        }
        chain.doFilter(request, response);
    }

    /** Same ProblemDetail shape as GlobalExceptionHandler; written here because filters run before it. */
    private static void unauthorized(HttpServletRequest request, HttpServletResponse response, String reason)
            throws IOException {
        response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
        response.setHeader("WWW-Authenticate", "Bearer error=\"invalid_token\"");
        response.setContentType("application/problem+json");
        response.getWriter().write("""
                {"type":"about:blank","title":"Unauthorized","status":401,"detail":"%s","instance":"%s","code":"UNAUTHENTICATED"}"""
                .formatted(reason.replace("\"", "'"), request.getRequestURI().replace("\"", "")));
    }
}
