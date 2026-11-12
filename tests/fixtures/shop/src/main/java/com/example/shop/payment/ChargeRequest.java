package com.example.shop.payment;

public record ChargeRequest(String customerEmail, long amountCents) {
}
