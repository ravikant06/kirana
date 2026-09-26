package com.kirana.controller;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.kirana.service.CartService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

@WebMvcTest(CartController.class)
class CartControllerTest {

    @Autowired
    MockMvc mvc;

    @MockitoBean
    CartService carts;

    @Test
    void missingUserHeaderIs400() throws Exception {
        mvc.perform(get("/cart"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.detail").value("Required header 'X-User-Id' is not present."));
    }

    @Test
    void quantityMustBePositive() throws Exception {
        mvc.perform(post("/cart/items").header("X-User-Id", "1").contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"productId": 1, "quantity": 0}
                                """))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.errors[0].field").value("quantity"));
    }
}
