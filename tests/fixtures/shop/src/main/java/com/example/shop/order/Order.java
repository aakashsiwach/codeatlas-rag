package com.example.shop.order;

import com.example.shop.customer.Customer;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.Id;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.Table;

@Entity
@Table(name = "orders")
public class Order {

    @Id
    @GeneratedValue
    private Long id;

    @ManyToOne
    private Customer customer;

    private long amountCents;

    private OrderStatus status = OrderStatus.PENDING;

    protected Order() {
    }

    public Order(Customer customer, long amountCents) {
        this.customer = customer;
        this.amountCents = amountCents;
    }

    public Customer getCustomer() {
        return customer;
    }

    public void markPaid() {
        this.status = OrderStatus.PAID;
    }

    public enum OrderStatus {
        PENDING, PAID, FAILED
    }
}
