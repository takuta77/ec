# Item Search + Category 設計

**Date:** 2026-05-14
**Status:** Draft → ユーザレビュー待ち
**Scope:** EC API の商品一覧 `/items` にテキスト検索 (`q`) とカテゴリフィルタ (`category`) を追加し、利用中の category 一覧を返す `/items/categories` を新設する。

---

## 1. 目的と背景

現状の `GET /items` は `is_active=true` の商品を `created_at DESC` で limit/offset 返却するのみで、購入導線として最低限の体裁しかない。EC として:

- 商品名・説明での部分一致検索
- カテゴリでの絞り込み

がない状態では、顧客が商品を見つける手段が「全件スクロール」しかなく、カタログが増えるほど実用性が落ちる。spec `2026-05-12-ec-api-design.md` §13 のオープン項目「商品検索 / カテゴリエンドポイント」の最小実装版として、ここに着手する。

カテゴリ階層 (Electronics > Phones > ...) や全文検索 (形態素・スコアリング) は YAGNI として後回し。今回はフラットな単一文字列カテゴリ + Postgres `ILIKE` の組み合わせで十分な検索体験を提供する。

## 2. ゴール / 非ゴール

### ゴール
- `GET /items?q=<text>` で `name` または `description` の部分一致を OR 検索
- `GET /items?category=<str>` でカテゴリ完全一致フィルタ
- `q` と `category` の AND 組み合わせ
- 既存の `limit` / `offset` / `is_active=true` フィルタは維持
- `GET /items/categories` で現在使われている distinct な category を昇順で返す
- `Item` モデルに `category: str | None` (max 50 chars) を追加するマイグレーション
- `ItemCreate` / `ItemOut` を `category` 対応に拡張
- `q` 中の SQL ワイルドカード文字 (`%`, `_`) は literal 扱い (エスケープ)

### 非ゴール
- カテゴリ階層 (parent/child) や複数カテゴリ (M:N)
- 全文検索 (tsvector / pg_trgm / Elasticsearch)
- ソート切替 (現状は `created_at DESC` 固定のまま)
- 検索結果の relevance ranking
- 検索クエリのサジェスト / オートコンプリート
- カテゴリ管理用の admin API (`POST /categories` 等)
- フロントエンド UI

## 3. データモデル変更

### `Item` モデル

`app/modules/items/models.py` に列追加:

```python
category: Mapped[str | None] = mapped_column(String(50), nullable=True)
```

### マイグレーション

`migrations/versions/0008_items_add_category.py`:

```python
def upgrade() -> None:
    op.add_column("items", sa.Column("category", sa.String(50), nullable=True))
    op.create_index("ix_items_category", "items", ["category"])

def downgrade() -> None:
    op.drop_index("ix_items_category", table_name="items")
    op.drop_column("items", "category")
```

B-tree 単独 index を category に張る (完全一致フィルタ用)。`name` / `description` への ILIKE は index 無しで線形スキャンになるが、データ規模が小さい今は許容。後で pg_trgm GIN index へ拡張する余地は残す。

## 4. API

### `GET /items` (拡張)

```
GET /items?q=<text>&category=<str>&limit=20&offset=0
```

| Query | 型 | 既存/新規 | 仕様 |
|---|---|---|---|
| `q` | `str?` | 新規 | 任意。1〜100 文字。`ILIKE '%q%'` を `name` または `description` に適用 (case-insensitive、部分一致)。`%`/`_` は escape して literal 扱い |
| `category` | `str?` | 新規 | 任意。1〜50 文字。完全一致 (case-sensitive、空白除去後) |
| `limit` | `int` | 既存 | 1〜100、default 20 |
| `offset` | `int` | 既存 | ≥ 0、default 0 |

レスポンス:
- 200 OK: `list[ItemOut]` (既存と同じスキーマ、`category` フィールド追加)
- 422: query parameter バリデーション失敗

### `GET /items/categories` (新規)

```
GET /items/categories
```

- 認証不要 (既存 `/items` と同じ public)
- 現在の `items.category` (NULL 除外、distinct、ASC) を返す
- レスポンス: `200 OK` `{"categories": ["beverages", "electronics", "snacks"]}`
- アクティブな商品に限らない (`is_active=false` を含む) — admin の用途も想定して全件 distinct

### `GET /items/{item_id}` (変更なし)

レスポンス schema に `category` フィールドが含まれるようになるが、URL / ステータスコード / 認可はそのまま。

## 5. ファイル構成変更

```
migrations/versions/
└── 0008_items_add_category.py        # 新規

app/modules/items/
├── models.py                          # Item.category 列追加
├── schemas.py                         # ItemCreate / ItemOut に category, 新規 CategoryListOut
├── repository.py                      # list_active 拡張、list_categories 新規
├── service.py                         # 同上
└── router.py                          # /items query param 追加、/items/categories ルート

tests/modules/items/
├── test_schemas.py                    # category バリデーション (max length, optional)
└── test_router.py                     # 既存 + 検索/フィルタ/categories の slow テスト追加
```

## 6. 内部設計

### `ItemsRepository`

既存の `list_active` を以下のシグネチャに拡張:

```python
async def list_active(
    self,
    *,
    limit: int,
    offset: int,
    q: str | None = None,
    category: str | None = None,
) -> list[Item]: ...
```

- `q` が与えられたら `Item.name.ilike(pattern)` または `Item.description.ilike(pattern)` を OR 条件で追加
- pattern 構築: `q` 中の `\` / `%` / `_` を `\<char>` にエスケープしてから `f"%{escaped}%"` を作る。SQLAlchemy の `.ilike(..., escape='\\')` を使用
- `category` が与えられたら `Item.category == category.strip()` で AND 追加
- 並び順は既存どおり `created_at DESC`

新規メソッド:

```python
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

### `ItemsService`

`list_active` のシグネチャを repository に合わせて拡張。新規 `list_categories()` も同じく薄いラッパー。

### `router.py`

```python
@router.get("", response_model=list[ItemOut])
async def list_items(
    session: Annotated[AsyncSession, Depends(get_session)],
    q: str | None = Query(default=None, min_length=1, max_length=100),
    category: str | None = Query(default=None, min_length=1, max_length=50),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[Item]:
    return await _service(session).list_active(
        limit=limit, offset=offset, q=q, category=category,
    )


@router.get("/categories")
async def list_categories(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, list[str]]:
    return {"categories": await _service(session).list_categories()}
```

`/categories` を `/{item_id}` より先に登録する (FastAPI のルーティング順)。

### Schemas

```python
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

`CategoryListOut` のような専用 schema は導入せず、`dict[str, list[str]]` の inline 型で十分。後で複数フィールド (count 等) が必要になったら schema 化する。

## 7. エラーハンドリング

- バリデーションエラーは FastAPI の標準 422 (既存 envelope `error_envelope` 経由)
- DB エラー (接続切れ等) は既存の `AppError` ハンドラに乗る
- `q` の特殊文字は service / repository 内で escape するので、ユーザに 4xx を返すことはない
- 空文字 `?q=` / `?category=` は Pydantic Query の `min_length=1` バリデーションで 422 になる。これは「フィルタを掛けたいなら 1 文字以上指定する」明示的 API 契約として意図したもの。フィルタ無しで全件欲しい時はそのキーを送らない

## 8. テスト戦略

### Unit (`tests/modules/items/test_schemas.py` 新規)
- `ItemCreate(category="ok")` で `ok` が保持される
- `ItemCreate(category="")` で `ValidationError`
- `ItemCreate(category="x" * 51)` で `ValidationError`
- `ItemCreate()` で `category` 不在は OK (None)

### Slow (Testcontainers, `tests/modules/items/test_router.py` 拡張)
シナリオ:

1. **`q` ヒット (name)**: name="Apple Juice", description=None → `q=juice` でヒット
2. **`q` ヒット (description)**: name="ABC", description="organic green tea" → `q=green` でヒット
3. **`q` 不一致**: ヒットなし、空配列
4. **`q` のワイルドカードリテラル化**: name="50% off", `q=50%` で 1 件、`q=%` だけでは全件マッチしない (escape 効いている)
5. **`category` フィルタ**: 3 件中 2 件が `category="beverages"` → 該当 2 件のみ
6. **`q + category` の AND**: name="Apple Juice" with category="beverages" のみが返る
7. **`is_active=false` 除外**: 既存挙動が壊れないことを確認
8. **`/items/categories` distinct**: 3 件 (2 つ重複) → distinct で 2 件返却 (NULL カテゴリは除外、ASC ソート)
9. **`/items/categories` 空**: カテゴリ無しの状態で `[]`

既存テスト (`test_open_cart_and_add_remove` 他) は `ItemsRepository.create(...)` を category 引数なしで呼んでいる。これは default で None になるので互換性あり、変更不要。

### マイグレーションテスト
`alembic upgrade head` → `alembic downgrade -1` → `alembic upgrade head` を CI ではなく開発者ローカルで確認。Spec の検証ステップに含めるが自動化はしない (既存 0001〜0007 も同様の運用)。

## 9. 互換性 / マイグレーション運用

- 既存データは `category = NULL` のまま、API 経由で個別に更新する想定 (本 PR では admin update API は含めない)
- `Item.category` は nullable、追加列なので既存 ORM クエリは何も壊れない
- `ItemOut` に `category` が増えるが、JSON のクライアント側は通常 unknown field を許容する設計のはず (本プロジェクトに既存クライアントは無し)

## 10. ロールアウト計画

1. PR: マイグレーション + モデル + schemas + repo/service + router + 新規テストを 1 つにまとめる
2. CI green (lint/type/test-unit/test-slow + security/*) を確認
3. main へマージ、`alembic upgrade head` を本番に適用 (本番デプロイ先未定なので手動運用)
4. 既存商品に category を付ける運用は別途 (admin 更新 API or 一括 SQL)

## 11. オープン項目 (将来検討)

- カテゴリ階層 (`parent_id` 追加 / categories テーブル正規化)
- 複数カテゴリ (M:N)
- 全文検索 (`tsvector` + GIN index、または pg_trgm)
- 検索結果の relevance ranking
- カテゴリの CRUD admin API
- 価格レンジフィルタ (`min_price` / `max_price`)
- ソート切替 (`sort=price_asc|price_desc|created_desc`)
- 検索のサジェスト / オートコンプリート
