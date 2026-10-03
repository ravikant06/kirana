package com.kirana.idempotency;

/** 503: the key store can't be reached, so the request can't be protected; it is refused (7d, Redis store). */
public class IdempotencyUnavailableException extends RuntimeException {

    public IdempotencyUnavailableException(Throwable cause) {
        super("Idempotency keys can't be checked right now, so the request was not run. Retry with the same key.", cause);
    }
}
