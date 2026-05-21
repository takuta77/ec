# C-1c Production Config Reconciliation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the three gaps preventing live ECS startup — switch JWT keys to env-based PEM strings, add an `otel-collector` sidecar to every ECS task, and route OTel→New Relic through that sidecar.

**Architecture:** App reads JWT PEM directly from `JWT_PRIVATE_KEY` / `JWT_PUBLIC_KEY` env (Secrets Manager continues injecting them). Each ECS task (api + 3 workers) gains a second container — a custom-built `otel-collector` image — that receives OTLP on `localhost:4317` and forwards to NR. CD builds both images in parallel from the same git SHA.

**Tech Stack:** FastAPI, Pydantic Settings, python-jose, OpenTelemetry Collector (otel/opentelemetry-collector-contrib 0.111.0), AWS ECS Fargate, AWS Secrets Manager, GitHub Actions, ecspresso v2.5+, Terraform 1.10+.

**Scope boundary:** Static validation only — no live AWS apply. Operator runs the post-merge steps documented in spec §8.

**Spec:** `docs/superpowers/specs/2026-05-21-aws-infra-c1c-design.md`

**Working directory:** worktree `.worktrees/aws-infra-c1c/`, branch `feature/aws-infra-c1c` (already created, spec already committed).

---

## File map

| File | Action | Owns |
|---|---|---|
| `app/core/config.py` | Modify | `jwt_*_path: Path` → `jwt_*_key: str` |
| `app/modules/auth/dependencies.py` | Modify | `.read_text()` → direct attr read |
| `app/modules/auth/router.py` | Modify | same |
| `tests/conftest.py` | Modify | fixture: write tmp PEM files → set env to PEM string directly |
| `tests/core/test_config.py` | Modify | use new env names |
| `tests/test_spa_serving.py` | Modify | 3 places × 2 envs |
| `tests/db/test_alembic.py` | Modify | 2 envs |
| `tests/mq/test_dlq_admin.py` | Modify | 2 envs |
| `docker/otel-collector/Dockerfile` | Create | `FROM otel/opentelemetry-collector-contrib:0.111.0` + COPY config |
| `docker/otel-collector/config.yaml` | (no change) | already exists, used by docker-compose |
| `infra/terraform/ecr.tf` | Modify | append `aws_ecr_repository.otel_collector` + lifecycle |
| `infra/terraform/outputs.tf` | Modify | append `otel_collector_ecr_repository_url` output |
| `infra/ecspresso/api/ecs-task-def.json` | Modify | add sidecar + remove `NEW_RELIC_LICENSE_KEY` from `app` |
| `infra/ecspresso/outbox-relay/ecs-task-def.json` | Modify | same |
| `infra/ecspresso/order-consumer/ecs-task-def.json` | Modify | same |
| `infra/ecspresso/checkout-sweeper/ecs-task-def.json` | Modify | same |
| `.github/workflows/cd.yml` | Modify | `build-and-push` job → matrix (app + collector) |
| `docker-compose.yml` | Modify | drop secrets volume, add `environment.JWT_*` to all 4 services |
| `.env.example` | Modify | `JWT_*_PATH` → `JWT_PRIVATE_KEY` / `JWT_PUBLIC_KEY` (empty placeholder, comment) |
| `Makefile` | Create | `load-jwt` target |
| `.gitignore` | Modify | add `.env.jwt` |
| `infra/terraform/README.md` | Modify | §5 補足 (cd.yml matrix), §1 はそのまま |

Note: spec §6.4 said "worker cpu/memory を 512/1024 へ引き上げ if 不足". 実機確認の結果、**全 4 service が既に 512/1024** なので変更不要 (`memoryReservation: 128` sidecar で十分)。

---

## Pre-flight

Verify tools:
```bash
cd /Users/takuma/cross/ec/.worktrees/aws-infra-c1c
/Users/takuma/.local/bin/mise exec -- terraform version  # expect 1.10.5
jq --version
actionlint --version
docker --version    # for `docker build` smoke test in Task 5
uv --version
```

All should print versions, none should fail.

---

## Task 1: App config — switch JWT to env-based PEM strings

**Files:**
- Modify: `app/core/config.py`

- [ ] **Step 1: Replace JWT field declarations**

In `app/core/config.py`:

1. Remove `from pathlib import Path` on line 2.
2. Change lines 15-16 from
   ```python
       jwt_private_key_path: Path
       jwt_public_key_path: Path
   ```
   to
   ```python
       jwt_private_key: str
       jwt_public_key: str
   ```

Full file after edit:

```python
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    database_url: str
    rabbitmq_url: str

    jwt_algorithm: str = "RS256"
    jwt_private_key: str
    jwt_public_key: str
    jwt_access_ttl_min: int = 15
    jwt_refresh_ttl_days: int = 14

    otel_exporter_otlp_endpoint: str
    otel_exporter_otlp_protocol: str = "grpc"
    otel_service_name: str = "ec-api"
    otel_resource_attributes: str = "service.namespace=ec,deployment.environment=local"

    checkout_sweep_interval_sec: int = 300
    checkout_timeout_hours: int = 24
    max_consumer_retries: int = 5

    serve_frontend: bool = False
    frontend_dist_path: str = "frontend/dist"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # pydantic-settings populates required fields from env at runtime
```

- [ ] **Step 2: ruff check (will fail because callers still reference old names, ignore lint, but check that this file parses)**

```bash
uv run ruff check app/core/config.py
```

Expected: clean (no errors specific to this file).

- [ ] **Step 3: Do NOT commit yet** — Tasks 2 and 3 must land in the same commit (otherwise app/tests are temporarily broken).

Move on to Task 2.

---

## Task 2: App auth code — read PEM directly

**Files:**
- Modify: `app/modules/auth/dependencies.py:26`
- Modify: `app/modules/auth/router.py:24-25`

- [ ] **Step 1: dependencies.py**

In `app/modules/auth/dependencies.py`, line 26 currently reads:
```python
pub = settings.jwt_public_key_path.read_text()
```

Change to:
```python
pub = settings.jwt_public_key
```

- [ ] **Step 2: router.py**

In `app/modules/auth/router.py`, lines 24-25 currently read:
```python
jwt_private_key=s.jwt_private_key_path.read_text(),
jwt_public_key=s.jwt_public_key_path.read_text(),
```

Change to:
```python
jwt_private_key=s.jwt_private_key,
jwt_public_key=s.jwt_public_key,
```

- [ ] **Step 3: ruff + mypy on the changed files**

```bash
uv run ruff check app/modules/auth/dependencies.py app/modules/auth/router.py
uv run mypy app/modules/auth/dependencies.py app/modules/auth/router.py
```

Expected: both clean. (mypy may surface unrelated issues from imported modules; verify no errors on the specific lines above.)

- [ ] **Step 4: Do NOT commit yet** — Task 3 must complete first.

Move on to Task 3.

---

## Task 3: Test fixtures — supply PEM strings directly

**Files:**
- Modify: `tests/conftest.py:64-79`
- Modify: `tests/core/test_config.py:9-10`
- Modify: `tests/test_spa_serving.py:12,13,71,72,87,88` (3 places × 2 envs each)
- Modify: `tests/db/test_alembic.py:27-28`
- Modify: `tests/mq/test_dlq_admin.py:374-375`

- [ ] **Step 1: tests/conftest.py — the `app_with_db` fixture**

Current (lines 65-79):
```python
@pytest.fixture
async def app_with_db(database_url, jwt_keys, monkeypatch):
    priv, pub = jwt_keys
    from pathlib import Path
    import tempfile

    priv_path = Path(tempfile.mkstemp(suffix=".pem")[1])
    priv_path.write_text(priv)
    pub_path = Path(tempfile.mkstemp(suffix=".pem")[1])
    pub_path.write_text(pub)

    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("RABBITMQ_URL", "amqp://guest:guest@localhost/")
    monkeypatch.setenv("JWT_PRIVATE_KEY_PATH", str(priv_path))
    monkeypatch.setenv("JWT_PUBLIC_KEY_PATH", str(pub_path))
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
```

Replace with (drop tmpfile creation, set PEM strings directly):

```python
@pytest.fixture
async def app_with_db(database_url, jwt_keys, monkeypatch):
    priv, pub = jwt_keys

    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("RABBITMQ_URL", "amqp://guest:guest@localhost/")
    monkeypatch.setenv("JWT_PRIVATE_KEY", priv)
    monkeypatch.setenv("JWT_PUBLIC_KEY", pub)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
```

(Remove the now-unused `from pathlib import Path` and `import tempfile` lines.)

- [ ] **Step 2: tests/core/test_config.py — use new env names**

Replace lines 9-10:
```python
    monkeypatch.setenv("JWT_PRIVATE_KEY_PATH", "/tmp/priv.pem")
    monkeypatch.setenv("JWT_PUBLIC_KEY_PATH", "/tmp/pub.pem")
```

With:
```python
    monkeypatch.setenv("JWT_PRIVATE_KEY", "dummy-priv-pem")
    monkeypatch.setenv("JWT_PUBLIC_KEY", "dummy-pub-pem")
```

The test only exercises Settings parsing — it never uses the PEM content. Any non-empty string is fine.

- [ ] **Step 3: tests/test_spa_serving.py — 3 places × 2 envs**

Lines 12-13, 71-72, 87-88 — replace each pair:

```python
    monkeypatch.setenv("JWT_PRIVATE_KEY_PATH", "/tmp/x.pem")
    monkeypatch.setenv("JWT_PUBLIC_KEY_PATH", "/tmp/x.pem")
```

With:
```python
    monkeypatch.setenv("JWT_PRIVATE_KEY", "x")
    monkeypatch.setenv("JWT_PUBLIC_KEY", "x")
```

Apply at all three locations (use Edit with replace_all=true on the matching text if the file has exactly three identical pairs; verify by inspection first).

- [ ] **Step 4: tests/db/test_alembic.py — 2 envs**

Lines 27-28 — change:
```python
    env.setdefault("JWT_PRIVATE_KEY_PATH", "/tmp/priv.pem")
    env.setdefault("JWT_PUBLIC_KEY_PATH", "/tmp/pub.pem")
```

To:
```python
    env.setdefault("JWT_PRIVATE_KEY", "x")
    env.setdefault("JWT_PUBLIC_KEY", "x")
```

- [ ] **Step 5: tests/mq/test_dlq_admin.py — 2 envs**

Lines 374-375 — change:
```python
        "JWT_PRIVATE_KEY_PATH": "/tmp/x.pem",
        "JWT_PUBLIC_KEY_PATH": "/tmp/x.pem",
```

To:
```python
        "JWT_PRIVATE_KEY": "x",
        "JWT_PUBLIC_KEY": "x",
```

- [ ] **Step 6: Sanity grep**

```bash
grep -rn "JWT_PRIVATE_KEY_PATH\|JWT_PUBLIC_KEY_PATH\|jwt_private_key_path\|jwt_public_key_path" tests/ app/
```

Expected: no output (zero matches across the entire repo's tests/ and app/).

- [ ] **Step 7: Run unit tests**

```bash
uv run pytest -m "not slow" -q
```

Expected: all green (32+ tests pass). If any test fails on JWT loading, re-check Step 1 through 5.

- [ ] **Step 8: ruff**

```bash
uv run ruff check
```

Expected: All checks passed.

- [ ] **Step 9: Single commit covering Tasks 1, 2, 3**

```bash
git add app/core/config.py \
        app/modules/auth/dependencies.py \
        app/modules/auth/router.py \
        tests/conftest.py \
        tests/core/test_config.py \
        tests/test_spa_serving.py \
        tests/db/test_alembic.py \
        tests/mq/test_dlq_admin.py
git commit -m "feat(auth): read JWT keys from env (PEM string) instead of file path

- Settings.jwt_private_key / jwt_public_key are now plain strings.
- dependencies.py / router.py read the PEM directly without .read_text().
- Test fixtures provide PEM content via monkeypatch.setenv.
- Breaks JWT_PRIVATE_KEY_PATH / JWT_PUBLIC_KEY_PATH env (intentional)."
```

---

## Task 4: otel-collector Dockerfile

**Files:**
- Create: `docker/otel-collector/Dockerfile`

The `docker/otel-collector/config.yaml` already exists with the required content (verified — receivers/processors/exporters set up for OTLP→NR forwarding).

- [ ] **Step 1: Create the Dockerfile**

Write `docker/otel-collector/Dockerfile`:

```dockerfile
FROM otel/opentelemetry-collector-contrib:0.111.0

COPY config.yaml /etc/otelcol/config.yaml

CMD ["--config=/etc/otelcol/config.yaml"]
```

- [ ] **Step 2: Smoke-test the build locally**

```bash
docker build -t ec-api-otel-collector:smoke docker/otel-collector/
```

Expected: `Successfully built ...` / `Successfully tagged ec-api-otel-collector:smoke`. If `docker` is not running, skip this step and rely on CI to verify the build at the matrix-push step.

- [ ] **Step 3: Inspect the image (only if Step 2 succeeded)**

```bash
docker run --rm --entrypoint cat ec-api-otel-collector:smoke /etc/otelcol/config.yaml | head -5
```

Expected: prints the first lines of the config (`receivers:` etc.).

- [ ] **Step 4: Commit**

```bash
git add docker/otel-collector/Dockerfile
git commit -m "infra(otel-collector): Dockerfile to bundle config with the official image"
```

---

## Task 5: Terraform ECR repo for collector

**Files:**
- Modify: `infra/terraform/ecr.tf` (append)
- Modify: `infra/terraform/outputs.tf` (append)

- [ ] **Step 1: Append ECR repo + lifecycle policy to `infra/terraform/ecr.tf`**

Add at the end of `infra/terraform/ecr.tf`:

```hcl

resource "aws_ecr_repository" "otel_collector" {
  name                 = "${var.project}-otel-collector"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "otel_collector" {
  repository = aws_ecr_repository.otel_collector.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "expire untagged images older than 14 days"
      selection = {
        tagStatus   = "untagged"
        countType   = "sinceImagePushed"
        countUnit   = "days"
        countNumber = 14
      }
      action = { type = "expire" }
    }]
  })
}
```

- [ ] **Step 2: Append output to `infra/terraform/outputs.tf`**

```hcl

output "otel_collector_ecr_repository_url" {
  value = aws_ecr_repository.otel_collector.repository_url
}
```

- [ ] **Step 3: Validate**

```bash
cd /Users/takuma/cross/ec/.worktrees/aws-infra-c1c
/Users/takuma/.local/bin/mise exec -- terraform -chdir=infra/terraform fmt -check
/Users/takuma/.local/bin/mise exec -- terraform -chdir=infra/terraform validate
```

Expected: `Success!` and no fmt diffs. If fmt-check fails, run `terraform -chdir=infra/terraform fmt` and re-check.

- [ ] **Step 4: Commit**

```bash
git add infra/terraform/ecr.tf infra/terraform/outputs.tf
git commit -m "infra(tf): add ec-api-otel-collector ECR repo + lifecycle + output"
```

---

## Task 6: ecspresso api task def — add sidecar, remove NR from app

**Files:**
- Modify: `infra/ecspresso/api/ecs-task-def.json`

Current `containerDefinitions[]` has 1 container (`app`). Add a second (`otel-collector`), and remove `NEW_RELIC_LICENSE_KEY` from the `app` container's `secrets[]`.

- [ ] **Step 1: Replace the file with the updated content**

Replace the entire contents of `infra/ecspresso/api/ecs-task-def.json` with:

```jsonc
{
  "family": "ec-api-prod-api",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "512",
  "memory": "1024",
  "executionRoleArn": "{{ tfstate `aws_iam_role.ecs_task_execution.arn` }}",
  "taskRoleArn": "{{ tfstate `aws_iam_role.ecs_task.arn` }}",
  "containerDefinitions": [
    {
      "name": "app",
      "image": "{{ tfstate `aws_ecr_repository.app.repository_url` }}:{{ must_env `IMAGE_TAG` }}",
      "essential": true,
      "command": ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"],
      "portMappings": [{ "containerPort": 8000, "protocol": "tcp" }],
      "environment": [
        { "name": "SERVE_FRONTEND", "value": "false" },
        { "name": "OTEL_EXPORTER_OTLP_ENDPOINT", "value": "http://localhost:4317" }
      ],
      "secrets": [
        { "name": "DATABASE_URL", "valueFrom": "{{ tfstate `aws_secretsmanager_secret.app[\"database_url\"].arn` }}" },
        { "name": "RABBITMQ_URL", "valueFrom": "{{ tfstate `aws_secretsmanager_secret.app[\"rabbitmq_url\"].arn` }}" },
        { "name": "JWT_PRIVATE_KEY", "valueFrom": "{{ tfstate `aws_secretsmanager_secret.app[\"jwt_private_key\"].arn` }}" },
        { "name": "JWT_PUBLIC_KEY", "valueFrom": "{{ tfstate `aws_secretsmanager_secret.app[\"jwt_public_key\"].arn` }}" }
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "{{ tfstate `aws_cloudwatch_log_group.ecs.name` }}",
          "awslogs-region": "{{ must_env `AWS_REGION` }}",
          "awslogs-stream-prefix": "api"
        }
      }
    },
    {
      "name": "otel-collector",
      "image": "{{ tfstate `aws_ecr_repository.otel_collector.repository_url` }}:{{ must_env `IMAGE_TAG` }}",
      "essential": true,
      "cpu": 0,
      "memoryReservation": 128,
      "portMappings": [{ "containerPort": 4317, "protocol": "tcp" }],
      "secrets": [
        { "name": "NEW_RELIC_LICENSE_KEY", "valueFrom": "{{ tfstate `aws_secretsmanager_secret.app[\"new_relic_license_key\"].arn` }}" }
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "{{ tfstate `aws_cloudwatch_log_group.ecs.name` }}",
          "awslogs-region": "{{ must_env `AWS_REGION` }}",
          "awslogs-stream-prefix": "otel-collector"
        }
      }
    }
  ]
}
```

- [ ] **Step 2: Verify JSON syntax**

```bash
jq empty infra/ecspresso/api/ecs-task-def.json
```

Expected: no output.

- [ ] **Step 3: Verify `NEW_RELIC_LICENSE_KEY` is no longer in app's secrets and is in collector's secrets**

```bash
jq '.containerDefinitions[] | {name, secrets: [.secrets[]?.name]}' \
  infra/ecspresso/api/ecs-task-def.json
```

Expected: `app` has `["DATABASE_URL","RABBITMQ_URL","JWT_PRIVATE_KEY","JWT_PUBLIC_KEY"]`; `otel-collector` has `["NEW_RELIC_LICENSE_KEY"]`.

- [ ] **Step 4: Commit**

```bash
git add infra/ecspresso/api/ecs-task-def.json
git commit -m "infra(ecspresso): api task def — add otel-collector sidecar, move NR key to collector"
```

---

## Task 7: ecspresso worker task defs — add sidecar to all 3 workers

**Files:**
- Modify: `infra/ecspresso/outbox-relay/ecs-task-def.json`
- Modify: `infra/ecspresso/order-consumer/ecs-task-def.json`
- Modify: `infra/ecspresso/checkout-sweeper/ecs-task-def.json`

Each worker keeps its own `family` / `command` / `awslogs-stream-prefix`. The transform pattern is identical to Task 6 (add sidecar container, drop NR secret from app).

- [ ] **Step 1: outbox-relay**

Replace the entire content of `infra/ecspresso/outbox-relay/ecs-task-def.json` with:

```jsonc
{
  "family": "ec-api-prod-outbox-relay",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "512",
  "memory": "1024",
  "executionRoleArn": "{{ tfstate `aws_iam_role.ecs_task_execution.arn` }}",
  "taskRoleArn": "{{ tfstate `aws_iam_role.ecs_task.arn` }}",
  "containerDefinitions": [
    {
      "name": "app",
      "image": "{{ tfstate `aws_ecr_repository.app.repository_url` }}:{{ must_env `IMAGE_TAG` }}",
      "essential": true,
      "command": ["python", "-m", "app.workers.outbox_relay"],
      "environment": [
        { "name": "SERVE_FRONTEND", "value": "false" },
        { "name": "OTEL_EXPORTER_OTLP_ENDPOINT", "value": "http://localhost:4317" }
      ],
      "secrets": [
        { "name": "DATABASE_URL", "valueFrom": "{{ tfstate `aws_secretsmanager_secret.app[\"database_url\"].arn` }}" },
        { "name": "RABBITMQ_URL", "valueFrom": "{{ tfstate `aws_secretsmanager_secret.app[\"rabbitmq_url\"].arn` }}" },
        { "name": "JWT_PRIVATE_KEY", "valueFrom": "{{ tfstate `aws_secretsmanager_secret.app[\"jwt_private_key\"].arn` }}" },
        { "name": "JWT_PUBLIC_KEY", "valueFrom": "{{ tfstate `aws_secretsmanager_secret.app[\"jwt_public_key\"].arn` }}" }
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "{{ tfstate `aws_cloudwatch_log_group.ecs.name` }}",
          "awslogs-region": "{{ must_env `AWS_REGION` }}",
          "awslogs-stream-prefix": "outbox-relay"
        }
      }
    },
    {
      "name": "otel-collector",
      "image": "{{ tfstate `aws_ecr_repository.otel_collector.repository_url` }}:{{ must_env `IMAGE_TAG` }}",
      "essential": true,
      "cpu": 0,
      "memoryReservation": 128,
      "portMappings": [{ "containerPort": 4317, "protocol": "tcp" }],
      "secrets": [
        { "name": "NEW_RELIC_LICENSE_KEY", "valueFrom": "{{ tfstate `aws_secretsmanager_secret.app[\"new_relic_license_key\"].arn` }}" }
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "{{ tfstate `aws_cloudwatch_log_group.ecs.name` }}",
          "awslogs-region": "{{ must_env `AWS_REGION` }}",
          "awslogs-stream-prefix": "outbox-relay-otel"
        }
      }
    }
  ]
}
```

- [ ] **Step 2: order-consumer**

Same shape as Step 1. Change these four fields from the outbox-relay version:
- `family`: `"ec-api-prod-order-consumer"`
- `command`: `["python", "-m", "app.workers.order_consumer"]`
- `awslogs-stream-prefix` on app container: `"order-consumer"`
- `awslogs-stream-prefix` on otel-collector container: `"order-consumer-otel"`

All other lines identical to outbox-relay.

- [ ] **Step 3: checkout-sweeper**

Same shape. Changes:
- `family`: `"ec-api-prod-checkout-sweeper"`
- `command`: `["python", "-m", "app.workers.checkout_sweeper"]`
- `awslogs-stream-prefix` on app: `"checkout-sweeper"`
- `awslogs-stream-prefix` on otel-collector: `"checkout-sweeper-otel"`

- [ ] **Step 4: Verify all 3 files**

```bash
for svc in outbox-relay order-consumer checkout-sweeper; do
  jq empty "infra/ecspresso/$svc/ecs-task-def.json" || echo "FAIL: $svc"
  jq -r '.family' "infra/ecspresso/$svc/ecs-task-def.json"
  jq -r '.containerDefinitions[].name' "infra/ecspresso/$svc/ecs-task-def.json"
done
```

Expected: no FAIL lines. Each prints its family, then "app" and "otel-collector".

- [ ] **Step 5: Confirm NR removed from app, present in collector across all 3**

```bash
for svc in outbox-relay order-consumer checkout-sweeper; do
  echo "=== $svc ==="
  jq '.containerDefinitions[] | {name, secrets: [.secrets[]?.name]}' \
    "infra/ecspresso/$svc/ecs-task-def.json"
done
```

Expected: `app` has 4 secrets (no NR), `otel-collector` has 1 (NR).

- [ ] **Step 6: Commit**

```bash
git add infra/ecspresso/outbox-relay/ecs-task-def.json \
        infra/ecspresso/order-consumer/ecs-task-def.json \
        infra/ecspresso/checkout-sweeper/ecs-task-def.json
git commit -m "infra(ecspresso): worker task defs — add otel-collector sidecar (x3)"
```

---

## Task 8: cd.yml — matrix-ify build-and-push

**Files:**
- Modify: `.github/workflows/cd.yml`

Current `build-and-push` job builds the app image only. Change it to a matrix of `[app, otel-collector]` so both images are built and pushed in parallel from the same git SHA.

- [ ] **Step 1: Read the current `build-and-push` job to understand its structure**

```bash
sed -n '/^  build-and-push:/,/^  migrate:/p' .github/workflows/cd.yml | head -60
```

Note the exact indentation, the existing SHA pins, and the output structure.

- [ ] **Step 2: Replace the `build-and-push:` block**

Replace the entire `build-and-push:` job (from `build-and-push:` line through the empty line before `migrate:`) with:

```yaml
  build-and-push:
    name: build-and-push (${{ matrix.image }})
    # AWS アカウント未プロビジョン (= vars.AWS_ACCOUNT_ID 未設定) の間は skip。
    if: ${{ vars.AWS_ACCOUNT_ID != '' }}
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        include:
          - image: app
            dockerfile: docker/Dockerfile
            context: .
            ecr_repo: ec-api
          - image: otel-collector
            dockerfile: docker/otel-collector/Dockerfile
            context: docker/otel-collector
            ecr_repo: ec-api-otel-collector
    outputs:
      image_tag: ${{ steps.tag.outputs.tag }}
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd  # v6.0.2
        with:
          ref: ${{ env.IMAGE_TAG }}
      - id: tag
        run: |
          SHORT=$(git rev-parse --short=7 HEAD)
          echo "tag=$SHORT" >> "$GITHUB_OUTPUT"
      - uses: aws-actions/configure-aws-credentials@7474bc4690e29a8392af63c5b98e7449536d5c3a  # v4.3.1
        with:
          role-to-assume: arn:aws:iam::${{ vars.AWS_ACCOUNT_ID }}:role/ec-api-prod-gh-deploy
          aws-region: ${{ env.AWS_REGION }}
      - uses: aws-actions/amazon-ecr-login@fa648b43de3d4d023bcb3f89ed6940096949c419  # v2.1.5
        id: ecr
      - uses: docker/setup-buildx-action@8d2750c68a42422c14e847fe6c8ac0403b4cbd6f  # v3.12.0
      - uses: docker/build-push-action@10e90e3645eae34f1e60eeb005ba3a3d33f178e8  # v6.19.2
        with:
          context: ${{ matrix.context }}
          file: ${{ matrix.dockerfile }}
          push: true
          tags: ${{ steps.ecr.outputs.registry }}/${{ matrix.ecr_repo }}:${{ steps.tag.outputs.tag }}
          provenance: false
```

Note: the `file` input on `docker/build-push-action` is the relative path **from the workflow root** (NOT relative to `context`). For the matrix entry `image: otel-collector`, `context: docker/otel-collector` and `file: docker/otel-collector/Dockerfile` together tell Buildx to use that Dockerfile and pass `docker/otel-collector/` as the build context root.

The downstream jobs (`migrate`, `approval`, `deploy-api`, `deploy-workers`) already reference `needs.build-and-push.outputs.image_tag`. Since matrix jobs all produce the same `image_tag` value (same git SHA), GitHub Actions reports the matrix's combined output and downstream consumers see it correctly. No changes to downstream jobs.

- [ ] **Step 3: actionlint**

```bash
actionlint .github/workflows/cd.yml
```

Expected: no errors. (`vars.AWS_ACCOUNT_ID` may be flagged as an unknown variable; that's expected and acceptable.)

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/cd.yml
git commit -m "ci(cd): matrix-ify build-and-push (app + otel-collector)"
```

---

## Task 9: docker-compose + .env.example + Makefile + .gitignore

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Create: `Makefile`
- Modify: `.gitignore`

- [ ] **Step 1: docker-compose.yml — remove secrets volume, add JWT env pass-through**

Apply to **all 4 services** (`api`, `outbox-relay`, `order-event-consumer`, `checkout-sweeper`):

a. Delete the line `volumes: ["./secrets:/run/secrets:ro"]` (or `- ./secrets:/run/secrets:ro` if it's a list element) from each service.

b. For the `api` service, add (or extend) an `environment:` block under it:

```yaml
    environment:
      JWT_PRIVATE_KEY: ${JWT_PRIVATE_KEY:-}
      JWT_PUBLIC_KEY: ${JWT_PUBLIC_KEY:-}
```

c. For each worker (`outbox-relay`, `order-event-consumer`, `checkout-sweeper`), the existing `environment:` block already has `OTEL_SERVICE_NAME: ...`. Append the two JWT lines to that block:

```yaml
    environment:
      OTEL_SERVICE_NAME: ec-outbox-relay   # (or the worker-specific value)
      JWT_PRIVATE_KEY: ${JWT_PRIVATE_KEY:-}
      JWT_PUBLIC_KEY: ${JWT_PUBLIC_KEY:-}
```

Verify with:
```bash
grep -c 'JWT_PRIVATE_KEY' docker-compose.yml   # expect 4 (one per service)
grep -c '/run/secrets' docker-compose.yml       # expect 0
```

- [ ] **Step 2: .env.example — replace JWT lines, add helper comment**

Replace lines 5-6 (`JWT_PRIVATE_KEY_PATH=...` / `JWT_PUBLIC_KEY_PATH=...`) with:

```bash
# JWT keys are PEM content (multi-line). Use `make load-jwt` to populate
# .env.jwt from secrets/jwt_private.pem & secrets/jwt_public.pem, then:
#   docker-compose --env-file .env --env-file .env.jwt up
JWT_PRIVATE_KEY=
JWT_PUBLIC_KEY=
```

- [ ] **Step 3: Create `Makefile` (project root)**

```makefile
.PHONY: load-jwt

load-jwt:
	@printf 'JWT_PRIVATE_KEY="%s"\nJWT_PUBLIC_KEY="%s"\n' \
		"$$(cat secrets/jwt_private.pem)" \
		"$$(cat secrets/jwt_public.pem)" > .env.jwt
	@echo "Wrote .env.jwt. Use: docker-compose --env-file .env --env-file .env.jwt up"
```

- [ ] **Step 4: .gitignore — append `.env.jwt`**

The current `.gitignore` already has `.env` on its own line. Add `.env.jwt` immediately below it (so both ignore entries live together):

```
.env
.env.jwt
```

- [ ] **Step 5: Smoke-check the Makefile syntax**

```bash
make -n load-jwt
```

Expected: prints the printf+echo commands without executing them (`make -n` is dry-run). No errors.

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml .env.example Makefile .gitignore
git commit -m "dev: switch local dev to env-based JWT (Makefile load-jwt helper)"
```

---

## Task 10: README — note cd.yml matrix and dev workflow

**Files:**
- Modify: `infra/terraform/README.md`

The bootstrap section (§1), secret population (§4) are unchanged. Add a note in §5.1 ("Automated") that the build step pushes both images in parallel.

- [ ] **Step 1: Update §5.1 in `infra/terraform/README.md`**

Find this block (currently around §5.1):
```markdown
### 5.1 Automated (default — via `cd.yml`)

Push or merge to `main` (excluding pure docs/infra-only changes) triggers `cd.yml`:

1. **build-and-push**: builds the docker image, tags with the short git SHA, pushes to ECR
```

Change item 1 to:
```markdown
1. **build-and-push** (matrix: `app`, `otel-collector`): builds both docker images in parallel from the same short git SHA, pushes each to its dedicated ECR repo (`ec-api`, `ec-api-otel-collector`)
```

- [ ] **Step 2: Add a new bullet point or note about the otel-collector sidecar in §5.1 or §8 (Known boundaries)**

Append to the §8 "Known boundaries" table:

```markdown
| OTel sidecar | Each ECS task includes an `otel-collector` sidecar (port 4317 internal) that forwards OTLP traffic to New Relic via `otlphttp/newrelic` exporter. `NEW_RELIC_LICENSE_KEY` is read only by the sidecar container (not the app). | Centralized collector (single ECS service via Service Connect) for resource efficiency |
```

- [ ] **Step 3: Validate markdown sanity (balanced fences)**

```bash
grep -c '^```' infra/terraform/README.md
```

Expected: even number.

- [ ] **Step 4: Commit**

```bash
git add infra/terraform/README.md
git commit -m "infra(tf): README — note cd.yml matrix build + otel-collector sidecar"
```

---

## Task 11: Verify + push + PR

**Files:** (none — verification only)

- [ ] **Step 1: All-files static checks**

```bash
cd /Users/takuma/cross/ec/.worktrees/aws-infra-c1c

# Terraform
/Users/takuma/.local/bin/mise exec -- terraform -chdir=infra/terraform fmt -check -recursive
/Users/takuma/.local/bin/mise exec -- terraform -chdir=infra/terraform validate

# ecspresso JSON
find infra/ecspresso -name '*.json' -exec jq empty {} \;

# workflows
actionlint .github/workflows/*.yml

# app
uv run ruff check
uv run mypy app
uv run pytest -m "not slow" -q
```

Expected: every command exits 0; `pytest` reports all green.

- [ ] **Step 2: Inspect commit history**

```bash
git log --oneline feature/aws-infra-c1c ^origin/main
```

Expected: ~10 commits (spec + plan + 8 implementation commits — Tasks 1–3 are one commit; Task 4 one; Task 5 one; Task 6 one; Task 7 one; Task 8 one; Task 9 one; Task 10 one).

- [ ] **Step 3: Confirm no `JWT_*_PATH` remains anywhere**

```bash
grep -rn "JWT_PRIVATE_KEY_PATH\|JWT_PUBLIC_KEY_PATH\|jwt_private_key_path\|jwt_public_key_path" \
  --include="*.py" --include="*.json" --include="*.yml" --include="*.yaml" --include="*.md" --include="*.example"
```

Expected: no output across the entire repo (or only matches inside historical commit messages, which is fine).

- [ ] **Step 4: Push**

```bash
git push -u origin feature/aws-infra-c1c
```

If the sandbox blocks `git push`, ask the user to run it from their shell:
> `! git -C /Users/takuma/cross/ec/.worktrees/aws-infra-c1c push -u origin feature/aws-infra-c1c`

- [ ] **Step 5: Open PR**

```bash
gh pr create --base main --head feature/aws-infra-c1c \
  --title "C-1c: production config (JWT env / OTel sidecar / NR)" \
  --body "$(cat <<'EOF'
## Summary

C-1a/C-1b で揃えた本番インフラと CD を「実際に動く」状態にする 3 件の整合修正:

1. **JWT key 注入** — App が file path 経由ではなく **env 経由の PEM 文字列** で読むよう変更 (破壊的変更: `JWT_PRIVATE_KEY_PATH` / `JWT_PUBLIC_KEY_PATH` 廃止)
2. **otel-collector sidecar** を全 4 ECS task (api + 3 worker) に追加。app は `localhost:4317` へ OTLP 送信、sidecar が NR の OTLP/HTTPS endpoint へ forward
3. **collector image** を専用 ECR (`ec-api-otel-collector`) で管理、`cd.yml` の build を matrix 化して app と同じ git short SHA で同期 push

実 AWS apply は本 PR では実施しない (operator runbook で実施)。

### 追加・変更

**App:**
- `app/core/config.py`: `jwt_*_path: Path` → `jwt_*_key: str`
- `app/modules/auth/{dependencies,router}.py`: `.read_text()` 削除
- テスト fixture: PEM 文字列を直接 env へ

**Infra:**
- `infra/terraform/ecr.tf`: `aws_ecr_repository.otel_collector` + lifecycle 追加
- `infra/terraform/outputs.tf`: `otel_collector_ecr_repository_url` 追加
- `infra/ecspresso/*/ecs-task-def.json` x4: sidecar container 追加、app から NR secret を collector container へ移動

**CI / CD:**
- `.github/workflows/cd.yml`: `build-and-push` を matrix 化 (`app`, `otel-collector`)

**Dev:**
- `docker/otel-collector/Dockerfile`: 公式 image + config 同梱
- `docker-compose.yml`: secrets volume 削除、JWT env pass-through
- `.env.example` / `Makefile` (load-jwt) / `.gitignore` (.env.jwt)

**Docs:**
- `infra/terraform/README.md` §5.1, §8 更新

## Test plan

- [x] `terraform fmt -check` / `terraform validate` clean
- [x] `find infra/ecspresso -name '*.json' -exec jq empty {} \;` ok
- [x] `actionlint .github/workflows/*.yml` clean
- [x] `uv run ruff check` All checks passed
- [x] `uv run mypy app` clean
- [x] `uv run pytest -m "not slow"` all green
- [x] `docker build docker/otel-collector/` succeeds (ローカル)
- [ ] CI all green (`plan` / `apply` / `build-and-push` は `vars.AWS_ACCOUNT_ID` 未設定で skip)
- [ ] Operator が runbook 通り `terraform.yml` apply 後、`cd.yml` を回して B/G abort が発火しないことを確認 (post-merge)

## Spec / Plan

- spec: `docs/superpowers/specs/2026-05-21-aws-infra-c1c-design.md`
- plan: `docs/superpowers/plans/2026-05-21-aws-infra-c1c.md`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

If `gh pr create` is blocked by the sandbox, ask the user to run the same command manually.

---

## Self-Review Checklist (run by plan author before handoff)

**Spec coverage:**
- §3 主要決定: JWT env (T1–3), sidecar (T6–7), custom image (T4), shared ECR repo (T5), matrix CD (T8), all 4 services (T6–7), breaking change (T1) — all covered
- §5 ファイル変更マップ: every file listed has a corresponding task — verified
- §6.1 App コード: T1, T2, T3
- §6.2 Collector image: T4
- §6.3 Terraform: T5
- §6.4 ecspresso task def: T6 + T7
- §6.5 cd.yml matrix: T8
- §6.6 開発環境: T9
- §6.7 README: T10
- §7 Failure modes: documented in spec; runtime — not tested in PR
- §8 Migration: T10 README + post-merge operator steps
- §9 DoD: T11 covers all checks

**Placeholder scan:**
- No "TBD" / "TODO" / "implement later"
- Every code block has actual content
- Every SHA reference is a concrete commit hash (reused from C-1b cd.yml)

**Type / naming consistency:**
- `aws_ecr_repository.otel_collector` defined in T5, referenced in T6, T7 (tfstate plugin)
- `JWT_PRIVATE_KEY` / `JWT_PUBLIC_KEY` env names consistent across T1 (Settings), T3 (tests), T6/T7 (ecspresso secrets), T9 (docker-compose, .env.example, Makefile)
- `awslogs-stream-prefix` is unique per task and matches existing naming convention
- `image_tag` output name in cd.yml unchanged

No issues. Plan ready for implementation.
