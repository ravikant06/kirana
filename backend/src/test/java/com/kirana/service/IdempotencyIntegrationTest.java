package com.kirana.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;

import java.sql.Timestamp;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

import com.kirana.PostgresContainerConfig;
import com.kirana.RedisContainerConfig;
import com.kirana.dto.ProductRequest;
import com.kirana.dto.UserRequest;
import com.kirana.idempotency.IdempotencyCleanup;
import com.kirana.idempotency.IdempotentRequests;
import com.kirana.payment.PaymentGateways;
import com.kirana.service.CheckoutSagaIntegrationTest.StubGateway;
import io.minio.MinioClient;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * Stage 7b over HTTP, on real Postgres: each rule of the Idempotency-Key header, and the resume
 * after an attempt that died between doing the work and storing its response.
 */
@SpringBootTest(properties = "kirana.payment.jobs-enabled=false")
@AutoConfigureMockMvc
@Import({PostgresContainerConfig.class, RedisContainerConfig.class})
class IdempotencyIntegrationTest {

    @MockitoBean MinioClient minio;
    @MockitoBean PaymentGateways gateways;

    @Autowired MockMvc mvc;
    @Autowired JsonMapper json;
    @Autowired JdbcTemplate jdbc;
    @Autowired UserService users;
    @Autowired ProductService products;
    @Autowired InventoryService inventory;
    @Autowired CartService carts;
    @Autowired OrderService orders;

    private final StubGateway gateway = new StubGateway();

    @BeforeEach
    void stubGateway() {
        when(gateways.forCheckout(any())).thenReturn(gateway);
        when(gateways.of(anyString())).thenReturn(gateway);
    }

    @Test
    void withoutAKeyTheRequestIsRefused() throws Exception {
        long[] f = userAndProduct();
        MockHttpServletResponse r = post(f[0], null, "/cart/items", addTea(f[1]));
        assertThat(r.getStatus()).isEqualTo(400);
        assertThat(r.getContentAsString()).contains("Idempotency-Key");
        assertThat(quantity(f[0])).isZero();
    }

    @Test
    void aRetriedAddToCartAddsOnceAndGetsTheSameAnswer() throws Exception {
        long[] f = userAndProduct();
        String key = UUID.randomUUID().toString();

        MockHttpServletResponse first = post(f[0], key, "/cart/items", addTea(f[1]));
        MockHttpServletResponse retry = post(f[0], key, "/cart/items", addTea(f[1]));

        assertThat(quantity(f[0])).isEqualTo(1);
        assertThat(retry.getStatus()).isEqualTo(200);
        assertThat(retry.getHeader("Idempotent-Replayed")).isEqualTo("true");
        assertThat(json.readTree(retry.getContentAsString())).isEqualTo(json.readTree(first.getContentAsString()));

        post(f[0], UUID.randomUUID().toString(), "/cart/items", addTea(f[1])); // a new tap: a new key
        assertThat(quantity(f[0])).isEqualTo(2);
    }

    @Test
    void theSameKeyForADifferentRequestIs422() throws Exception {
        long[] f = userAndProduct();
        String key = UUID.randomUUID().toString();
        post(f[0], key, "/cart/items", addTea(f[1]));

        MockHttpServletResponse other = post(f[0], key, "/cart/items",
                "{\"productId\": %d, \"quantity\": 3}".formatted(f[1]));

        assertThat(other.getStatus()).isEqualTo(422);
        assertThat(quantity(f[0])).isEqualTo(1);
    }

    @Test
    void keysBelongToOneShopper() throws Exception {
        long[] a = userAndProduct();
        long[] b = userAndProduct();
        String key = "same-key-" + System.nanoTime();
        post(a[0], key, "/cart/items", addTea(a[1]));
        MockHttpServletResponse other = post(b[0], key, "/cart/items", addTea(b[1]));
        assertThat(other.getHeader("Idempotent-Replayed")).isNull(); // B's request is B's own
        assertThat(quantity(b[0])).isEqualTo(1);
    }

    @Test
    void aRetriedPlaceOrderReturnsTheSameOrder() throws Exception {
        long[] f = userAndProduct();
        post(f[0], UUID.randomUUID().toString(), "/cart/items", addTea(f[1]));
        String key = UUID.randomUUID().toString();

        MockHttpServletResponse first = post(f[0], key, "/orders", "{\"paymentProvider\":\"stub\"}");
        MockHttpServletResponse retry = post(f[0], key, "/orders", "{\"paymentProvider\":\"stub\"}");

        assertThat(first.getStatus()).isEqualTo(201);
        assertThat(retry.getStatus()).isEqualTo(201); // not 409 "Cart is empty"
        assertThat(retry.getHeader("Location")).isEqualTo(first.getHeader("Location"));
        assertThat(orderId(retry)).isEqualTo(orderId(first));
        assertThat(orders.list(f[0])).hasSize(1);
    }

    @Test
    void aRetriedCancelGetsTheFirstAnswer() throws Exception {
        long[] f = userAndProduct();
        post(f[0], UUID.randomUUID().toString(), "/cart/items", addTea(f[1]));
        long orderId = orderId(post(f[0], UUID.randomUUID().toString(), "/orders", "{\"paymentProvider\":\"stub\"}"));
        String key = UUID.randomUUID().toString();

        MockHttpServletResponse first = post(f[0], key, "/orders/" + orderId + "/cancel", null);
        MockHttpServletResponse retry = post(f[0], key, "/orders/" + orderId + "/cancel", null);

        assertThat(first.getStatus()).isEqualTo(200);
        assertThat(retry.getStatus()).isEqualTo(200); // not 409 "Order not awaiting payment"
        assertThat(json.readTree(retry.getContentAsString()).path("status").asString()).isEqualTo("CANCELLED");
        assertThat(inventory.get(f[1]).quantity()).isEqualTo(5); // stock released once
    }

    @Test
    void twoSimultaneousAttemptsPlaceOneOrder() throws Exception {
        long[] f = userAndProduct();
        post(f[0], UUID.randomUUID().toString(), "/cart/items", addTea(f[1]));
        String key = UUID.randomUUID().toString();
        ExecutorService pool = Executors.newFixedThreadPool(4);
        CountDownLatch start = new CountDownLatch(1);
        List<Future<Integer>> attempts = new ArrayList<>();
        for (int i = 0; i < 4; i++) {
            attempts.add(pool.submit(() -> {
                start.await();
                return post(f[0], key, "/orders", "{\"paymentProvider\":\"stub\"}").getStatus();
            }));
        }
        start.countDown();
        List<Integer> statuses = new ArrayList<>();
        for (Future<Integer> a : attempts) {
            statuses.add(a.get(30, TimeUnit.SECONDS));
        }
        pool.shutdown();

        assertThat(statuses).contains(201).allMatch(s -> s == 201 || s == 409); // 409 = "in progress, retry"
        assertThat(orders.list(f[0])).hasSize(1);
    }

    @Test
    void aFailedAttemptIsForgottenSoTheRetryRunsAgain() throws Exception {
        long[] f = userAndProduct();
        String key = UUID.randomUUID().toString();
        assertThat(post(f[0], key, "/orders", "{\"paymentProvider\":\"stub\"}").getStatus()).isEqualTo(409); // cart empty
        assertThat(keyRows(f[0], key)).isZero(); // nothing happened, nothing stored

        post(f[0], UUID.randomUUID().toString(), "/cart/items", addTea(f[1]));
        assertThat(post(f[0], key, "/orders", "{\"paymentProvider\":\"stub\"}").getStatus()).isEqualTo(201);
    }

    @Test
    void anAttemptThatDiedAfterPlacingTheOrderIsResumedNotRepeated() throws Exception {
        long[] f = userAndProduct();
        carts.add(f[0], f[1], 1);
        // What a crash leaves behind: TX1 committed the order AND "order_created" on the key, but the
        // process died before calling the gateway and storing the response. Its lock has run out.
        long placed = orders.place(f[0]).id();
        String key = UUID.randomUUID().toString();
        diedAfter(f[0], key, "POST /orders", "{\"paymentProvider\":\"stub\"}", "order_created", placed);

        MockHttpServletResponse retry = post(f[0], key, "/orders", "{\"paymentProvider\":\"stub\"}");

        assertThat(retry.getStatus()).isEqualTo(201);
        assertThat(orderId(retry)).isEqualTo(placed); // the same order, now with its payment session
        assertThat(json.readTree(retry.getContentAsString()).path("payment").path("gatewayOrderId").asString()).isNotBlank();
        assertThat(orders.list(f[0])).hasSize(1);
        assertThat(inventory.get(f[1]).quantity()).isEqualTo(4); // stock taken once
    }

    @Test
    void anAttemptThatDiedAfterTheCartIncrementIsNotIncrementedAgain() throws Exception {
        long[] f = userAndProduct();
        carts.add(f[0], f[1], 1); // the increment that committed
        String key = UUID.randomUUID().toString();
        diedAfter(f[0], key, "POST /cart/items", json.writeValueAsString(json.readTree(addTea(f[1]))), "cart_updated", null);

        MockHttpServletResponse retry = post(f[0], key, "/cart/items", addTea(f[1]));

        assertThat(retry.getStatus()).isEqualTo(200);
        assertThat(quantity(f[0])).isEqualTo(1);
    }

    @Test
    void keysOlderThanTheRetentionAreDeleted() {
        long[] f = userAndProduct();
        jdbc.update("""
                INSERT INTO idempotency_keys (user_id, idempotency_key, endpoint, request_hash, status, locked_until, created_at)
                VALUES (?, 'old', 'POST /cart/items', ?, 'COMPLETED', now(), now() - interval '25 hours')
                """, f[0], "0".repeat(64));
        new IdempotencyCleanup(jdbc, java.time.Duration.ofHours(24)).run();
        assertThat(keyRows(f[0], "old")).isZero();
    }

    // ---------------------------------------------------------------- helpers

    private long[] userAndProduct() {
        long user = users.create(new UserRequest("Idem", "idem-" + System.nanoTime() + "@t.com")).id();
        long tea = products.create(new ProductRequest("Idem tea " + System.nanoTime(), null, "10")).id();
        inventory.set(tea, 5);
        return new long[] {user, tea};
    }

    private static String addTea(long productId) {
        return "{\"productId\": %d, \"quantity\": 1}".formatted(productId);
    }

    private MockHttpServletResponse post(long user, String key, String path, String body) throws Exception {
        var req = org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post(path)
                .header("X-User-Id", user).contentType(MediaType.APPLICATION_JSON);
        if (key != null) {
            req.header(IdempotentRequests.HEADER, key);
        }
        if (body != null) {
            req.content(body);
        }
        return mvc.perform(req).andReturn().getResponse();
    }

    private long orderId(MockHttpServletResponse r) throws Exception {
        JsonNode node = json.readTree(r.getContentAsString());
        return node.path("order").path("id").asLong();
    }

    private int quantity(long user) {
        return carts.get(user).items().stream().mapToInt(i -> i.quantity()).sum();
    }

    private long keyRows(long user, String key) {
        return jdbc.queryForObject("SELECT count(*) FROM idempotency_keys WHERE user_id = ? AND idempotency_key = ?",
                Long.class, user, key);
    }

    /** The row a crashed attempt leaves: IN_PROGRESS, a recovery point, an expired lock. */
    private void diedAfter(long user, String key, String endpoint, String body, String point, Long resource) throws Exception {
        String canonical = json.writeValueAsString(json.readTree(body)); // as the controller would serialize it
        jdbc.update("""
                INSERT INTO idempotency_keys (user_id, idempotency_key, endpoint, request_hash, status,
                    recovery_point, resource_id, locked_until)
                VALUES (?, ?, ?, ?, 'IN_PROGRESS', ?, ?, ?)
                """, user, key, endpoint, IdempotentRequests.fingerprint(endpoint, canonical), point, resource,
                Timestamp.from(Instant.now().minusSeconds(1)));
    }
}
