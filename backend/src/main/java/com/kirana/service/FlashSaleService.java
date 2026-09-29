package com.kirana.service;

import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.OptionalLong;

import com.kirana.cache.FlashSaleCounter;
import com.kirana.dto.FlashSaleResponse;
import com.kirana.dto.FlashSaleSummary;
import com.kirana.exception.StorageException;
import com.kirana.repository.InventoryRepository;
import com.kirana.repository.ProductRepository;
import org.springframework.dao.DataAccessException;
import org.springframework.stereotype.Service;

/**
 * Admin switch for the flash-sale gate (D44). Arming copies the product's current stock from
 * Postgres into Redis. While armed, restocking in Postgres is not seen by the gate until the
 * sale is armed again; the gate can be stricter than the database, never looser.
 */
@Service
public class FlashSaleService {

    private final FlashSaleCounter counter;
    private final ProductService products;
    private final InventoryRepository inventory;
    private final ProductRepository productRepository;

    public FlashSaleService(FlashSaleCounter counter, ProductService products, InventoryRepository inventory,
                            ProductRepository productRepository) {
        this.counter = counter;
        this.products = products;
        this.inventory = inventory;
        this.productRepository = productRepository;
    }

    public FlashSaleResponse arm(Long productId) {
        products.requireLive(productId);
        int stock = inventory.findById(productId).orElseThrow().getQuantity();
        try {
            counter.arm(productId, stock);
        } catch (DataAccessException e) {
            throw new StorageException("Redis is unavailable, so the flash-sale gate cannot be armed", e);
        }
        return new FlashSaleResponse(productId, true, (long) stock);
    }

    public void disarm(Long productId) {
        products.requireLive(productId);
        try {
            counter.disarm(productId);
        } catch (DataAccessException e) {
            throw new StorageException("Redis is unavailable, so the flash-sale gate cannot be stopped", e);
        }
    }

    /** Active sales for the shop's flash-sale strip: live units from Redis, names and prices from Postgres. */
    public List<FlashSaleSummary> active() {
        Map<Long, Long> left;
        try {
            left = counter.active();
        } catch (DataAccessException e) {
            return List.of(); // Redis down: no gate, so nothing to advertise
        }
        if (left.isEmpty()) {
            return List.of();
        }
        return productRepository.findAllById(left.keySet()).stream()
                .filter(p -> !p.isDeleted())
                .map(p -> new FlashSaleSummary(p.getId(), p.getName(), p.getPrice(), left.get(p.getId())))
                .sorted(Comparator.comparing(FlashSaleSummary::productId))
                .toList();
    }

    public FlashSaleResponse status(Long productId) {
        products.requireLive(productId);
        OptionalLong left;
        try {
            left = counter.remaining(productId);
        } catch (DataAccessException e) {
            left = OptionalLong.empty();
        }
        return new FlashSaleResponse(productId, left.isPresent(), left.isPresent() ? left.getAsLong() : null);
    }
}
