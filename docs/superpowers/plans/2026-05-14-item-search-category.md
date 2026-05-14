# Item Search + Category Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add text-search (`q`) and category filtering to `GET /items`, expose `GET /items/categories` for distinct used categories, and add the `Item.category: str | None` column behind a new Alembic migration.

**Architecture:** Pure additive extension of the existing `items` module. New migration (`0008_items_add_category.py`), new column on the `items` table with B-tree index, extended repository / service / router signatures, and integration tests using Testcontainers. No cross-module impact.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async + asyncpg, Alembic, pydantic v2, pytest + Testcontainers (Postgres + RabbitMQ).

---

## Working Branch

Working directory: `/Users/takuma/cross/ec/.worktrees/item-search-category`
Branch: `feature/item-search-category` (off `origin/main` at `9b86f99`).
Spec: `docs/superpowers/specs/2026-05-14-item-search-category-design.md`.

---

## File Structure

```
migrations/versions/
└── 0008_items_add_category.py        # new — add column + index

app/modules/items/
├── models.py                          # modify — add `category` column
├── schemas.py                         # modify — add `category` to ItemCreate / ItemOut
├── repository.py                      # modify — extend list_active, add list_categories
├── service.py                         # modify — extend list_active, add list_categories
└── router.py                          # modify — query params + /categories route

tests/modules/items/                   # new directory if absent
├── __init__.py                        # empty
├── test_schemas.py                    # new — schema validation unit tests
└── test_router.py                     # new — integration tests (slow)
```

---

## Task 1: Migration 0008 + Item model

**Files:**
- Create: `migrations/versions/0008_items_add_category.py`
- Modify: `app/modules/items/models.py`

- [ ] **Step 1: Inspect the latest migration revision id**

```bash
ls migrations/versions/
sed -n 's/^revision = //p; s/^down_revision = //p' migrations/versions/0007_*.py
```

Note the existing `revision` (latest), which becomes our `down_revision`.

- [ ] **Step 2: Create the new migration**

`migrations/versions/0008_items_add_category.py`:

```python
"""add category column to items

Revision ID: 0008_items_add_category
Revises: <copy revision id from 0007 here>
Create Date: 2026-05-14

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_items_add_category"
down_revision = "<copy revision id from 0007 here>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("items", sa.Column("category", sa.String(length=50), nullable=True))
    op.create_index("ix_items_category", "items", ["category"])


def downgrade() -> None:
    op.drop_index("ix_items_category", table_name="items")
    op.drop_column("items", "category")
```

(Replace `<copy revision id from 0007 here>` with the actual id printed in Step 1.)

- [ ] **Step 3: Update the SQLAlchemy model**

Edit `app/modules/items/models.py`. Insert `category` after `description`:

```python
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
```

- [ ] **Step 4: Verify alembic recognizes the migration**

```bash
uv run alembic check
```

Expected: clean or "Model schema differs from database" only because the migration hasn't been applied to a database (that's fine for our offline check).

- [ ] **Step 5: Spin up Testcontainers Postgres briefly to verify upgrade/downgrade round-trip**

Skip if Docker is unavailable; otherwise:

```bash
# Manual: start a throwaway postgres, run upgrade + downgrade + upgrade
docker run --rm -d --name ec-migcheck -e POSTGRES_PASSWORD=p -e POSTGRES_DB=ec -p 55432:5432 postgres:16
sleep 3
DATABASE_URL=postgresql+asyncpg://postgres:p@localhost:55432/ec uv run alembic upgrade head
DATABASE_URL=postgresql+asyncpg://postgres:p@localhost:55432/ec uv run alembic downgrade -1
DATABASE_URL=postgresql+asyncpg://postgres:p@localhost:55432/ec uv run alembic upgrade head
docker rm -f ec-migcheck
```

Expected: each step exits 0 without error.

- [ ] **Step 6: Run ruff/mypy on the changed files**

```bash
uv run ruff check app/modules/items/models.py migrations/versions/0008_items_add_category.py
uv run mypy app/modules/items/models.py
```

Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add migrations/versions/0008_items_add_category.py app/modules/items/models.py
git commit -m "feat(items): add category column + 0008 migration"
```

---

## Task 2: Schemas — add `category` to ItemCreate and ItemOut (TDD)

**Files:**
- Modify: `app/modules/items/schemas.py`
- Create: `tests/modules/items/__init__.py` (empty)
- Create: `tests/modules/items/test_schemas.py`

- [ ] **Step 1: Create empty package marker**

```bash
mkdir -p tests/modules/items
touch tests/modules/items/__init__.py
```

- [ ] **Step 2: Write the failing tests**

`tests/modules/items/test_schemas.py`:

```python
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.modules.items.schemas import ItemCreate


def test_item_create_accepts_category() -> None:
    payload = ItemCreate(name="Tea", price_cents=100, currency="JPY", category="beverages")
    assert payload.category == "beverages"


def test_item_create_category_optional() -> None:
    payload = ItemCreate(name="Tea", price_cents=100, currency="JPY")
    assert payload.category is None


def test_item_create_rejects_empty_category() -> None:
    with pytest.raises(ValidationError):
        ItemCreate(name="Tea", price_cents=100, currency="JPY", category="")


def test_item_create_rejects_overlong_category() -> None:
    with pytest.raises(ValidationError):
        ItemCreate(name="Tea", price_cents=100, currency="JPY", category="x" * 51)
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
uv run pytest tests/modules/items/test_schemas.py -v
```

Expected: 4 FAIL — `ItemCreate.__init__()` got unexpected keyword `category`.

- [ ] **Step 4: Implement the schema changes**

Edit `app/modules/items/schemas.py`:

```python
from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


class ItemCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    price_cents: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    category: str | None = Field(default=None, min_length=1, max_length=50)


class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    description: str | None
    price_cents: int
    currency: str
    is_active: bool
    category: str | None
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/modules/items/test_schemas.py -v
```

Expected: 4 PASS.

- [ ] **Step 6: Run lint + type**

```bash
uv run ruff check tests/modules/items app/modules/items/schemas.py
uv run mypy app/modules/items/schemas.py
```

Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add app/modules/items/schemas.py tests/modules/items/__init__.py tests/modules/items/test_schemas.py
git commit -m "feat(items): add category to ItemCreate/ItemOut + schema tests"
```

---

## Task 3: Repository — extend `list_active`, add `list_categories`

**Files:**
- Modify: `app/modules/items/repository.py`

`list_active` gains optional `q` and `category` parameters. New `list_categories` returns distinct non-null categories asc.

- [ ] **Step 1: Edit `app/modules/items/repository.py`**

Replace the file contents with:

```python
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.items.models import Item


class ItemsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        name: str,
        price_cents: int,
        currency: str,
        description: str | None = None,
        category: str | None = None,
    ) -> Item:
        item = Item(
            name=name,
            description=description,
            price_cents=price_cents,
            currency=currency,
            category=category,
        )
        self.session.add(item)
        await self.session.flush()
        return item

    async def find_by_id(self, item_id: uuid.UUID) -> Item | None:
        return await self.session.get(Item, item_id)

    async def list_active(
        self,
        *,
        limit: int,
        offset: int,
        q: str | None = None,
        category: str | None = None,
    ) -> list[Item]:
        stmt = select(Item).where(Item.is_active.is_(True))
        if q is not None:
            # Escape SQL LIKE wildcards so user input is literal.
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            stmt = stmt.where(
                Item.name.ilike(pattern, escape="\\")
                | Item.description.ilike(pattern, escape="\\")
            )
        if category is not None:
            stmt = stmt.where(Item.category == category.strip())
        stmt = stmt.order_by(Item.created_at.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_categories(self) -> list[str]:
        stmt = (
            select(Item.category)
            .where(Item.category.is_not(None))
            .distinct()
            .order_by(Item.category.asc())
        )
        result = await self.session.execute(stmt)
        return [row[0] for row in result.all()]
```

- [ ] **Step 2: Verify lint + type**

```bash
uv run ruff check app/modules/items/repository.py
uv run mypy app/modules/items/repository.py
```

Expected: clean. If mypy complains about `row[0]` being `Any`, add `from typing import cast` and `cast(str, row[0])`.

- [ ] **Step 3: Run unit tests to confirm nothing else broke**

```bash
uv run pytest -m "not slow"
```

Expected: schema tests + any pre-existing unit tests still pass.

- [ ] **Step 4: Commit**

```bash
git add app/modules/items/repository.py
git commit -m "feat(items): repository supports q/category filter + list_categories"
```

---

## Task 4: Service — thin wrappers

**Files:**
- Modify: `app/modules/items/service.py`

- [ ] **Step 1: Edit `app/modules/items/service.py`**

Replace the file contents with:

```python
from __future__ import annotations

import uuid

from app.core.exceptions import NotFoundError
from app.modules.items.models import Item
from app.modules.items.repository import ItemsRepository


class ItemsService:
    def __init__(self, items: ItemsRepository) -> None:
        self.items = items

    async def create(
        self,
        *,
        name: str,
        price_cents: int,
        currency: str,
        description: str | None = None,
        category: str | None = None,
    ) -> Item:
        return await self.items.create(
            name=name,
            price_cents=price_cents,
            currency=currency,
            description=description,
            category=category,
        )

    async def get(self, item_id: uuid.UUID) -> Item:
        i = await self.items.find_by_id(item_id)
        if i is None or not i.is_active:
            raise NotFoundError("Item not found", details={"item_id": str(item_id)})
        return i

    async def list_active(
        self,
        *,
        limit: int,
        offset: int,
        q: str | None = None,
        category: str | None = None,
    ) -> list[Item]:
        return await self.items.list_active(
            limit=limit, offset=offset, q=q, category=category
        )

    async def list_categories(self) -> list[str]:
        return await self.items.list_categories()
```

- [ ] **Step 2: Verify lint + type**

```bash
uv run ruff check app/modules/items/service.py
uv run mypy app/modules/items/service.py
```

Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add app/modules/items/service.py
git commit -m "feat(items): service wires q/category through to repository"
```

---

## Task 5: Router — query params + `/categories` route

**Files:**
- Modify: `app/modules/items/router.py`

Note: `/categories` route must be registered BEFORE `/{item_id}` to avoid FastAPI matching it as an `item_id` path param.

- [ ] **Step 1: Edit `app/modules/items/router.py`**

Replace the file contents with:

```python
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.items.models import Item
from app.modules.items.repository import ItemsRepository
from app.modules.items.schemas import ItemOut
from app.modules.items.service import ItemsService


router = APIRouter(prefix="/items", tags=["items"])


def _service(session: AsyncSession) -> ItemsService:
    return ItemsService(ItemsRepository(session))


@router.get("", response_model=list[ItemOut])
async def list_items(
    session: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    category: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[Item]:
    return await _service(session).list_active(
        limit=limit, offset=offset, q=q, category=category
    )


@router.get("/categories")
async def list_categories(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, list[str]]:
    return {"categories": await _service(session).list_categories()}


@router.get("/{item_id}", response_model=ItemOut)
async def get_item(
    item_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Item:
    return await _service(session).get(item_id)
```

- [ ] **Step 2: Verify lint + type**

```bash
uv run ruff check app/modules/items/router.py
uv run ruff format --check app/modules/items/router.py
uv run mypy app/modules/items/router.py
```

Expected: clean.

- [ ] **Step 3: Run unit tests**

```bash
uv run pytest -m "not slow"
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add app/modules/items/router.py
git commit -m "feat(items): GET /items q/category params + GET /items/categories"
```

---

## Task 6: Slow integration tests

**Files:**
- Create: `tests/modules/items/test_router.py`

These run against Testcontainers Postgres + RabbitMQ via the existing `app_with_db` and `db_session` fixtures from `tests/conftest.py`. The file goes under `tests/modules/items/` which was created in Task 2 (empty `__init__.py` already present).

- [ ] **Step 1: Create the integration test file**

`tests/modules/items/test_router.py`:

```python
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


pytestmark = [pytest.mark.slow, pytest.mark.asyncio]


async def _seed(db_session, name: str, *, description: str | None = None,
                category: str | None = None, is_active: bool = True,
                price_cents: int = 100, currency: str = "JPY"):
    from app.modules.items.repository import ItemsRepository
    from sqlalchemy import update as sql_update
    from app.modules.items.models import Item

    item = await ItemsRepository(db_session).create(
        name=name, description=description, price_cents=price_cents,
        currency=currency, category=category,
    )
    if not is_active:
        await db_session.execute(
            sql_update(Item).where(Item.id == item.id).values(is_active=False)
        )
    await db_session.commit()
    return item


async def test_q_matches_name(app_with_db, db_session):
    await _seed(db_session, "Apple Juice")
    await _seed(db_session, "Green Tea")
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        r = await c.get("/items", params={"q": "juice"})
    assert r.status_code == 200
    names = [it["name"] for it in r.json()]
    assert names == ["Apple Juice"]


async def test_q_matches_description(app_with_db, db_session):
    await _seed(db_session, "ABC", description="organic green tea blend")
    await _seed(db_session, "XYZ", description="instant coffee")
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        r = await c.get("/items", params={"q": "green"})
    assert r.status_code == 200
    names = [it["name"] for it in r.json()]
    assert names == ["ABC"]


async def test_q_no_match_returns_empty(app_with_db, db_session):
    await _seed(db_session, "Apple Juice")
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        r = await c.get("/items", params={"q": "absolutely-not-present"})
    assert r.status_code == 200
    assert r.json() == []


async def test_q_wildcard_is_literal(app_with_db, db_session):
    # Two items: one literally containing "50%", one not.
    await _seed(db_session, "50% off bundle")
    await _seed(db_session, "Plain bundle")
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        # Literal "50%" should match only the first item.
        r1 = await c.get("/items", params={"q": "50%"})
        # "%" alone should be treated as literal "%", so matches only items
        # whose name/description contains "%". Only the first qualifies.
        r2 = await c.get("/items", params={"q": "%"})
    assert r1.status_code == 200 and [it["name"] for it in r1.json()] == ["50% off bundle"]
    assert r2.status_code == 200 and [it["name"] for it in r2.json()] == ["50% off bundle"]


async def test_category_filter(app_with_db, db_session):
    await _seed(db_session, "Apple Juice", category="beverages")
    await _seed(db_session, "Green Tea", category="beverages")
    await _seed(db_session, "Notebook", category="stationery")
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        r = await c.get("/items", params={"category": "beverages"})
    assert r.status_code == 200
    names = sorted(it["name"] for it in r.json())
    assert names == ["Apple Juice", "Green Tea"]


async def test_q_and_category_combined(app_with_db, db_session):
    await _seed(db_session, "Apple Juice", category="beverages")
    await _seed(db_session, "Apple Pen", category="stationery")
    await _seed(db_session, "Green Tea", category="beverages")
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        r = await c.get("/items", params={"q": "apple", "category": "beverages"})
    assert r.status_code == 200
    names = [it["name"] for it in r.json()]
    assert names == ["Apple Juice"]


async def test_inactive_items_excluded(app_with_db, db_session):
    await _seed(db_session, "Old Item", category="beverages", is_active=False)
    await _seed(db_session, "New Item", category="beverages")
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        r = await c.get("/items", params={"category": "beverages"})
    assert r.status_code == 200
    names = [it["name"] for it in r.json()]
    assert names == ["New Item"]


async def test_categories_distinct_sorted(app_with_db, db_session):
    await _seed(db_session, "A", category="beverages")
    await _seed(db_session, "B", category="beverages")     # duplicate category
    await _seed(db_session, "C", category="stationery")
    await _seed(db_session, "D", category=None)            # null excluded
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        r = await c.get("/items/categories")
    assert r.status_code == 200
    assert r.json() == {"categories": ["beverages", "stationery"]}


async def test_categories_empty(app_with_db, db_session):
    await _seed(db_session, "Uncategorized")  # category=None
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        r = await c.get("/items/categories")
    assert r.status_code == 200
    assert r.json() == {"categories": []}


async def test_empty_q_returns_422(app_with_db, db_session):
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as c:
        r = await c.get("/items", params={"q": ""})
    assert r.status_code == 422
```

- [ ] **Step 2: Run the slow tests**

```bash
uv run pytest tests/modules/items/test_router.py -v -m slow
```

Expected: 10 PASS (each test exercises a different slice of the new behavior).

- [ ] **Step 3: Run ruff/format/mypy on the file**

```bash
uv run ruff check tests/modules/items/test_router.py
uv run ruff format --check tests/modules/items/test_router.py
```

Expected: clean. (Mypy is not configured to check `tests/` per pyproject — only `app/`.)

- [ ] **Step 4: Commit**

```bash
git add tests/modules/items/test_router.py
git commit -m "test(items): integration tests for search/category endpoints"
```

---

## Task 7: Final verification, push, and PR

**Files:**
- None (verification + integration).

- [ ] **Step 1: Run the entire check matrix locally**

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest -m "not slow"
uv run pytest -m slow
```

Expected:
- ruff check: clean
- ruff format --check: clean
- mypy: `Success: no issues found`
- pytest unit: previous count + 4 schema tests
- pytest slow: previous count + 10 router tests

- [ ] **Step 2: Inspect commit history**

```bash
git log --oneline origin/main..HEAD
```

Expected: 6 feature commits + 1 spec commit (from earlier) = 7 commits total.

- [ ] **Step 3: Push the branch (user-driven if harness blocks)**

```bash
git push -u origin feature/item-search-category
```

- [ ] **Step 4: Open the PR**

```bash
gh pr create \
  --base main \
  --head feature/item-search-category \
  --title "Add item search (q) + category filter + GET /items/categories" \
  --body "$(cat <<'EOF'
## Summary

Implements spec \`docs/superpowers/specs/2026-05-14-item-search-category-design.md\` (option A): flat single-string \`Item.category\` + Postgres \`ILIKE\` search.

- \`GET /items?q=<text>&category=<str>\` — q does \`ILIKE\` on name OR description (with %/_ escaping), category does exact-match.
- \`GET /items/categories\` — distinct non-null categories used by current inventory, ASC.
- Alembic migration \`0008_items_add_category\` adds the column + B-tree index.
- \`ItemCreate\` / \`ItemOut\` extended with \`category: str | None\` (max 50).
- Backwards compatible: existing data has \`category = NULL\`; existing API clients are unaffected.

## Test plan

- [x] \`uv run ruff check .\` / \`uv run ruff format --check .\` / \`uv run mypy app\` clean
- [x] \`uv run pytest -m "not slow"\` includes 4 new schema tests
- [x] \`uv run pytest -m slow\` includes 10 new router/integration tests
- [ ] CI green on PR

## Follow-ups (deferred per spec §11)

- Category hierarchy (parent_id / categories table normalization)
- Multi-category (M:N)
- Full-text search (tsvector / pg_trgm)
- Sort switching, price-range filter, suggest/autocomplete
- Admin CRUD for categories
EOF
)"
```

- [ ] **Step 5: Watch CI**

After CI runs, all 7 required checks must be green. If any fail, dispatch fix subagent.

---

## Self-Review Notes

**Spec coverage:**

- §3 model + migration → Task 1
- §4 GET /items extension → Tasks 4 + 5 (service + router)
- §4 GET /items/categories → Tasks 3 + 4 + 5 (repo + service + router)
- §5 file structure → matches plan
- §6 q escape, /categories route ordering → Tasks 3 + 5
- §7 422 on empty q → Task 6 test case `test_empty_q_returns_422`
- §8 test scenarios 1-9 → Task 6 (with 1 extra: empty q 422)
- §11 follow-ups → mentioned in PR body, no task

**Placeholder scan:** No "TBD" or "as needed" content. The migration's `down_revision` is a fill-in-blank from Step 1's command output, which is concrete.

**Type consistency:**

- `q: str | None`, `category: str | None` — consistent across repository, service, router.
- `list_active(*, limit, offset, q, category) -> list[Item]` — same signature at all three layers.
- `list_categories() -> list[str]` — same at repo + service; router wraps in `{"categories": ...}` for JSON.
- `ItemOut.category` matches model column (both `str | None`).
- Migration constants match: `revision = "0008_items_add_category"` used as filename and `revision` field.
