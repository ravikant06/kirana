package com.kirana.auth;

/**
 * What a caller may do. Sent in the token's "scope" claim (OAuth's convention: space-separated),
 * so the AI service checks the same names without asking Kirana.
 */
public enum Permission {
    /** Own cart, own orders, payments, cancels. */
    SHOP("shop"),
    /** Talk to the AI assistant (checked by kirana-ai). */
    CHAT("chat"),
    /** Create, edit and delete products, images, stock and flash sales. */
    CATALOG_WRITE("catalog:write"),
    /** List and search every user. */
    USERS_READ("users:read"),
    /** Upload and delete knowledge-base documents (checked by kirana-ai). */
    KB_WRITE("kb:write"),
    /** The resilience lab: breakers, chaos, dead letters. */
    SYSTEM("system");

    private final String scope;

    Permission(String scope) {
        this.scope = scope;
    }

    public String scope() {
        return scope;
    }
}
