package com.kirana.idempotency;

/** 422: the key was already used for a different request (another endpoint, or another body). */
public class IdempotencyKeyReusedException extends RuntimeException {

    public IdempotencyKeyReusedException(String firstEndpoint) {
        super("This Idempotency-Key was already used for a different request (first used on " + firstEndpoint
                + "). Use a new key for a new action.");
    }
}
