package com.kirana.auth;

/** 401: no valid token. */
public class UnauthenticatedException extends RuntimeException {
    public UnauthenticatedException(String detail) {
        super(detail);
    }
}
