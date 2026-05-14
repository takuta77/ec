# `checkout.requested` cross-service contract

This service publishes `checkout.requested` with **at-least-once** delivery. Receivers (the Checkout app) MUST be safe under duplicates.

## Idempotency keys

- `event_id` — unique per outbox row. Stable across relay retries of the same row.
- `data.checkout_request_id` — stable per user submission. Reissued only when this service starts a new submission.

## Receiver requirements

Before any irreversible side effect (stock hold, payment authorization, order creation), Checkout MUST:

1. Check whether `event_id` has already been processed; if yes, ack and stop.
2. Otherwise, check whether `checkout_request_id` already produced an order; if yes, re-emit the same `order.created` (or stored failure) for this service to converge state.
3. Otherwise, process and emit `order.created` (or `order.failed`).

## Order response correlation

Response events MUST include `data.checkout_request_id`. This service does not require `cart_id` in responses — it looks up the cart via the unique partial index on `carts.checkout_request_id`.

## Verification

Contract tests in this repo (`tests/contracts/`) assert the envelope shape. Cross-service duplicate-delivery tests live in the Checkout app's repo.
