package com.kirana.auth;

/** 403: signed in, but without the permission. */
public class ForbiddenException extends RuntimeException {
    public ForbiddenException(String detail) {
        super(detail);
    }
}
