# ADR 0001: Retry payment gateway calls

## Status

Accepted

## Context

The payment gateway times out under load. A failed charge used to fail the whole order.

## Decision

Use Spring Retry on `PaymentService.charge` with exponential backoff and at most three attempts.
After the last attempt the `@Recover` method returns a failed `PaymentResult`.
