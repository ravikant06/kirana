package com.kirana.entity;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;

import jakarta.persistence.CascadeType;
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
import jakarta.persistence.OneToMany;
import jakarta.persistence.OrderBy;
import jakarta.persistence.SequenceGenerator;
import jakarta.persistence.Table;
import org.hibernate.annotations.CreationTimestamp;
import org.hibernate.annotations.UpdateTimestamp;

/** Order header (D5). The total is frozen at checkout: the sum of the line totals. */
@Entity
@Table(name = "orders")
public class Order {

    @Id
    @GeneratedValue(strategy = GenerationType.SEQUENCE, generator = "orders_seq")
    @SequenceGenerator(name = "orders_seq", sequenceName = "orders_seq", allocationSize = 50)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "user_id")
    private User user;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 20)
    private OrderStatus status;

    @Column(nullable = false)
    private double total;

    // Lazy (the default for collections). Loading an order does not load its lines;
    // touching them later does, which is what the N+1 and open-in-view experiments show.
    @OneToMany(mappedBy = "order", cascade = CascadeType.ALL)
    @OrderBy("id")
    private List<OrderItem> items = new ArrayList<>();

    // Stage 5 payment fields. Status changes go through OrderRepository's conditional
    // transitions (UPDATE ... WHERE status = 'CREATED'), never through setters here.
    private String paymentProvider;
    private String gatewayOrderId;
    private String paymentId;
    private Instant paymentDueAt;
    private Instant paidAt;
    private String closedReason;
    private String latePaymentId; // Stage 6c: paid after it closed; a refund is needed

    @CreationTimestamp
    @Column(nullable = false, updatable = false)
    private Instant createdAt;

    @UpdateTimestamp
    @Column(nullable = false)
    private Instant updatedAt;

    protected Order() {
        // for JPA
    }

    public Order(User user, Instant paymentDueAt) {
        this.user = user;
        this.status = OrderStatus.CREATED;
        this.paymentDueAt = paymentDueAt;
    }

    /** Copies name and price from the product as they are now (D2 snapshot). */
    public void addLine(Product product, int quantity) {
        OrderItem item = new OrderItem(this, product, quantity);
        items.add(item);
        total += item.getLineTotal();
    }

    public Long getId() { return id; }
    public User getUser() { return user; }
    public OrderStatus getStatus() { return status; }
    public double getTotal() { return total; }
    public List<OrderItem> getItems() { return items; }
    public String getPaymentProvider() { return paymentProvider; }
    public String getGatewayOrderId() { return gatewayOrderId; }
    public String getPaymentId() { return paymentId; }
    public Instant getPaymentDueAt() { return paymentDueAt; }
    public Instant getPaidAt() { return paidAt; }
    public String getClosedReason() { return closedReason; }
    public String getLatePaymentId() { return latePaymentId; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
}
