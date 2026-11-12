package com.example.shop.payment;

import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;

@FeignClient(name = "payment-gateway", path = "/gateway")
public interface PaymentGatewayClient {

    @PostMapping("/charges")
    PaymentResult createCharge(@RequestBody ChargeRequest request);
}
