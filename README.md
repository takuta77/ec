# EC API

See `docs/superpowers/specs/2026-05-12-ec-api-design.md` for the design and `docs/superpowers/plans/2026-05-12-ec-api.md` for the implementation plan.

## Quick start

```bash
uv sync
./scripts/gen_jwt_keys.sh
cp .env.example .env             # set NEW_RELIC_LICENSE_KEY
make up
make migrate
open http://localhost:8000/docs
```

## Tests

```bash
make test         # pure unit tests
make test-slow    # integration / workers / contracts (Docker required)
```

## Layout

- `app/modules/<name>/{router,schemas,models,repository,service}.py` — feature modules
- `app/workers/` — outbox_relay, order_consumer, checkout_sweeper
- `app/core/` — config, security, telemetry, exceptions, logging
- `app/db/`, `app/mq/`, `app/shared/`

## Contracts

`docs/contracts/checkout.md` defines the cross-service idempotency requirements for `checkout.requested`.
