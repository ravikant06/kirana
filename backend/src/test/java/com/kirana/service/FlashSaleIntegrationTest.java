package com.kirana.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

import com.kirana.PostgresContainerConfig;
import com.kirana.RedisContainerConfig;
import com.kirana.cache.FlashSaleCounter;
import com.kirana.dto.ProductRequest;
import com.kirana.dto.UserRequest;
import com.kirana.exception.OutOfStockException;
import io.minio.MinioClient;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

@SpringBootTest
@AutoConfigureMockMvc
@Import({PostgresContainerConfig.class, RedisContainerConfig.class})
class FlashSaleIntegrationTest {

    @MockitoBean MinioClient minio;

    @Autowired MockMvc mvc;
    @Autowired UserService users;
    @Autowired ProductService products;
    @Autowired InventoryService inventory;
    @Autowired CartService carts;
    @Autowired OrderService orders;
    @Autowired FlashSaleService flashSales;
    @Autowired FlashSaleCounter counter;

    @Test
    void theGateSellsExactlyTheStock() throws Exception {
        long tea = newProduct("Flash tea", 5);
        List<Long> buyers = buyersWithOneInCart(tea, 30, "fs");
        flashSales.arm(tea);

        List<Throwable> errors = placeTogether(buyers);

        assertThat(errors.stream().filter(e -> e == null)).hasSize(5);
        assertThat(errors.stream().filter(e -> e instanceof OutOfStockException)).hasSize(25);
        assertThat(inventory.get(tea).quantity()).isZero();
        assertThat(counter.remaining(tea)).hasValue(0);
    }

    @Test
    void unitsComeBackToTheGateWhenTheDatabaseRefuses() throws Exception {
        long tea = newProduct("Restocked-down tea", 5);
        flashSales.arm(tea);          // gate: 5
        inventory.set(tea, 2);        // database now has only 2
        List<Long> buyers = buyersWithOneInCart(tea, 10, "comp");

        List<Throwable> errors = placeTogether(buyers);

        // The database is the final guard: only 2 orders. Units the gate handed out to buyers
        // the database refused were given back.
        assertThat(errors.stream().filter(e -> e == null)).hasSize(2);
        assertThat(inventory.get(tea).quantity()).isZero();
        assertThat(counter.remaining(tea)).hasValue(3);
    }

    @Test
    void activeSalesAreListedWithUnitsLeft() {
        long tea = newProduct("Listed flash tea", 7);
        flashSales.arm(tea);
        assertThat(flashSales.active()).anySatisfy(s -> {
            assertThat(s.productId()).isEqualTo(tea);
            assertThat(s.remaining()).isEqualTo(7);
        });

        flashSales.disarm(tea);
        assertThat(flashSales.active()).noneMatch(s -> s.productId().equals(tea));
    }

    @Test
    void aBuyerRefusedAtTheGateCostsOneQuery() throws Exception {
        long tea = newProduct("Sold-out tea", 0);
        flashSales.arm(tea);
        long buyer = buyersWithOneInCart(tea, 1, "late").getFirst();

        mvc.perform(post("/orders").header("X-User-Id", buyer))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.title").value("Out of stock"))
                .andExpect(header().string("X-Query-Count", "1")); // the cart read; no transaction
    }

    private List<Throwable> placeTogether(List<Long> buyers) throws Exception {
        ExecutorService pool = Executors.newFixedThreadPool(buyers.size());
        CountDownLatch start = new CountDownLatch(1);
        List<Future<Throwable>> futures = new ArrayList<>();
        for (Long buyer : buyers) {
            Callable<Throwable> call = () -> {
                start.await();
                try {
                    orders.place(buyer);
                    return null;
                } catch (Exception e) {
                    return e;
                }
            };
            futures.add(pool.submit(call));
        }
        start.countDown();
        List<Throwable> results = new ArrayList<>();
        for (Future<Throwable> f : futures) {
            results.add(f.get(30, TimeUnit.SECONDS));
        }
        pool.shutdown();
        return results;
    }

    private List<Long> buyersWithOneInCart(long product, int n, String prefix) {
        List<Long> buyers = new ArrayList<>();
        for (int i = 0; i < n; i++) {
            long id = users.create(new UserRequest(prefix + i, prefix + i + "-" + System.nanoTime() + "@test.com")).id();
            carts.add(id, product, 1);
            buyers.add(id);
        }
        return buyers;
    }

    private long newProduct(String name, int stock) {
        long id = products.create(new ProductRequest(name, null, "100")).id();
        inventory.set(id, stock);
        return id;
    }
}
