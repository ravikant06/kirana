package com.kirana.controller;

import com.kirana.dto.AdjustInventoryRequest;
import com.kirana.dto.InventoryResponse;
import com.kirana.dto.SetInventoryRequest;
import com.kirana.service.InventoryService;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/products/{productId}/inventory")
public class InventoryController {

    private final InventoryService inventory;

    public InventoryController(InventoryService inventory) {
        this.inventory = inventory;
    }

    @GetMapping
    public InventoryResponse get(@PathVariable Long productId) {
        return inventory.get(productId);
    }

    @PutMapping
    public InventoryResponse set(@PathVariable Long productId, @Valid @RequestBody SetInventoryRequest req) {
        return inventory.set(productId, req.quantity());
    }

    @PostMapping("/adjustments")
    public InventoryResponse adjust(@PathVariable Long productId, @Valid @RequestBody AdjustInventoryRequest req) {
        return inventory.adjust(productId, req.delta());
    }
}
