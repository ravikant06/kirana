package com.kirana.controller;

import com.kirana.auth.Permission;
import com.kirana.auth.RequiresPermission;
import java.util.List;

import com.kirana.dto.FlashSaleResponse;
import com.kirana.dto.FlashSaleSummary;
import com.kirana.service.FlashSaleService;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class FlashSaleController {

    private final FlashSaleService flashSales;

    public FlashSaleController(FlashSaleService flashSales) {
        this.flashSales = flashSales;
    }

    /** Every product with an armed sale and its units left (shoppers poll this). */
    @GetMapping("/flash-sales")
    public List<FlashSaleSummary> active() {
        return flashSales.active();
    }

    @GetMapping("/products/{productId}/flash-sale")
    public FlashSaleResponse status(@PathVariable Long productId) {
        return flashSales.status(productId);
    }

    @RequiresPermission(Permission.CATALOG_WRITE)
    @PostMapping("/products/{productId}/flash-sale")
    public FlashSaleResponse arm(@PathVariable Long productId) {
        return flashSales.arm(productId);
    }

    @RequiresPermission(Permission.CATALOG_WRITE)
    @DeleteMapping("/products/{productId}/flash-sale")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void disarm(@PathVariable Long productId) {
        flashSales.disarm(productId);
    }
}
