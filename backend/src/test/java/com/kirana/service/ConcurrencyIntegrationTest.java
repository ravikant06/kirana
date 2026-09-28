package com.kirana.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.when;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.function.IntFunction;

import com.kirana.dto.CartResponse;
import com.kirana.dto.ImageResponse;
import com.kirana.dto.ProductDetail;
import com.kirana.dto.ProductRequest;
import com.kirana.dto.UploadRequest;
import com.kirana.dto.UserRequest;
import com.kirana.exception.ConflictException;
import com.kirana.exception.OutOfStockException;
import com.kirana.storage.ImageStorage;
import com.kirana.storage.ImageStorage.SignedUpload;
import com.kirana.storage.ImageStorage.StoredObject;
import io.minio.MinioClient;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import com.kirana.PostgresContainerConfig;
import com.kirana.RedisContainerConfig;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.Import;
import org.springframework.orm.ObjectOptimisticLockingFailureException;
import org.springframework.test.context.bean.override.mockito.MockitoBean;

/**
 * Stage 3: each test fires many requests at the same instant (a CountDownLatch start gate)
 * against a real Postgres. Every one of them failed on the Stage 1 read-then-write code.
 */
@SpringBootTest
@Import({PostgresContainerConfig.class, RedisContainerConfig.class})
class ConcurrencyIntegrationTest {

    @MockitoBean MinioClient minio;
    @MockitoBean ImageStorage storage;

    @Autowired UserService users;
    @Autowired ProductService products;
    @Autowired InventoryService inventory;
    @Autowired CartService carts;
    @Autowired OrderService orders;
    @Autowired ImageService images;

    @Test
    void concurrentAdjustmentsAreAllApplied() throws Exception {
        long tea = newProduct("Tea", 0);

        List<Outcome<Object>> results = together(50, i -> () -> inventory.adjust(tea, 1));

        assertThat(results).allMatch(Outcome::ok);
        assertThat(inventory.get(tea).quantity()).isEqualTo(50);
    }

    @Test
    void lastUnitIsSoldExactlyOnce() throws Exception {
        long tea = newProduct("Last tea", 1);
        List<Long> buyers = new ArrayList<>();
        for (int i = 0; i < 20; i++) {
            long buyer = newUser("buyer" + i);
            carts.add(buyer, tea, 1);
            buyers.add(buyer);
        }

        List<Outcome<Object>> results = together(20, i -> () -> orders.place(buyers.get(i)));

        assertThat(results.stream().filter(Outcome::ok)).hasSize(1);
        assertThat(results.stream().filter(r -> r.error() instanceof OutOfStockException)).hasSize(19);
        assertThat(inventory.get(tea).quantity()).isZero();
    }

    @Test
    void cartsLockingProductsInOppositeOrderDoNotDeadlock() throws Exception {
        long tea = newProduct("Deadlock tea", 1000);
        long rice = newProduct("Deadlock rice", 1000);
        List<Long> buyers = new ArrayList<>();
        for (int i = 0; i < 20; i++) {
            long buyer = newUser("pair" + i);
            // Half the carts hold (Tea, Rice), half (Rice, Tea).
            carts.add(buyer, i % 2 == 0 ? tea : rice, 1);
            carts.add(buyer, i % 2 == 0 ? rice : tea, 1);
            buyers.add(buyer);
        }

        List<Outcome<Object>> results = together(20, i -> () -> orders.place(buyers.get(i)));

        assertThat(results).allMatch(Outcome::ok);
        assertThat(inventory.get(tea).quantity()).isEqualTo(980);
        assertThat(inventory.get(rice).quantity()).isEqualTo(980);
    }

    @Test
    void concurrentAddsCreateOneCartAndAllCount() throws Exception {
        long tea = newProduct("Clicked tea", 10);
        long shopper = newUser("clicker");

        List<Outcome<Object>> results = together(10, i -> () -> carts.add(shopper, tea, 1));

        assertThat(results).allMatch(Outcome::ok);
        CartResponse cart = carts.get(shopper);
        assertThat(cart.items()).hasSize(1);
        assertThat(cart.items().getFirst().quantity()).isEqualTo(10);
    }

    @Test
    void staleProductSaveIsRejected() {
        long tea = newProduct("Edited tea", 0);
        long loaded = products.get(tea).version();

        products.update(tea, new ProductRequest("Tea, new price", null, "120", loaded));

        // Admin B saves a form opened before admin A's save.
        Outcome<ProductDetail> stale = attempt(() -> products.update(tea, new ProductRequest("Tea typo fix", null, "100", loaded)));
        assertThat(stale.error()).isInstanceOf(ConflictException.class);
        assertThat(products.get(tea).price()).isEqualTo(120.0);
    }

    @Test
    void twoSavesOfTheSameVersionAtOnceOnlyOneWins() throws Exception {
        long tea = newProduct("Raced tea", 0);
        long loaded = products.get(tea).version();

        List<Outcome<Object>> results = together(2, i ->
                () -> products.update(tea, new ProductRequest("Admin " + i, null, "10" + i, loaded)));

        assertThat(results.stream().filter(Outcome::ok)).hasSize(1);
        assertThat(results.stream().filter(r -> r.error() instanceof ConflictException
                || r.error() instanceof ObjectOptimisticLockingFailureException)).hasSize(1);
    }

    @Test
    void concurrentImageConfirmsGetDistinctPositions() throws Exception {
        when(storage.signUpload(anyString(), anyString()))
                .thenReturn(new SignedUpload("http://storage/bucket", Map.of(), Instant.now()));
        when(storage.stat(anyString())).thenReturn(Optional.of(new StoredObject(100, "image/png")));
        when(storage.readUrl(any())).thenReturn("http://storage/read");
        long tea = newProduct("Pictured tea", 0);
        List<Long> imageIds = new ArrayList<>();
        for (int i = 0; i < 5; i++) {
            imageIds.add(images.requestUpload(tea, new UploadRequest("p" + i + ".png", "image/png", 100L)).imageId());
        }

        List<Outcome<ImageResponse>> results = together(5, i -> () -> images.confirm(tea, imageIds.get(i)));

        assertThat(results).allMatch(Outcome::ok);
        assertThat(results.stream().map(r -> r.value().position())).containsExactlyInAnyOrder(0, 1, 2, 3, 4);
    }

    // ---------------------------------------------------------------- helpers

    record Outcome<T>(T value, Throwable error) {
        boolean ok() {
            return error == null;
        }
    }

    /** Runs n tasks on n threads, all released at the same instant by one latch. */
    private <T> List<Outcome<T>> together(int n, IntFunction<Callable<T>> task) throws Exception {
        ExecutorService pool = Executors.newFixedThreadPool(n);
        CountDownLatch start = new CountDownLatch(1);
        try {
            List<Future<Outcome<T>>> futures = new ArrayList<>();
            for (int i = 0; i < n; i++) {
                Callable<T> work = task.apply(i);
                futures.add(pool.submit(() -> {
                    start.await();
                    return attempt(work);
                }));
            }
            start.countDown();
            List<Outcome<T>> results = new ArrayList<>();
            for (Future<Outcome<T>> f : futures) {
                results.add(f.get(30, TimeUnit.SECONDS));
            }
            return results;
        } finally {
            pool.shutdownNow();
        }
    }

    private static <T> Outcome<T> attempt(Callable<T> work) {
        try {
            return new Outcome<>(work.call(), null);
        } catch (Exception e) {
            return new Outcome<>(null, e);
        }
    }

    private long newUser(String name) {
        return users.create(new UserRequest(name, name + "-" + System.nanoTime() + "@test.com")).id();
    }

    private long newProduct(String name, int stock) {
        long id = products.create(new ProductRequest(name, null, "100")).id();
        inventory.set(id, stock);
        return id;
    }
}
