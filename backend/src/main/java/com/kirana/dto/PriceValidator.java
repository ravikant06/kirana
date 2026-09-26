package com.kirana.dto;

import java.math.BigDecimal;
import java.util.regex.Pattern;

import jakarta.validation.ConstraintValidator;
import jakarta.validation.ConstraintValidatorContext;

/**
 * Parses the price as a decimal (BigDecimal), not a double, so "12.345" can be rejected
 * exactly. Storage is still double (D3); conversion happens only after validation passes.
 */
public class PriceValidator implements ConstraintValidator<ValidPrice, String> {

    static final BigDecimal MAX = new BigDecimal("10000000");

    // Digits with an optional sign and fraction. No exponents, commas, "NaN" or "Infinity".
    private static final Pattern PLAIN_DECIMAL = Pattern.compile("[+-]?\\d+(\\.\\d+)?");

    @Override
    public boolean isValid(String value, ConstraintValidatorContext context) {
        if (value == null) {
            return true;
        }
        String message = check(value);
        if (message == null) {
            return true;
        }
        context.disableDefaultConstraintViolation();
        context.buildConstraintViolationWithTemplate(message).addConstraintViolation();
        return false;
    }

    /** Returns why the price is invalid, or null when it is valid. */
    static String check(String value) {
        String s = value.trim();
        if (s.isEmpty()) {
            return "must not be blank";
        }
        if (!PLAIN_DECIMAL.matcher(s).matches()) {
            return "must be a number, like 49 or 49.50";
        }
        BigDecimal price = new BigDecimal(s);
        if (price.signum() <= 0) {
            return "must be greater than 0";
        }
        if (price.stripTrailingZeros().scale() > 2) {
            return "must have at most 2 decimal places";
        }
        if (price.compareTo(MAX) > 0) {
            return "must be at most " + MAX.toPlainString();
        }
        return null;
    }
}
