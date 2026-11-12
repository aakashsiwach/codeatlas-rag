package com.example.shop.order;

import com.example.shop.customer.Customer;
import com.example.shop.customer.CustomerRepository;
import com.example.shop.payment.PaymentService;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class OrderServiceImpl implements OrderService {

    private final OrderRepository orderRepository;
    private final CustomerRepository customerRepository;
    private final PaymentService paymentService;

    public OrderServiceImpl(OrderRepository orderRepository, CustomerRepository customerRepository,
            PaymentService paymentService) {
        this.orderRepository = orderRepository;
        this.customerRepository = customerRepository;
        this.paymentService = paymentService;
    }

    @Override
    public Order findOrder(Long id) {
        return orderRepository.findById(id).orElseThrow(() -> new OrderNotFoundException(id));
    }

    @Override
    @Transactional
    public Order placeOrder(Long customerId, long amountCents) {
        Customer customer = customerRepository.findById(customerId).orElseThrow();
        Order order = new Order(customer, amountCents);
        paymentService.charge(customer, amountCents);
        order.markPaid();
        return orderRepository.save(order);
    }
}
