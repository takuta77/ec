# EC API Server — Design Spec

- **Date:** 2026-05-12
- **Status:** Draft (pending review)
- **Owner:** t-tanimiya@r-up.jp
- **Scope:** 本体側 EC API サーバ（カタログ・ユーザ・認証・カート）と、Checkout アプリ（別アプリ: Order/Payment/Stock）との連携基盤
- **Out of scope:** Checkout 側アプリの実装、フロントエンド、本番インフラ構築（K8s マニフェスト等）

## 1. 目的と背景

EC ドメインの「商品閲覧・ユーザ管理・認証・カート保持」までを担う API サーバを構築する。注文確定以降（在庫引当・決済・注文確定）は別の **Checkout アプリ** が担当し、本体は RabbitMQ 経由でメッセージを送受信する。観測性は OpenTelemetry（Traces/Metrics/Logs）で統一し、OTLP Collector 経由で New Relic に転送する。

設計原則:

- `modules/` 配下は機能ドメイン単位、各 module 内は技術的責務（router / schemas / models / repository / service）で分割。
- 依存方向は **router → service → repository → models/db** 単方向。
- `service` は FastAPI の Request/Response に依存しない。`repository` は HTTP に依存しない。
- 共通化は `shared/` に閉じる。複数 module で本当に必要なもののみ。
- module 間の依存は最小限。循環 import を避ける。

## 2. 技術スタック

| 領域 | 採用 |
|---|---|
| 言語 / ランタイム | Python 3.12 |
| パッケージ管理 | uv (pyproject.toml + uv.lock) |
| Web フレームワーク | FastAPI |
| ASGI サーバ | uvicorn |
| DB | PostgreSQL 16 |
| ORM | SQLAlchemy 2.0 (async) + asyncpg |
| マイグレーション | Alembic |
| MQ | RabbitMQ 3.13 (aio-pika) |
| 認証 | JWT (RS256, Access + Refresh) / passlib (bcrypt) |
| 観測性 | OpenTelemetry (Traces/Metrics/Logs) → otel-collector → New Relic |
| テスト | pytest, pytest-asyncio, httpx, Testcontainers |
| 静的解析 | ruff, mypy |
| コンテナ | Docker / docker compose |

## 3. アーキテクチャ概要

本体は 3 プロセスに分離。すべて単一の `app/` パッケージから起動し、エントリポイント（`command:`）のみ異なる。

```
                    ┌──────────────────────────┐
       HTTP ─────►  │  api (FastAPI)           │ ──► PostgreSQL (writes incl. outbox)
                    │  uvicorn app.main:app    │ ──► (reads) PostgreSQL
                    └──────────────────────────┘

                    ┌──────────────────────────┐         RabbitMQ
                    │  outbox-relay            │ ─────►  exchange: ec.events
                    │  app.workers.outbox_relay│         routing: checkout.requested
                    └──────────────────────────┘
                              ▲ poll
                    PostgreSQL.outbox_events

                    ┌──────────────────────────┐    queue: ec.api.order-events
   from Checkout ►  │  order-event-consumer    │ ◄──── routing: order.created / order.failed
                    │  app.workers.order_consumer │   retry: ec.events.retry (TTL backoff)
                    └──────────────────────────┘
                              │
                              ▼
                      PostgreSQL (carts を更新)

                    ┌──────────────────────────┐
                    │  checkout-sweeper        │ ──► PostgreSQL (submitted > 24h → failed/timeout)
                    │  app.workers.checkout_sweeper │
                    └──────────────────────────┘

   全プロセスの Traces/Metrics/Logs ──► OTLP ──► otel-collector ──► New Relic
```

### 設計上の決定

- **A-1: Transactional Outbox** — DB トランザクション内で `outbox_events` テーブルに書き、別プロセスが publish。at-least-once 保証 + DB と MQ の二重書き失敗の排除。
- **B-1: 専用 consumer ワーカー** — `api` とは別プロセスで返信を購読。API のスケールと consumer のスケールを独立させる。
- **C-1: W3C TraceContext 伝播** — `traceparent` を AMQP headers に注入し、publish → consume → 返信 → consume を 1 本のトレースに繋ぐ。`opentelemetry-instrumentation-aio-pika` を利用。

## 4. ディレクトリ構成

```
ec/
├── pyproject.toml
├── uv.lock
├── README.md
├── .env.example
├── docker/
│   ├── Dockerfile
│   └── otel-collector/
│       └── config.yaml
├── docker-compose.yml
├── alembic.ini
├── migrations/
│   ├── env.py
│   └── versions/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app factory, ルータ登録, OTel 初期化
│   ├── core/
│   │   ├── config.py        # pydantic-settings
│   │   ├── security.py      # JWT, パスワードハッシュ
│   │   ├── exceptions.py    # AppError 階層 + handlers
│   │   ├── logging.py       # structlog + OTel LoggingHandler
│   │   └── telemetry.py     # Tracer/Meter/Logger provider 初期化
│   ├── db/
│   │   ├── session.py       # AsyncSession factory, get_session 依存
│   │   └── base.py          # DeclarativeBase + metadata
│   ├── mq/
│   │   ├── connection.py    # aio-pika 接続/チャネル
│   │   ├── publisher.py     # outbox 行を publish
│   │   └── consumer.py      # subscribe & dispatch 基盤
│   ├── modules/
│   │   ├── users/           # router, schemas, models, repository, service
│   │   ├── items/           # router, schemas, models, repository, service
│   │   ├── auth/            # router, schemas, models, repository, service, dependencies
│   │   ├── carts/           # router, schemas, models, repository, service
│   │   └── outbox/          # models, repository, service (HTTP なし)
│   ├── workers/
│   │   ├── outbox_relay.py    # entrypoint
│   │   ├── order_consumer.py  # entrypoint
│   │   └── checkout_sweeper.py# entrypoint (submitted > 24h を failed/timeout に)
│   └── shared/
│       ├── pagination.py
│       ├── responses.py
│       └── utils.py
└── tests/
    ├── conftest.py          # Testcontainers fixtures
    ├── modules/
    │   ├── users/
    │   ├── items/
    │   ├── auth/
    │   └── carts/
    ├── workers/
    │   ├── test_outbox_relay.py
    │   ├── test_order_consumer.py
    │   └── test_checkout_sweeper.py
    └── contracts/           # JSON Schema for events
```

### ファイル責務（再掲）

- `router.py`: FastAPI のエンドポイント定義のみ。`service` を呼び、`schemas` で I/O する。
- `schemas.py`: Pydantic のリクエスト/レスポンス DTO。
- `models.py`: SQLAlchemy ORM モデル。`db.base.Base` を継承。
- `repository.py`: DB アクセス。`AsyncSession` を受け取り、`Result`/`models.*` を返す。HTTP 依存禁止。
- `service.py`: ビジネスロジック。`Request`/`Response` 依存禁止。`repository` と他 module の `service` のみに依存。
- `dependencies.py` (auth のみ): `get_current_user` などの FastAPI 依存を集約。

## 5. データモデル

### テーブル定義

| テーブル | カラム |
|---|---|
| `users` | id (uuid PK), email (unique), hashed_password, is_active (bool), created_at, updated_at |
| `refresh_tokens` | id (uuid PK), user_id FK, token_hash (unique), expires_at, revoked_at |
| `items` | id (uuid PK), name, description, price_cents (int), currency (char3), is_active, created_at, updated_at |
| `carts` | id (uuid PK), user_id FK, status enum('open','submitted','ordered','failed'), checkout_request_id (uuid nullable), order_id (uuid nullable), submitted_at (nullable), failure_reason text nullable, created_at, updated_at — **partial unique index** on (user_id) where status='open' |
| `cart_items` | id (uuid PK), cart_id FK, item_id FK, quantity (int), unit_price_cents (int), UNIQUE(cart_id, item_id) |
| `outbox_events` | id (uuid PK), aggregate_type, aggregate_id, event_type, payload (jsonb), headers (jsonb), created_at, published_at (nullable), attempts (int default 0), dead_letter_at (nullable) |
| `processed_events` | event_id (uuid PK), event_type, processed_at |

メモ:

- `cart_items.unit_price_cents` は注文確定スナップショット。送信後に価格変動しても整合性を保つ。
- `carts.status` は本体側ローカル状態の state machine。遷移: `open → submitted → (ordered | failed)`。ターミナル状態（`ordered`/`failed`）からの再遷移は不可。
- `outbox_events` は publish 待ちキュー。`published_at IS NULL AND dead_letter_at IS NULL` を `FOR UPDATE SKIP LOCKED` でバッチ取得。
- `processed_events` は consumer の冪等性キー。最低 30 日保持。

### イベント Envelope

全メッセージ共通:

```json
{
  "event_id": "uuid",
  "event_type": "checkout.requested | order.created | order.failed",
  "occurred_at": "2026-05-12T03:21:00Z",
  "trace_id": "...",
  "span_id": "...",
  "data": { ... }
}
```

`traceparent` は AMQP headers にも入れる（自動計装で連結される）。

#### 本体 → Checkout: `checkout.requested`

```json
{
  "checkout_request_id": "uuid",
  "user_id": "uuid",
  "cart_id": "uuid",
  "items": [
    {"item_id": "uuid", "name": "...", "quantity": 2, "unit_price_cents": 1980, "currency": "JPY"}
  ],
  "total_cents": 3960,
  "currency": "JPY"
}
```

#### Checkout → 本体: `order.created`

```json
{
  "checkout_request_id": "uuid",
  "order_id": "uuid",
  "status": "confirmed",
  "confirmed_at": "2026-05-12T03:21:05Z"
}
```

#### Checkout → 本体: `order.failed`

```json
{
  "checkout_request_id": "uuid",
  "reason": "out_of_stock | payment_declined | ...",
  "details": { "...": "..." }
}
```

### RabbitMQ トポロジ

- exchange `ec.events` (topic, durable)
- 本体 publish: routing key `checkout.requested`
- 本体 consume: queue `ec.api.order-events` を `order.created` / `order.failed` にバインド (durable)
- **retry exchange** `ec.events.retry` — `ec.api.order-events` の retry 用 TTL キュー群を保持（バックオフ段階別: 1s/5s/30s/2m/10m）。各 retry キューは TTL 経過後に DLX 経由で `ec.events` に戻り `ec.api.order-events` で再受信される。
- DLX `ec.events.dlx` を共通で持つ。`MAX_CONSUMER_RETRIES` 超過、または non-retryable と判定されたメッセージは `ec.api.order-events.dlq` へ送られる。
- attempts は `x-death` ヘッダで参照（アプリ側に独自カウンタを置かない）。

## 6. フロー

### 6.1 カート → 注文確定 (publish 側)

```
POST /carts/{id}/checkout
  ▼
router.checkout(cart_id, user=Depends(get_current_user))
  ▼
cart_service.submit_checkout(cart_id, user):
  async with session.begin():
      cart = cart_repo.get_open_for_update(cart_id, user.id)   # SELECT ... FOR UPDATE
      items = cart_item_repo.list_with_item(cart_id)
      total = sum(i.quantity * i.unit_price_cents for i in items)
      cart.status = 'submitted'
      cart.checkout_request_id = uuid4()
      cart.submitted_at = now()
      outbox_repo.append(
          event_type='checkout.requested',
          aggregate_type='cart',
          aggregate_id=cart.id,
          payload=...,
          headers={'traceparent': get_current_traceparent()},
      )
  return 202 Accepted { checkout_request_id }
```

### 6.2 outbox-relay

```
loop forever:
  rows = outbox_repo.fetch_unpublished(limit=100)   # FOR UPDATE SKIP LOCKED
  for row in rows:
      try:
          publisher.publish('ec.events', row.event_type, envelope(row))
          outbox_repo.mark_published(row.id)
      except Exception:
          outbox_repo.bump_attempts(row.id)
          if row.attempts + 1 >= MAX_ATTEMPTS:
              outbox_repo.mark_dead_letter(row.id)
  await asyncio.sleep(0.2 if rows else 1.0)
```

publish と `mark_published` は **同一トランザクション内で完結しない**（MQ と DB は別系）。重複は consumer 側で吸収する設計とする。

### 6.3 order-event-consumer (冪等性 3 層)

at-least-once 配送下で effectively-once に処理するため、以下 3 層で防御する。

**1. メッセージ ID による重複排除 (技術層)**

ハンドラ内の業務 DB 更新と `processed_events.insert` を **同一 PostgreSQL トランザクション** で実行する。

```
async def handle(msg):
    envelope = parse(msg.body)
    async with session.begin():
        ok = await processed_events_repo.try_insert(envelope.event_id, envelope.event_type)
        if not ok:
            await msg.ack(); return
        await cart_service.apply_order_result(envelope.event_type, envelope.data)
    await msg.ack()
```

`processed_events.event_id` は PK 制約 + `INSERT ... ON CONFLICT DO NOTHING`。`RETURNING` の有無で「自分が最初の処理者か」を判定。DB コミット後に ack が失敗してブローカ再送された場合も、次回は `try_insert → false` で no-op。

**2. 状態遷移制約による業務層冪等性 (二重防御)**

`processed_events` だけに頼らず、`carts.status` を state machine として扱い、ターミナル状態からの再遷移を許さない。これは「異なる `event_id` で論理的に重複するイベント」（再生成された返信、順序逆転）にも効く。条件付き UPDATE:

```sql
UPDATE carts
   SET status = 'ordered',
       order_id = :order_id,
       updated_at = now()
 WHERE id = :cart_id
   AND checkout_request_id = :checkout_request_id
   AND status = 'submitted';
```

`rowcount == 0` の場合は「既にターミナル状態 or 別のリクエスト ID」と判断してログに残してから ack。例外にはしない。`order.created` と `order.failed` のどちらが先に処理されても、後着は no-op で潰される。

**3. 順序逆転と古いイベントの扱い (補強)**

- `processed_events` は最低 30 日保持。短すぎると再送が再処理される。
- `carts.checkout_request_id` を一致条件に含めることで、「同じカートで再度 checkout した後に古い返信が届く」ケースも自然に弾かれる。

### 6.4 Checkout タイムアウトと recovery (in-scope)

Checkout 側が受領しても返信を失う（ロスト / 誤ルーティング / DLQ 行き / 未送信）ケースがあり得るため、`submitted` のまま放置されるカートを必ず終端へ進めるスイーパを本スペックの一部として実装する。

#### スイーパ プロセス

- 4 つ目の worker として `app/workers/checkout_sweeper.py` を追加（`api` / `outbox-relay` / `order-event-consumer` と並ぶ）。
- 起動: `python -m app.workers.checkout_sweeper`。docker compose では独立サービス `checkout-sweeper`。
- 走査間隔: 既定 300s (`CHECKOUT_SWEEP_INTERVAL_SEC` で調整)。
- タイムアウト閾値: 既定 24h (`CHECKOUT_TIMEOUT_HOURS` で調整)。

#### クエリ / 遷移

`FOR UPDATE SKIP LOCKED` で複数 sweeper レプリカと衝突しない:

```sql
SELECT id, checkout_request_id
  FROM carts
 WHERE status = 'submitted'
   AND submitted_at < now() - make_interval(hours => :timeout_hours)
 ORDER BY submitted_at
 LIMIT 100
 FOR UPDATE SKIP LOCKED;
```

各行に対して条件付き UPDATE（状態遷移制約と同じ仕組み）:

```sql
UPDATE carts
   SET status = 'failed',
       failure_reason = 'timeout',
       updated_at = now()
 WHERE id = :cart_id
   AND status = 'submitted';
```

`carts.failure_reason text NULL` を §5 のテーブル定義に追加する。

#### 遅延返信に対する冪等性

`status='submitted'` でのみ遷移許可しているため、後着の `order.created` / `order.failed` は §6.3 の状態遷移制約（`WHERE status='submitted'`）で no-op になる。`processed_events` も合わせて二重防御。

#### メトリクスとアラート

- counter: `ec.checkout.timeout.total` — sweeper が `failed (timeout)` に遷移させた件数。
- alert: 直近 15 分の累積が閾値超過で New Relic アラート。「Checkout 側で滞留が起きている」サインとして扱う。

#### ユーザ向け動線

- `GET /carts/{id}` は `status='failed'` + `failure_reason='timeout'` を返す。レスポンス schema に `failure_reason` を追加。
- フロントは「再試行」「キャンセル」を提示できる。`POST /carts/{id}/reopen` のような明示エンドポイントは現スコープでは設けず、ユーザが新たに `POST /carts` で新規カートを作る運用とする（実装の単純化）。reopen が必要になったら別チケットで足す。

#### テスト

- `tests/workers/test_checkout_sweeper.py`:
  - `submitted` 経過カートが `failed (reason='timeout')` に遷移する。
  - 経過していない `submitted` カートは触らない。
  - 既に `ordered` のカートは触らない。
  - sweep 後に到着した `order.created` を consumer に流しても no-op（ターミナル状態維持）。

## 7. 認証

### 7.1 エンドポイント

- `POST /auth/register` — email + password。バリデーション + 重複チェック → users 作成。
- `POST /auth/login` — email + password → access + refresh の JWT ペア。
- `POST /auth/refresh` — refresh → 新しい access + 回転された refresh（旧 refresh は revoke）。
- `POST /auth/logout` — refresh を revoke。

### 7.2 トークン

- アルゴリズム: **RS256**（Checkout 側と公開鍵だけ共有すれば検証可能）。
- Access JWT: 15 分。`sub=user_id`、`scope`、`iat`、`exp`。
- Refresh JWT: 14 日。`jti` を含め、サーバ側 `refresh_tokens` に hash 保存。ローテーション必須。
- 検証は `core/security.py` の `decode_token`。

### 7.3 FastAPI 依存

`modules/auth/dependencies.py` に集約:

- `get_current_user(token: Annotated[str, Depends(bearer)]) -> User`
- `require_active_user(user: Annotated[User, Depends(get_current_user)]) -> User`

`HTTPBearer(auto_error=False)` で `Authorization: Bearer <token>` を読む。

## 8. エラーハンドリング

- **アプリ例外**: `core/exceptions.py` に `AppError(code: str, http_status: int, message: str, details: dict | None)` 階層。`NotFoundError`, `ConflictError`, `ValidationError`, `AuthError` などを派生。
- **handler**: `app.main.py` で `@app.exception_handler(AppError)` を登録し、`shared/responses.py::error_envelope(code, message, details)` で統一フォーマットを返す。
- **DB 例外**: `IntegrityError` などは repository 層で `ConflictError` に翻訳。`service` に DB 例外を漏らさない。
- **Pydantic 422**: handler でラップして同じ envelope 形式に揃える。
- **MQ publish 失敗 (relay)**: `attempts++`、指数バックオフ。`attempts >= 8` で `dead_letter_at` を立てて alert（観測性側で検知）。
- **MQ consume 失敗**: 例外を 3 種に分類し、種類別に処理する。
  1. **Retryable (infra)** — `ConnectionError` / `OperationalError` / `DeadlockDetected` / `TimeoutError` / `OSError` 等の一時障害。delayed retry exchange を経由して指数バックオフでリトライ。
     - retry exchange: `ec.events.retry`（x-message-ttl + DLX で時間差再投入）
     - バックオフ段階: 1s, 5s, 30s, 2m, 10m
     - `MAX_CONSUMER_RETRIES = 5`（環境変数で調整可）超過で DLQ 行き。
     - attempts は `x-death` ヘッダで参照。アプリ側で別途カウントを持たない。
  2. **Non-retryable (data)** — JSON パース失敗 / envelope スキーマ違反 / 未知 `event_type` / `AppError(retryable=False)`。即 DLQ。
  3. **Already processed** — `processed_events` の `try_insert → false` で検出される重複。ack のみ。

  どのケースでも consumer の ack/nack 戦略はメッセージ単位で完結し、コネクション全体を落とさない。

- **DLQ 監視**: `ec.consumer.dlq.total{event_type, reason}` を OTel counter で記録し、New Relic でアラート。DLQ メッセージは別ツール (管理 UI / 再送スクリプト) で対処する想定。

エラー envelope:

```json
{
  "error": {
    "code": "not_found",
    "message": "Cart not found",
    "details": { "cart_id": "..." },
    "trace_id": "..."
  }
}
```

## 9. OpenTelemetry

### 9.1 計装範囲

- **自動計装**: `FastAPIInstrumentor`, `SQLAlchemyInstrumentor`, `AsyncPGInstrumentor`, `AioPikaInstrumentor`, `HTTPXClientInstrumentor`, `LoggingInstrumentor`。
- **手動 span**: `cart_service.submit_checkout`, `outbox_relay.publish_batch`, `order_consumer.handle`, `checkout_sweeper.sweep_batch` を `tracer.start_as_current_span` で囲む。
- **Metrics**: 既定のプロセス/ランタイムメトリクス + カスタム counter:
  - `ec.checkout.submitted.total`
  - `ec.outbox.published.total`
  - `ec.outbox.dead_letter.total`
  - `ec.consumer.processed.total{event_type}`
  - `ec.consumer.retried.total{event_type, attempt}` — retry exchange 経由で再投入された件数
  - `ec.consumer.dlq.total{event_type, reason}` — DLQ 行きの件数（reason は `max_retries` / `non_retryable` / `parse_error` 等）
  - `ec.checkout.timeout.total` — sweeper が `failed (timeout)` に遷移させた件数
- **Logs**: structlog → `LoggingHandler` で OTLP に送出。`trace_id`/`span_id` を自動付与。

### 9.2 Resource attributes

- `service.name`: `ec-api` / `ec-outbox-relay` / `ec-order-consumer` / `ec-checkout-sweeper`
- `service.namespace`: `ec`
- `service.instance.id`: コンテナ ID
- `deployment.environment`: `local` / `staging` / `production`

### 9.3 Collector

`docker/otel-collector/config.yaml`:

- **receivers**: `otlp` (grpc:4317, http:4318)
- **processors**: `memory_limiter`, `batch`, `resourcedetection`
- **exporters**: `otlphttp/newrelic`
  - `endpoint: https://otlp.nr-data.net`
  - headers: `api-key: ${env:NEW_RELIC_LICENSE_KEY}`
- **service.pipelines**:
  - `traces`: otlp → memory_limiter → batch → otlphttp/newrelic
  - `metrics`: 同上
  - `logs`: 同上

## 10. テスト戦略

| レイヤ | 目的 | 手段 |
|---|---|---|
| unit | ビジネスロジック | `tests/modules/<m>/test_service.py` — repository をフェイクに差し替え |
| integration (router) | HTTP → DB | `httpx.AsyncClient` + Testcontainers PostgresContainer |
| integration (repository) | SQL の正しさ | 実 PostgreSQL に対する CRUD |
| workers | MQ 連携 | Testcontainers RabbitmqContainer + PostgresContainer |
| contract | イベントスキーマ | `tests/contracts/*.schema.json` を publish/consume 両端で検証 |
| observability smoke | span 連結 | InMemorySpanExporter でトレース連結確認 |

冪等性の検証は **workers のテストで明示**:

- `test_consumer_idempotent_same_event_id` — 同じ envelope を 2 回 publish → cart 状態が 1 回分しか進まない。
- `test_consumer_terminal_state_no_op` — `ordered` 後に `order.failed` が来ても無視。

Consumer の retry / DLQ ポリシー検証（`tests/workers/test_order_consumer.py`）:

- `test_retry_on_db_failure_before_commit` — ハンドラ内で `OperationalError` を発生 → メッセージが retry exchange へ → TTL 後再配送 → 業務 DB は 1 回分のみ反映。
- `test_no_double_apply_when_ack_lost_after_commit` — commit 後に ack を失敗させて再配送 → `processed_events` の `try_insert → false` で no-op。
- `test_poison_message_goes_to_dlq` — JSON 破損 envelope → 即 DLQ（`ec.consumer.dlq.total{reason='parse_error'}` 増加）。
- `test_dlq_after_max_retries` — 常に `OperationalError` を返すスタブで `MAX_CONSUMER_RETRIES` 超過 → DLQ 行き（`reason='max_retries'`）。

Checkout タイムアウト sweeper の検証（`tests/workers/test_checkout_sweeper.py`）:

- `test_sweeper_marks_submitted_over_threshold_as_timeout` — `submitted_at` が閾値超のカートが `failed (reason='timeout')` に遷移。
- `test_sweeper_skips_fresh_submitted` — 閾値内の `submitted` は触らない。
- `test_sweeper_skips_terminal_carts` — `ordered` / 既存 `failed` は触らない。
- `test_late_order_created_after_timeout_is_noop` — sweeper でタイムアウト後に届いた `order.created` を consumer に流しても `WHERE status='submitted'` の条件付き UPDATE で 0 行更新 → ターミナル状態維持。

CI 想定: `uv sync` → `ruff check` → `mypy` → `pytest -m "not slow"` を必須、`pytest -m slow` を別ジョブ。**`slow` マーカーの基準**: Testcontainers でコンテナを起動するテスト（integration/workers/contracts/observability smoke）。pure unit は `not slow` 側で走らせる。

## 11. コンテナ構成

### 11.1 イメージ

`docker/Dockerfile` — 2-stage:

1. **builder**: `python:3.12-slim` に `uv` を入れ、`uv sync --frozen` で `.venv` 生成。
2. **runtime**: `python:3.12-slim` に `.venv` をコピー、非 root ユーザで起動。`PATH=/app/.venv/bin:$PATH`。

`api` / `outbox-relay` / `order-event-consumer` は同じイメージを共有し `command:` だけ差し替える。

### 11.2 docker-compose.yml サービス

| service | command | depends_on |
|---|---|---|
| `api` | `uvicorn app.main:app --host 0.0.0.0 --port 8000` | postgres, rabbitmq, otel-collector |
| `outbox-relay` | `python -m app.workers.outbox_relay` | 同上 |
| `order-event-consumer` | `python -m app.workers.order_consumer` | 同上 |
| `checkout-sweeper` | `python -m app.workers.checkout_sweeper` | postgres, otel-collector |
| `postgres` | (image: postgres:16) | — |
| `rabbitmq` | (image: rabbitmq:3.13-management) | — |
| `otel-collector` | `--config=/etc/otelcol/config.yaml` | — |

healthcheck:

- postgres: `pg_isready`
- rabbitmq: `rabbitmq-diagnostics ping`
- api / workers: `depends_on: { service: postgres, condition: service_healthy }`

### 11.3 起動手順

```bash
cp .env.example .env       # NEW_RELIC_LICENSE_KEY 等を埋める
# JWT 用 RS256 鍵を生成（初回のみ）
openssl genrsa -out secrets/jwt_private.pem 2048
openssl rsa -in secrets/jwt_private.pem -pubout -out secrets/jwt_public.pem

docker compose up -d --build
docker compose exec api alembic upgrade head
docker compose exec api python -m app.scripts.seed   # 開発用シード（items）
open http://localhost:8000/docs
```

ホスト側 `./secrets/` を `api` / `outbox-relay` / `order-event-consumer` の `/run/secrets/` に read-only bind mount する想定。本番 (K8s 等) ではプラットフォーム標準のシークレット機構に置き換える。

### 11.4 環境変数 (.env.example 主要項目)

```
APP_ENV=local
DATABASE_URL=postgresql+asyncpg://ec:ec@postgres:5432/ec
RABBITMQ_URL=amqp://guest:guest@rabbitmq:5672/
JWT_ALGORITHM=RS256
JWT_PRIVATE_KEY_PATH=/run/secrets/jwt_private.pem
JWT_PUBLIC_KEY_PATH=/run/secrets/jwt_public.pem
JWT_ACCESS_TTL_MIN=15
JWT_REFRESH_TTL_DAYS=14
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
OTEL_EXPORTER_OTLP_PROTOCOL=grpc
OTEL_SERVICE_NAME=ec-api          # workers は ec-outbox-relay / ec-order-consumer / ec-checkout-sweeper に上書き
OTEL_RESOURCE_ATTRIBUTES=service.namespace=ec,deployment.environment=local
NEW_RELIC_LICENSE_KEY=...
# Checkout タイムアウト sweeper
CHECKOUT_SWEEP_INTERVAL_SEC=300
CHECKOUT_TIMEOUT_HOURS=24
# Consumer retry / DLQ
MAX_CONSUMER_RETRIES=5
```

## 12. 依存方向と禁止事項（再掲）

```
router  →  service  →  repository  →  models/db
   │          │            │
   └──schemas─┘            └──(SQL only)
```

禁止:

- `router.py` にビジネスロジックを書かない。
- `service.py` に FastAPI の `Request`/`Response`/`Depends` 等を持ち込まない。
- `repository.py` に HTTP や FastAPI 固有の処理を書かない。
- `shared/` に何でも置かない（複数 module で本当に必要なもののみ）。
- 技術レイヤー別の巨大な `schemas/`, `models/`, `services/` ディレクトリをトップレベルに作らない。

## 13. オープン項目（次フェーズ）

- 失敗カートの **reopen / 明示的 cancel API**（現スコープではユーザが新規カート作成で対応）。
- **DLQ 管理 UI / 再送スクリプト**（現スコープではメトリクスとアラートのみ）。
- 商品の **検索 / カテゴリ**（現スコープでは見送り）。
- **管理画面 / 在庫表示**（在庫は Checkout 側 Stock の真実、本体は表示用ミラーが必要なら別途 read API）。
- **CI/CD** パイプライン詳細（GitHub Actions 等）。
- **本番デプロイ先**（K8s / ECS など）。
