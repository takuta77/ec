# Changelog

## 0.2.0

- Add `POST /cart/reopen` to restore `failed(reason='timeout')` carts back to `open` with line items and price snapshots preserved
- Add `POST /cart/cancel` to transition `open` carts to a new terminal `cancelled` state
- Rename `/carts/me/*` endpoints to `/cart/*` (singular) for REST hygiene; drop unused `cart_id` path parameter from checkout
- Migrate FastAPI startup/shutdown handlers from `@app.on_event` to the `lifespan` context manager

## 0.1.0 (initial)

- FastAPI EC API with users / items / auth / carts
- Transactional outbox + outbox-relay worker
- Order-event consumer with retry/DLQ (RabbitMQ retry exchange)
- Checkout timeout sweeper
- OpenTelemetry (traces / metrics / logs) → otel-collector → New Relic
- Containerized via docker compose
- Cross-service `checkout.requested` idempotency contract documented
