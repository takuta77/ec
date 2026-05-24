# OTel ecosystem coordinated bump (1.37 → 1.42 / 0.58b0 → 0.63b0)

**Date:** 2026-05-22
**Status:** Approved (designing)
**Predecessors:** (none — independent housekeeping)
**Successors:** (none planned)

## 1. 目的

dependabot が分割して開けた 2 件の OTel pip bump PR (`#36` opentelemetry-exporter-otlp → 1.42、`#39` opentelemetry-instrumentation-asyncpg → 0.63b0) は **個別マージ不可能**。OTel エコシステムは「stable 1.X.0」と「contrib 0.(X+21)b0」が同じリリーストレインに乗っており、片方だけ進めると `uv lock` が以下のように unsatisfiable と判定する:

```
opentelemetry-instrumentation-asyncpg>=0.63b0 requires
  opentelemetry-sdk>=1.42.0
but project pins opentelemetry-sdk<1.37.0,>=1.29
```

本 PR は **全 OTel パッケージを 1 トレイン分まとめて進める** こと (= 1.37 → 1.42 / 0.58b0 → 0.63b0) で `uv lock` を解決し、CI を緑に戻す。

## 2. テスタビリティの制約

C-1a/C-1b/C-1c と同じく、実 AWS / 実 New Relic への送信検証は **operator runbook 任せ**。本 PR は static 検証のみ:

- `uv lock` 解決成功
- `uv sync --frozen` 成功
- `uv run ruff check` / `uv run mypy app` グリーン
- `uv run pytest -m "not slow" -q` グリーン
- `from app.core.telemetry import init_telemetry` の import smoke 成功

実 telemetry (gRPC で collector 経由 NR) は本 PR で確認しない。

## 3. 主要決定

| 項目 | 決定 |
|---|---|
| バージョン target | dependabot の floor (stable `>=1.42` / contrib `>=0.63b0`) |
| 上限ピン | **なし** (既存スタイル `>=` のみを踏襲) |
| pyproject.toml で変更する直接依存 | 9 件 (api/sdk/exporter-otlp + 6 contrib) |
| transitive (uv.lock のみ) | 約 10 件、`uv lock` 再生成で同 train に整列 |
| dev dep の transitive 衝突 | `[tool.uv] override-dependencies` で `opentelemetry-api/sdk/exporter-otlp-proto-http >=1.42` を強制 (semgrep が `<1.38` に pin しているため) |
| 検証範囲 | static のみ (ruff/mypy/pytest + import smoke) |
| `app/core/telemetry.py` 修正 | 必要時のみ (instrumentation API 変更があれば追従) |
| dependabot 既存 PR | #36 / #39 を本 PR にて supersede、merge 後 close |
| ブランチ | `feature/otel-train-bump` (worktree: `.worktrees/otel-train-bump/`、`origin/main` 起点) |

## 4. アーキテクチャ (影響範囲)

```
pyproject.toml ──┐
                 │  uv lock
                 ▼
uv.lock (19+ OTel packages, train aligned to 1.42 / 0.63b0)
                 │  uv sync --frozen
                 ▼
.venv ──────────► import app.core.telemetry  (smoke)
                  ruff / mypy / pytest        (static)
```

`app/core/telemetry.py` のインポート対象 (現状):

```python
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
# (関数内)
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
from opentelemetry.instrumentation.aio_pika import AioPikaInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
```

これらが 0.63b0 で rename / 削除されていれば最小限の追従修正を入れる。

## 5. ファイル変更マップ

| File | Action | Owns |
|---|---|---|
| `pyproject.toml` | Modify | 9 行の specifier 更新 + `[tool.uv] override-dependencies` セクション追加 (下記 §6.1, §6.5) |
| `uv.lock` | Regenerate | `uv lock` の出力差分 (約 19 packages の version 行) |
| `app/core/telemetry.py` | Modify (条件付き) | instrumentation API 破壊的変更が出た時のみ追従 |

それ以外は触らない:
- ECS task def (`infra/ecspresso/*/ecs-task-def.json`)
- otel-collector image (`docker/otel-collector/`)
- CI workflows (`.github/workflows/*.yml`)
- tests (`tests/`) — telemetry 直接テストはなく、import 経由でのみ exercise される

## 6. 実装詳細

### 6.1 pyproject.toml の specifier 更新

現状 (`pyproject.toml`):

```toml
"opentelemetry-api>=1.29",
"opentelemetry-sdk>=1.29",
"opentelemetry-exporter-otlp>=1.29",
"opentelemetry-instrumentation-fastapi>=0.50b0",
"opentelemetry-instrumentation-sqlalchemy>=0.50b0",
"opentelemetry-instrumentation-asyncpg>=0.50b0",
"opentelemetry-instrumentation-aio-pika>=0.50b0",
"opentelemetry-instrumentation-httpx>=0.50b0",
"opentelemetry-instrumentation-logging>=0.50b0",
```

After:

```toml
"opentelemetry-api>=1.42",
"opentelemetry-sdk>=1.42",
"opentelemetry-exporter-otlp>=1.42",
"opentelemetry-instrumentation-fastapi>=0.63b0",
"opentelemetry-instrumentation-sqlalchemy>=0.63b0",
"opentelemetry-instrumentation-asyncpg>=0.63b0",
"opentelemetry-instrumentation-aio-pika>=0.63b0",
"opentelemetry-instrumentation-httpx>=0.63b0",
"opentelemetry-instrumentation-logging>=0.63b0",
```

### 6.2 lock 再生成

```bash
uv lock        # pyproject.toml の specifier 更新を反映
uv sync --frozen
```

`uv.lock` 内の OTel 関連 package version は `1.37.0` → `1.42.x`、`0.58b0` → `0.63b0` (相当) に更新される。

### 6.3 検証

```bash
uv run ruff check
uv run mypy app
uv run pytest -m "not slow" -q
uv run python -c "from app.core.telemetry import init_telemetry; print('ok')"
```

すべて exit 0、`pytest` は全 green。

### 6.4 instrumentation API 追従 (条件付き)

ローカル検証で `ImportError` / `AttributeError` が出た場合のみ:

- 該当 import の rename / 移動を OTel 上流 changelog で確認
- `app/core/telemetry.py` の該当行を最小限修正
- 再度 §6.3 を実行 → 全 green になるまで反復

### 6.5 [tool.uv] override-dependencies (semgrep transitive 衝突回避)

`semgrep` (dev dep) が `opentelemetry-api` / `opentelemetry-sdk` / `opentelemetry-exporter-otlp-proto-http` を古いバージョン (`<1.38`) に pin しているため、§6.1 の specifier 更新だけでは `uv lock` が解決不能になる。`pyproject.toml` に以下のセクションを追加:

```toml
[tool.uv]
# semgrep pins opentelemetry-* to narrow ranges (<1.26 or <1.38) that conflict
# with our runtime requirement (>=1.42). semgrep uses OTel only for its own
# telemetry; forcing the newer packages is safe — the public API is
# backwards-compatible within the 1.x train.
override-dependencies = [
    "opentelemetry-api>=1.42",
    "opentelemetry-sdk>=1.42",
    "opentelemetry-exporter-otlp-proto-http>=1.42",
]
```

範囲は semgrep が実際 pin している 3 パッケージに限定。semgrep が将来 OTel pin を緩めた時はこのセクションを削除して `uv lock` 再生成。

## 7. 失敗モードと対処

| 失敗 | 対処 |
|---|---|
| `uv lock` が unsatisfiable | 1 minor 下に下げ (例: 1.42 → 1.41) 再試行。それも不可なら旧 train (1.37) に戻して原因調査 |
| `ImportError` from `app/core/telemetry.py` | §6.4 で最小限追従 |
| 既存 test が落ちる | テスト fixture が古い API を参照していないか確認、必要なら fixture 修正 |
| 上記いずれも解決不能 | `git restore .` で巻き戻し、本 spec に追記して再 brainstorming |

## 8. dependabot PR の整理

本 PR merge 後に実行:

```bash
gh pr close 36 --comment "Superseded by #<this-PR>: OTel ecosystem coordinated bump"
gh pr close 39 --comment "Superseded by #<this-PR>: OTel ecosystem coordinated bump"
```

両 PR の dependabot ブランチは GitHub が自動 close 後に削除する設定があれば自動。なければ手動 `git push origin --delete <branch>` で削除。

## 9. Definition of Done

- [ ] `pyproject.toml` の 9 specifier 更新済み
- [ ] `uv lock` 再生成済み、unresolved なし
- [ ] `uv run ruff check` green
- [ ] `uv run mypy app` green
- [ ] `uv run pytest -m "not slow"` 全 green
- [ ] `from app.core.telemetry import init_telemetry` import smoke 成功
- [ ] CI (`ci.yml` + `security.yml`) 全 green
- [ ] PR merge 済み
- [ ] #36 / #39 close 済み (supersedes コメント付き)

## 10. スコープ外 (やらない)

- 上限ピン (`<1.43` 等) の追加 — 既存スタイルを保つ
- 新しい OTel instrumentation の追加 — 既存 6 件のみ
- `docker/otel-collector/config.yaml` の更新 — collector image は別管理
- ECS task def の OTel env 変更
- 実 telemetry (gRPC for collector → NR) の動作確認 — operator runbook 任せ
