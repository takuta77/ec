# Admin Console UI 設計

**Date:** 2026-05-19
**Status:** Draft → ユーザレビュー待ち
**Scope:** EC API に server-rendered な管理コンソール UI を追加する。C-4 で実装した `/admin/*` JSON API のデータを、運用者がブラウザから閲覧できるようにする。

---

## 1. 目的と背景

`docs/superpowers/specs/2026-05-15-admin-read-api-design.md` (C-4, PR #24 マージ済) で admin read API を整備した。しかし現状その API を叩く手段は `curl` / Swagger UI しかなく、運用者が日常的に状態を確認する UI が無い。

本 spec は C-4 の `AdminService` をそのまま再利用し、Jinja2 + HTMX による軽量な server-rendered コンソールを追加する。SPA・ビルドツール・別デプロイを持ち込まず、既存 FastAPI アプリ内で完結させる (内部運用ツールに対する YAGNI)。

## 2. ゴール / 非ゴール

### ゴール

- `/admin/ui/*` 配下の HTML ページ群 (login / dashboard / carts / dlq / logout)
- httpOnly cookie ベースの認証 (既存 JWT/auth ロジックを再利用、token 輸送路を header → cookie に変えるだけ)
- UI ルートは既存 `AdminService` を直接呼ぶ (自分の JSON API への内部 HTTP 往復をしない)
- HTMX による部分更新 (carts の status フィルタはテーブルフラグメントだけ差し替え)
- HTMX を `app/static/` に vendoring (CDN 非依存、バージョン固定)
- Slow (Testcontainers) + unit テストで認証・各ページ・フィルタを検証
- 既存 JSON API / auth / モジュールを壊さない (追加のみ)

### 非ゴール (§13 オープン項目で trace)

- DLQ redrive/drain の UI ボタン (write 操作)
- カート強制状態遷移の UI
- ページネーション UI (今回は first page 固定、limit/offset は内部デフォルト)
- 検索ボックス (全文)
- CSS フレームワーク / デザインシステム (最小インライン CSS のみ)
- ダークモード / i18n / リアルタイム更新 (WebSocket/SSE)
- 監査ログ表示
- admin user 作成 UI (DB 直接 UPDATE のまま)

## 3. アーキテクチャ

```
Browser ──GET /admin/ui (Cookie: admin_token)──▶ require_admin_cookie
                                                   │ decode JWT (既存ロジック)
                                                   │ load user, check is_admin
                                                   │ fail → 302 /admin/ui/login
                                                   ▼
                                          admin_ui/router.py handler
                                                   │
                                                   ▼
                                          AdminService (C-4, 既存) ──▶ DB / MQ
                                                   │
                                                   ▼
                                          Jinja2 render (full page or HTMX fragment)
                                                   │
                                                   ▼
                                          HTML response
```

UI 層は presentation のみ。集計ロジックは C-4 の `AdminService` / `AdminRepository` をそのまま使う。重複実装ゼロ。

## 4. 認証

### Cookie

- 名前: `admin_token`
- 属性: `HttpOnly`, `SameSite=Lax`, `Path=/admin/ui`, `Secure` は設定で切替 (本番 true / ローカル false。`Settings` に `cookie_secure: bool = False` を追加)
- 値: 既存の access JWT (auth service が発行するものと同一)
- 有効期限: JWT の exp に従う (cookie 自体は session cookie、ブラウザ閉じで消える + JWT exp で実効期限)

### フロー

| Route | 動作 |
|---|---|
| `GET /admin/ui/login` | login.html を描画 (既ログインなら dashboard へ 302) |
| `POST /admin/ui/login` | form `email`,`password` を既存 auth service で検証。成功かつ `is_admin=True` → JWT 発行 → `admin_token` cookie set → `/admin/ui` へ 302。失敗 → login.html を `error` 付きで 200 再描画 |
| `POST /admin/ui/logout` | `admin_token` cookie 削除 → `/admin/ui/login` へ 302 |

`POST /admin/ui/login` の検証:
- email/password が既存ユーザと一致しない → error 「メールアドレスまたはパスワードが正しくありません」
- 一致するが `is_admin=False` → error 「管理者権限がありません」(情報過多にならない範囲で。ユーザ存在は秘匿しなくてよい内部ツール前提)

### `require_admin_cookie` dependency

`app/modules/admin_ui/dependencies.py`:

```python
async def require_admin_cookie(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> User:
    token = request.cookies.get("admin_token")
    if not token:
        raise _RedirectToLogin
    try:
        payload = decode_access_token(token)   # 既存 app/core/security の関数を再利用
    except Exception:
        raise _RedirectToLogin
    user = await UsersRepository(session).find_by_id(uuid.UUID(payload["sub"]))
    if user is None or not user.is_admin:
        raise _RedirectToLogin
    return user
```

`_RedirectToLogin` は `RedirectResponse('/admin/ui/login', status_code=302)` を返すための仕組み。FastAPI の exception handler で `AdminUIRedirect` 例外を 302 に変換する (router 単位の `@router` 例外ハンドラ、または各ハンドラで try/except)。実装は「カスタム例外 `AdminUIAuthRequired` を投げ、`app/main.py` で `@app.exception_handler(AdminUIAuthRequired)` が `RedirectResponse('/admin/ui/login', 302)` を返す」方式とする (既存 `AppError` ハンドラとは別系統、HTML 用)。

既存 `app/core/security.py` の token decode 関数名は実装時に確認 (`decode_access_token` 等)。auth service のパスワード検証関数も同様に再利用する。

## 5. ページ仕様

### `GET /admin/ui` (dashboard)

4 つの stat カードを縦/グリッドで表示:

- **Items**: total / active / by_category (上位カテゴリをリスト)
- **Carts**: status 別カウント (open/submitted/ordered/failed/cancelled) + failed_with_timeout
- **Outbox**: pending / dispatched / oldest_pending_at
- **DLQ**: queue 別 message_count (MQ 不可なら「MQ 接続不可」表示)

データは `AdminService.items_stats()` 等を直接呼ぶ。

### `GET /admin/ui/carts?status=<>`

- status フィルタ dropdown (全 5 status + 「すべて」)
- テーブル: id (短縮表示) / user_id (短縮) / status / failure_reason / line_count / created_at
- dropdown 変更時 HTMX で `GET /admin/ui/carts?status=X` を呼び、`hx-target="#carts-table"` に `_carts_table.html` フラグメントだけ swap
- 件数は `AdminService.list_carts(status=..., limit=50, offset=0)` 固定 (ページネーションは非ゴール)
- `?status=` が HTMX リクエスト (`HX-Request` ヘッダ有) ならフラグメントのみ、通常リクエストならフルページを返す

### `GET /admin/ui/dlq?queue=<>`

- queue 選択 dropdown (`KNOWN_CONSUMER_QUEUES` から生成)
- peek テーブル: event_id / routing_key / death_count / body_preview
- `AdminService.peek_dlq(queue, limit=20, preview_chars=200)` を呼ぶ
- `MQConnectionUnavailable` → 「MQ 接続不可」メッセージ (503 にしない、画面は維持)
- `DLQNotFoundError` → 「キュー <name>.dlq は存在しません (滞留メッセージ無し)」表示

## 6. ファイル構成

```
app/modules/admin_ui/
├── __init__.py
├── dependencies.py            # require_admin_cookie, AdminUIAuthRequired 例外
├── router.py                  # login/logout/dashboard/carts/dlq routes
└── templates/
    ├── base.html              # layout: header + sidebar nav + content block, インライン CSS, htmx script tag
    ├── login.html
    ├── dashboard.html
    ├── carts.html             # フルページ (テーブルは _carts_table を include)
    ├── _carts_table.html      # HTMX フラグメント
    └── dlq.html

app/static/
└── htmx.min.js                # vendored, バージョンをコメントで固定明記

app/main.py                    # admin_ui router include + StaticFiles mount("/static") + Jinja2Templates
pyproject.toml                 # jinja2 を dependencies に追加 (python-multipart は導入済)
```

Jinja2 テンプレートのロード: `fastapi.templating.Jinja2Templates(directory="app/modules/admin_ui/templates")`。`app/main.py` か router モジュールでインスタンス化。

## 7. 依存追加

- `jinja2>=3.1` を `[project] dependencies` に追加 (現状未導入を確認済み)
- `python-multipart` は導入済 (form POST 解析に使用)
- HTMX は JS のみ。`app/static/htmx.min.js` に vendoring (例: htmx 2.x の固定版。ファイル先頭コメントにバージョンと取得元 URL を記載)

## 8. CSS / レイアウト方針

- `base.html` に最小限のインライン `<style>` (sidebar 固定幅 + content、テーブル罫線、stat カード枠)
- 外部 CSS フレームワーク無し (Tailwind/Bootstrap は YAGNI)
- レスポンシブ対応は最小 (デスクトップ運用前提、モバイル最適化は非ゴール)

## 9. エラーハンドリング

| 状況 | 動作 |
|---|---|
| cookie 無し / 不正 JWT / 期限切れ / non-admin | `AdminUIAuthRequired` → 302 `/admin/ui/login` |
| login 資格情報不正 | login.html 200 + error 文言 |
| login で非 admin | login.html 200 + 「管理者権限がありません」 |
| DLQ で MQ 不可 | dlq.html 内に注意表示 (200、画面維持) |
| DLQ で queue 不在 | dlq.html 内に「滞留無し」表示 (200) |
| その他予期せぬ例外 | 既存の `AppError`/FastAPI デフォルトに委譲 (UI 専用整形はしない) |

## 10. セキュリティ考慮

- cookie は `HttpOnly` (JS から読めない、XSS で token 窃取されにくい)
- `SameSite=Lax` (CSRF 緩和。POST フォームは same-origin なので Lax で十分。state 変更操作は login/logout のみ)
- `POST /admin/ui/login` / `logout` は same-origin フォーム。CSRF トークンは **本 spec では導入しない** (内部ツール + SameSite=Lax + 状態変更が auth のみ。将来 write 操作追加時に CSRF トークン導入を §13 に記載)
- `cookie_secure` 設定で本番 HTTPS 時に `Secure` 付与
- テンプレートは Jinja2 オートエスケープ有効 (XSS 防止、`.html` 拡張子で自動 ON)

## 11. テスト戦略

### Unit (`tests/modules/admin_ui/test_dependencies.py`)
- `require_admin_cookie`: cookie 無し → AdminUIAuthRequired
- 不正 JWT → AdminUIAuthRequired
- 有効 JWT だが user.is_admin=False → AdminUIAuthRequired
- 有効 admin → User 返却

### Slow (Testcontainers, `tests/modules/admin_ui/test_router.py`)
1. 未ログインで `GET /admin/ui` → 302 Location `/admin/ui/login`
2. `GET /admin/ui/login` → 200, フォーム HTML 含む
3. `POST /admin/ui/login` 正資格 (admin) → 302 to `/admin/ui` + `admin_token` cookie set (HttpOnly)
4. `POST /admin/ui/login` 誤パスワード → 200 + error 文言
5. `POST /admin/ui/login` 非 admin user → 200 + 「管理者権限がありません」
6. login 後 cookie 付きで `GET /admin/ui` → 200 + stat 数値がレンダリングされている
7. `GET /admin/ui/carts` (cookie 付) → 200, テーブル行が seed したカート数と一致
8. `GET /admin/ui/carts?status=failed` 通常リクエスト → フルページ、failed のみ
9. `GET /admin/ui/carts?status=failed` + `HX-Request: true` ヘッダ → フラグメントのみ (`<html>` 含まない、テーブルのみ)
10. `GET /admin/ui/dlq` (cookie 付、MQ 無し環境) → 200 + 「MQ 接続不可」表示 (503 でない)
11. `POST /admin/ui/logout` → 302 to login + cookie 削除 (Set-Cookie with empty/expired)

### 静的検証
- `jinja2` テンプレートの構文は実行時にレンダリングテストで担保 (テンプレートリンタは導入しない)
- ruff/mypy は Python コード (`router.py`, `dependencies.py`) のみ対象、テンプレートは対象外

## 12. ロールアウト計画

1. PR: jinja2 依存 + admin_ui モジュール + テンプレート + static + テスト
2. CI green (全 7 必須チェック + warn-only)
3. main マージ後、本番で 1 admin ユーザに `is_admin=true` (C-4 で既に手順化済)
4. 運用者が `/admin/ui/login` からアクセス
5. フィードバックを §13 に反映し、write 操作 (DLQ redrive 等) を次 spec で検討

## 13. オープン項目 (将来検討)

### 直近で必要になりそう
- **DLQ redrive/drain の UI ボタン** — write 操作。CSRF トークン導入とセットで別 spec
- **カート強制状態遷移 UI** — 救済操作、慎重に
- **ページネーション UI** — carts/dlq が大量化したら next/prev
- **CSRF トークン** — write 操作追加時に必須化

### 将来検討
- 検索ボックス (全文)
- CSS フレームワーク / デザインシステム
- ダークモード / i18n
- リアルタイム更新 (HTMX polling or SSE)
- 監査ログ表示画面
- admin user 招待フロー UI
- セッション一覧 / 強制ログアウト
