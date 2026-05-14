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

## Operations: DLQ tools

Manage the per-consumer dead-letter queues (`<queue>.dlq`). Run from a shell
with access to RabbitMQ via the `RABBITMQ_URL` env var. The CLI is a thin
wrapper over the reusable helpers in `app/mq/dlq_admin.py`; the same
helpers will back HTTP admin endpoints and monitoring jobs in later work.

```bash
# Show how many messages are stuck.
uv run python scripts/dlq.py count ec.order_consumer

# Inspect up to N messages (non-destructive).
uv run python scripts/dlq.py peek ec.order_consumer --limit 5

# Re-publish to the main exchange (dry-run by default).
uv run python scripts/dlq.py redrive ec.order_consumer --limit 10
uv run python scripts/dlq.py redrive ec.order_consumer --all --apply

# Permanently discard.
uv run python scripts/dlq.py drain ec.order_consumer --limit 10
uv run python scripts/dlq.py drain ec.order_consumer --all --apply
```

Exit codes: `0` ok, `2` queue not found / arg error, `3` AMQP connection failed, `130` SIGINT.

## Local checks (same as CI)

CI runs each tool directly via `uv run`; you can reproduce locally with the
same commands. No Makefile or task runner is used.

### Development server

```bash
# Auto-reload dev server
uv run fastapi dev app/main.py

# Production-like (multi-worker)
uv run fastapi run app/main.py --workers 4
```

### Lint / type / tests

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest -m "not slow"
uv run pytest -m slow         # requires Docker for Testcontainers
```

### Security

```bash
# Python dependency vulnerabilities
uv export --no-hashes --no-dev --no-emit-project > /tmp/req.txt
uv run pip-audit -r /tmp/req.txt

# SAST
uv run semgrep ci \
  --config p/python \
  --config p/security-audit \
  --config p/owasp-top-ten \
  --config p/jwt

# Secrets scan
gitleaks detect --redact --no-banner

# Dockerfile lint
docker run --rm -i hadolint/hadolint < docker/Dockerfile

# Container image scan
docker build -f docker/Dockerfile -t ec-api:dev .
trivy image --severity HIGH,CRITICAL ec-api:dev
```

External binaries (`gitleaks`, `hadolint`, `trivy`) are installed via
`brew install gitleaks hadolint aquasecurity/trivy/trivy`. CI uses the
respective GitHub Actions, so these are optional for contributors.

## Branch protection (one-time setup)

Apply the following to `main` (and any long-lived `feature/*` branch):

1. Go to **Settings → Branches → Branch protection rules → Add rule**
2. Branch name pattern: `main`
3. Tick:
   - **Require a pull request before merging** (1 approval, dismiss stale reviews)
   - **Require status checks to pass before merging**
     - Required checks (initial rollout):
       - `ci / test-unit`
       - `ci / test-slow`
       - `security / scan / deps (pip-audit)`
       - `security / scan / sast (semgrep)`
       - `security / scan / secrets (gitleaks)`
     - **Require branches to be up to date**: ON
   - **Require linear history**: ON
   - **Do not allow force pushes**: ON
   - **Do not allow deletions**: ON

### Why `ci / lint` and `ci / type` are not required yet

The current codebase has pre-existing `ruff` and `mypy --strict` violations
inherited from earlier feature work (≈ 7 ruff errors, 38 format mismatches,
25 mypy errors). The `lint` and `type` jobs run on every PR and the results
appear in the Checks panel, but they are intentionally left off the
required-checks list until a dedicated cleanup PR lands. See the
"Open follow-ups" section in `docs/superpowers/specs/2026-05-14-ci-security-design.md`
for the tracked work.

After the cleanup PR is merged, add these two checks to the required list:
- `ci / lint`
- `ci / type`

### Warn-only jobs

`image`, `dockerfile`, `iac` are warn-only and intentionally left off the
required list until their noise level is evaluated.
