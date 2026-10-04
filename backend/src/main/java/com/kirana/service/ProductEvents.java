package com.kirana.service;

import java.util.UUID;

import com.kirana.entity.Product;
import com.kirana.messaging.Topics;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

/**
 * Product facts for search indexes (AI track, Phase 4), written to the outbox in the same
 * transaction as the product change and relayed to catalog.v1 (key: the product id).
 *
 * The outbox is the point: an index kept in sync by "save, then publish" loses the event when
 * the process dies between the two (the dual write, Stage 6e). Here the event commits if and
 * only if the product change commits; the relay delivers it, at least once.
 *
 * Only descriptive fields travel. Price and stock change constantly and must never be served
 * from an index: consumers read them live from GET /products/batch.
 */
@Component
public class ProductEvents {

    /** What a search index may store about a product. */
    public record ProductData(String name, String description, String category) {
    }

    private final OutboxWriter outbox;

    public ProductEvents(OutboxWriter outbox) {
        this.outbox = outbox;
    }

    /** Created or edited. Sent on every save, even a price-only edit: consumers skip unchanged text. */
    @Transactional(propagation = Propagation.MANDATORY)
    public void upserted(Product p) {
        outbox.append(UUID.randomUUID(), Topics.CATALOG, "product", p.getId(), "ProductUpserted",
                new ProductData(p.getName(), p.getDescription(), p.getCategory()));
    }

    /** Soft-deleted (D8): gone from every product endpoint, so gone from every index too. */
    @Transactional(propagation = Propagation.MANDATORY)
    public void deleted(long productId) {
        outbox.append(UUID.randomUUID(), Topics.CATALOG, "product", productId, "ProductDeleted", null);
    }
}
