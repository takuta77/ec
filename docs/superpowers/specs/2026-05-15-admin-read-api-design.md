# Admin Read API 設計

**Date:** 2026-05-15
**Status:** Draft → ユーザレビュー待ち
**Scope:** EC API に admin-only の read-only エンドポイント群 (`/admin/*`) を追加する。在庫・カート・Outbox・DLQ の運用観察に必要な状態を集計形式で公開し、Admin console (C-5) のバックエンドとして機能させる。

---

## 1. 目的と背景

`docs/superpowers/specs/2026-05-12-ec-api-design.md` §13 のオープン項目「在庫ミラー read API / Admin console」のうち、API 側を本 spec で扱う。

現状、システム状態を確認する手段が:

- 直接 SQL を叩く (危険、運用負荷)
- ログを grep する (一過性で集計困難)
- C-2 の DLQ CLI (MQ 限定)

しかない。本 spec では admin 認可付きの HTTP 集計エンドポイントを提供し、C-5 (Admin console) の UI が叩く先を作る。並行して監視・ジョブ等から HTTP で叩ける汎用性も得る。

「在庫数 (stock / quantity)」フィールドは現状 `Item` モデルに存在しないため本 spec では扱わない。Items は count / category 分布の集計に留める。

## 2. ゴール / 非ゴール

### ゴール

- Admin 認可機構の最小実装 (`User.is_admin: bool` + `require_admin` dependency)
- Read-only エンドポイント 6 本 (items/carts/outbox/dlq stats + carts list + dlq peek)
- 既存の C-2 DLQ helpers (`count_dlq` / `peek_dlq`) を HTTP で公開 (ロジック再利用、副作用無し)
- Slow tests (Testcontainers) で認可 + 各エンドポイントの集計正確性を検証
- 既存の auth / users / carts / items モジュールを壊さない (追加のみ)

### 非ゴール (本 spec のスコープ外、§14 オープン項目で trace)

- Admin **write** 操作 (DLQ redrive / drain の HTTP 化、role/permission CRUD、cart の admin 強制状態遷移)
- Admin user の招待・作成 API (本 spec では DB 直接 `UPDATE users SET is_admin = true`)
- メトリクス公開 (Prometheus `/metrics`)
- 検索 / フィルタの全文化
- 監査ログ (誰がどの admin 操作をしたか)
- 在庫数 (stock / quantity) — `Item` モデル拡張前提なので別 spec
- C-5 (Admin console UI)

## 3. アーキテクチャ概要

```
                 ┌─ JWT → get_current_user → require_admin (is_admin == true)
                 │                              │ false → AuthorizationError 403
GET /admin/stats/items    ─┐                    │
GET /admin/stats/carts     │                    ▼
GET /admin/stats/outbox    ├──▶ admin/router.py ──▶ admin/service.py ──▶ admin/repository.py (SQL 集計)
GET /admin/stats/dlq       │                                       └──▶ mq/dlq_admin.count_dlq (C-2 再利用)
GET /admin/carts           │
GET /admin/dlq/{q}/peek   ─┘                                            mq/dlq_admin.peek_dlq (C-2 再利用)
```

集計クエリ専用の repository を新設 (admin の query は既存 module の repository とパターンが異なるため). モジュールは `app/modules/admin/` 配下に閉じる。

## 4. データモデル変更

### `User` モデル

`app/modules/users/models.py` に列追加:

```python
is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, server_default="false")
```

`server_default="false"` で既存行は全て `false` のまま (= 一般ユーザ). 既存 API / テストへの影響なし。

### マイグレーション `0009_users_add_is_admin.py`

```python
def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )

def downgrade() -> None:
    op.drop_column("users", "is_admin")
```

index は不要 (admin 数が極小、フィルタ対象にもならない).

## 5. 認可

### `app/core/exceptions.py` に追加

```python
class AuthorizationError(AppError):
    code = "forbidden"
    http_status = 403
```

(既存 `NotFoundError` / `AppError` パターンに合わせる).

### `app/modules/admin/dependencies.py` 新規

```python
async def require_admin(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    if not user.is_admin:
        raise AuthorizationError(
            "Admin privileges required", details={"user_id": str(user.id)}
        )
    return user
```

各 `/admin/*` ルートで `Depends(require_admin)` を要求。

## 6. API 仕様

### 6.1 `GET /admin/stats/items`

レスポンス: `ItemsStats`

```json
{
  "total": 42,
  "active": 38,
  "by_category": {"beverages": 15, "stationery": 10, "books": 13}
}
```

- `total`: 全 Item 件数 (`is_active` 不問)
- `active`: `is_active = true` の件数
- `by_category`: `category IS NOT NULL` でグループ化、カテゴリ別の `is_active=true` 件数 (NULL カテゴリは含めない)

### 6.2 `GET /admin/stats/carts`

レスポンス: `CartsStats`

```json
{
  "by_status": {"open": 12, "submitted": 3, "ordered": 87, "failed": 5, "cancelled": 9},
  "failed_with_timeout": 4
}
```

- `by_status`: 全 5 status (`open/submitted/ordered/failed/cancelled`) のカウント。データに存在しない status も `0` で出す
- `failed_with_timeout`: `status='failed' AND failure_reason='timeout'` のカウント

### 6.3 `GET /admin/stats/outbox`

レスポンス: `OutboxStats`

```json
{
  "pending": 7,
  "dispatched": 1432,
  "oldest_pending_at": "2026-05-15T01:23:45+00:00"
}
```

- `pending`: `dispatched_at IS NULL` の件数
- `dispatched`: `dispatched_at IS NOT NULL` の件数
- `oldest_pending_at`: `min(created_at) WHERE dispatched_at IS NULL` (no rows なら `null`)

### 6.4 `GET /admin/stats/dlq`

レスポンス: `list[DLQQueueStats]`

```json
[
  {"queue": "ec.order_consumer.dlq", "message_count": 3}
]
```

既知の consumer queue リスト (constants) を回して `count_dlq` を呼ぶ。queue が存在しなければ (`DLQNotFoundError`) `message_count = 0` として返す (passive declare が落ちただけで、エラーとして外に出さない)。

### 6.5 `GET /admin/carts?status=<>&limit=<>&offset=<>`

レスポンス: `list[CartAdminOut]`

| Query | 型 | 仕様 |
|---|---|---|
| `status` | `str?` | 任意。指定時は対応する `CartStatus` だけ返す。不正値は 422 |
| `limit` | `int` | 1〜100、default 20 |
| `offset` | `int` | ≥0、default 0 |

並び順は `created_at DESC` (新しい順). `CartAdminOut`:

```json
{
  "id": "uuid",
  "user_id": "uuid",
  "status": "failed",
  "failure_reason": "timeout",
  "submitted_at": "2026-05-15T00:00:00Z",
  "line_count": 3,
  "created_at": "2026-05-15T00:00:00Z",
  "updated_at": "2026-05-15T01:00:00Z"
}
```

`line_count` は `cart_items` テーブルとの subquery で算出。

### 6.6 `GET /admin/dlq/{consumer_queue}/peek?limit=<>&preview_chars=<>`

レスポンス: `list[DLQMessageOut]`

- C-2 の `peek_dlq` を呼ぶラッパー
- `consumer_queue` は任意の文字列 (e.g. `ec.order_consumer`)、内部で `<queue>.dlq` に展開
- `DLQNotFoundError` → 404 (`code: dlq_not_found`)
- 既知 queue 制限は設けない (列挙化は future work)

`DLQMessageOut` (DLQ helpers の `DLQMessage` から `delivery_tag` / `headers` 詳細を除いた public 表現):

```json
{
  "event_id": "evt-xyz",
  "routing_key": "ec.order.completed",
  "death_count": 5,
  "body_preview": "{...}"
}
```

## 7. ファイル構成

```
app/modules/admin/
├── __init__.py
├── dependencies.py        # require_admin
├── router.py              # 6 routes
├── repository.py          # 集計クエリ (count / group_by)
├── service.py             # repository + dlq_admin の薄いオーケストレーション
└── schemas.py             # 5 response models + DLQMessageOut

app/mq/
└── queues.py              # 新規 — KNOWN_CONSUMER_QUEUES: tuple[str, ...]

app/core/
└── exceptions.py          # AuthorizationError 追加

app/modules/users/
└── models.py              # is_admin 列追加

app/main.py                # admin_router を include

migrations/versions/
└── 0009_users_add_is_admin.py

tests/modules/admin/
├── __init__.py
├── conftest.py            # admin_user / non_admin_user fixtures + admin_token / user_token
├── test_dependencies.py   # require_admin の許可/拒否 (unit)
├── test_stats.py          # /admin/stats/* slow tests
├── test_carts.py          # /admin/carts slow tests
└── test_dlq.py            # /admin/dlq/* slow tests (DLQ count + peek)
```

## 8. 内部設計

### `AdminRepository`

純粋な集計クエリ専用:

```python
class AdminRepository:
    def __init__(self, session: AsyncSession) -> None: ...

    async def items_stats(self) -> tuple[int, int, dict[str, int]]: ...
        # returns (total, active, by_category_active)

    async def carts_stats(self) -> tuple[dict[str, int], int]: ...
        # returns (by_status all-5-keys, failed_with_timeout)

    async def outbox_stats(self) -> tuple[int, int, datetime | None]: ...
        # returns (pending, dispatched, oldest_pending_at)

    async def list_carts(
        self, *, status: CartStatus | None, limit: int, offset: int
    ) -> list[CartAdminRow]: ...
        # returns rows with line_count populated via subquery
```

`CartAdminRow` は内部 dataclass (`id`, `user_id`, `status`, `failure_reason`, `submitted_at`, `line_count`, `created_at`, `updated_at`) で、router 層で `CartAdminOut` (pydantic schema) に変換。

### `AdminService`

repository + `dlq_admin` の wiring:

```python
class AdminService:
    def __init__(
        self,
        repo: AdminRepository,
        connection: aio_pika.abc.AbstractRobustConnection,
    ) -> None: ...

    async def items_stats(self) -> ItemsStats: ...
    async def carts_stats(self) -> CartsStats: ...
    async def outbox_stats(self) -> OutboxStats: ...
    async def dlq_stats(self) -> list[DLQQueueStats]: ...   # KNOWN_CONSUMER_QUEUES を回す
    async def list_carts(self, *, status, limit, offset) -> list[CartAdminOut]: ...
    async def peek_dlq(self, queue: str, *, limit: int, preview_chars: int) -> list[DLQMessageOut]: ...
```

`dlq_stats` は `DLQNotFoundError` を swallow して `message_count=0` として返す。`peek_dlq` は `DLQNotFoundError` をそのまま外に出す (router で 404 マップ).

### MQ Connection の dependency 化

現状の `app.main` は MQ 接続を起動時に作る箇所が無い (publisher / consumer は worker 側で独自に持つ). admin endpoint で MQ 接続が必要なため、`app/main.py` の lifespan で `aio_pika.connect_robust()` を 1 つ確保し、FastAPI dependency `get_mq_connection` で配る:

```python
# app/main.py lifespan に追加
mq_connection = await aio_pika.connect_robust(settings.rabbitmq_url)
app.state.mq_connection = mq_connection
try:
    yield
finally:
    await mq_connection.close()
    await dispose_engine()

# app/mq/connection.py に追加
async def get_mq_connection(request: Request) -> aio_pika.abc.AbstractRobustConnection:
    return request.app.state.mq_connection
```

`admin/router.py` の DLQ endpoint だけがこれを使う (他は DB のみ).

## 9. エラーハンドリング

| 条件 | 動作 |
|---|---|
| 未認証 (JWT 無し / 不正) | 既存 `get_current_user` の 401 |
| 認証済みだが `is_admin=false` | `AuthorizationError` → 403, `{code: "forbidden", message: "Admin privileges required"}` |
| `/admin/carts?status=invalid` | Pydantic enum バリデーション → 422 |
| `/admin/dlq/{queue}/peek` で queue 不在 | `DLQNotFoundError` → 404, `{code: "dlq_not_found"}` |
| RabbitMQ 接続失敗 | 既存 `AppError` ハンドラ → 5xx |

## 10. テスト戦略

### Unit
- `test_dependencies.py`: `require_admin` を mock User で 2 ケース (admin / non-admin)
- schemas のバリデーション (enum / required fields)

### Slow (Testcontainers Postgres + RabbitMQ)
1. **認可ゲート**: 非 admin user で `/admin/stats/items` → 403
2. **未認証**: token 無しで `/admin/stats/items` → 401
3. **items stats 集計**: 5 件 seed (active 3 + inactive 2、3 カテゴリ) → `total=5, active=3, by_category` 正確
4. **carts stats**: 5 statuses 各 1〜2 件 seed → `by_status` 全 5 キー正確、failed+timeout カウント
5. **outbox stats**: pending 2 + dispatched 3 seed → pending/dispatched/oldest_pending_at 正確
6. **dlq stats (空)**: 既知 queue が空 → `[{queue, message_count: 0}]`
7. **dlq stats (件数あり)**: dlq に投入 → 反映
8. **list carts (no filter)**: 全 status 含まれる
9. **list carts (status filter)**: failed のみ
10. **list carts (line_count)**: 商品入りカート → `line_count` 正確
11. **peek dlq**: 既知 queue + 投入 → `DLQMessageOut` 形式
12. **peek dlq 404**: 存在しない queue → 404 `code: dlq_not_found`

## 11. 互換性

- `User.is_admin` は `server_default='false'`、既存 row は影響なし
- 既存 `/users/me` のレスポンスに `is_admin` を含めるかは scope 外 (含めない、フロントが必要になったら別 PR)
- admin user 作成は本 spec では DB 直接 SQL。`gh issue create --title 'Admin user invitation API'` などで follow-up

## 12. ロールアウト計画

1. PR: migration + auth + admin module + tests
2. CI green (全 7 必須チェック)
3. main へマージ後、本番 DB に migration 適用
4. 1 ユーザを `UPDATE users SET is_admin = true WHERE id = '...'` で admin 化
5. C-5 (Admin console) で UI を被せる

## 13. パフォーマンス

集計クエリの規模感:
- items: 数千件程度なら全件 GROUP BY で OK
- carts: status カラムは index が無いと遅くなる可能性。現状の `0004_carts.py` を確認、必要なら 0010 で `ix_carts_status` 追加 (本 PR の範囲外、follow-up)
- outbox: `dispatched_at` index は既存 (outbox_relay が使うはず). 不在なら follow-up

## 14. オープン項目 (将来検討)

### 直近で必要 (スコープ外として明示的に積む)

- **Admin write 操作** — DLQ redrive/drain を HTTP 化 (C-2 helpers をそのまま呼ぶラッパー、副作用ガード `?apply=true` のような同様の dry-run 既定で)
- **Cart の admin 強制状態遷移** — failed → ordered 等の手動遷移 (運用での救済用、極めて慎重に)
- **Admin user 作成 API** — 現状は DB 直接 UPDATE。invitation flow 形式の方が安全
- **メトリクス公開** — Prometheus `/metrics`、`/admin/stats/*` と並列に exposition format
- **監査ログ** — admin 操作のすべてを `audit_log` テーブルに記録 (誰が・いつ・何を・対象)
- **検索 / フィルタの全文化** — `/admin/carts` に `q` パラメータで partial-match (user email など)
- **C-5 (Admin console UI)** — 本 API を消費するフロント、別 spec
- **`carts.status` index** (`ix_carts_status`) — `/admin/carts?status=X` が遅くなったら
- **`outbox.dispatched_at` index** — outbox_relay 自身が使っていれば既存。要確認

### 将来検討

- Role/permission の RBAC (`is_admin` 二値より細かい)
- Field-level masking (PII 隠蔽)
- Cross-region admin (read replica からの集計)
- Cached stats (頻繁にポーリングされる場合に Redis にキャッシュ)
