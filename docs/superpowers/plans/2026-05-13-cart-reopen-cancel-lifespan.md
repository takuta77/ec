# Cart Reopen/Cancel + Lifespan Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `POST /cart/reopen` (failed-timeout → open) and `POST /cart/cancel` (open → cancelled), rename `/carts/me/*` to `/cart/*` for REST hygiene, and migrate `@app.on_event` to FastAPI `lifespan`.

**Architecture:** Single feature branch off `feature/ec-api-impl`. Extends the carts module with two new state transitions (conditional UPDATE on the existing `carts` table), adds one enum value, and refactors `app/main.py` startup/shutdown into a `lifespan` context manager. No new modules, no new infrastructure.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 async, Alembic, PostgreSQL 16, pytest (asyncio + Testcontainers).

**Spec:** `docs/superpowers/specs/2026-05-13-cart-reopen-cancel-lifespan-design.md`.

---

## Task 1: Rename /carts/me/* to /cart/*

**Files:**
- Modify: `app/modules/carts/router.py` (prefix + 4 endpoints)
- Modify: `tests/modules/carts/test_router.py` (URL strings)

- [ ] **Step 1: Update existing router URLs to fail tests**

In `tests/modules/carts/test_router.py`, replace every URL string `/carts/me` → `/cart` and `/carts/{item.id}/checkout` → `/cart/checkout`. The existing test `test_open_cart_and_add_remove` becomes:

```python
import pytest
from httpx import AsyncClient, ASGITransport


pytestmark = [pytest.mark.slow, pytest.mark.asyncio]


async def _auth(c):
    await c.post("/auth/register", json={"email": "k@example.com", "password": "pw"})
    r = await c.post("/auth/login", json={"email": "k@example.com", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_open_cart_and_add_remove(app_with_db, db_session):
    from app.modules.items.repository import ItemsRepository
    item = await ItemsRepository(db_session).create(name="T", price_cents=100, currency="JPY")
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        h = await _auth(c)
        r = await c.get("/cart", headers=h)
        assert r.status_code == 200 and r.json()["status"] == "open"

        r = await c.post("/cart/items", json={"item_id": str(item.id), "quantity": 2}, headers=h)
        assert r.status_code == 200
        assert r.json()["lines"][0]["quantity"] == 2

        r = await c.delete(f"/cart/items/{item.id}", headers=h)
        assert r.status_code == 200
        assert r.json()["lines"] == []
```

- [ ] **Step 2: Run test, expect fail**

```bash
cd /Users/takuma/cross/ec/.worktrees/cart-reopen-cancel-lifespan
uv run pytest tests/modules/carts/test_router.py -v -m slow
```
Expected: FAIL — `/cart` and `/cart/items` routes don't exist (404).

- [ ] **Step 3: Rewrite the router**

Replace `app/modules/carts/router.py` entirely with:

```python
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.auth.dependencies import get_current_user
from app.modules.carts.repository import CartsRepository
from app.modules.carts.schemas import AddItemIn, CartLineOut, CartOut, CheckoutOut
from app.modules.carts.service import CartsService
from app.modules.items.repository import ItemsRepository
from app.modules.outbox.repository import OutboxRepository
from app.modules.users.models import User


router = APIRouter(prefix="/cart", tags=["cart"])


def _service(session: AsyncSession) -> CartsService:
    return CartsService(
        CartsRepository(session),
        ItemsRepository(session),
        outbox=OutboxRepository(session),
    )


async def _cart_with_lines(session: AsyncSession, cart) -> CartOut:
    repo = CartsRepository(session)
    lines = await repo.list_lines(cart.id)
    return CartOut(
        id=cart.id,
        status=cart.status.value,
        failure_reason=cart.failure_reason,
        lines=[
            CartLineOut(item_id=l.item_id, quantity=l.quantity, unit_price_cents=l.unit_price_cents)
            for l in lines
        ],
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
async def add_item(
    payload: AddItemIn,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CartOut:
    cart, _ = await _service(session).add_item(
        user_id=user.id, item_id=payload.item_id, quantity=payload.quantity
    )
    await session.commit()
    return await _cart_with_lines(session, cart)


@router.delete("/items/{item_id}", response_model=CartOut)
async def remove_item(
    item_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CartOut:
    cart = await _service(session).remove_item(user_id=user.id, item_id=item_id)
    await session.commit()
    return await _cart_with_lines(session, cart)


@router.post("/checkout", response_model=CheckoutOut, status_code=202)
async def checkout(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CheckoutOut:
    result = await _service(session).submit_checkout(user_id=user.id)
    await session.commit()
    return result
```

Changes from previous version: `prefix="/cart"` (was `/carts`), `tags=["cart"]`, removed `/me` and `{cart_id}` from path strings, removed `cart_id: uuid.UUID` positional from `checkout`.

- [ ] **Step 4: Run, expect pass**

```bash
uv run pytest tests/modules/carts/test_router.py -v -m slow
```
Expected: PASS.

Also run full slow suite to confirm no other test hardcodes the old URLs:

```bash
uv run pytest -m slow -v
```
Expected: all 33 pass.

- [ ] **Step 5: Commit**

```bash
git add app/modules/carts/router.py tests/modules/carts/test_router.py
git commit -m "refactor(api): rename /carts/me/* to /cart/* and drop unused cart_id"
```

---

## Task 2: Add `cancelled` to CartStatus enum

**Files:**
- Modify: `app/modules/carts/models.py` (add enum value)
- Create: `migrations/versions/0007_add_cancelled_status.py`
- Create: `tests/modules/carts/test_cancelled_enum.py`

- [ ] **Step 1: Write the failing test**

`tests/modules/carts/test_cancelled_enum.py`:

```python
import pytest

from app.modules.carts.models import CartStatus


def test_cancelled_enum_value_exists():
    assert CartStatus.cancelled.value == "cancelled"
    assert "cancelled" in {s.value for s in CartStatus}
```

- [ ] **Step 2: Run, expect fail**

```bash
uv run pytest tests/modules/carts/test_cancelled_enum.py -v
```
Expected: FAIL — `AttributeError: cancelled`.

- [ ] **Step 3: Add enum value**

In `app/modules/carts/models.py`, update the `CartStatus` class:

```python
class CartStatus(str, enum.Enum):
    open = "open"
    submitted = "submitted"
    ordered = "ordered"
    failed = "failed"
    cancelled = "cancelled"
```

Create `migrations/versions/0007_add_cancelled_status.py`:

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
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE cart_status ADD VALUE IF NOT EXISTS 'cancelled'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values directly.
    pass
```

- [ ] **Step 4: Run, expect pass**

```bash
uv run pytest tests/modules/carts/test_cancelled_enum.py -v
```
Expected: PASS.

Also verify the full non-slow suite (lints the enum into existing models):

```bash
uv run pytest -m "not slow" -v
```
Expected: 14 + 1 = 15 pass.

- [ ] **Step 5: Commit**

```bash
git add app/modules/carts/models.py migrations/versions/0007_add_cancelled_status.py tests/modules/carts/test_cancelled_enum.py
git commit -m "feat(carts): add 'cancelled' status enum value"
```

---

## Task 3: POST /cart/cancel

**Files:**
- Modify: `app/modules/carts/repository.py` (add `cancel_open`)
- Modify: `app/modules/carts/service.py` (add `cancel_my_open_cart`)
- Modify: `app/modules/carts/router.py` (add `POST /cart/cancel`)
- Modify: `tests/modules/carts/test_service.py` (add 2 tests)
- Modify: `tests/modules/carts/test_router.py` (add 1 test)

- [ ] **Step 1: Write failing service tests**

Append to `tests/modules/carts/test_service.py`:

```python
async def test_cancel_open_cart(db_session):
    from app.modules.carts.models import Cart, CartStatus
    from sqlalchemy import select

    u = await UsersRepository(db_session).create(email="ca@example.com", hashed_password="h")
    await db_session.commit()
    svc = CartsService(CartsRepository(db_session), ItemsRepository(db_session))
    cart = await svc.open_or_get(u.id)
    await db_session.commit()

    cancelled_id = await svc.cancel_my_open_cart(user_id=u.id)
    await db_session.commit()
    assert cancelled_id == cart.id

    row = (await db_session.execute(select(Cart).where(Cart.id == cart.id))).scalar_one()
    assert row.status == CartStatus.cancelled

    # After cancel, partial unique no longer blocks: new open cart can be created.
    new_cart = await svc.open_or_get(u.id)
    await db_session.commit()
    assert new_cart.id != cart.id
    assert new_cart.status == CartStatus.open


async def test_cancel_returns_404_when_no_open(db_session):
    from app.core.exceptions import NotFoundError

    u = await UsersRepository(db_session).create(email="cnone@example.com", hashed_password="h")
    await db_session.commit()
    svc = CartsService(CartsRepository(db_session), ItemsRepository(db_session))

    with pytest.raises(NotFoundError):
        await svc.cancel_my_open_cart(user_id=u.id)
```

- [ ] **Step 2: Run, expect fail**

```bash
uv run pytest tests/modules/carts/test_service.py::test_cancel_open_cart tests/modules/carts/test_service.py::test_cancel_returns_404_when_no_open -v -m slow
```
Expected: FAIL — `AttributeError: 'CartsRepository' object has no attribute 'cancel_open'`.

- [ ] **Step 3: Implement repository, service, router**

Append to `app/modules/carts/repository.py`:

```python
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

Append to `app/modules/carts/service.py`:

```python
    async def cancel_my_open_cart(self, *, user_id: uuid.UUID) -> uuid.UUID:
        cart_id = await self.carts.cancel_open(user_id)
        if cart_id is None:
            raise NotFoundError(
                "No open cart to cancel",
                details={"user_id": str(user_id)},
            )
        return cart_id
```

Append to `app/modules/carts/router.py` (right after the `checkout` handler):

```python
@router.post("/cancel")
async def cancel(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    cart_id = await _service(session).cancel_my_open_cart(user_id=user.id)
    await session.commit()
    return {"status": "cancelled", "cart_id": str(cart_id)}
```

- [ ] **Step 4: Run service tests, expect pass**

```bash
uv run pytest tests/modules/carts/test_service.py::test_cancel_open_cart tests/modules/carts/test_service.py::test_cancel_returns_404_when_no_open -v -m slow
```
Expected: PASS.

- [ ] **Step 5: Add router integration test**

Append to `tests/modules/carts/test_router.py`:

```python
async def test_cancel_then_new_cart_flow(app_with_db, db_session):
    from app.modules.items.repository import ItemsRepository
    item = await ItemsRepository(db_session).create(name="T", price_cents=100, currency="JPY")
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        await c.post("/auth/register", json={"email": "cancel@example.com", "password": "pw"})
        r = await c.post("/auth/login", json={"email": "cancel@example.com", "password": "pw"})
        h = {"Authorization": f"Bearer {r.json()['access_token']}"}

        await c.post("/cart/items", json={"item_id": str(item.id), "quantity": 1}, headers=h)
        r = await c.post("/cart/cancel", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "cancelled"

        # GET /cart should now return a fresh open cart with no lines
        r = await c.get("/cart", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "open"
        assert body["lines"] == []

        # Second cancel without first having added items still works (still has the open cart)
        r = await c.post("/cart/cancel", headers=h)
        assert r.status_code == 200
```

- [ ] **Step 6: Run router test, expect pass**

```bash
uv run pytest tests/modules/carts/test_router.py::test_cancel_then_new_cart_flow -v -m slow
```
Expected: PASS.

Full slow suite:

```bash
uv run pytest -m slow -v
```
Expected: 33 + 3 = 36 pass.

- [ ] **Step 7: Commit**

```bash
git add app/modules/carts/repository.py app/modules/carts/service.py app/modules/carts/router.py tests/modules/carts/test_service.py tests/modules/carts/test_router.py
git commit -m "feat(carts): POST /cart/cancel transitions open → cancelled"
```

---

## Task 4: POST /cart/reopen

**Files:**
- Modify: `app/core/exceptions.py` (add `OpenCartAlreadyExistsError`)
- Modify: `app/modules/carts/repository.py` (add `reopen_failed_timeout`)
- Modify: `app/modules/carts/service.py` (add `reopen_my_cart`)
- Modify: `app/modules/carts/router.py` (add `POST /cart/reopen`)
- Modify: `tests/modules/carts/test_service.py` (4 tests)
- Modify: `tests/modules/carts/test_router.py` (1 integration test)
- Modify: `tests/workers/test_checkout_sweeper.py` (1 test)

- [ ] **Step 1: Add `OpenCartAlreadyExistsError`**

Append to `app/core/exceptions.py`:

```python
class OpenCartAlreadyExistsError(ConflictError):
    code = "open_cart_already_exists"
```

- [ ] **Step 2: Write failing service tests**

Append to `tests/modules/carts/test_service.py`:

```python
async def test_reopen_restores_failed_timeout_cart(db_session):
    from app.modules.outbox.repository import OutboxRepository
    from app.modules.carts.models import Cart, CartStatus
    from sqlalchemy import select, update as sql_update

    u = await UsersRepository(db_session).create(email="re@example.com", hashed_password="h")
    item = await ItemsRepository(db_session).create(name="X", price_cents=300, currency="JPY")
    await db_session.commit()

    svc = CartsService(
        CartsRepository(db_session),
        ItemsRepository(db_session),
        outbox=OutboxRepository(db_session),
    )
    await svc.add_item(user_id=u.id, item_id=item.id, quantity=2)
    sub = await svc.submit_checkout(user_id=u.id)
    await db_session.commit()
    crid = sub.checkout_request_id

    # Force into failed/timeout (simulate sweeper)
    await db_session.execute(
        sql_update(Cart)
        .where(Cart.checkout_request_id == crid)
        .values(status=CartStatus.failed, failure_reason="timeout")
    )
    await db_session.commit()

    cart = await svc.reopen_my_cart(user_id=u.id)
    await db_session.commit()
    assert cart.status == CartStatus.open
    assert cart.failure_reason is None
    assert cart.submitted_at is None
    assert cart.checkout_request_id is None
    assert cart.order_id is None

    # Lines and unit_price_cents preserved
    lines = await CartsRepository(db_session).list_lines(cart.id)
    assert len(lines) == 1
    assert lines[0].quantity == 2
    assert lines[0].unit_price_cents == 300


async def test_reopen_returns_404_when_no_reopenable(db_session):
    from app.core.exceptions import NotFoundError

    u = await UsersRepository(db_session).create(email="rn@example.com", hashed_password="h")
    await db_session.commit()
    svc = CartsService(CartsRepository(db_session), ItemsRepository(db_session))

    with pytest.raises(NotFoundError):
        await svc.reopen_my_cart(user_id=u.id)


async def test_reopen_returns_404_when_failure_reason_not_timeout(db_session):
    from app.core.exceptions import NotFoundError
    from app.modules.outbox.repository import OutboxRepository
    from app.modules.carts.models import Cart, CartStatus
    from sqlalchemy import update as sql_update

    u = await UsersRepository(db_session).create(email="rnt@example.com", hashed_password="h")
    item = await ItemsRepository(db_session).create(name="X", price_cents=100, currency="JPY")
    await db_session.commit()

    svc = CartsService(
        CartsRepository(db_session),
        ItemsRepository(db_session),
        outbox=OutboxRepository(db_session),
    )
    await svc.add_item(user_id=u.id, item_id=item.id, quantity=1)
    sub = await svc.submit_checkout(user_id=u.id)
    await db_session.commit()

    await db_session.execute(
        sql_update(Cart)
        .where(Cart.checkout_request_id == sub.checkout_request_id)
        .values(status=CartStatus.failed, failure_reason="out_of_stock")
    )
    await db_session.commit()

    with pytest.raises(NotFoundError):
        await svc.reopen_my_cart(user_id=u.id)


async def test_reopen_raises_409_when_open_cart_already_exists(db_session):
    from app.core.exceptions import OpenCartAlreadyExistsError
    from app.modules.outbox.repository import OutboxRepository
    from app.modules.carts.models import Cart, CartStatus
    from sqlalchemy import insert as sql_insert
    import uuid as _uuid

    u = await UsersRepository(db_session).create(email="re2@example.com", hashed_password="h")
    item = await ItemsRepository(db_session).create(name="X", price_cents=200, currency="JPY")
    await db_session.commit()

    # First cart: submit and force to failed/timeout
    svc = CartsService(
        CartsRepository(db_session),
        ItemsRepository(db_session),
        outbox=OutboxRepository(db_session),
    )
    await svc.add_item(user_id=u.id, item_id=item.id, quantity=1)
    sub = await svc.submit_checkout(user_id=u.id)
    await db_session.commit()
    from sqlalchemy import update as sql_update
    await db_session.execute(
        sql_update(Cart)
        .where(Cart.checkout_request_id == sub.checkout_request_id)
        .values(status=CartStatus.failed, failure_reason="timeout")
    )
    await db_session.commit()

    # Second cart: create a fresh open cart for the same user
    await svc.open_or_get(u.id)
    await db_session.commit()

    with pytest.raises(OpenCartAlreadyExistsError):
        await svc.reopen_my_cart(user_id=u.id)
```

- [ ] **Step 3: Run, expect fail**

```bash
uv run pytest tests/modules/carts/test_service.py -k "reopen" -v -m slow
```
Expected: FAIL — `AttributeError: 'CartsService' object has no attribute 'reopen_my_cart'`.

- [ ] **Step 4: Implement repository**

Append to `app/modules/carts/repository.py` (imports section needs `from sqlalchemy.exc import IntegrityError` and `from app.core.exceptions import OpenCartAlreadyExistsError`):

```python
    async def reopen_failed_timeout(self, user_id: uuid.UUID) -> int:
        try:
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
        except IntegrityError as e:
            raise OpenCartAlreadyExistsError(
                "Open cart already exists; cancel it before reopen",
                details={"user_id": str(user_id)},
            ) from e
```

At the top of `app/modules/carts/repository.py` add:

```python
from sqlalchemy.exc import IntegrityError
from app.core.exceptions import OpenCartAlreadyExistsError
```

- [ ] **Step 5: Implement service**

Append to `app/modules/carts/service.py`:

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
```

- [ ] **Step 6: Run service tests, expect pass**

```bash
uv run pytest tests/modules/carts/test_service.py -k "reopen" -v -m slow
```
Expected: 4 PASS.

- [ ] **Step 7: Implement router**

Append to `app/modules/carts/router.py` (right after the `cancel` handler):

```python
@router.post("/reopen", response_model=CartOut)
async def reopen(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CartOut:
    cart = await _service(session).reopen_my_cart(user_id=user.id)
    await session.commit()
    return await _cart_with_lines(session, cart)
```

- [ ] **Step 8: Add router integration test**

Append to `tests/modules/carts/test_router.py`:

```python
async def test_reopen_and_resubmit_flow(app_with_db, db_session):
    from app.modules.items.repository import ItemsRepository
    from app.modules.carts.models import Cart, CartStatus
    from sqlalchemy import update as sql_update

    item = await ItemsRepository(db_session).create(name="T", price_cents=500, currency="JPY")
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        await c.post("/auth/register", json={"email": "reopen@example.com", "password": "pw"})
        r = await c.post("/auth/login", json={"email": "reopen@example.com", "password": "pw"})
        h = {"Authorization": f"Bearer {r.json()['access_token']}"}

        # Setup: add item, checkout
        await c.post("/cart/items", json={"item_id": str(item.id), "quantity": 3}, headers=h)
        r = await c.post("/cart/checkout", headers=h)
        assert r.status_code == 202
        first_crid = r.json()["checkout_request_id"]

        # Simulate timeout: directly mutate the DB
        await db_session.execute(
            sql_update(Cart)
            .where(Cart.checkout_request_id == first_crid)
            .values(status=CartStatus.failed, failure_reason="timeout")
        )
        await db_session.commit()

        # Reopen
        r = await c.post("/cart/reopen", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "open"
        assert body["failure_reason"] is None
        assert body["lines"][0]["quantity"] == 3
        assert body["lines"][0]["unit_price_cents"] == 500

        # Re-checkout yields a new checkout_request_id
        r = await c.post("/cart/checkout", headers=h)
        assert r.status_code == 202
        assert r.json()["checkout_request_id"] != first_crid
```

- [ ] **Step 9: Add sweeper compatibility test**

Append to `tests/workers/test_checkout_sweeper.py`:

```python
async def test_reopened_cart_is_not_swept_again(db_session):
    from app.modules.carts.service import CartsService
    from app.modules.outbox.repository import OutboxRepository

    crid = await _setup_submitted(db_session, age_hours=30)
    count = await sweep_once(db_session, timeout_hours=24)
    await db_session.commit()
    assert count == 1

    # Reopen
    cart = (
        await db_session.execute(select(Cart).where(Cart.checkout_request_id == crid))
    ).scalar_one()
    svc = CartsService(
        CartsRepository(db_session), ItemsRepository(db_session), outbox=OutboxRepository(db_session)
    )
    await svc.reopen_my_cart(user_id=cart.user_id)
    await db_session.commit()

    # Subsequent sweep finds 0 carts to time out
    count2 = await sweep_once(db_session, timeout_hours=24)
    await db_session.commit()
    assert count2 == 0
```

- [ ] **Step 10: Run all new tests, expect pass**

```bash
uv run pytest tests/modules/carts/ tests/workers/test_checkout_sweeper.py -v -m slow
```
Expected: PASS for all new + existing.

Full slow suite:

```bash
uv run pytest -m slow -v
```
Expected: 36 + 6 = 42 pass.

- [ ] **Step 11: Commit**

```bash
git add app/core/exceptions.py app/modules/carts/repository.py app/modules/carts/service.py app/modules/carts/router.py tests/modules/carts/test_service.py tests/modules/carts/test_router.py tests/workers/test_checkout_sweeper.py
git commit -m "feat(carts): POST /cart/reopen restores failed(timeout) → open"
```

---

## Task 5: Migrate @app.on_event to lifespan

**Files:**
- Modify: `app/main.py` (replace startup/shutdown with `lifespan`)

- [ ] **Step 1: Verify current tests pass (baseline)**

```bash
uv run pytest -m "not slow" -v
```
Expected: 15 pass (Task 2 added 1).

```bash
uv run pytest tests/test_app_factory.py -v
```
Expected: 2 pass.

- [ ] **Step 2: Rewrite `app/main.py`**

Replace `app/main.py` entirely with:

```python
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.exceptions import AppError
from app.db.session import dispose_engine, init_engine
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
                code=exc.code,
                message=exc.message,
                details=exc.details,
                trace_id=None,
            ),
        )

    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(items_router)
    app.include_router(cart_router)
    return app


app = create_app()
```

Changes from previous: removed both `@app.on_event` blocks, added `lifespan` context manager, passed `lifespan=lifespan` to `FastAPI(...)`. The `cart_router` import replaces what would have been `carts_router` (Task 1 already renamed the router file's exported `router` to the new prefix; this import is consistent).

- [ ] **Step 3: Run non-slow tests, expect pass (no deprecation warning)**

```bash
uv run pytest tests/test_app_factory.py -v -W error::DeprecationWarning
```
Expected: 2 pass, no DeprecationWarning about `on_event`.

```bash
uv run pytest -m "not slow" -v
```
Expected: 15 pass.

- [ ] **Step 4: Run full slow suite (lifespan must not break integration tests)**

```bash
uv run pytest -m slow -v
```
Expected: 42 pass (Task 4 added 6 to baseline 36).

- [ ] **Step 5: Commit**

```bash
git add app/main.py
git commit -m "refactor(api): migrate @app.on_event to FastAPI lifespan"
```

---

## Task 6: CHANGELOG entry for 0.2.0

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Update CHANGELOG**

Prepend a new entry to `CHANGELOG.md` above the existing `## 0.1.0 (initial)` heading:

```markdown
## 0.2.0

- Add `POST /cart/reopen` to restore `failed(reason='timeout')` carts back to `open` with line items and price snapshots preserved
- Add `POST /cart/cancel` to transition `open` carts to a new terminal `cancelled` state
- Rename `/carts/me/*` endpoints to `/cart/*` (singular) for REST hygiene; drop unused `cart_id` path parameter from checkout
- Migrate FastAPI startup/shutdown handlers from `@app.on_event` to the `lifespan` context manager

## 0.1.0 (initial)
```

(Keep the existing 0.1.0 section verbatim below.)

- [ ] **Step 2: Verify file looks right**

```bash
head -20 CHANGELOG.md
```

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: CHANGELOG entry for 0.2.0"
```

---

## Final Verification

After all 6 tasks:

```bash
git log --oneline | head -10
# Expected: 6 new commits + spec commit + Phase 9 (PR #1) commits
```

```bash
uv run pytest -m "not slow" -v
# Expected: 15 pass
```

```bash
uv run pytest -m slow -v
# Expected: 42 pass
```

```bash
uv run ruff check . && uv run mypy app
# Expected: no errors (or document any pre-existing ones)
```

Total: 6 new commits, 8 new tests added, 47 + 7 - 0 = 54 new tests at conclusion (matching spec §9.6 estimate of ~7 tests added).

## Notes for the executor

- Branch base: `feature/cart-reopen-cancel-lifespan` is already created off `feature/ec-api-impl` at `71c2bbb`. Don't rebase onto main yet — PR #1 (`feature/ec-api-impl` → main) is still in flight.
- Worktree: `/Users/takuma/cross/ec/.worktrees/cart-reopen-cancel-lifespan`. All `git`/`uv`/`pytest` commands assume CWD is this directory.
- Docker must be running for `-m slow` tests (Testcontainers).
- The `_setup_submitted` helper in `tests/workers/test_checkout_sweeper.py` already exists from PR #1 — reuse it for Task 4 Step 9.
- The `UsersRepository`, `ItemsRepository`, `CartsRepository`, `CartsService`, and `OutboxRepository` imports at the top of `tests/modules/carts/test_service.py` already exist from PR #1.
- Tasks must run in order (Task 2's enum must exist before Task 3 references `CartStatus.cancelled`, etc.).
