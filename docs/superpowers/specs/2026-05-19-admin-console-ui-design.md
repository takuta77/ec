# Admin Console UI 設計 (React + Vite SPA)

**Date:** 2026-05-19
**Status:** Draft → ユーザレビュー待ち
**Scope:** EC API の `/admin/*` JSON API (C-4, PR #24 マージ済) を消費する React + Vite 製の管理コンソール SPA。

**実装フェーズ分割 (重要):**
- **Phase 1 (本 spec / 本 PR のスコープ)** — バックエンド側の SPA 配信インフラのみ。`Settings.serve_frontend` フラグ + `app/main.py` の StaticFiles マウント & SPA フォールバック。`frontend/` がまだ存在しなくても安全に動く。
- **Phase 2 (別タスク / 別 spec で後日)** — `frontend/` 配下の React + Vite 実装一式。本 spec は Phase 2 の設計も記録するが、実装は分離する。

---

## 1. 目的と背景

C-4 (`docs/superpowers/specs/2026-05-15-admin-read-api-design.md`, PR #24) で `/admin/*` の read-only JSON API を整備済み。運用者がブラウザから閲覧する UI を、React + Vite の SPA として提供する。

ユーザ指示により実装を 2 フェーズに分割する: 本 PR ではバックエンドが将来の SPA ビルド成果物を配信できる土台だけを作り、React 実装は別タスクに切り出す。これにより `app/main.py` への変更を React 作業から隔離し、フロント着手時に backend を触らずに済む。

## 2. ゴール / 非ゴール

### Phase 1 ゴール (本 PR)
- `Settings.serve_frontend: bool = False` 設定追加
- `app/main.py`: `serve_frontend=True` かつ `frontend/dist` 存在時のみ、SPA を `/admin/ui` 配下で配信 (StaticFiles + SPA フォールバック)
- `frontend/dist` が存在しない (= React 未実装) 状態でもアプリ起動・既存テストが壊れない
- `/admin/ui/*` の未知パスは `index.html` を返す (client-side routing 用)。`/admin/*`・`/auth/*` 等の API ルートは従来通り JSON
- slow テストで「serve_frontend=True + ダミー dist → `/admin/ui/x` が index.html、API は JSON のまま」を検証
- 既存 auth / admin API / 他モジュールを一切変更しない

### Phase 2 ゴール (別タスク、本 spec §8 に設計記録)
- `frontend/` 配下に React 18 + TS + Vite SPA を実装
- ページ: login / dashboard / carts / dlq
- CI に frontend ジョブ追加、dependabot npm 追加

### 非ゴール (両フェーズ共通、§9 で trace)
- httpOnly cookie 認証 (backend 改修必要)
- DLQ redrive/drain 等 write 操作 UI
- ページネーション / 検索 / CSS framework / ダークモード / i18n / リアルタイム更新 / 監査ログ画面
- E2E (Playwright)
- 認証バックエンドの改修 (既存 `/auth/login`,`/auth/refresh`,`/auth/logout` をそのまま使う)

## 3. 認証モデル (Phase 2 で利用、backend 改修不要なことの確認)

確認済み: `app/modules/auth/router.py` に `POST /auth/login` (TokenPair 返却), `POST /auth/refresh` (TokenPair), `POST /auth/logout`, `POST /auth/register` が既存。`/admin/*` は `require_admin` が `Authorization: Bearer` ヘッダから JWT を読む。

→ **Phase 2 の SPA は既存エンドポイントをそのまま使えば良く、backend 認証コードの改修はゼロ**。SPA 側のトークン保持方針 (access=メモリ / refresh=sessionStorage) は §8 に記載。httpOnly cookie 化は backend 改修を伴うため非ゴール (§9 follow-up)。

本 Phase 1 では認証コードに一切触れない。

## 4. Phase 1 アーキテクチャ (backend SPA 配信)

```
本番 (serve_frontend=True, frontend/dist あり):
  GET /admin/stats/items   → 既存 admin API (JSON, require_admin)
  GET /admin/ui            → frontend/dist/index.html
  GET /admin/ui/carts      → frontend/dist/index.html  (SPA fallback, React Router が処理)
  GET /admin/ui/assets/x   → frontend/dist/assets/x    (StaticFiles)

dev / テスト (serve_frontend=False もしくは dist 無し):
  /admin/ui* ルートは登録されない (API のみ稼働、Vite が UI 配信)
```

### 配信パス設計

- SPA ベースパス = `/admin/ui` (JSON API prefix `/admin/*` と名前空間が衝突しない: `/admin/stats/*`,`/admin/carts`,`/admin/dlq/*` は具体パス、`/admin/ui` は別サブツリー)
- 静的アセット: `/admin/ui/assets/*` → `frontend/dist/assets/*`
- SPA フォールバック: `GET /admin/ui` および `GET /admin/ui/{rest:path}` で、対応する静的ファイルが無ければ `frontend/dist/index.html` を返す
- React Router の `basename` は Phase 2 で `/admin/ui` に設定

### Settings

`app/core/config.py` の `Settings` に追加:

```python
serve_frontend: bool = False
frontend_dist_path: str = "frontend/dist"
```

env から上書き可能 (`SERVE_FRONTEND=true`)。

### app/main.py 変更

`create_app()` 内、API ルータ include の後に:

```python
if settings.serve_frontend:
    dist = Path(settings.frontend_dist_path)
    if dist.is_dir():
        app.mount(
            "/admin/ui/assets",
            StaticFiles(directory=dist / "assets"),
            name="admin-ui-assets",
        )

        @app.get("/admin/ui", include_in_schema=False)
        @app.get("/admin/ui/{rest:path}", include_in_schema=False)
        async def _spa_fallback(rest: str = "") -> FileResponse:
            return FileResponse(dist / "index.html")
```

- `settings` は `create_app` 内で `Settings()` を取得 (既存パターンに合わせる。既に取得していれば再利用)
- `dist.is_dir()` ガードにより、`serve_frontend=True` でも `frontend/dist` 不在なら何もしない (React 未実装期間でも安全)
- `include_in_schema=False` で OpenAPI に出さない
- `/admin/ui/assets` の StaticFiles マウントが先、フォールバックの catch-all が後 (ルート評価順。FastAPI は mount を優先解決)
- API ルート (`/admin/stats/*` 等) は catch-all `/admin/ui/{rest:path}` と prefix が異なるため影響なし

依存: `from pathlib import Path`, `from fastapi.staticfiles import StaticFiles`, `from fastapi.responses import FileResponse` を `app/main.py` に追加。`StaticFiles` は `starlette` 経由で fastapi に含まれる (追加依存なし)。

## 5. Phase 1 ファイル変更

```
app/core/config.py             # serve_frontend, frontend_dist_path 追加
app/main.py                    # SPA 配信 (serve_frontend ガード)
tests/test_spa_serving.py      # 新規 — slow テスト
```

`frontend/` ディレクトリは Phase 1 では作らない (Phase 2)。

## 6. Phase 1 エラーハンドリング / エッジケース

| 状況 | 動作 |
|---|---|
| `serve_frontend=False` (デフォルト) | `/admin/ui*` ルート未登録。`GET /admin/ui` は 404 (FastAPI 標準) |
| `serve_frontend=True` だが `frontend/dist` 不在 | ルート未登録。404。アプリ起動は正常 |
| `serve_frontend=True` + dist あり、`GET /admin/ui/carts` | `index.html` (200) |
| `GET /admin/ui/assets/main.js` (存在) | StaticFiles が配信 |
| `GET /admin/ui/assets/missing.js` (不在) | StaticFiles の 404 (フォールバックには流さない。assets は実ファイル前提) |
| `GET /admin/stats/items` | 従来通り admin API (catch-all に食われない、prefix 不一致) |

## 7. Phase 1 テスト戦略

`tests/test_spa_serving.py` (slow 不要、`app_with_db` も不要 — `create_app` を直接構築できるなら unit。ただし Settings 依存があるため、環境変数で `serve_frontend` を切り替える形にする):

1. **serve_frontend=False**: `create_app()` → `GET /admin/ui` が 404
2. **serve_frontend=True + dist 無し**: `GET /admin/ui` が 404、アプリ生成は成功
3. **serve_frontend=True + ダミー dist**: 一時ディレクトリに `index.html` + `assets/app.js` を作り `frontend_dist_path` をそこに向ける →
   - `GET /admin/ui` → 200, body に index.html の内容
   - `GET /admin/ui/carts` → 200, 同じ index.html (SPA fallback)
   - `GET /admin/ui/assets/app.js` → 200, app.js の内容
   - `GET /admin/stats/items` → API に届く (認証無しなら 401、SPA fallback されない)

Settings をテストで差し替える方法: `Settings` は pydantic-settings なので、テスト内で環境変数 (`SERVE_FRONTEND`, `FRONTEND_DIST_PATH`) を `monkeypatch.setenv` してから `create_app()` を呼ぶ。`create_app` が `Settings()` を内部生成する前提。もし `create_app` がグローバル settings をキャッシュしている場合は、テストで明示的に再生成できるよう最小リファクタ (関数引数 `settings: Settings | None = None`) を許容 — ただし既存呼び出し互換を壊さないこと。

## 8. Phase 2 設計記録 (別タスク、本 PR では実装しない)

将来の React 実装タスクのための設計メモ。実装時はこの §8 を別 spec に切り出すか、本 spec を参照する。

- **スタック**: React 18 + TypeScript + Vite、React Router v6、薄い fetch ラッパー (TanStack Query 不使用)、プレーン CSS、Biome (lint+format)、Vitest、npm
- **ディレクトリ**: `frontend/` (repo ルート直下)。`src/{api,auth,pages,components}`、`vite.config.ts` に dev proxy (`/admin`,`/auth` → `localhost:8000`)
- **認証**: 既存 `/auth/login` → access=メモリ / refresh=sessionStorage。401 で `/auth/refresh` リトライ。`/admin/*` に Bearer 付与。React Router basename=`/admin/ui`
- **ページ**: `/admin/ui/login`, `/admin/ui` (dashboard 4 stat カード), `/admin/ui/carts` (status フィルタ), `/admin/ui/dlq` (queue 選択 peek)
- **ビルド**: `npm run build` → `frontend/dist/`。本番は Phase 1 の配信機構が拾う
- **CI**: `ci.yml` に `frontend` ジョブ (npm ci → biome ci → tsc --noEmit → vitest run → build)。`dependabot.yml` に npm ecosystem (`/frontend`)
- **テスト**: Vitest + Testing Library + MSW (API モック)、AuthContext / api client / 各 page

## 9. オープン項目 (将来検討)

### 直近で必要になりそう
- **Phase 2: React 実装本体** (別タスク化、ユーザ指示)
- **httpOnly cookie 認証** — XSS 耐性強化、backend 改修必要
- **DLQ redrive/drain UI / カート強制遷移 UI** — write 操作、CSRF トークンとセット
- **ページネーション UI**

### 将来検討
- 検索ボックス / CSS framework / ダークモード / i18n / リアルタイム更新 (SSE) / 監査ログ画面 / E2E (Playwright) / admin user 招待 UI / セッション管理画面

## 10. ロールアウト

1. **Phase 1 PR (本 PR)**: config + main.py SPA 配信 + テスト。`serve_frontend` デフォルト false なので本番挙動に影響なし
2. CI green、main マージ
3. **Phase 2 タスク**: 別 spec/plan で React 実装 → `frontend/dist` 生成 → 本番で `SERVE_FRONTEND=true` 有効化
