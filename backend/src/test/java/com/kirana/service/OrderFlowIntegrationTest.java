package com.kirana.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.kirana.dto.CartResponse;
import com.kirana.dto.OrderResponse;
import com.kirana.dto.ProductRequest;
import com.kirana.dto.UserRequest;
import com.kirana.exception.OutOfStockException;
import io.minio.MinioClient;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.postgresql.PostgreSQLContainer;

/**
 * Real Postgres (Testcontainers), real Flyway migration, real transactions. Only MinIO is mocked,
 * since no images are involved. Needs Docker.
 */
@SpringBootTest
@Testcontainers
class OrderFlowIntegrationTest {

    @Container
    @ServiceConnection
    static PostgreSQLContainer postgres = new PostgreSQLContainer("postgres:17");

    @MockitoBean
    MinioClient minio;

    @Autowired UserService users;
    @Autowired ProductService products;
    @Autowired InventoryService inventory;
    @Autowired CartService carts;
    @Autowired OrderService orders;

    @Test
    void outOfStockLineRollsBackStockOfEarlierLines() {
        long user = newUser("a@test.com");
        long tea = newProduct("Tea", "100", 5);
        long rice = newProduct("Rice", "50", 5);
        long salt = newProduct("Salt", "20", 0);
        carts.add(user, tea, 2);
        carts.add(user, rice, 2);
        carts.add(user, salt, 2);

        assertThatThrownBy(() -> orders.place(user))
                .isInstanceOf(OutOfStockException.class)
                .hasMessage("Only 0 units of 'Salt' left, 2 requested");

        // Tea and Rice were decremented in memory before Salt failed; the rollback undid it.
        assertThat(inventory.get(tea).quantity()).isEqualTo(5);
        assertThat(inventory.get(rice).quantity()).isEqualTo(5);
        assertThat(carts.get(user).items()).hasSize(3);
        assertThat(orders.list(user)).isEmpty();
    }

    @Test
    void successfulOrderSubtractsStockEmptiesCartAndSnapshotsPrices() {
        long user = newUser("b@test.com");
        long tea = newProduct("Tea", "100", 5);
        long rice = newProduct("Rice", "49.50", 3);
        carts.add(user, tea, 2);
        carts.add(user, rice, 1);
        carts.add(user, rice, 1); // adds to the existing line

        OrderResponse order = orders.place(user);

        assertThat(order.total()).isEqualTo(299.0);
        assertThat(order.items()).hasSize(2);
        assertThat(inventory.get(tea).quantity()).isEqualTo(3);
        assertThat(inventory.get(rice).quantity()).isEqualTo(1);
        CartResponse cart = carts.get(user);
        assertThat(cart.items()).isEmpty();

        // D2: changing the product afterwards does not rewrite the order.
        products.update(tea, new ProductRequest("Green tea", null, "150"));
        OrderResponse reloaded = orders.get(user, order.id());
        assertThat(reloaded.items()).anySatisfy(line -> {
            assertThat(line.productName()).isEqualTo("Tea");
            assertThat(line.unitPrice()).isEqualTo(100.0);
        });
    }

    private long newUser(String email) {
        return users.create(new UserRequest("Test", email)).id();
    }

    private long newProduct(String name, String price, int stock) {
        long id = products.create(new ProductRequest(name, null, price)).id();
        inventory.set(id, stock);
        return id;
    }
}
