package com.example.shop.reporting;

import org.springframework.stereotype.Service;

@Service
public class ReportingService {

    private final OrderLookupClient orders;

    public ReportingService(OrderLookupClient orders) {
        this.orders = orders;
    }

    public String describeOrder(Long orderId) {
        OrderSummary summary = orders.fetchOrder(orderId);
        return summary.id() + ": " + summary.status();
    }
}
