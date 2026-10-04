package com.kirana.messaging;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.List;
import java.util.Map;

import com.kirana.PostgresContainerConfig;
import com.kirana.RedisContainerConfig;
import com.kirana.dto.ProductRequest;
import com.kirana.dto.ProductSummary;
import com.kirana.exception.ConflictException;
import com.kirana.exception.InvalidFieldException;
import com.kirana.service.InventoryService;
import com.kirana.service.ProductService;
import io.minio.MinioClient;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.Import;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * AI track, Phase 4: product changes publish catalog.v1 events through the outbox, in the same
 * transaction, with descriptive fields only; and GET /products/batch serves live price and stock.
 */
@SpringBootTest(properties = {"kirana.kafka.enabled=false", "kirana.payment.jobs-enabled=false"})
@Import({PostgresContainerConfig.class, RedisContainerConfig.class})
class ProductEventsIntegrationTest {

    @MockitoBean MinioClient minio;

    @Autowired ProductService products;
    @Autowired InventoryService inventory;
    @Autowired JdbcTemplate jdbc;
    @Autowired JsonMapper json;

    @Test
    void createUpdateDeleteEachQueueOneCatalogEventWithoutPriceOrStock() {
        long id = products.create(new ProductRequest("Toor Dal (1 kg)", "Split pigeon peas.", "160", null, "Staples")).id();
        products.update(id, new ProductRequest("Toor Dal (1 kg)", "Unpolished split pigeon peas.", "165",
                products.get(id).version(), "Staples"));
        products.delete(id);

        List<Map<String, Object>> rows = jdbc.queryForList(
                "SELECT event_type, payload FROM outbox WHERE topic = 'catalog.v1' AND message_key = ? ORDER BY id",
                Long.toString(id));
        assertThat(rows).extracting(r -> r.get("event_type"))
                .containsExactly("ProductUpserted", "ProductUpserted", "ProductDeleted");

        JsonNode edited = json.readTree((String) rows.get(1).get("payload"));
        assertThat(edited.get("productId").asLong()).isEqualTo(id);
        assertThat(edited.get("data").get("description").asString()).isEqualTo("Unpolished split pigeon peas.");
        assertThat(edited.get("data").get("category").asString()).isEqualTo("Staples");
        // Never price or stock: an index must read those live.
        assertThat(edited.get("data").has("price")).isFalse();
        assertThat(edited.get("data").has("stock")).isFalse();
    }

    @Test
    void aRejectedEditPublishesNothing() {
        long id = products.create(new ProductRequest("Jaggery (1 kg)", null, "90", null, "Staples")).id();
        long before = catalogEvents(id);

        // Stale version: optimistic locking refuses the save, and its event never commits either.
        assertThatThrownBy(() -> products.update(id, new ProductRequest("Jaggery", null, "95", 99L, "Staples")))
                .isInstanceOf(ConflictException.class);

        assertThat(catalogEvents(id)).isEqualTo(before);
    }

    @Test
    void batchReturnsLivePriceAndStockInTheOrderAskedSkippingDeleted() {
        long rice = products.create(new ProductRequest("Basmati Rice (5 kg)", null, "650", null, "Staples")).id();
        long ghee = products.create(new ProductRequest("Ghee (500 ml)", null, "340", null, "Dairy & Eggs")).id();
        long gone = products.create(new ProductRequest("Old item", null, "10", null, null)).id();
        inventory.set(rice, 7);
        products.delete(gone);

        List<ProductSummary> got = products.batch(List.of(ghee, gone, rice, 999_999L));

        assertThat(got).extracting(ProductSummary::id).containsExactly(ghee, rice);
        assertThat(got.get(1).stock()).isEqualTo(7);
        assertThat(got.get(1).price()).isEqualTo(650.0);
        assertThat(got.get(0).category()).isEqualTo("Dairy & Eggs");
    }

    @Test
    void batchIsBounded() {
        List<Long> tooMany = java.util.stream.LongStream.rangeClosed(1, 51).boxed().toList();
        assertThatThrownBy(() -> products.batch(tooMany)).isInstanceOf(InvalidFieldException.class);
        assertThatThrownBy(() -> products.batch(List.of())).isInstanceOf(InvalidFieldException.class);
    }

    private long catalogEvents(long productId) {
        return jdbc.queryForObject("SELECT count(*) FROM outbox WHERE topic = 'catalog.v1' AND message_key = ?",
                Long.class, Long.toString(productId));
    }
}
