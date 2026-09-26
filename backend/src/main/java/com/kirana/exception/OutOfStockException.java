package com.kirana.exception;

/**
 * Unchecked on purpose: Spring rolls back @Transactional methods on RuntimeException by default.
 * Stage 1 experiment 2 makes this checked to see what changes.
 */
public class OutOfStockException extends ConflictException {

    public OutOfStockException(String productName, int available, int requested) {
        super("Out of stock", "Only %d %s of '%s' left, %d requested"
                .formatted(available, available == 1 ? "unit" : "units", productName, requested));
    }
}
