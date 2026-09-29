package com.kirana.cache;

/**
 * Every Redis key in one place. The "v1" is the shape version: change what a key holds and
 * bump it, so a deploy never reads entries written by the old code.
 */
public final class CacheKeys {

    private CacheKeys() {
    }

    public static String product(Long id) {
        return "product:v1:" + id;
    }

    public static String productPage(int page, int size) {
        return "products:page:v1:" + page + ":" + size;
    }

    /** Set of product IDs with an armed flash sale, so active sales can be listed without KEYS. */
    public static final String FLASH_ACTIVE = "flash:active";

    public static String flashStock(Long productId) {
        return "flash:stock:" + productId;
    }

    public static String rateLimit(String policy, Long userId) {
        return "rl:" + policy + ":user:" + userId;
    }
}
