package com.kirana.dto;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.params.provider.ValueSource;

class PriceValidatorTest {

    @ParameterizedTest
    @ValueSource(strings = {"1", "49", "49.5", "49.50", " 12.30 ", "0.01", "10000000", "12.300"})
    void acceptsValidPrices(String price) {
        assertThat(PriceValidator.check(price)).isNull();
    }

    @ParameterizedTest
    @CsvSource(delimiter = '|', value = {
            "abc       | must be a number, like 49 or 49.50",
            "1e5       | must be a number, like 49 or 49.50",
            "1,000     | must be a number, like 49 or 49.50",
            "NaN       | must be a number, like 49 or 49.50",
            "0         | must be greater than 0",
            "-3        | must be greater than 0",
            "0.00      | must be greater than 0",
            "12.345    | must have at most 2 decimal places",
            "10000000.01 | must be at most 10000000",
    })
    void rejectsInvalidPrices(String price, String expected) {
        assertThat(PriceValidator.check(price)).isEqualTo(expected);
    }

    @ParameterizedTest
    @ValueSource(strings = {"", "   "})
    void rejectsBlank(String price) {
        assertThat(PriceValidator.check(price)).isEqualTo("must not be blank");
    }
}
