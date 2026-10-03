package com.kirana.service;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.UUID;

import com.kirana.PostgresContainerConfig;
import com.kirana.dto.ProductRequest;
import com.kirana.dto.UserRequest;
import io.minio.MinioClient;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders;

/**
 * Stage 7d: with keys in Redis, Redis down means no cart writes at all (fail closed). With the
 * Postgres store the same outage only bypasses the cache and rate limits (RedisDownIntegrationTest).
 */
@SpringBootTest(properties = {"kirana.idempotency.store=redis", "spring.data.redis.host=127.0.0.1",
        "spring.data.redis.port=1"})
@AutoConfigureMockMvc
@Import(PostgresContainerConfig.class)
class RedisDownIdempotencyIntegrationTest {

    @MockitoBean MinioClient minio;

    @Autowired MockMvc mvc;
    @Autowired UserService users;
    @Autowired ProductService products;
    @Autowired InventoryService inventory;
    @Autowired CartService carts;

    @Test
    void redisDownRefusesProtectedRequests() throws Exception {
        long user = users.create(new UserRequest("NoRedis", "noredis-" + System.nanoTime() + "@t.com")).id();
        long tea = products.create(new ProductRequest("NoRedis tea " + System.nanoTime(), null, "10")).id();
        inventory.set(tea, 5);

        MockHttpServletResponse r = mvc.perform(MockMvcRequestBuilders.post("/cart/items").header("X-User-Id", user)
                        .header("Idempotency-Key", UUID.randomUUID().toString()).contentType(MediaType.APPLICATION_JSON)
                        .content("{\"productId\": %d, \"quantity\": 1}".formatted(tea)))
                .andReturn().getResponse();

        assertThat(r.getStatus()).isEqualTo(503);
        assertThat(r.getHeader("Retry-After")).isEqualTo("5");
        assertThat(r.getContentAsString()).contains("Idempotency unavailable");
        assertThat(carts.get(user).items()).isEmpty(); // not run unprotected
    }
}
