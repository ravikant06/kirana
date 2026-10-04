package com.kirana.controller;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.time.Instant;
import java.util.List;

import com.kirana.dto.ProductDetail;
import com.kirana.exception.NotFoundException;
import com.kirana.service.ProductService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.transaction.CannotCreateTransactionException;

/** Web slice: controller + validation + GlobalExceptionHandler, with the service mocked. */
@WebMvcTest(ProductController.class)
class ProductControllerTest {

    @Autowired
    MockMvc mvc;

    @MockitoBean
    ProductService products;

    @Test
    void invalidBodyReturnsProblemDetailWithFieldErrors() throws Exception {
        mvc.perform(post("/products").contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"name": "", "price": "12.345"}
                                """))
                .andExpect(status().isBadRequest())
                .andExpect(header().string("Content-Type", "application/problem+json"))
                .andExpect(jsonPath("$.title").value("Validation failed"))
                .andExpect(jsonPath("$.status").value(400))
                .andExpect(jsonPath("$.errors[?(@.field == 'name')].message").value("must not be blank"))
                .andExpect(jsonPath("$.errors[?(@.field == 'price')].message").value("must have at most 2 decimal places"));

        verify(products, never()).create(any());
    }

    @Test
    void numericPriceIsAcceptedAsWellAsString() throws Exception {
        when(products.create(any())).thenReturn(new ProductDetail(7L, "Tea", null, 80.0, 0, List.of(),
                Instant.EPOCH, Instant.EPOCH, 0, "Beverages"));

        mvc.perform(post("/products").contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"name": "Tea", "price": 80}
                                """))
                .andExpect(status().isCreated())
                .andExpect(header().string("Location", "/products/7"))
                .andExpect(jsonPath("$.id").value(7));
    }

    @Test
    void malformedJsonIs400ProblemDetail() throws Exception {
        mvc.perform(post("/products").contentType(MediaType.APPLICATION_JSON).content("{bad"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.title").value("Malformed request"));
    }

    @Test
    void exhaustedPoolIs503ProblemDetail() throws Exception {
        when(products.list(0, 12)).thenThrow(new CannotCreateTransactionException(
                "Could not open JPA EntityManager for transaction",
                new java.sql.SQLTransientConnectionException("HikariPool-1 - Connection is not available")));

        mvc.perform(get("/products"))
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.title").value("Database busy"));
    }

    @Test
    void missingProductIs404ProblemDetail() throws Exception {
        when(products.get(42L)).thenThrow(new NotFoundException("Product 42 not found"));

        mvc.perform(get("/products/42"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.title").value("Not found"))
                .andExpect(jsonPath("$.detail").value("Product 42 not found"));
    }
}
