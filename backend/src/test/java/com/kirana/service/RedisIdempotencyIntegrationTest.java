package com.kirana.service;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.Map;
import java.util.UUID;

import com.kirana.PostgresContainerConfig;
import com.kirana.RedisContainerConfig;
import com.kirana.dto.ProductRequest;
import com.kirana.dto.UserRequest;
import com.kirana.idempotency.IdempotentRequests;
import io.minio.MinioClient;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.context.annotation.Import;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.http.MediaType;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders;
import tools.jackson.databind.json.JsonMapper;

/**
 * Stage 7d: the same rules with keys in Redis, and where Redis falls short of Postgres.
 */
@SpringBootTest(properties = {"kirana.idempotency.store=redis", "kirana.payment.jobs-enabled=false"})
@AutoConfigureMockMvc
@Import({PostgresContainerConfig.class, RedisContainerConfig.class})
class RedisIdempotencyIntegrationTest {

    @MockitoBean MinioClient minio;

    @Autowired MockMvc mvc;
    @Autowired StringRedisTemplate redis;
    @Autowired JsonMapper json;
    @Autowired UserService users;
    @Autowired ProductService products;
    @Autowired InventoryService inventory;
    @Autowired CartService carts;

    @Test
    void aRetriedAddIsReplayedFromRedis() throws Exception {
        long[] f = userAndProduct();
        String key = UUID.randomUUID().toString();
        post(f[0], key, addTea(f[1]));
        MockHttpServletResponse retry = post(f[0], key, addTea(f[1]));

        assertThat(retry.getHeader("Idempotent-Replayed")).isEqualTo("true");
        assertThat(quantity(f[0])).isEqualTo(1);
        assertThat(redis.opsForHash().get("idem:" + f[0] + ":" + key, "status")).isEqualTo("COMPLETED");
        assertThat(redis.getExpire("idem:" + f[0] + ":" + key)).isPositive(); // expires on its own (24 h)
        assertThat(post(f[0], key, "{\"productId\": %d, \"quantity\": 2}".formatted(f[1])).getStatus()).isEqualTo(422);
    }

    @Test
    void aRecoveryPointInRedisIsResumed() throws Exception {
        long[] f = userAndProduct();
        carts.add(f[0], f[1], 1);
        String key = UUID.randomUUID().toString();
        String body = json.writeValueAsString(json.readTree(addTea(f[1])));
        redis.opsForHash().putAll("idem:" + f[0] + ":" + key, Map.of("endpoint", "POST /cart/items",
                "hash", IdempotentRequests.fingerprint("POST /cart/items", body), "status", "IN_PROGRESS",
                "locked_until", "0", "recovery_point", "cart_updated", "resource_id", ""));

        assertThat(post(f[0], key, addTea(f[1])).getStatus()).isEqualTo(200);
        assertThat(quantity(f[0])).isEqualTo(1);
    }

    @Test
    void whenRedisForgetsAKeyTheRetryRunsAgain() throws Exception {
        // Kirana's Redis has no persistence and evicts under memory pressure (allkeys-lru).
        long[] f = userAndProduct();
        String key = UUID.randomUUID().toString();
        post(f[0], key, addTea(f[1]));
        redis.delete("idem:" + f[0] + ":" + key); // a restart, or an eviction

        post(f[0], key, addTea(f[1]));

        assertThat(quantity(f[0])).isEqualTo(2); // the duplicate idempotency keys exist to prevent
    }

    // ---------------------------------------------------------------- helpers

    private long[] userAndProduct() {
        long user = users.create(new UserRequest("RIdem", "ridem-" + System.nanoTime() + "@t.com")).id();
        long tea = products.create(new ProductRequest("RIdem tea " + System.nanoTime(), null, "10")).id();
        inventory.set(tea, 5);
        return new long[] {user, tea};
    }

    private static String addTea(long productId) {
        return "{\"productId\": %d, \"quantity\": 1}".formatted(productId);
    }

    private MockHttpServletResponse post(long user, String key, String body) throws Exception {
        return mvc.perform(MockMvcRequestBuilders.post("/cart/items").header("X-User-Id", user)
                .header(IdempotentRequests.HEADER, key).contentType(MediaType.APPLICATION_JSON).content(body))
                .andReturn().getResponse();
    }

    private int quantity(long user) {
        return carts.get(user).items().stream().mapToInt(i -> i.quantity()).sum();
    }
}
