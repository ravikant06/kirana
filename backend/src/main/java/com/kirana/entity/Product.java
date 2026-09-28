package com.kirana.entity;

import java.time.Instant;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.SequenceGenerator;
import jakarta.persistence.Table;
import jakarta.persistence.Version;
import org.hibernate.annotations.CreationTimestamp;
import org.hibernate.annotations.UpdateTimestamp;

/**
 * Product details only. Stock lives in {@link Inventory} (D1), images in {@link ProductImage}.
 * Neither is mapped as a collection here, so loading a product never drags them along.
 */
@Entity
@Table(name = "products")
public class Product {

    @Id
    @GeneratedValue(strategy = GenerationType.SEQUENCE, generator = "products_seq")
    @SequenceGenerator(name = "products_seq", sequenceName = "products_seq", allocationSize = 50)
    private Long id;

    @Column(nullable = false, length = 200)
    private String name;

    @Column(columnDefinition = "text")
    private String description;

    @Column(length = 100)
    private String category;

    // D3: double on purpose. Known cost: binary floating point.
    @Column(nullable = false)
    private double price;

    @CreationTimestamp
    @Column(nullable = false, updatable = false)
    private Instant createdAt;

    @UpdateTimestamp
    @Column(nullable = false)
    private Instant updatedAt;

    // D8: soft delete. NULL means live.
    private Instant deletedAt;

    // R3 optimistic locking: every UPDATE is "... WHERE id = ? AND version = ?" and bumps it.
    // If another save got there first, 0 rows match and Hibernate throws.
    @Version
    @Column(nullable = false)
    private long version;

    protected Product() {
        // for JPA
    }

    public Product(String name, String description, double price) {
        update(name, description, price);
    }

    public void update(String name, String description, double price) {
        this.name = name;
        this.description = description;
        this.price = price;
    }

    public void softDelete(Instant when) {
        this.deletedAt = when;
    }

    public boolean isDeleted() { return deletedAt != null; }

    public Long getId() { return id; }
    public String getName() { return name; }
    public String getDescription() { return description; }
    public String getCategory() { return category; }
    public double getPrice() { return price; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
    public Instant getDeletedAt() { return deletedAt; }
    public long getVersion() { return version; }
}
