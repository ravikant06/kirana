package com.kirana.cache;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import com.kirana.PostgresContainerConfig;
import com.kirana.RedisContainerConfig;
import com.kirana.dto.ProductRequest;
import com.kirana.service.InventoryService;
import com.kirana.service.ProductService;
import io.minio.MinioClient;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.context.annotation.Import;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import tools.jackson.core.type.TypeReference;

/** Stage 4 cache behaviour through the real HTTP stack, with real Postgres and Redis. */
@SpringBootTest
@AutoConfigureMockMvc
@Import({PostgresContainerConfig.class, RedisContainerConfig.class})
class CacheIntegrationTest {

    @MockitoBean MinioClient minio;

    @Autowired MockMvc mvc;
    @Autowired ProductService products;
    @Autowired InventoryService inventory;
    @Autowired CacheAside cache;
    @Autowired StringRedisTemplate redis;

    @Test
    void productDetailIsCachedButStockStaysFresh() throws Exception {
        long id = products.create(new ProductRequest("Cached tea", null, "100")).id();
        inventory.set(id, 5);

        mvc.perform(get("/products/{id}", id)).andExpect(header().string("X-Cache", "MISS"));
        // Only the stock lookup reaches Postgres now.
        mvc.perform(get("/products/{id}", id))
                .andExpect(header().string("X-Cache", "HIT"))
                .andExpect(header().string("X-Query-Count", "1"))
                .andExpect(jsonPath("$.stock").value(5));

        inventory.set(id, 3);
        mvc.perform(get("/products/{id}", id))
                .andExpect(header().string("X-Cache", "HIT"))
                .andExpect(jsonPath("$.stock").value(3));
    }

    @Test
    void anEditEvictsTheCachedProductAfterCommit() throws Exception {
        long id = products.create(new ProductRequest("Repriced tea", null, "100")).id();
        mvc.perform(get("/products/{id}", id)).andExpect(jsonPath("$.price").value(100.0));

        products.update(id, new ProductRequest("Repriced tea", null, "120", products.get(id).version()));

        mvc.perform(get("/products/{id}", id))
                .andExpect(header().string("X-Cache", "MISS"))
                .andExpect(jsonPath("$.price").value(120.0));
    }

    @Test
    void aMissingProductIsCachedAsNotFound() throws Exception {
        mvc.perform(get("/products/{id}", 987_654_321L))
                .andExpect(status().isNotFound())
                .andExpect(header().string("X-Cache", "MISS"));
        // The bot's second request never reaches Postgres.
        mvc.perform(get("/products/{id}", 987_654_321L))
                .andExpect(status().isNotFound())
                .andExpect(header().string("X-Cache", "HIT"))
                .andExpect(header().string("X-Query-Count", "0"));
    }

    @Test
    void aDeletedProductStopsBeingServedFromCache() throws Exception {
        long id = products.create(new ProductRequest("Deleted tea", null, "100")).id();
        mvc.perform(get("/products/{id}", id)).andExpect(status().isOk());

        products.delete(id);

        mvc.perform(get("/products/{id}", id)).andExpect(status().isNotFound());
    }

    @Test
    void productListPagesAreCached() throws Exception {
        products.create(new ProductRequest("Listed tea", null, "10"));
        redis.delete(CacheKeys.productPage(0, 3));

        mvc.perform(get("/products").param("size", "3"))
                .andExpect(header().string("X-Cache", "MISS"));
        mvc.perform(get("/products").param("size", "3"))
                .andExpect(header().string("X-Cache", "HIT"))
                .andExpect(header().string("X-Query-Count", "0"));
    }

    @Test
    void aStampedeOnAnExpiredKeyLoadsFromTheDatabaseOnce() throws Exception {
        String key = "test:stampede:" + System.nanoTime();
        AtomicInteger loads = new AtomicInteger();
        int requests = 30;
        ExecutorService pool = Executors.newFixedThreadPool(requests);
        CountDownLatch start = new CountDownLatch(1);
        List<Future<String>> results = new ArrayList<>();
        for (int i = 0; i < requests; i++) {
            results.add(pool.submit(() -> {
                start.await();
                return cache.getOrLoad(key, Duration.ofMinutes(1), new TypeReference<String>() { }, () -> {
                    loads.incrementAndGet();
                    sleep(50); // a slow database query
                    return "value";
                });
            }));
        }
        start.countDown();
        for (Future<String> f : results) {
            assertThat(f.get(10, TimeUnit.SECONDS)).isEqualTo("value");
        }
        pool.shutdown();

        assertThat(loads.get()).isEqualTo(1);
    }

    private static void sleep(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }
}
