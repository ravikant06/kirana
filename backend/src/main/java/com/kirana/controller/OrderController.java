package com.kirana.controller;

import java.net.URI;
import java.util.List;

import com.kirana.dto.OrderResponse;
import com.kirana.service.OrderService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/orders")
public class OrderController {

    private final OrderService orders;

    public OrderController(OrderService orders) {
        this.orders = orders;
    }

    @PostMapping
    public ResponseEntity<OrderResponse> place(@RequestHeader(Headers.USER_ID) Long userId) {
        OrderResponse order = orders.place(userId);
        return ResponseEntity.created(URI.create("/orders/" + order.id())).body(order);
    }

    @GetMapping
    public List<OrderResponse> list(@RequestHeader(Headers.USER_ID) Long userId) {
        return orders.list(userId);
    }

    @GetMapping("/{id}")
    public OrderResponse get(@RequestHeader(Headers.USER_ID) Long userId, @PathVariable Long id) {
        return orders.get(userId, id);
    }
}
