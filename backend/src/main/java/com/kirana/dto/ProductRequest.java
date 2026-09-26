package com.kirana.dto;

import java.math.BigDecimal;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

/**
 * Body for create and update. price arrives as a string from the form (or a number from
 * other clients; Jackson turns 100 into "100"), and is validated by {@link ValidPrice}.
 */
public record ProductRequest(
        @NotBlank @Size(max = 200) String name,
        @Size(max = 5000) String description,
        @NotNull @ValidPrice String price) {

    /** Only call after validation. */
    public double priceValue() {
        return new BigDecimal(price.trim()).doubleValue();
    }
}
