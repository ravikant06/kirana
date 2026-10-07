package com.kirana.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.util.List;

import com.kirana.dto.ProductRequest;
import com.kirana.dto.UserRequest;
import io.minio.MinioClient;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import com.kirana.PostgresContainerConfig;
import com.kirana.RedisContainerConfig;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.Import;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

/**
 * Guards the Stage 2 fixes through the real HTTP stack, including the QueryMetricsFilter,
 * so a regression (an N+1 coming back, an unbounded list) fails the build.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Import({PostgresContainerConfig.class, RedisContainerConfig.class})
class Stage2IntegrationTest {

    @MockitoBean
    MinioClient minio;

    @Autowired MockMvc mvc;
    @Autowired JdbcTemplate jdbc;
    @Autowired UserService users;
    @Autowired ProductService products;
    @Autowired InventoryService inventory;
    @Autowired CartService carts;
    @Autowired OrderService orders;

    @Test
    void orderHistoryIsTwoStatementsHowEverManyOrders() throws Exception {
        long user = users.create(new UserRequest("Many orders", "many@test.com")).id();
        long tea = products.create(new ProductRequest("Tea", null, "100")).id();
        long rice = products.create(new ProductRequest("Rice", null, "50")).id();
        inventory.set(tea, 100);
        inventory.set(rice, 100);
        for (int i = 0; i < 5; i++) {
            carts.add(user, tea, 1);
            carts.add(user, rice, 2);
            orders.place(user);
        }

        // Stage 1 ran 1 + 1 + 5 = 7 here (user, orders, then lines per order). Now: user + orders-with-lines.
        mvc.perform(get("/orders").with(com.kirana.auth.TestAuth.as(user)))
                .andExpect(status().isOk())
                .andExpect(header().string("X-Query-Count", "2"))
                .andExpect(jsonPath("$.length()").value(5))
                .andExpect(jsonPath("$[0].items.length()").value(2))
                .andExpect(jsonPath("$[0].items[0].productName").value("Tea"));
    }

    @Test
    void userSearchIsBoundedAndTreatsWildcardsLiterally() throws Exception {
        users.create(new UserRequest("Asha Rao", "asha@test.com"));
        users.create(new UserRequest("Bala", "bala@test.com"));
        users.create(new UserRequest("Asha K", "asha.k@test.com"));
        users.create(new UserRequest("Percent", "50%off@test.com"));

        mvc.perform(get("/users").with(com.kirana.auth.TestAuth.admin()).param("q", "ASHA"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(2))
                .andExpect(jsonPath("$[0].name").value("Asha K")); // newest first
        mvc.perform(get("/users").with(com.kirana.auth.TestAuth.admin()).param("limit", "1"))
                .andExpect(jsonPath("$.length()").value(1));
        mvc.perform(get("/users").with(com.kirana.auth.TestAuth.admin()).param("q", "50%"))
                .andExpect(jsonPath("$.length()").value(1))
                .andExpect(jsonPath("$[0].name").value("Percent"));
        mvc.perform(get("/users").with(com.kirana.auth.TestAuth.admin()).param("q", "_"))
                .andExpect(jsonPath("$.length()").value(0)); // "_" is a literal, not "any character"
    }

    @Test
    void productListRunsFourStatementsPerPage() throws Exception {
        for (int i = 0; i < 3; i++) {
            products.create(new ProductRequest("Listed " + i, null, "10"));
        }
        // page, count, stock, thumbnails, all in one REPEATABLE READ snapshot
        mvc.perform(get("/products").param("size", "2"))
                .andExpect(status().isOk())
                .andExpect(header().string("X-Query-Count", "4"));
    }

    @Test
    void stage2IndexesExist() {
        List<String> names = jdbc.queryForList("select indexname from pg_indexes where schemaname = 'public'", String.class);
        assertThat(names).contains("idx_orders_user_created", "idx_product_images_product", "idx_products_live_created");
    }
}
