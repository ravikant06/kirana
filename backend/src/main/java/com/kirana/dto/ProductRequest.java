package com.kirana.dto;

import java.math.BigDecimal;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

/**
 * Body for create and update. price arrives as a string from the form (or a number from
 * other clients; Jackson turns 100 into "100"), and is validated by {@link ValidPrice}.
 * version: ignored on create, required on update. It is the version the client loaded,
 * so a save based on stale data can be rejected (optimistic locking, R3).
 */
public record ProductRequest(
        @NotBlank @Size(max = 200) String name,
        @Size(max = 5000) String description,
        @NotNull @ValidPrice String price,
        Long version) {

    public ProductRequest(String name, String description, String price) {
        this(name, description, price, null);
    }

    /** Only call after validation. */
    public double priceValue() {
        return new BigDecimal(price.trim()).doubleValue();
    }
}
