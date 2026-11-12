package com.example.shop.payment;

public record PaymentResult(boolean success, String message) {

    public static PaymentResult failed(String message) {
        return new PaymentResult(false, message);
    }
}
