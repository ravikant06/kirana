package com.kirana.service;

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

    @Transactional
    public InventoryResponse set(Long productId, int quantity) {
        Inventory inv = require(productId);
        inv.setQuantity(quantity);
        inventory.flush();
        return InventoryMapper.toResponse(inv);
    }

    /**
     * Read, add in Java, write back. Two adjustments at the same moment can both read 10 and
     * both write 11, losing one. Left as is: concurrency is Stage 3.
     */
    @Transactional
    public InventoryResponse adjust(Long productId, int delta) {
        Inventory inv = require(productId);
        int next = inv.getQuantity() + delta;
        if (next < 0) {
            throw new ConflictException("Insufficient stock",
                    "Stock is %d, so it cannot be adjusted by %d".formatted(inv.getQuantity(), delta));
        }
        inv.setQuantity(next);
        inventory.flush();
        return InventoryMapper.toResponse(inv);
    }

    private Inventory require(Long productId) {
        products.requireLive(productId);
        return inventory.findById(productId)
                .orElseThrow(() -> new IllegalStateException("No inventory row for product " + productId));
    }
}
