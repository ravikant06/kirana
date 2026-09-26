package com.kirana.controller;

import java.net.URI;

import com.kirana.dto.PageResponse;
import com.kirana.dto.ProductDetail;
import com.kirana.dto.ProductRequest;
import com.kirana.dto.ProductSummary;
import com.kirana.service.ProductService;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/products")
public class ProductController {

    private final ProductService products;

    public ProductController(ProductService products) {
        this.products = products;
    }

    /** Out-of-range page and size are clamped (size to 1..100), not rejected. */
    @GetMapping
    public PageResponse<ProductSummary> list(@RequestParam(defaultValue = "0") int page,
                                             @RequestParam(defaultValue = "12") int size) {
        return products.list(page, size);
    }

    @GetMapping("/{id}")
    public ProductDetail get(@PathVariable Long id) {
        return products.get(id);
    }

    @PostMapping
    public ResponseEntity<ProductDetail> create(@Valid @RequestBody ProductRequest req) {
        ProductDetail created = products.create(req);
        return ResponseEntity.created(URI.create("/products/" + created.id())).body(created);
    }

    @PutMapping("/{id}")
    public ProductDetail update(@PathVariable Long id, @Valid @RequestBody ProductRequest req) {
        return products.update(id, req);
    }

    @DeleteMapping("/{id}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void delete(@PathVariable Long id) {
        products.delete(id);
    }
}
