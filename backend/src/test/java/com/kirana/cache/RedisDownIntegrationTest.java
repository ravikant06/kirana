package com.kirana.cache;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.kirana.PostgresContainerConfig;
import com.kirana.dto.ProductRequest;
import com.kirana.dto.UserRequest;
import com.kirana.service.InventoryService;
import com.kirana.service.ProductService;
import com.kirana.service.UserService;
import io.minio.MinioClient;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

/** D42: with Redis unreachable the shop keeps working from Postgres (fail open). */
@SpringBootTest(properties = {"spring.data.redis.host=127.0.0.1", "spring.data.redis.port=1"})
@AutoConfigureMockMvc
@Import(PostgresContainerConfig.class)
class RedisDownIntegrationTest {

    @MockitoBean MinioClient minio;

    @Autowired MockMvc mvc;
    @Autowired ProductService products;
    @Autowired InventoryService inventory;
    @Autowired UserService users;

    @Test
    void productsAreServedFromPostgres() throws Exception {
        long id = products.create(new ProductRequest("No-cache tea", null, "100")).id();
        mvc.perform(get("/products/{id}", id))
                .andExpect(status().isOk())
                .andExpect(header().string("X-Cache", "BYPASS"));
        mvc.perform(get("/products")).andExpect(status().isOk());
    }

    @Test
    void rateLimitsAndTheFlashSaleGateStepAside() throws Exception {
        long id = products.create(new ProductRequest("No-limit tea", null, "100")).id();
        inventory.set(id, 100);
        long user = users.create(new UserRequest("Unlimited", "unlimited@test.com")).id();
        for (int i = 0; i < 25; i++) { // cart limit is 20 per 10 s when Redis is up
            mvc.perform(post("/cart/items").header("X-User-Id", user).contentType(MediaType.APPLICATION_JSON)
                            .content("{\"productId\": %d, \"quantity\": 1}".formatted(id)))
                    .andExpect(status().isOk());
        }
        mvc.perform(post("/orders").header("X-User-Id", user)).andExpect(status().isCreated());
    }
}
