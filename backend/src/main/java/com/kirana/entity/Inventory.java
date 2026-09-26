package com.kirana.entity;

import java.time.Instant;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.FetchType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.MapsId;
import jakarta.persistence.OneToOne;
import jakarta.persistence.Table;
import org.hibernate.annotations.UpdateTimestamp;

/**
 * Stock for one product (D1). Shares the product's primary key via {@code @MapsId}:
 * inventory.product_id is both the PK and the FK, so there is exactly one row per product.
 * The mapping is one-directional (Product does not point back) so it can stay lazy.
 */
@Entity
@Table(name = "inventory")
public class Inventory {

    @Id
    private Long productId;

    @MapsId
    @OneToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "product_id")
    private Product product;

    @Column(nullable = false)
    private int quantity;

    @UpdateTimestamp
    @Column(nullable = false)
    private Instant updatedAt;

    protected Inventory() {
        // for JPA
    }

    public Inventory(Product product) {
        this.product = product;
        this.quantity = 0;
    }

    public Long getProductId() { return productId; }
    public Product getProduct() { return product; }
    public int getQuantity() { return quantity; }
    public Instant getUpdatedAt() { return updatedAt; }

    public void setQuantity(int quantity) {
        this.quantity = quantity;
    }
}
