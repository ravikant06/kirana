package com.kirana.auth;

/**
 * What a caller may do. Sent in the token's "scope" claim (OAuth's convention: space-separated),
 * so the AI service checks the same names without asking Kirana.
 */
public enum Permission {
    /** Read your own orders. The AI's order questions get a token with only this (Phase 6 M3). */
    ORDERS_READ("orders:read"),
    /** Place, pay and cancel your own orders. */
    ORDERS_WRITE("orders:write"),
    /** Read your own cart. */
    CART_READ("cart:read"),
    /** Change your own cart. */
    CART_WRITE("cart:write"),
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
