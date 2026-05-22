# C-1c: 本番設定整合 (JWT env / OTel / NR sidecar)

**Date:** 2026-05-21
**Status:** Approved (designing)
**Predecessors:** `2026-05-19-aws-infra-c1a-design.md`, `2026-05-20-aws-infra-c1b-design.md`
**Successors:** (none — C-1 umbrella の最終サブタスク)

## 1. 目的

C-1a/C-1b で揃えた本番インフラと CD を「実際に動く」状態にするため、3 つの未整合を解消する:

1. **JWT key 注入の不整合** — task def は Secrets Manager から `JWT_PRIVATE_KEY` を env 文字列で注入するが、app は `JWT_PRIVATE_KEY_PATH` (file path) から読む設計になっており起動失敗する
2. **OTel endpoint の未配備** — app は `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317` を想定するが、本番には otel-collector が存在せず全 telemetry 呼び出しが connection refused
3. **NR への送信経路の欠如** — `NEW_RELIC_LICENSE_KEY` は app container に env 注入されているが、app 自身は NR と通信する手段を持たない (docker-compose では collector 経由で送信していた)

## 2. テスタビリティの制約

C-1a/C-1b と同じ。実 AWS への apply 不可:
- App コード変更は `uv run pytest -m "not slow"` でローカル検証
- Collector image は `docker build` でローカル build 検証 (push は CD 任せ)
- Terraform は `terraform fmt -check` / `validate` / `tflint`
- ecspresso JSON は `jq empty`
- Workflow は `actionlint`

実 ECS 上での挙動確認は operator の merge 後手順 (§7) で行う。

## 3. 主要決定 (Approval 済み)

| 項目 | 決定 |
|---|---|
| C-1c のスコープ | JWT + OTel/NR を 1 PR に同梱 (C-1d への分割はしない) |
| JWT key 受け渡し | env に PEM 文字列を直接渡す (`jwt_private_key: str`、file 経由なし) |
| OTel→NR 経路 | ECS task の sidecar として otel-collector を同梱 (各 task 内 `localhost:4317`) |
| Collector config 配布 | カスタム image にバンドル (`docker/otel-collector/Dockerfile`) |
| Sidecar 適用範囲 | api + 3 worker = 全 4 サービスに付与 |
| Collector image の CD | 専用 ECR repo (`ec-api-otel-collector`)、`cd.yml` の build-and-push を matrix 化、app と同じ git short SHA で同期 tag |
| 互換性 | 破壊的変更 — `JWT_PRIVATE_KEY_PATH` 受付は廃止 (YAGNI) |

## 4. アーキテクチャ

```
ECS Task (api / outbox-relay / order-consumer / checkout-sweeper × 4 種)
┌────────────────────────────────────────────────────────────────────┐
│ Container 1: app                                                    │
│   image:    {{ tfstate `aws_ecr_repository.app.repository_url`      │
│             }}:{{ must_env `IMAGE_TAG` }}                            │
│   env:      OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317       │
│   secrets:  DATABASE_URL, RABBITMQ_URL,                              │
│             JWT_PRIVATE_KEY, JWT_PUBLIC_KEY                          │
│                                                                     │
│ Container 2: otel-collector  (sidecar — 新規)                        │
│   image:    {{ tfstate `aws_ecr_repository.otel_collector            │
│             .repository_url` }}:{{ must_env `IMAGE_TAG` }}           │
│   secrets:  NEW_RELIC_LICENSE_KEY                                   │
│   config:   /etc/otelcol/config.yaml (image 同梱、NR exporter)       │
│   port:     4317/tcp (otlp grpc receiver、localhost で app から受信) │
└────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼ (otlphttp/newrelic exporter)
                  https://otlp.nr-data.net (api-key: NR_LICENSE)
```

主要原則:

- **JWT は env-based PEM 文字列** — Secrets Manager → task def `secrets[]` → container env
- **otel-collector は sidecar として 4 task 全部に同梱** — `localhost:4317` 一閃で全サービス共通
- **collector image は custom build** — config 同梱、cd.yml で git short SHA で並列 build/push
- **NR 経由は collector のみ** — app は NR の license key を一切持たない (collector container だけが持つ)、責務分離

## 5. ファイル変更マップ

| File | Action | 役割 |
|---|---|---|
| `app/core/config.py` | Modify | `jwt_private_key_path: Path` → `jwt_private_key: str`、同 public |
| `app/modules/auth/dependencies.py` | Modify | `s.jwt_public_key_path.read_text()` → `s.jwt_public_key` |
| `app/modules/auth/router.py` | Modify | 同様、`*_path.read_text()` → 直接 `*_key` |
| `tests/**/conftest.py`, `tests/modules/auth/test_*.py` | Modify | fixture が file path → PEM 文字列を返すように |
| `tests/core/test_config.py` | Create | Settings 読込テスト (新 env 必須、不在時 ValidationError) |
| `docker/otel-collector/Dockerfile` | Create | 公式 image + `COPY config.yaml /etc/otelcol/config.yaml` |
| `docker/otel-collector/config.yaml` | (既存維持) | 既に docker-compose で使用中。custom image でも同ファイルを使う |
| `infra/terraform/ecr.tf` | Modify | `aws_ecr_repository.otel_collector` + lifecycle policy を append |
| `infra/terraform/outputs.tf` | Modify | `otel_collector_ecr_repository_url` output 追加 |
| `infra/ecspresso/api/ecs-task-def.json` | Modify | sidecar container 追加、`app` から `NEW_RELIC_LICENSE_KEY` 削除 |
| `infra/ecspresso/outbox-relay/ecs-task-def.json` | Modify | 同上 + cpu/memory 引き上げ |
| `infra/ecspresso/order-consumer/ecs-task-def.json` | Modify | 同上 |
| `infra/ecspresso/checkout-sweeper/ecs-task-def.json` | Modify | 同上 |
| `.github/workflows/cd.yml` | Modify | `build-and-push` を matrix 化 (app + collector) |
| `docker-compose.yml` | Modify | `./secrets:/run/secrets:ro` 削除、`environment.JWT_*` を env pass-through に |
| `.env.example` | Modify | `JWT_*_PATH` → `JWT_PRIVATE_KEY` / `JWT_PUBLIC_KEY` (空 placeholder、helper への参照) |
| `Makefile` | Create or Modify | `load-jwt` target を追加 (file → `.env.jwt` 変換ヘルパー) |
| `.gitignore` | Modify | `.env.jwt` を追記 |
| `infra/terraform/README.md` | Modify | bootstrap / secrets セクションを C-1c 反映 |

## 6. 詳細

### 6.1 App コード変更

**`app/core/config.py`**

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
    return Settings()  # type: ignore[call-arg]
```

`from pathlib import Path` の import は削除。

**`app/modules/auth/dependencies.py:26`**

```python
# Before
pub = settings.jwt_public_key_path.read_text()
# After
pub = settings.jwt_public_key
```

**`app/modules/auth/router.py:24-25`**

```python
# Before
jwt_private_key=s.jwt_private_key_path.read_text(),
jwt_public_key=s.jwt_public_key_path.read_text(),
# After
jwt_private_key=s.jwt_private_key,
jwt_public_key=s.jwt_public_key,
```

**テスト fixture**

実装時に `grep -rn "jwt_private_key_path\|JWT_PRIVATE_KEY_PATH" tests/` で具体的な箇所を特定し、fixture が PEM 文字列を返すように修正。

### 6.2 Collector image

**`docker/otel-collector/Dockerfile`** (新規)

```dockerfile
FROM otel/opentelemetry-collector-contrib:0.111.0

COPY config.yaml /etc/otelcol/config.yaml

CMD ["--config=/etc/otelcol/config.yaml"]
```

**`docker/otel-collector/config.yaml`** は既存ファイル維持 (docker-compose で使われている内容)。Dockerfile が同ディレクトリの config を COPY するので追加変更不要。

### 6.3 Terraform

**`infra/terraform/ecr.tf`** に append:

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

**`infra/terraform/outputs.tf`** に append:

```hcl
output "otel_collector_ecr_repository_url" {
  value = aws_ecr_repository.otel_collector.repository_url
}
```

> `iam.tf` の `EcrPush` statement は既に `resources = ["*"]` なので新 repo にも自動適用。変更不要。

### 6.4 ecspresso task def 修正

全 4 つの `ecs-task-def.json` に対して同じ pattern:

#### (a) `app` container の `secrets[]` から `NEW_RELIC_LICENSE_KEY` を削除

#### (b) `containerDefinitions[]` 末尾に sidecar container を追加

```jsonc
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
```

#### (c) Worker 3 個の task サイズ引き上げ

worker 3 個 (outbox-relay / order-consumer / checkout-sweeper) の `cpu` / `memory` が `256` / `512` の場合、sidecar 用に `512` / `1024` に引き上げる。api は既に `512` / `1024` なので据え置きで `memoryReservation: 128` の sidecar に十分。

実装時に現在値を確認し、不足ならこのスコープ内で引き上げる。

### 6.5 `cd.yml` の `build-and-push` を matrix 化

C-1b で既に SHA-pin 済みの action (`actions/checkout` / `configure-aws-credentials` / `amazon-ecr-login` / `setup-buildx-action` / `build-push-action`) を再利用する。新規 action 追加なし — pinned SHA は現在の `cd.yml` から流用。

変更後の `build-and-push` job 骨子 (action 行の `@<sha>` は既存 cd.yml の SHA を流用):

```yaml
jobs:
  build-and-push:
    name: build-and-push (${{ matrix.image }})
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
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd  # v6.0.2 (existing pin)
        with:
          ref: ${{ env.IMAGE_TAG }}
      - id: tag
        run: |
          SHORT=$(git rev-parse --short=7 HEAD)
          echo "tag=$SHORT" >> "$GITHUB_OUTPUT"
      - uses: aws-actions/configure-aws-credentials@7474bc4690e29a8392af63c5b98e7449536d5c3a  # v4.3.1 (existing pin)
        with:
          role-to-assume: arn:aws:iam::${{ vars.AWS_ACCOUNT_ID }}:role/ec-api-prod-gh-deploy
          aws-region: ${{ env.AWS_REGION }}
      - uses: aws-actions/amazon-ecr-login@fa648b43de3d4d023bcb3f89ed6940096949c419  # v2.1.5 (existing pin)
        id: ecr
      - uses: docker/setup-buildx-action@8d2750c68a42422c14e847fe6c8ac0403b4cbd6f  # v3.12.0 (existing pin)
      - uses: docker/build-push-action@10e90e3645eae34f1e60eeb005ba3a3d33f178e8  # v6.19.2 (existing pin)
        with:
          context: ${{ matrix.context }}
          file: ${{ matrix.dockerfile }}
          push: true
          tags: ${{ steps.ecr.outputs.registry }}/${{ matrix.ecr_repo }}:${{ steps.tag.outputs.tag }}
          provenance: false
```

> Matrix の各 job で同じ short SHA を計算する (両方が main HEAD を checkout するため `git rev-parse --short=7 HEAD` は同値)。`outputs.image_tag` は matrix 内の複数 job が同じ output を上書きするが値が同じなので問題なし。下流 (migrate / approval / deploy-api / deploy-workers) は変更不要。

下流の `migrate` / `deploy-api` / `deploy-workers` は `needs: [..., build-and-push]` のままで OK。

### 6.6 開発環境

**`docker-compose.yml`** (4 service すべて):

```yaml
api:
  build: ...
  env_file: .env
  environment:
    JWT_PRIVATE_KEY: ${JWT_PRIVATE_KEY:-}
    JWT_PUBLIC_KEY: ${JWT_PUBLIC_KEY:-}
  depends_on: ...
  # volumes: ["./secrets:/run/secrets:ro"]   ← 削除
```

worker 3 個も同様に `environment.JWT_*` 追加 + `volumes` 削除。

**`.env.example`**

```bash
APP_ENV=local
DATABASE_URL=postgresql+asyncpg://ec:ec@postgres:5432/ec
RABBITMQ_URL=amqp://guest:guest@rabbitmq:5672/
JWT_ALGORITHM=RS256
# JWT keys are PEM content (multi-line). Use `make load-jwt` to populate
# .env.jwt from secrets/jwt_private.pem & secrets/jwt_public.pem, then:
#   docker-compose --env-file .env --env-file .env.jwt up
JWT_PRIVATE_KEY=
JWT_PUBLIC_KEY=
JWT_ACCESS_TTL_MIN=15
JWT_REFRESH_TTL_DAYS=14
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
OTEL_EXPORTER_OTLP_PROTOCOL=grpc
OTEL_SERVICE_NAME=ec-api
OTEL_RESOURCE_ATTRIBUTES=service.namespace=ec,deployment.environment=local
NEW_RELIC_LICENSE_KEY=replace-me
CHECKOUT_SWEEP_INTERVAL_SEC=300
CHECKOUT_TIMEOUT_HOURS=24
MAX_CONSUMER_RETRIES=5
```

**`Makefile`** に追加 (or 新規):

```makefile
.PHONY: load-jwt
load-jwt:
	@printf 'JWT_PRIVATE_KEY="%s"\nJWT_PUBLIC_KEY="%s"\n' \
		"$$(cat secrets/jwt_private.pem)" \
		"$$(cat secrets/jwt_public.pem)" > .env.jwt
	@echo "Wrote .env.jwt. Use: docker-compose --env-file .env --env-file .env.jwt up"
```

**`.gitignore`** に追加: `.env.jwt`

### 6.7 README runbook 更新

`infra/terraform/README.md` の §4 "Populate secrets" は変更不要 (Secrets Manager の key 名は同じ、値も同じ)。

§5 (Deploys) に補足を追加: cd.yml が **app と collector を並列 build/push する** ことを明記。初回 deploy 時の挙動 (B/G 起動時に両 container image が必要、片方が ECR に無いと task launch 失敗) を runbook §5.3 に補足。

## 7. Failure modes

| シナリオ | 検知 | 挙動 | リカバリ |
|---|---|---|---|
| app 起動時 `JWT_PRIVATE_KEY` env 不在 | container exit (ValidationError) | ECS B/G が立ち上げ失敗を検知 → alarm 発火 → 自動 abort | Secrets Manager に値が入っているか確認、再 deploy |
| collector 起動時 `NEW_RELIC_LICENSE_KEY` env 不在 | collector container は起動するが OTLP export が 401 で失敗、ログにエラー | app 本体は影響なし、NR には届かないだけ → B/G abort 条件は 5xx / unhealthy なのでこの状態では発火しない | logs を見て NR key を確認 |
| collector image が ECR に未 push | task launch 時に `CannotPullContainerError` | ECS deployment circuit breaker / B/G abort | cd.yml が collector も push するので通常発生しない |
| NR への OTLP/HTTPS 接続失敗 (一過性) | collector ログに retry 警告 | batch processor がバッファリング → backpressure → 古い span は drop | NR ステータス確認 |
| ローカル dev で `make load-jwt` 実行忘れ | `docker-compose up` 時に app container が ValidationError で exit | `.env.jwt` を生成して再起動 | README に明記 |

## 8. Migration (operator が PR merge 後に踏む手順)

1. PR を main へ merge
2. `terraform.yml` auto-trigger → plan で `aws_ecr_repository.otel_collector` 追加 → approval → apply
3. `cd.yml` auto-trigger → build matrix (app + collector 並列) → migrate → approval → deploy-api (B/G) + deploy-workers (rolling)
4. New Relic dashboard で trace/metric/logs 到達確認
5. B/G abort が発火しないことを確認 (app の起動 / sidecar の起動とも成功している)

## 9. 完了条件 (Definition of Done)

- [ ] App コード: `jwt_private_key: str` / `jwt_public_key: str` に置換、`Path` import 削除
- [ ] `dependencies.py` / `router.py` の `*_path.read_text()` 全削除
- [ ] テスト fixture 修正、`uv run pytest -m "not slow"` 全 green
- [ ] `tests/core/test_config.py` で新 Settings の validation を確認
- [ ] `docker/otel-collector/Dockerfile` 作成、`docker build` で成功 (ローカル検証)
- [ ] `infra/terraform/ecr.tf` に `aws_ecr_repository.otel_collector` + lifecycle、`outputs.tf` に URL 追加
- [ ] 全 4 個の `ecs-task-def.json` に sidecar 追加、`app` から `NEW_RELIC_LICENSE_KEY` 削除、worker は cpu/memory 必要分引き上げ
- [ ] `cd.yml` の `build-and-push` を matrix 化
- [ ] `docker-compose.yml` の secrets volume 削除、`environment.JWT_*` 追加
- [ ] `.env.example` 更新、`Makefile` に `load-jwt` target、`.gitignore` に `.env.jwt`
- [ ] `infra/terraform/README.md` §5 に補足
- [ ] `terraform fmt -check` / `terraform validate` / `jq empty` / `actionlint` / `ruff` / `pytest` 全 pass
- [ ] live AWS への apply は本 PR では実施しない (operator runbook 化)

## 10. オープン項目 (follow-up)

- Centralized collector への移行 (専用 ECS service + Service Connect) — リソース効率と運用分離
- Auto-instrumentation 拡張 (現状 FastAPI / SQLAlchemy / asyncpg / aio_pika / httpx / logging のみ)
- Collector の `prometheusremotewrite` exporter 経由で別宛先にも分岐
- PII redaction processor 追加
- TLS / Route53 / ACM (既存 follow-up)
- staging 環境分離 (既存 follow-up)
- `python-jose` PYSEC-2025-185 対応 (task #121 として別追跡)

## 11. 非ゴール

- 実 AWS provisioning (operator runbook 化)
- `JWT_PRIVATE_KEY_PATH` への backwards-compat (YAGNI、破壊的変更で OK)
- 別の telemetry バックエンド (Datadog / Honeycomb / etc) への対応
- collector の高可用性チューニング (sender_pool / retry_on_failure の詳細)
