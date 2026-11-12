package com.example.shop.order;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * REST API for orders.
 */
@RestController
@RequestMapping("/orders")
public class OrderController {

    private final OrderService orderService;

    public OrderController(OrderService orderService) {
        this.orderService = orderService;
    }

    /** Returns a single order by id. */
    @GetMapping("/{id}")
    public Order getOrder(@PathVariable Long id) {
        return orderService.findOrder(id);
    }

    @PostMapping
    public Order placeOrder(@RequestBody PlaceOrderRequest request) {
        return orderService.placeOrder(request.customerId(), request.amountCents());
    }

    public record PlaceOrderRequest(Long customerId, long amountCents) {
    }
}
