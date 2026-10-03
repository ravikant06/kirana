package com.kirana.idempotency;

/** 409: the first attempt with this key is still running. Retry shortly with the same key. */
public class IdempotencyInProgressException extends RuntimeException {

    public IdempotencyInProgressException() {
        super("A request with this Idempotency-Key is still being processed. Retry in a moment with the same key.");
    }
}
