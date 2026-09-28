package com.kirana.service;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

import com.kirana.cache.CacheAside;
import com.kirana.cache.CacheKeys;
import com.kirana.cache.ProductSnapshot;
import com.kirana.config.CacheProperties;
import com.kirana.dto.ImageResponse;
import com.kirana.dto.PageResponse;
import com.kirana.dto.ProductDetail;
import com.kirana.dto.ProductRequest;
import com.kirana.dto.ProductSummary;
import com.kirana.entity.Inventory;
import com.kirana.entity.Product;
import com.kirana.exception.ConflictException;
import com.kirana.exception.InvalidFieldException;
import com.kirana.exception.NotFoundException;
import com.kirana.mapper.ProductMapper;
import com.kirana.repository.InventoryRepository;
import com.kirana.repository.ProductRepository;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.core.type.TypeReference;

@Service
public class ProductService {

    static final int MAX_PAGE_SIZE = 100;

    private static final TypeReference<PageResponse<ProductSummary>> PAGE = new TypeReference<>() { };
    private static final TypeReference<ProductSnapshot> SNAPSHOT = new TypeReference<>() { };

    private final ProductRepository products;
    private final InventoryRepository inventory;
    private final ImageService images;
    private final CacheAside cache;
    private final CacheProperties cacheProps;
    private final TransactionTemplate readTx;
    private final TransactionTemplate snapshotReadTx;

    public ProductService(ProductRepository products, InventoryRepository inventory, ImageService images,
                          CacheAside cache, CacheProperties cacheProps, PlatformTransactionManager txManager) {
        this.products = products;
        this.inventory = inventory;
        this.images = images;
        this.cache = cache;
        this.cacheProps = cacheProps;
        this.readTx = new TransactionTemplate(txManager);
        this.readTx.setReadOnly(true);
        // P7 (D29): page, count, stock and thumbnails from one snapshot.
        this.snapshotReadTx = new TransactionTemplate(txManager);
        this.snapshotReadTx.setReadOnly(true);
        this.snapshotReadTx.setIsolationLevel(TransactionDefinition.ISOLATION_REPEATABLE_READ);
    }

    /**
     * Product list pages are cached whole for a short TTL (D41): a new or edited product shows
     * up within kirana.cache.list-ttl. Precise eviction is impractical, because one new product
     * shifts every page. Stock shown in the list can be that old too; the product page and
     * checkout always read it fresh.
     *
     * Not @Transactional: the Redis lookup must not hold a database connection. Only a miss
     * opens a transaction, a read-only REPEATABLE READ one so the page and its count agree (P7).
     */
    public PageResponse<ProductSummary> list(int page, int size) {
        int p = Math.max(page, 0);
        int s = Math.clamp(size, 1, MAX_PAGE_SIZE);
        return cache.getOrLoad(CacheKeys.productPage(p, s), cacheProps.listTtl(), PAGE,
                () -> snapshotReadTx.execute(status -> loadPage(p, s)));
    }

    /** Four statements per page, however many products: page, count, stock, thumbnails. */
    private PageResponse<ProductSummary> loadPage(int page, int size) {
        PageRequest request = PageRequest.of(page, size, Sort.by(Sort.Order.desc("createdAt"), Sort.Order.desc("id")));
        Page<Product> result = products.findAllByDeletedAtIsNull(request);

        List<Long> ids = result.map(Product::getId).toList();
        Map<Long, Integer> stock = inventory.findAllById(ids).stream()
                .collect(Collectors.toMap(Inventory::getProductId, Inventory::getQuantity));
        Map<Long, String> thumbnails = images.thumbnailUrls(ids);

        List<ProductSummary> content = result.stream()
                .map(p -> ProductMapper.toSummary(p, stock.getOrDefault(p.getId(), 0), thumbnails.get(p.getId())))
                .toList();
        return PageResponse.of(result, content);
    }

    /**
     * Cache-aside (D40). Product fields and image keys come from Redis when cached; stock is
     * always one fresh primary-key read, so a sold-out product never shows as available.
     * A missing or deleted product is cached as "not found" for a minute (penetration).
     * Not @Transactional, for the same reason as list().
     */
    public ProductDetail get(Long id) {
        ProductSnapshot p = cache.getOrLoad(CacheKeys.product(id), cacheProps.productTtl(), SNAPSHOT,
                () -> readTx.execute(status -> products.findByIdAndDeletedAtIsNull(id)
                        .map(product -> ProductMapper.toSnapshot(product, images.activeImageRows(id)))
                        .orElse(null)));
        if (p == null) {
            throw notFound(id);
        }
        List<ImageResponse> signed = p.images().stream().map(images::signed).toList();
        return ProductMapper.toDetail(p, stockOf(id), signed);
    }

    /**
     * Product and inventory rows are written in one transaction (D1): if the inventory insert
     * fails, the product insert is rolled back, so a product without stock cannot exist.
     */
    @Transactional
    public ProductDetail create(ProductRequest req) {
        Product product = products.save(new Product(req.name().trim(), blankToNull(req.description()), req.priceValue()));
        inventory.save(new Inventory(product));
        // Run the INSERTs now so the generated timestamps are in the response.
        products.flush();
        // A bot may have asked for this id before it existed; drop the cached "not found".
        cache.evictAfterCommit(CacheKeys.product(product.getId()));
        return ProductMapper.toDetail(product, 0, List.of());
    }

    /**
     * R3: the client must send the version it loaded. An older version means someone saved in
     * between, so we refuse instead of overwriting their change, and do not retry: retrying
     * would re-apply the stale form. Two saves with the same version at the same instant both
     * pass this check; Hibernate's "WHERE version = ?" then rejects the second at flush.
     */
    @Transactional
    public ProductDetail update(Long id, ProductRequest req) {
        if (req.version() == null) {
            throw new InvalidFieldException("version", "is required: send the version you loaded");
        }
        Product product = requireLive(id);
        if (product.getVersion() != req.version()) {
            throw productChanged();
        }
        cache.evictAfterCommit(CacheKeys.product(id));
        product.update(req.name().trim(), blankToNull(req.description()), req.priceValue());
        products.flush();
        return ProductMapper.toDetail(product, stockOf(id), images.activeImages(id));
    }

    /** Soft delete (D8). Orders keep pointing at the row; every product endpoint now 404s. */
    @Transactional
    public void delete(Long id) {
        requireLive(id).softDelete(Instant.now());
        cache.evictAfterCommit(CacheKeys.product(id));
    }

    static ConflictException productChanged() {
        return new ConflictException("Product changed",
                "Someone else saved this product after you opened it. Reload to see their changes.");
    }

    /** For other services: a live product, or 404 (deleted counts as missing). */
    public Product requireLive(Long id) {
        return products.findByIdAndDeletedAtIsNull(id).orElseThrow(() -> notFound(id));
    }

    private static NotFoundException notFound(Long id) {
        return new NotFoundException("Product %d not found".formatted(id));
    }

    private int stockOf(Long productId) {
        return inventory.findById(productId)
                .orElseThrow(() -> new IllegalStateException("No inventory row for product " + productId))
                .getQuantity();
    }

    private static String blankToNull(String s) {
        return s == null || s.isBlank() ? null : s.trim();
    }
}
