package com.kirana.controller;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.kirana.idempotency.IdempotencyStore;
import com.kirana.idempotency.IdempotentRequests;
import com.kirana.service.CartService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

@WebMvcTest(CartController.class)
@Import(IdempotentRequests.class)
class CartControllerTest {

    @Autowired
    MockMvc mvc;

    @MockitoBean
    CartService carts;

    @MockitoBean
    IdempotencyStore keys;

    @Test
    void missingIdempotencyKeyIs400() throws Exception {
        mvc.perform(post("/cart/items").with(com.kirana.auth.TestAuth.as("1")).contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"productId": 1, "quantity": 1}
                                """))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.errors[0].field").value("Idempotency-Key"));
    }

    @Test
    void noTokenIs401() throws Exception {
        mvc.perform(get("/cart"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("UNAUTHENTICATED"));
    }

    @Test
    void quantityMustBePositive() throws Exception {
        mvc.perform(post("/cart/items").with(com.kirana.auth.TestAuth.as("1")).header("Idempotency-Key", "k1")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"productId": 1, "quantity": 0}
                                """))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.errors[0].field").value("quantity"));
    }
}
