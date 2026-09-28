package com.kirana.service;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

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
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

@Service
public class ProductService {

    static final int MAX_PAGE_SIZE = 100;

    private final ProductRepository products;
    private final InventoryRepository inventory;
    private final ImageService images;

    public ProductService(ProductRepository products, InventoryRepository inventory, ImageService images) {
        this.products = products;
        this.inventory = inventory;
        this.images = images;
    }

    /**
     * Four statements per page, however many products: page, count, stock, thumbnails.
     * REPEATABLE READ makes all four read one snapshot, so totalElements always matches the
     * page and the stock shown is from the same moment (P7). Free in Postgres for a
     * read-only transaction: no locks, and no serialization errors.
     */
    @Transactional(readOnly = true, isolation = Isolation.REPEATABLE_READ)
    public PageResponse<ProductSummary> list(int page, int size) {
        PageRequest request = PageRequest.of(Math.max(page, 0), Math.clamp(size, 1, MAX_PAGE_SIZE),
                Sort.by(Sort.Order.desc("createdAt"), Sort.Order.desc("id")));
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

    @Transactional(readOnly = true)
    public ProductDetail get(Long id) {
        Product product = requireLive(id);
        return ProductMapper.toDetail(product, stockOf(id), images.activeImages(id));
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
        product.update(req.name().trim(), blankToNull(req.description()), req.priceValue());
        products.flush();
        return ProductMapper.toDetail(product, stockOf(id), images.activeImages(id));
    }

    /** Soft delete (D8). Orders keep pointing at the row; every product endpoint now 404s. */
    @Transactional
    public void delete(Long id) {
        requireLive(id).softDelete(Instant.now());
    }

    static ConflictException productChanged() {
        return new ConflictException("Product changed",
                "Someone else saved this product after you opened it. Reload to see their changes.");
    }

    /** For other services: a live product, or 404 (deleted counts as missing). */
    public Product requireLive(Long id) {
        return products.findByIdAndDeletedAtIsNull(id)
                .orElseThrow(() -> new NotFoundException("Product %d not found".formatted(id)));
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
