package com.kirana.controller;

import com.kirana.dto.FlashSaleResponse;
import com.kirana.service.FlashSaleService;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/products/{productId}/flash-sale")
public class FlashSaleController {

    private final FlashSaleService flashSales;

    public FlashSaleController(FlashSaleService flashSales) {
        this.flashSales = flashSales;
    }

    @GetMapping
    public FlashSaleResponse status(@PathVariable Long productId) {
        return flashSales.status(productId);
    }

    @PostMapping
    public FlashSaleResponse arm(@PathVariable Long productId) {
        return flashSales.arm(productId);
    }

    @DeleteMapping
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void disarm(@PathVariable Long productId) {
        flashSales.disarm(productId);
    }
}
