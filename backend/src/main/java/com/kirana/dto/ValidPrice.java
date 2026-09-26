package com.kirana.dto;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

import jakarta.validation.Constraint;
import jakarta.validation.Payload;

/**
 * A price given as text: a plain decimal number, greater than 0, at most 2 decimal places.
 * Null is allowed here; combine with @NotNull.
 */
@Target({ElementType.FIELD, ElementType.RECORD_COMPONENT, ElementType.PARAMETER})
@Retention(RetentionPolicy.RUNTIME)
@Constraint(validatedBy = PriceValidator.class)
public @interface ValidPrice {

    String message() default "must be a valid price";

    Class<?>[] groups() default {};

    Class<? extends Payload>[] payload() default {};
}
