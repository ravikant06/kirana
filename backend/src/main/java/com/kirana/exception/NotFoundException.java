package com.kirana.exception;

/** 404. Also used for soft-deleted products and for resources owned by another user. */
public class NotFoundException extends RuntimeException {

    public NotFoundException(String detail) {
        super(detail);
    }
}
