# DLQ Admin Tools 設計

**Date:** 2026-05-15
**Status:** Draft → ユーザレビュー待ち
**Scope:** EC API の RabbitMQ Dead Letter Queue (`<queue>.dlq`) を運用する管理ツールを実装する。初期は CLI 提供だが、将来 HTTP admin 経由や定常監視ジョブから同じロジックを呼び出せる API レイヤーを確保しておく。

---

## 1. 目的と背景

`docs/superpowers/specs/2026-05-12-ec-api-design.md` で構築した MQ トポロジは Consumer 側の指数バックオフ retry + 最終的な DLQ 終端を持つ。具体的には:

- メイン: `ec.events` (topic)
- リトライ: `ec.events.retry` (topic、TTL 1s/5s/30s/120s/600s)
- 死蔵: `ec.events.dlx` (topic、`<queue>.dlq` に流入)

retry が尽きたメッセージは `<queue>.dlq` (例: `ec.order_consumer.dlq`) に滞留し続け、誰も触らない限り永久に消費されない。本ツールは:

- 滞留件数の確認 (`count`)
- メッセージ内容の覗き見 (`peek`)
- 原因解消後の再投入 (`redrive`)
- 永久廃棄 (`drain`)

を、最初は CLI で提供する。**ただし C-2 単独で完結させず、後の HTTP 公開や監視ジョブ統合に耐えるよう、ロジック層と表示層を最初から分離する**。プロダクション運用では CLI は一時的な手当てで、最終的には API / scheduled job からの利用が想定される。

## 2. ゴール / 非ゴール

### ゴール

- 4 つの操作 (count / peek / redrive / drain) を `app/mq/dlq_admin.py` に **純粋なロジック関数**として実装
- ロジック層は **typed return values と typed exceptions** を返す (print/exit 禁止)
- ロジック層は **任意の `aio_pika.AbstractRobustConnection` を受け取る** (CLI / HTTP / cron いずれからも再利用可)
- ロジック層は **structlog で構造化ログを出す** (`queue`, `event_id`, `routing_key`, `operation`, `dry_run` フィールド)
- CLI `scripts/dlq.py` はロジック層を呼ぶ薄いラッパー (argparse + 結果整形 + exit code)
- `redrive` / `drain` は **dry_run デフォルト ON**、明示的 `--apply` 時のみ副作用発生
- `peek` は **non-destructive** (DLQ から消費しない、`nack(requeue=True)` で戻す)
- Slow (Testcontainers RabbitMQ) で全 4 操作の振る舞いを検証

### 非ゴール (将来検討)

- HTTP admin endpoint (`/admin/dlq/*`) — C-4 のスコープに移譲
- Web UI — C-5 のスコープに移譲
- 認可 (admin role) — CLI は ops shell 経由前提
- `event_id` / `routing_key` 単位の選択的 redrive (今回は FIFO で limit 件数のみ)
- 注釈付与 (Linear/Jira ticket リンク等)
- Slack / PagerDuty 通知連携

## 3. アーキテクチャ

```
                         ┌──────────────────────────────────┐
                         │  app/mq/dlq_admin.py             │
CLI (scripts/dlq.py) ───▶│                                  │
                         │  count_dlq(conn, queue)          │
HTTP (future, C-4) ─────▶│  peek_dlq(conn, queue, limit)    │───▶ RabbitMQ
                         │  redrive_dlq(conn, queue, ...)   │
cron / job (future) ────▶│  drain_dlq(conn, queue, ...)     │
                         │                                  │
                         │  Returns typed results, raises   │
                         │  DLQAdminError subclasses        │
                         └──────────────────────────────────┘
```

- **CLI 層** (`scripts/dlq.py`): I/O concerns only — argparse、結果フォーマット (テーブル形式)、exit code (`0` 成功 / `2` 引数 or queue 不在 / `3` 接続失敗)
- **ロジック層** (`app/mq/dlq_admin.py`): 接続 / queue / 操作の純粋関数、副作用ゲートは引数 `dry_run` で制御
- **Type 層** (`app/mq/dlq_admin.py` 内 dataclasses): 戻り値スキーマを定義

## 4. ロジック層 API

### Exceptions

```python
class DLQAdminError(Exception):
    """Base for DLQ admin operations."""

class DLQNotFoundError(DLQAdminError):
    """Queue does not exist (passive declare failed)."""

class NoRoutingKeyError(DLQAdminError):
    """Cannot determine original routing key for redrive (no x-death header)."""
```

CLI が exit code に、HTTP が status code (404 / 422) にマップする。

### Return types (dataclasses)

```python
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class CountResult:
    queue: str          # "<queue>.dlq"
    message_count: int

@dataclass(frozen=True, slots=True)
class DLQMessage:
    delivery_tag: int   # internal, for ack accounting
    event_id: str | None
    routing_key: str | None    # extracted from x-death[0].routing-keys[0]
    death_count: int           # x-death[0].count or 0
    body_preview: str          # first 200 chars of payload
    headers: dict[str, object] # full headers (incl. x-death)

@dataclass(frozen=True, slots=True)
class RedriveResult:
    queue: str
    dry_run: bool
    requested: int             # how many were targeted
    redriven: int              # how many were re-published (== requested if no skips)
    skipped: list[str]         # event_ids skipped due to NoRoutingKeyError

@dataclass(frozen=True, slots=True)
class DrainResult:
    queue: str
    dry_run: bool
    drained: int
```

### Functions

```python
async def count_dlq(
    connection: aio_pika.abc.AbstractRobustConnection,
    *,
    queue: str,
) -> CountResult: ...

async def peek_dlq(
    connection: aio_pika.abc.AbstractRobustConnection,
    *,
    queue: str,
    limit: int,
    preview_chars: int = 200,
) -> list[DLQMessage]: ...

async def redrive_dlq(
    connection: aio_pika.abc.AbstractRobustConnection,
    *,
    queue: str,
    limit: int | None,    # None = all
    dry_run: bool = True,
) -> RedriveResult: ...

async def drain_dlq(
    connection: aio_pika.abc.AbstractRobustConnection,
    *,
    queue: str,
    limit: int | None,
    dry_run: bool = True,
) -> DrainResult: ...
```

すべて **keyword-only** 引数 (`*,` 強制) で将来の引数追加に耐える。

### `queue` パラメータの規約

呼び出し側は **consumer queue 名** (例: `ec.order_consumer`) を渡す。ロジック層が `<queue>.dlq` を内部で組み立てる。これによりオペレーション人間 / API クライアントが consumer 単位で抽象化できる。

### 動作詳細

- **`count_dlq`**: `<queue>.dlq` を `passive=True` で declare して `.declaration_result.message_count` を返す。Queue 不在は `DLQNotFoundError`
- **`peek_dlq`**: `<queue>.dlq` から最大 `limit` 件を short-loop で受信 (`channel.iterator()` を使い `consume`)。各メッセージは `nack(requeue=True)` で戻す。`body_preview` は `payload[:preview_chars]` を UTF-8 デコード (デコード失敗時は `repr(bytes)`)。`routing_key` は `headers["x-death"][0]["routing-keys"][0]` から取得 (なければ None)
- **`redrive_dlq`**:
  - 取得 → 元 routing key 抽出
  - dry_run=True: 何も publish しない、ack もしない (`nack(requeue=True)`)、requested 件数を返す
  - dry_run=False: `ec.events` exchange に `routing_key` で publish (body / headers / message_id / `traceparent` ヘッダ等保持)、main で ack
  - `NoRoutingKeyError` ケースのみ ack せず戻し、`skipped` に event_id を記録
- **`drain_dlq`**: dry_run=False で ack のみ (publish 無し)、dry_run=True は nack(requeue=True) で戻す
- 全関数で structlog logger を使用 (`logger.info("dlq_admin.<op>", queue=..., dry_run=..., **fields)`)

### 中断耐性 (signal handling)

`redrive_dlq` / `drain_dlq` の途中で `asyncio.CancelledError` が発生した場合: in-flight メッセージは `nack(requeue=True)` で DLQ に戻す。CLI 側は `signal.SIGINT` を `asyncio.CancelledError` に変換する標準的なパターン。

### 並行性

複数プロセスが同時に同じ DLQ に対して redrive を実行することは想定外。RabbitMQ 自体は per-consumer に配信するので衝突は起きないが、運用上は 1 プロセスのみが触る前提。

## 5. CLI 層

`scripts/dlq.py`:

```
uv run python scripts/dlq.py count <queue>
uv run python scripts/dlq.py peek <queue> [--limit N] [--preview-chars N]
uv run python scripts/dlq.py redrive <queue> [--limit N | --all] [--apply]
uv run python scripts/dlq.py drain <queue> [--limit N | --all] [--apply]
```

- `--apply` 不在 = dry_run。stderr に `dry-run` ラベルを出す
- 出力フォーマット: `peek` は固定幅テーブル (event_id, routing_key, deaths, preview)、その他は 1 行サマリ
- Exit code: `0` 成功、`2` 引数不正 or `DLQNotFoundError`、`3` AMQP 接続失敗 (ConnectionError)、`130` SIGINT
- 設定: `app.core.config.Settings()` から `rabbitmq_url` を読む
- 内部で `aio_pika.connect_robust()` → ロジック関数呼び出し → close

CLI コードは概ね 100 行程度に収まる想定。引数解析以外のロジックは持たない。

## 6. ファイル構成

```
app/mq/
└── dlq_admin.py                       # 新規 — exceptions, dataclasses, 4 functions

scripts/
└── dlq.py                             # 新規 — CLI entrypoint, argparse, formatter

tests/mq/
└── test_dlq_admin.py                  # 新規 — Testcontainers + unit
```

## 7. テスト戦略

### Unit (`tests/mq/test_dlq_admin.py` 内、`pytest.mark.asyncio` 単独)
- `peek_dlq` の body_preview 切り詰めロジック (200 chars 超過、UTF-8 decode 失敗のフォールバック)
- `redrive_dlq` で `headers["x-death"]` が無いメッセージは `NoRoutingKeyError` → skipped に積まれる
- `_extract_routing_key` helper の境界 (空 list / 不完全な dict / None)

### Slow (Testcontainers RabbitMQ, `pytest.mark.slow`)
fixture で `ec.events` / `ec.events.dlx` / `<test_queue>.dlq` をセットアップし、以下:

1. **count 空**: `count_dlq("ec.test")` → `CountResult(message_count=0)`
2. **count N 件**: dlx 経由で 3 件投入 → `count` が 3 を返す
3. **DLQNotFoundError**: 存在しない queue → 例外
4. **peek 非破壊**: 3 件投入 → `peek(limit=10)` で 3 件取得 + 取得後の `count` が依然 3
5. **peek limit**: 5 件投入 → `peek(limit=2)` で 2 件、count 依然 5
6. **redrive dry-run**: 2 件投入 → `redrive(dry_run=True)` で requested=2 redriven=2 (もしくは requested=2 redriven=0 か、設計どっち取るか) → spec で明示: dry_run=True 時は requested=N redriven=0 (publish していない)、dlq 件数は変わらない
7. **redrive apply**: 2 件投入 → `redrive(dry_run=False)` で requested=2 redriven=2、dlq=0、main exchange consumer で 2 件受信できる
8. **redrive skipped**: routing key 不在のメッセージを投入 → `NoRoutingKeyError` で skipped に積まれ、dlq に戻る
9. **drain dry-run**: 2 件 → `drain(dry_run=True)` で drained=0、dlq=2
10. **drain apply**: 2 件 → `drain(dry_run=False)` で drained=2、dlq=0、main にも届かない
11. **CLI 経由のスモークテスト** (subprocess で `python scripts/dlq.py count <queue>` を実行、stdout 検証)

合計 ~10 slow + ~3 unit。

## 8. 拡張ポイント (production-readiness)

CLI を「一時的」に保つための備え:

1. **HTTP 取り込み (C-4 と統合可能)**: `app/modules/admin/dlq.py` ルータを後で追加するだけ。`count_dlq`/`peek_dlq` をそのまま呼べる。`redrive_dlq`/`drain_dlq` は副作用付きなので `POST` + 認可必須 (admin role の設計が必要だが本 spec 外)
2. **監視 / 通知 cron**: 例えば「`count > 100` で Slack alert」のような定期ジョブから `count_dlq` をそのまま呼べる
3. **OpenTelemetry**: 既存の自動計装が AMQP span を取るため、redrive 経路の trace は自動で繋がる
4. **メトリクス**: 必要になれば logger 出力に加えて Prometheus counter (`ec_dlq_messages_redriven_total`) を `app/core/telemetry.py` の Meter 経由で追加可能 (本 spec 外)
5. **bulk operations**: `redrive_dlq` 1 関数が limit 引数で複数件処理する設計のため、API 化時にバッチサイズ調整のみで対応可

## 9. エラーハンドリング

| 状況 | ロジック層 | CLI 出力 | Exit |
|---|---|---|---|
| queue 不在 | `DLQNotFoundError` | `error: queue ec.foo.dlq does not exist` | 2 |
| RMQ 接続失敗 | `ConnectionError` がそのまま伝搬 | `error: cannot connect to <url>` | 3 |
| redrive 中の routing key 抽出失敗 | NoRoutingKeyError を catch、`skipped` に積む | `redriven=N skipped=M (event_ids: ...)` | 0 (成功扱い、要 follow-up) |
| SIGINT | `CancelledError`、in-flight は nack | `interrupted; rolled back in-flight messages` | 130 |

## 10. ロールアウト計画

1. PR: ロジック層 + CLI + テスト
2. CI green (lint/type/test-unit/test-slow + security)
3. main にマージ
4. README に「Operations: DLQ tools」セクション追加 (本 spec の §5 を簡潔化したもの)
5. 運用観察 — production で実利用したフィードバックを spec §8 にまとめ、C-4 で HTTP 化判断
