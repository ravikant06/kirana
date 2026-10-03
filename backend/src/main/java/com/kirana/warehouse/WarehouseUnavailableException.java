package com.kirana.warehouse;

/** Timeouts, connection errors, 5xx: the warehouse may be back soon. Safe to retry (idempotent call). */
public class WarehouseUnavailableException extends RuntimeException {

    public WarehouseUnavailableException(String message, Throwable cause) {
        super(message, cause);
    }
}
