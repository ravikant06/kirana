package com.kirana.payment;

/** The gateway could not be reached or answered 5xx. Transient: safe to try again later. */
public class PaymentUnavailableException extends RuntimeException {

    public PaymentUnavailableException(String message, Throwable cause) {
        super(message, cause);
    }
}
