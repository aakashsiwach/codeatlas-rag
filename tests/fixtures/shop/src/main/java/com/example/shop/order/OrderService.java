package com.example.shop.order;

public interface OrderService {

    Order findOrder(Long id);

    Order placeOrder(Long customerId, long amountCents);
}
