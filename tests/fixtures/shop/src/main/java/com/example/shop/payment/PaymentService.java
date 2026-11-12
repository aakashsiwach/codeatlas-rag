package com.example.shop.payment;

import com.example.shop.customer.Customer;
import org.springframework.retry.annotation.Backoff;
import org.springframework.retry.annotation.Recover;
import org.springframework.retry.annotation.Retryable;
import org.springframework.stereotype.Service;

@Service
public class PaymentService {

    private static final int MAX_ATTEMPTS = 3;

    private final PaymentGatewayClient gatewayClient;

    public PaymentService(PaymentGatewayClient gatewayClient) {
        this.gatewayClient = gatewayClient;
    }

    /**
     * Charges the customer. Gateway timeouts trigger a retry with exponential backoff,
     * up to three attempts in total.
     */
    @Retryable(retryFor = GatewayTimeoutException.class, maxAttempts = MAX_ATTEMPTS,
            backoff = @Backoff(delay = 200, multiplier = 2))
    public PaymentResult charge(Customer customer, long amountCents) {
        return gatewayClient.createCharge(new ChargeRequest(customer.getEmail(), amountCents));
    }

    @Recover
    public PaymentResult recover(GatewayTimeoutException e, Customer customer, long amountCents) {
        return PaymentResult.failed("payment gateway unavailable after " + MAX_ATTEMPTS + " attempts");
    }
}
