package com.kirana.entity;

import java.time.Instant;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.FetchType;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.SequenceGenerator;
import jakarta.persistence.Table;
import org.hibernate.annotations.CreationTimestamp;
import org.hibernate.annotations.UpdateTimestamp;

/** Metadata for one image in object storage. No URL column: read URLs are signed fresh. */
@Entity
@Table(name = "product_images")
public class ProductImage {

    @Id
    @GeneratedValue(strategy = GenerationType.SEQUENCE, generator = "product_images_seq")
    @SequenceGenerator(name = "product_images_seq", sequenceName = "product_images_seq", allocationSize = 50)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "product_id")
    private Product product;

    @Column(nullable = false, unique = true, length = 500)
    private String objectKey;

    @Column(nullable = false, length = 100)
    private String contentType;

    // NULL until confirmed; then the real size reported by storage, not the client's claim.
    private Long sizeBytes;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 20)
    private ImageStatus status;

    @Column(nullable = false)
    private int position;

    @CreationTimestamp
    @Column(nullable = false, updatable = false)
    private Instant createdAt;

    @UpdateTimestamp
    @Column(nullable = false)
    private Instant updatedAt;

    protected ProductImage() {
        // for JPA
    }

    public ProductImage(Product product, String objectKey, String contentType) {
        this.product = product;
        this.objectKey = objectKey;
        this.contentType = contentType;
        this.status = ImageStatus.PENDING;
        this.position = 0;
    }

    public void activate(long realSizeBytes, String realContentType, int position) {
        this.sizeBytes = realSizeBytes;
        this.contentType = realContentType;
        this.position = position;
        this.status = ImageStatus.ACTIVE;
    }

    public Long getId() { return id; }
    public Product getProduct() { return product; }
    public String getObjectKey() { return objectKey; }
    public String getContentType() { return contentType; }
    public Long getSizeBytes() { return sizeBytes; }
    public ImageStatus getStatus() { return status; }
    public int getPosition() { return position; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
}
