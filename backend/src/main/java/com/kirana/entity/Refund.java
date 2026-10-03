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

/**
 * Money we owe back for a payment that arrived after its order closed (Stage 6d). Inserted by
 * RefundService; status changes only through RefundRepository's conditional updates.
 */
@Entity
@Table(name = "refunds")
public class Refund {

    @Id
    @GeneratedValue(strategy = GenerationType.SEQUENCE, generator = "refunds_seq")
    @SequenceGenerator(name = "refunds_seq", sequenceName = "refunds_seq", allocationSize = 50)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "order_id")
    private Order order;

    @Column(nullable = false, unique = true, length = 100)
    private String paymentId;

    @Column(nullable = false, length = 20)
    private String provider;

    @Column(nullable = false)
    private double amount;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 20)
    private RefundStatus status;

    private String gatewayRefundId;

    @Column(nullable = false)
    private int attempts;

    @Column(length = 500)
    private String lastError;

    private Instant claimedUntil;

    @CreationTimestamp
    @Column(nullable = false, updatable = false)
    private Instant createdAt;

    @UpdateTimestamp
    @Column(nullable = false)
    private Instant updatedAt;

    private Instant processedAt;

    protected Refund() {
        // for JPA
    }

    public Refund(Order order, String paymentId, String provider, double amount) {
        this.order = order;
        this.paymentId = paymentId;
        this.provider = provider;
        this.amount = amount;
        this.status = RefundStatus.REQUESTED;
    }

    public Long getId() { return id; }
    public Order getOrder() { return order; }
    public String getPaymentId() { return paymentId; }
    public String getProvider() { return provider; }
    public double getAmount() { return amount; }
    public RefundStatus getStatus() { return status; }
    public String getGatewayRefundId() { return gatewayRefundId; }
    public int getAttempts() { return attempts; }
    public String getLastError() { return lastError; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
    public Instant getProcessedAt() { return processedAt; }
}
