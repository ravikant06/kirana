package com.kirana.exception;

/** 409: the request is well formed but clashes with the current state. */
public class ConflictException extends RuntimeException {

    private final String title;

    public ConflictException(String title, String detail) {
        super(detail);
        this.title = title;
    }

    public String getTitle() {
        return title;
    }
}
