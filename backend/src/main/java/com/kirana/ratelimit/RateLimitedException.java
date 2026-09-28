package com.kirana.ratelimit;

/** 429: this shopper sent too many requests of this kind. */
public class RateLimitedException extends RuntimeException {

    private final long retryAfterMillis;

    public RateLimitedException(String what, long retryAfterMillis) {
        super("Too many %s requests. Try again in %d s.".formatted(what, Math.max(1, (retryAfterMillis + 999) / 1000)));
        this.retryAfterMillis = retryAfterMillis;
    }

    public long getRetryAfterSeconds() {
        return Math.max(1, (retryAfterMillis + 999) / 1000);
    }
}
