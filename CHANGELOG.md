# Changelog

## 0.1.0 (initial)

- FastAPI EC API with users / items / auth / carts
- Transactional outbox + outbox-relay worker
- Order-event consumer with retry/DLQ (RabbitMQ retry exchange)
- Checkout timeout sweeper
- OpenTelemetry (traces / metrics / logs) → otel-collector → New Relic
- Containerized via docker compose
- Cross-service `checkout.requested` idempotency contract documented
