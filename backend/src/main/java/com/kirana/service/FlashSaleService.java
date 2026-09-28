package com.kirana.service;

import java.util.OptionalLong;

import com.kirana.cache.FlashSaleCounter;
import com.kirana.dto.FlashSaleResponse;
import com.kirana.exception.StorageException;
import com.kirana.repository.InventoryRepository;
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

    public FlashSaleService(FlashSaleCounter counter, ProductService products, InventoryRepository inventory) {
        this.counter = counter;
        this.products = products;
        this.inventory = inventory;
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
