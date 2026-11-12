package com.example.shop.reporting;

import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;

@FeignClient(name = "order-service")
public interface OrderLookupClient {

    @GetMapping("/orders/{orderId}")
    OrderSummary fetchOrder(@PathVariable("orderId") Long orderId);
}
