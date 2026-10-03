package com.kirana.warehouse;

/** 4xx: the warehouse will never accept this request as it is. Retrying cannot help. */
public class WarehouseRejectedException extends RuntimeException {

    public WarehouseRejectedException(String message) {
        super(message);
    }
}
