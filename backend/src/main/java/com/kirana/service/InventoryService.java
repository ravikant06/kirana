package com.kirana.service;

import java.time.Instant;

import com.kirana.dto.InventoryResponse;
import com.kirana.entity.Inventory;
import com.kirana.exception.ConflictException;
import com.kirana.mapper.InventoryMapper;
import com.kirana.repository.InventoryRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class InventoryService {

    private final InventoryRepository inventory;
    private final ProductService products;

    public InventoryService(InventoryRepository inventory, ProductService products) {
        this.inventory = inventory;
        this.products = products;
    }

    /** D9: a missing or deleted product is 404, which is different from "exists, zero stock". */
    @Transactional(readOnly = true)
    public InventoryResponse get(Long productId) {
        return InventoryMapper.toResponse(require(productId));
    }

    /** "Set" means overwrite: when two admins set stock at once, the last one wins, by design. */
    @Transactional
    public InventoryResponse set(Long productId, int quantity) {
        Inventory inv = require(productId);
        inv.setQuantity(quantity);
        inventory.flush();
        return InventoryMapper.toResponse(inv);
    }

    /**
     * R1: one atomic statement, quantity = quantity + delta, so concurrent adjustments all count.
     * (Stage 2 measured the old read-add-write version losing 91 of 100 concurrent +1s.)
     */
    @Transactional
    public InventoryResponse adjust(Long productId, int delta) {
        products.requireLive(productId);
        if (inventory.adjustIfValid(productId, delta, Instant.now()) == 0) {
            throw new ConflictException("Insufficient stock",
                    "Stock is %d, so it cannot be adjusted by %d".formatted(read(productId).getQuantity(), delta));
        }
        return InventoryMapper.toResponse(read(productId));
    }

    private Inventory require(Long productId) {
        products.requireLive(productId);
        return read(productId);
    }

    private Inventory read(Long productId) {
        return inventory.findById(productId)
                .orElseThrow(() -> new IllegalStateException("No inventory row for product " + productId));
    }
}
