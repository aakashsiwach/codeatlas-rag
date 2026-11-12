package com.example.shop.billing;

import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;

@Component
public class InvoiceJob {

    private final RestTemplate restTemplate;

    public InvoiceJob(RestTemplate restTemplate) {
        this.restTemplate = restTemplate;
    }

    @Scheduled(cron = "0 0 * * * *")
    public void syncInvoices() {
        restTemplate.getForObject("http://order-service/orders/{id}", String.class, 42L);
    }
}
