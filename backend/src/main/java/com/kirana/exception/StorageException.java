package com.kirana.exception;

/** Object storage failed or is unreachable. Mapped to 503. */
public class StorageException extends RuntimeException {

    public StorageException(String message, Throwable cause) {
        super(message, cause);
    }
}
