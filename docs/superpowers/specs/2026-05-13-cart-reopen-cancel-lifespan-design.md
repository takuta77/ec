# Cart Reopen / Cancel + FastAPI Lifespan Migration — Design Spec

- **Date:** 2026-05-13
- **Status:** Draft (pending review)
- **Owner:** t-tanimiya@r-up.jp
- **Scope:** 既存 EC API に `POST /cart/reopen` (failed/timeout 復元) と `POST /cart/cancel` (open → cancelled) を追加し、合わせて `/carts/me/*` 系エンドポイントを `/cart/*` 単数形にリネームする。あわせて `@app.on_event` を FastAPI `lifespan` に移行して deprecation を解消する。
- **Out of scope:** 失敗 reason が `timeout` 以外の reopen、submitted 状態の cancel、DLQ 管理 UI、商品検索 / カテゴリ。

## 1. 目的と背景

PR #1 (本体 EC API 初版) で `failed (reason='timeout')` カートが発生し得ることが明示された。現状ユーザはそれを再活用する手段がなく、新規カートを作り直すしかない。`POST /cart/reopen` で同じカート (cart_id / line / price snapshot) を `open` に戻し、再 checkout を可能にする。

`POST /cart/cancel` は「ユーザが open カートを破棄したい」ケースを正式に受け入れる。partial unique index `ix_carts_user_open` が `status='open'` でのみ効くため、`cancelled` 状態に遷移させれば同ユーザが新規 `open` カートをすぐ作れる。

リネーム (`/carts/me/*` → `/cart/*`) は REST のお作法に沿った命名への揃え (ユーザ毎にシングルトンなリソースは単数形)。PR #1 がまだ未マージで外部消費者がいないので、破壊コストゼロで実施する。

`lifespan` 移行は FastAPI の `@app.on_event` deprecation を解消する技術負債返済。`asynccontextmanager` ベースの 1 関数に集約する。

## 2. 影響範囲サマリ

| 領域 | 変更 |
|---|---|
| ORM | `CartStatus` enum に `cancelled = "cancelled"` 追加 |
| DB | `cart_status` PostgreSQL enum に `'cancelled'` を `ALTER TYPE ADD VALUE` |
| Repository | `reopen_failed_timeout`, `cancel_open` メソッド追加 |
| Service | `reopen_my_cart`, `cancel_my_open_cart` 追加 |
| Core exceptions | `OpenCartAlreadyExistsError(ConflictError)` を `app/core/exceptions.py` に追加 |
| Router | prefix `/carts` → `/cart`, `/me` セグメント除去, `{cart_id}` path param 除去, `POST /cart/reopen` / `POST /cart/cancel` 追加 |
| Tests | service 5 件 / router 2 件 / sweeper 1 件 / 既存 router テスト URL 更新 |
| App factory | `@app.on_event` 2 件を `lifespan` 1 関数に統合 |
| Docs | CHANGELOG 0.2.0 エントリ |

## 3. API 仕様

### 3.1 リネーム (REST 整合)

| Before | After |
|---|---|
| `GET /carts/me` | `GET /cart` |
| `POST /carts/me/items` | `POST /cart/items` |
| `DELETE /carts/me/items/{item_id}` | `DELETE /cart/items/{item_id}` |
| `POST /carts/{cart_id}/checkout` | `POST /cart/checkout` (cart_id path param 削除) |

`router.APIRouter(prefix="/cart", tags=["cart"])`。`get_current_user` 依存はそのまま。各 handler のシグネチャから `cart_id: uuid.UUID` を除去。

### 3.2 POST /cart/reopen (新規)

**Purpose:** `failed (reason='timeout')` のカートを `open` に戻し、line と price snapshot を保持する。

**Auth:** Bearer access token 必須。

**Request:** body なし。

**Responses:**
- `200 OK` + `CartOut` — 復元成功。元の `cart_id` / line / `unit_price_cents` を保持し、`status='open'`、`failure_reason=NULL`、`submitted_at=NULL`、`checkout_request_id=NULL`、`order_id=NULL`。
- `404 Not Found` + `error_envelope(code="not_found")` — そのユーザに `failed(reason='timeout')` のカートが存在しない。
- `409 Conflict` + `error_envelope(code="open_cart_already_exists")` — `failed(timeout)` カートを open に戻そうとしたが、既に同ユーザの `open` カートがあって partial unique index 違反になった。クライアントは先に `POST /cart/cancel` で既存の open を破棄してから再度 reopen を呼ぶ。
- `401 Unauthorized` — 認証失敗。

### 3.3 POST /cart/cancel (新規)

**Purpose:** 現在の `open` カートを `cancelled` に遷移。line items は保持 (audit)。

**Auth:** Bearer access token 必須。

**Request:** body なし。

**Responses:**
- `200 OK` + `{"status":"cancelled","cart_id":"<uuid>"}` — キャンセル成功。
- `404 Not Found` + `error_envelope(code="not_found")` — open カートが存在しない。
- `401 Unauthorized` — 認証失敗。

冪等性: cancel を再度呼ぶと open が無いので 404。これはエラーと言うより「再押下の通知」。クライアントは 404 を黙って許容できる。

## 4. データモデル変更

### 4.1 CartStatus enum 拡張

```python
class CartStatus(str, enum.Enum):
    open = "open"
    submitted = "submitted"
    ordered = "ordered"
    failed = "failed"
    cancelled = "cancelled"   # ← 追加
```

ORM 側は `Mapped[CartStatus]` のままで、`Enum(CartStatus, name="cart_status")` の SQLAlchemy 表現も変更なし (enum 名は同じ `cart_status`)。

### 4.2 状態遷移マシン

```
              POST /cart/checkout
   open  ─────────────────────────────►  submitted
    │                                       │
    │ POST /cart/cancel                     │  order.created
    ▼                                       ▼
cancelled (terminal)                     ordered (terminal)
                                            │
                                            │  order.failed (any reason)
                                            ▼
                                          failed
                                            │
                                            │  POST /cart/reopen (only if reason='timeout')
                                            ▼
                                          open  (re-enters the machine)
```

ターミナル状態: `cancelled` / `ordered` / `failed (reason ∈ {out_of_stock, payment_declined, …})`。`failed (reason='timeout')` のみ非ターミナル (reopen 経路がある)。

### 4.3 マイグレーション

`migrations/versions/0007_add_cancelled_status.py`:

```python
"""add cancelled to cart_status

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-13
"""
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE は単独トランザクションが必要
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE cart_status ADD VALUE IF NOT EXISTS 'cancelled'")


def downgrade() -> None:
    # PostgreSQL は enum 値の削除を直接サポートしない (型を作り直す必要がある)
    # 簡易 downgrade は提供しない。必要なら手動で対応。
    pass
```

`Base.metadata.create_all` を使うテスト fixture では `Enum(CartStatus, name="cart_status")` の Python enum 値が DB の enum を自動生成するので、テストには追加マイグレーションは不要。production の `alembic upgrade head` でのみこのマイグレーションが効く。

## 5. Repository 設計

`app/modules/carts/repository.py` に 2 メソッド追加。

```python
async def reopen_failed_timeout(self, user_id: uuid.UUID) -> int:
    stmt = (
        update(Cart)
        .where(
            Cart.user_id == user_id,
            Cart.status == CartStatus.failed,
            Cart.failure_reason == "timeout",
        )
        .values(
            status=CartStatus.open,
            failure_reason=None,
            submitted_at=None,
            checkout_request_id=None,
            order_id=None,
        )
    )
    result = await self.session.execute(stmt)
    return result.rowcount or 0


async def cancel_open(self, user_id: uuid.UUID) -> uuid.UUID | None:
    stmt = (
        update(Cart)
        .where(Cart.user_id == user_id, Cart.status == CartStatus.open)
        .values(status=CartStatus.cancelled)
        .returning(Cart.id)
    )
    result = await self.session.execute(stmt)
    return result.scalar_one_or_none()
```

両者とも条件付き UPDATE で state machine 違反を SQL レベルで防ぐ。`reopen_failed_timeout` は `rowcount` を返し、`cancel_open` は `RETURNING id` で遷移したカート ID を取得。

### 5.1 IntegrityError ハンドリング

`reopen_failed_timeout` 実行中、同ユーザに既存 `open` カートがあれば `ix_carts_user_open` partial unique index 違反で `IntegrityError`。`ConflictError` に変換するが、クライアントが「open がもう存在する」と「ただの conflict」を区別できるよう、固有 code を持つサブクラスを追加する:

```python
# app/core/exceptions.py に追加
class OpenCartAlreadyExistsError(ConflictError):
    code = "open_cart_already_exists"
```

```python
# app/modules/carts/repository.py
from sqlalchemy.exc import IntegrityError
from app.core.exceptions import OpenCartAlreadyExistsError

async def reopen_failed_timeout(self, user_id: uuid.UUID) -> int:
    try:
        result = await self.session.execute(...)  # 上記 UPDATE
        return result.rowcount or 0
    except IntegrityError as e:
        raise OpenCartAlreadyExistsError(
            "Open cart already exists; cancel it before reopen",
            details={"user_id": str(user_id)},
        ) from e
```

`ConflictError.http_status=409` を継承するので、global handler は `error_envelope(code="open_cart_already_exists")` を返す。クライアントは `code` で分岐できる。

### 5.2 同ユーザに複数の failed(timeout) カートがある場合

理論上は `failed(timeout)` カートが 2 件以上残るのは sweeper の意図的な動作 (古いものから timeout) + 新規 open → submitted → timeout が再度起きた場合。partial unique は `open` のみに効くので、`failed` は重複可能。

`reopen_failed_timeout` は全件を `open` に UPDATE しようとするが、1 件目が遷移した時点で partial unique が効いて 2 件目で IntegrityError。これは異常運用なので `ConflictError("open_cart_already_exists")` で返す。ユーザは最終的に手動で 1 件ずつ整理することになるが、本スコープでは「reopen は最大 1 件まで」を SQL 制約で担保する形にする。

将来的に「最も新しい failed(timeout) を 1 件だけ reopen する」セマンティクスにしたければ、`SELECT id ORDER BY submitted_at DESC LIMIT 1` してから UPDATE する。本スペックでは見送り。

## 6. Service 設計

`app/modules/carts/service.py` に 2 メソッド追加。

```python
async def reopen_my_cart(self, *, user_id: uuid.UUID) -> Cart:
    affected = await self.carts.reopen_failed_timeout(user_id)
    if affected == 0:
        raise NotFoundError(
            "No reopenable cart found",
            details={"user_id": str(user_id)},
        )
    cart = await self.carts.get_open_for_user(user_id)
    assert cart is not None
    return cart


async def cancel_my_open_cart(self, *, user_id: uuid.UUID) -> uuid.UUID:
    cart_id = await self.carts.cancel_open(user_id)
    if cart_id is None:
        raise NotFoundError(
            "No open cart to cancel",
            details={"user_id": str(user_id)},
        )
    return cart_id
```

`ConflictError` は repository から bubble up し、router の global handler に到達する。service 層では再 raise しない。

## 7. Router 設計

`app/modules/carts/router.py` を全面リネーム + 新規 2 件追加。既存 4 件のシグネチャと URL も更新。

```python
router = APIRouter(prefix="/cart", tags=["cart"])


def _service(session: AsyncSession) -> CartsService:
    return CartsService(
        CartsRepository(session),
        ItemsRepository(session),
        outbox=OutboxRepository(session),
    )


@router.get("", response_model=CartOut)
async def get_my_cart(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CartOut:
    cart = await _service(session).open_or_get(user.id)
    await session.commit()
    return await _cart_with_lines(session, cart)


@router.post("/items", response_model=CartOut)
async def add_item(...):
    ...


@router.delete("/items/{item_id}", response_model=CartOut)
async def remove_item(item_id: uuid.UUID, ...):
    ...


@router.post("/checkout", response_model=CheckoutOut, status_code=202)
async def checkout(user, session) -> CheckoutOut:
    result = await _service(session).submit_checkout(user_id=user.id)
    await session.commit()
    return result


@router.post("/reopen", response_model=CartOut)
async def reopen(user, session) -> CartOut:
    cart = await _service(session).reopen_my_cart(user_id=user.id)
    await session.commit()
    return await _cart_with_lines(session, cart)


@router.post("/cancel")
async def cancel(user, session) -> dict[str, str]:
    cart_id = await _service(session).cancel_my_open_cart(user_id=user.id)
    await session.commit()
    return {"status": "cancelled", "cart_id": str(cart_id)}
```

## 8. FastAPI lifespan 移行

`app/main.py` の `@app.on_event("startup")` / `@app.on_event("shutdown")` を削除し、`asynccontextmanager` ベースの `lifespan` 関数に統合。

```python
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.exceptions import AppError
from app.db.session import init_engine, dispose_engine
from app.modules.auth.router import router as auth_router
from app.modules.carts.router import router as cart_router
from app.modules.items.router import router as items_router
from app.modules.users.router import router as users_router
from app.shared.responses import error_envelope


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    from app.core.logging import configure_structlog
    from app.core.telemetry import init_telemetry, instrument_fastapi

    configure_structlog()
    init_telemetry(service_name="ec-api")
    instrument_fastapi(app)
    init_engine()
    try:
        yield
    finally:
        await dispose_engine()


def create_app() -> FastAPI:
    app = FastAPI(title="EC API", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content=error_envelope(
                code=exc.code, message=exc.message, details=exc.details, trace_id=None,
            ),
        )

    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(items_router)
    app.include_router(cart_router)
    return app


app = create_app()
```

`@app.on_event` の deprecation 警告 (`DeprecationWarning: on_event is deprecated, use lifespan event handlers instead`) が消える。テスト出力の警告も静かになる。

### 8.1 テスト互換性

- `tests/test_app_factory.py` の 2 テスト (`/healthz` / boom): `ASGITransport(app=app)` を使う。httpx 0.27+ の `ASGITransport` は `lifespan` を明示起動しない (`lifespan="on"` を渡さない限り)。現状の test は startup/shutdown を必要としないので問題なし。
- `tests/conftest.py` の `app_with_db` fixture: 既に `init_engine()` を fixture 内で呼び、`dispose_engine()` で teardown している。lifespan は fixture では起動しないため、fixture の挙動は変化なし。

## 9. テスト戦略

### 9.1 既存テストの更新

- `tests/modules/carts/test_router.py`: 4 箇所の URL 文字列を `/carts/me/...` → `/cart/...` に置換。`/carts/{id}/checkout` → `/cart/checkout`。

### 9.2 新規 service テスト

`tests/modules/carts/test_service.py` に 4 件追加 (slow):

- `test_reopen_restores_failed_timeout_cart` — 事前に `failed(timeout)` カートを用意し、`reopen_my_cart` で `open` に戻ること、cart_id / line / unit_price_cents が保持されることを検証。
- `test_reopen_returns_404_when_no_reopenable` — `failed(timeout)` カートが無いユーザで `NotFoundError` が上がること。
- `test_reopen_returns_404_when_failure_reason_not_timeout` — `failed(reason='out_of_stock')` カートでは reopen が 0 件 → `NotFoundError`。
- `test_cancel_open_cart` — `open` カートを `cancelled` に遷移。返り値の cart_id が一致。再度 `open_or_get` を呼ぶと **新規 open** が作られることを検証 (partial unique が cancelled を除外しているため)。
- `test_cancel_returns_404_when_no_open` — open カートが無いユーザで `NotFoundError`。

### 9.3 新規 router テスト

`tests/modules/carts/test_router.py` に 2 件追加 (slow):

- `test_reopen_and_resubmit_flow` — `app_with_db` fixture でユーザ作成 → カートに item 追加 → `POST /cart/checkout` で submitted → 内部で sweeper を直接呼んで `failed(timeout)` に遷移 → `POST /cart/reopen` で 200 + `open` 状態 → 再 `POST /cart/checkout` で 202 (新しい checkout_request_id)。
- `test_cancel_then_new_cart_flow` — open カートに item 追加 → `POST /cart/cancel` で 200 + `cancelled` → `GET /cart` で **新規 open 空カート** が返ること (再度 lines は空)。

### 9.4 sweeper 互換テスト

`tests/workers/test_checkout_sweeper.py` に 1 件追加:

- `test_reopened_cart_is_not_swept_again` — `failed(timeout)` → reopen → submitted_at が NULL に戻ったので sweep 対象外。新規 checkout 時に submitted_at が現在時刻になり、24h 経過していないので sweep が走っても 0 件。

### 9.5 IntegrityError 経路 (ConflictError)

`tests/modules/carts/test_service.py`:

- `test_reopen_returns_409_when_open_cart_exists` — `open` カート + `failed(timeout)` カートの両方を持つユーザで `reopen_my_cart` を呼ぶと `ConflictError` が上がること。partial unique を test 上で再現するため、DB 直接 INSERT で `failed(timeout)` カートを 1 件入れた後、現行 `open` カートを保ったまま reopen 試行。

### 9.6 CI 影響

- `make test`: 14 → 14 + 0 = 14 (service テストは slow マーカー、router も slow)。
- `make test-slow`: 33 → 33 + 8 = 41。

## 10. コミット計画

ベースブランチ: `feature/cart-reopen-cancel-lifespan` を `feature/ec-api-impl` から切る (PR #1 の HEAD `71c2bbb` がベース)。

```
1. docs(spec): cart-reopen-cancel-lifespan design
2. refactor(api): rename /carts/me/* to /cart/* and drop unused cart_id
3. feat(carts): add 'cancelled' status enum value
4. feat(carts): POST /cart/cancel transitions open → cancelled
5. feat(carts): POST /cart/reopen restores failed(timeout) → open
6. refactor(api): migrate @app.on_event to FastAPI lifespan
7. docs: CHANGELOG entry for 0.2.0
```

各コミットは TDD: 失敗テスト → 実装 → パステスト → コミット。

## 11. 依存関係 / 既知の制約

- PR #1 (`feature/ec-api-impl`) がまだマージされていないため、本 PR は PR #1 の差分を取り込んだベースで作業する (worktree base: `feature/ec-api-impl`)。PR #1 がマージされたら、本ブランチは main を `git merge` するか rebase する。
- `ALTER TYPE ADD VALUE` は PostgreSQL 12+ で `IF NOT EXISTS` をサポート。本プロジェクトは PostgreSQL 16 なので問題なし。
- 既存テスト `app_with_db` fixture は monkeypatch で env を上書きするため、lifespan に変更しても再キャッシュされた `get_settings()` がそのまま動く想定。テスト失敗が出たら fixture 側を調整する。

## 12. オープン項目 (本スペック外)

- 失敗 reason が `payment_declined` のカートの取り扱い (現状はターミナル)。
- 「最も新しい failed(timeout) のみ reopen」セマンティクス。
- submitted 状態を user 操作でキャンセルする場合 (Checkout への `checkout.cancel.requested` event を publish するセマンティクス)。
