# OTel ecosystem coordinated bump Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** OTel 全パッケージを 1 トレイン分まとめて bump (stable 1.37 → 1.42 / contrib 0.58b0 → 0.63b0) して dependabot #36/#39 の uv lock 解決不能を解消。

**Architecture:** `pyproject.toml` の 9 件の specifier を新しい下限に更新し `uv lock` を再生成するだけ。`app/core/telemetry.py` は instrumentation API の破壊的変更が出た場合のみ追従修正。検証は static (ruff/mypy/pytest + import smoke) のみで、実 telemetry の動作確認は operator runbook 任せ。

**Tech Stack:** uv 0.5+、Python 3.12、OpenTelemetry SDK / contrib、pytest、ruff、mypy。

**Spec:** `docs/superpowers/specs/2026-05-22-otel-train-bump-design.md`

**Working directory:** worktree `.worktrees/otel-train-bump/`、branch `feature/otel-train-bump` (作成済み、spec はコミット済み)。

---

## File map

| File | Action | Owns |
|---|---|---|
| `pyproject.toml` | Modify | 9 行の specifier を新下限へ |
| `uv.lock` | Regenerate | `uv lock` の出力差分 (約 19 OTel package version 行) |
| `app/core/telemetry.py` | Modify (条件付き) | instrumentation API 破壊的変更時の追従修正 (Task 5 で必要時のみ) |

---

## Pre-flight

ツール疎通確認:

```bash
cd /Users/takuma/cross/ec/.worktrees/otel-train-bump
uv --version             # >=0.5
uv run python --version  # 3.12.x
uv run ruff --version
uv run mypy --version
uv run pytest --version
```

すべて versions を出力すれば OK。

ベースライン確認 (まず現状が green であることを記録):

```bash
uv sync --frozen
uv run ruff check
uv run mypy app
uv run pytest -m "not slow" -q
```

すべて exit 0 になることを確認。落ちる場合は spec で記録した「不変条件 (検証が緑)」が成立していないので、Task 1 に進む前に原因調査。

---

## Task 1: pyproject.toml の specifier 更新

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: 現在の OTel ブロックを確認**

```bash
grep -n "opentelemetry" /Users/takuma/cross/ec/.worktrees/otel-train-bump/pyproject.toml
```

期待出力 (9 行、`>=1.29` x3 + `>=0.50b0` x6):

```
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

- [ ] **Step 2: Edit で stable 3 行を一気に置換**

`pyproject.toml` 内の OTel stable 3 行を新下限に置換 (Edit ツール、`replace_all: false`、まとめて 1 ブロック):

old_string:
```
    "opentelemetry-api>=1.29",
    "opentelemetry-sdk>=1.29",
    "opentelemetry-exporter-otlp>=1.29",
```

new_string:
```
    "opentelemetry-api>=1.42",
    "opentelemetry-sdk>=1.42",
    "opentelemetry-exporter-otlp>=1.42",
```

- [ ] **Step 3: Edit で contrib 6 行を一気に置換**

old_string:
```
    "opentelemetry-instrumentation-fastapi>=0.50b0",
    "opentelemetry-instrumentation-sqlalchemy>=0.50b0",
    "opentelemetry-instrumentation-asyncpg>=0.50b0",
    "opentelemetry-instrumentation-aio-pika>=0.50b0",
    "opentelemetry-instrumentation-httpx>=0.50b0",
    "opentelemetry-instrumentation-logging>=0.50b0",
```

new_string:
```
    "opentelemetry-instrumentation-fastapi>=0.63b0",
    "opentelemetry-instrumentation-sqlalchemy>=0.63b0",
    "opentelemetry-instrumentation-asyncpg>=0.63b0",
    "opentelemetry-instrumentation-aio-pika>=0.63b0",
    "opentelemetry-instrumentation-httpx>=0.63b0",
    "opentelemetry-instrumentation-logging>=0.63b0",
```

- [ ] **Step 4: 差分を確認**

```bash
git -C /Users/takuma/cross/ec/.worktrees/otel-train-bump diff pyproject.toml
```

期待: 9 行が `>=1.29` → `>=1.42` / `>=0.50b0` → `>=0.63b0` に置換されている。それ以外の行に変更がないこと。

---

## Task 2: uv lock 再生成

**Files:**
- Regenerate: `uv.lock`

- [ ] **Step 1: uv lock を実行**

```bash
cd /Users/takuma/cross/ec/.worktrees/otel-train-bump
uv lock
```

期待: 成功 (exit 0)、"Resolved N packages in ..." のサマリが出る。

**もし unsatisfiable で失敗する場合** — 例:
```
error: Because opentelemetry-instrumentation-aio-pika==0.63b0 was not found ...
```
が出たら、該当 package を 1 minor 下げて再試行 (例: `0.63b0` → `0.62b0`)。それでもダメなら spec §7 の失敗モード対処へ。

- [ ] **Step 2: lock の OTel パッケージが train ごと進んだことを確認**

```bash
grep -B0 -A1 "^name = \"opentelemetry" /Users/takuma/cross/ec/.worktrees/otel-train-bump/uv.lock | grep -E "name|version" | paste - -
```

期待: stable packages (api/sdk/exporter-* / proto) が `1.42.x`、contrib (instrumentation-* / semantic-conventions / util-http) が `0.63b0` 以上。

- [ ] **Step 3: uv sync --frozen で install できることを確認**

```bash
uv sync --frozen
```

期待: 成功、新しい OTel パッケージが install される。

---

## Task 3: 静的検証 (ruff / mypy)

**Files:** なし — 検証のみ

- [ ] **Step 1: ruff check**

```bash
cd /Users/takuma/cross/ec/.worktrees/otel-train-bump
uv run ruff check
```

期待: `All checks passed!` (exit 0)。

落ちた場合: `app/core/telemetry.py` 内で deprecated symbol を使っていれば ruff が拾うかもしれない。エラー内容を読み、Task 5 (instrumentation API 追従) で対処。

- [ ] **Step 2: mypy app**

```bash
uv run mypy app
```

期待: `Success: no issues found in N source files` (exit 0)。

落ちた場合: OTel 型シグネチャが変わっているとほぼここで顕在化する。`app/core/telemetry.py` のどの行が落ちたかを確認し、Task 5 で対処。

---

## Task 4: テスト + import smoke

**Files:** なし — 検証のみ

- [ ] **Step 1: pytest (not slow)**

```bash
cd /Users/takuma/cross/ec/.worktrees/otel-train-bump
uv run pytest -m "not slow" -q
```

期待: 全 test pass、`32 passed, ...` (or 現在の数字) で exit 0。

- [ ] **Step 2: telemetry import smoke**

```bash
uv run python -c "from app.core.telemetry import init_telemetry; print('ok')"
```

期待: `ok` が出力されて exit 0。

落ちた場合: instrumentation API の rename / 削除が原因の可能性大。Task 5 へ。

---

## Task 5: instrumentation API 追従 (条件付き)

**Files:**
- Modify (該当行のみ): `app/core/telemetry.py`

**条件:** Task 3 または Task 4 のいずれかが ImportError / AttributeError / 型エラーで落ちた場合のみ実行。落ちなければ **本 Task はスキップ**して Task 6 へ進む。

- [ ] **Step 1: 落ちた行を特定**

エラーメッセージから対象シンボルを抽出 (例: `ImportError: cannot import name 'AsyncPGInstrumentor' from 'opentelemetry.instrumentation.asyncpg'`)。

- [ ] **Step 2: 新しい API 名を確認**

OTel 上流の changelog (https://github.com/open-telemetry/opentelemetry-python-contrib/blob/main/CHANGELOG.md) で、0.58b0 → 0.63b0 の該当 instrumentation の rename を確認。

または PyPI の該当 package ページから新バージョンの __init__.py を確認 (`uv run python -c "import opentelemetry.instrumentation.asyncpg; print(dir(...))"` で実機確認も可)。

- [ ] **Step 3: app/core/telemetry.py の該当行を最小限修正**

例: `AsyncPGInstrumentor` が `AsyncPGEngineInstrumentor` にリネームされた場合:

```python
# before
from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
AsyncPGInstrumentor().instrument(...)

# after
from opentelemetry.instrumentation.asyncpg import AsyncPGEngineInstrumentor
AsyncPGEngineInstrumentor().instrument(...)
```

他のシンボルが落ちた場合も同じパターンで 1 つずつ追従。

- [ ] **Step 4: 検証を再実行**

```bash
uv run ruff check
uv run mypy app
uv run pytest -m "not slow" -q
uv run python -c "from app.core.telemetry import init_telemetry; print('ok')"
```

全 green になるまで Step 1-4 を反復。

- [ ] **Step 5: もし 3 回反復しても通らない場合**

spec §7 失敗モード表に従って 1 minor 下に下げる (`>=1.42` → `>=1.41` + `>=0.63b0` → `>=0.62b0`) を Task 1-4 で再試行、それも不可なら `git restore .` で巻き戻し、spec に追記して再 brainstorming。

---

## Task 6: コミット

**Files:** すべての変更を commit する。

- [ ] **Step 1: 変更ファイルを確認**

```bash
cd /Users/takuma/cross/ec/.worktrees/otel-train-bump
git status
git diff --stat
```

期待: `pyproject.toml`、`uv.lock`、(Task 5 走った場合のみ) `app/core/telemetry.py` が modified。

- [ ] **Step 2: Task 5 を走らなかった場合 (落ちなかった): 1 コミット**

```bash
git add pyproject.toml uv.lock
git commit -m "$(cat <<'EOF'
chore(deps): bump OTel ecosystem to 1.42 / 0.63b0

dependabot が分割した #36 / #39 を超越する 1 train 一括 bump。
pyproject.toml で 9 specifier を更新し uv lock 再生成で全 OTel
パッケージ (transitive 含む) を 1.42 / 0.63b0 train へ揃える。

検証 (static):
- ruff check / mypy app / pytest -m "not slow" all green
- from app.core.telemetry import init_telemetry → ok

実 telemetry (gRPC for collector → NR) は operator runbook 任せ。
EOF
)"
```

- [ ] **Step 3: Task 5 を走った場合: 2 コミットに分ける**

まず deps bump:

```bash
git add pyproject.toml uv.lock
git commit -m "chore(deps): bump OTel ecosystem to 1.42 / 0.63b0"
```

次に追従修正:

```bash
git add app/core/telemetry.py
git commit -m "fix(telemetry): adapt to OTel <new-API>

contrib 0.63b0 で <symbol> が rename されたため
app/core/telemetry.py を追従修正。"
```

メッセージの `<new-API>` / `<symbol>` は実際の rename 内容に置き換える。

- [ ] **Step 4: ログ確認**

```bash
git log --oneline -3
```

期待: spec コミット (7c3bbc9) の上に bump コミットが 1 or 2 個積まれている。

---

## Task 7: Push + PR

**Files:** なし — push + PR open。

- [ ] **Step 1: Push**

```bash
git -C /Users/takuma/cross/ec/.worktrees/otel-train-bump push -u origin feature/otel-train-bump
```

sandbox に弾かれた場合はユーザに以下を依頼:

> `! git -C /Users/takuma/cross/ec/.worktrees/otel-train-bump push -u origin feature/otel-train-bump`

- [ ] **Step 2: PR open**

```bash
gh pr create --base main --head feature/otel-train-bump \
  --title "chore(deps): OTel ecosystem coordinated bump (1.37 → 1.42 / 0.58b0 → 0.63b0)" \
  --body "$(cat <<'EOF'
## Summary

dependabot が分割して開けた 2 件の OTel pip bump PR (#36 / #39) は **個別マージ不可能**。OTel のリリーストレインは「stable 1.X.0」と「contrib 0.(X+21)b0」が連動しており、片方だけ進めると `uv lock` が unsatisfiable と判定する。

本 PR は **全 OTel パッケージを 1 トレイン分まとめて進める** (1.37 → 1.42 / 0.58b0 → 0.63b0) ことで `uv lock` を解決し、CI を緑に戻す。

### 変更

- `pyproject.toml`: 直接依存 9 件の specifier 更新 (stable 3 件 `>=1.29` → `>=1.42`、contrib 6 件 `>=0.50b0` → `>=0.63b0`)
- `uv.lock`: 約 19 OTel package の version 行が train ごと進行 (transitive 含む)
- `app/core/telemetry.py`: (Task 5 が走った場合のみ) instrumentation API rename に最小限追従

### 検証

- [x] `uv lock` 解決成功
- [x] `uv sync --frozen` 成功
- [x] `uv run ruff check` All checks passed
- [x] `uv run mypy app` Success: no issues
- [x] `uv run pytest -m "not slow"` all green
- [x] `from app.core.telemetry import init_telemetry` import smoke 成功
- [ ] CI all green (本 PR push 後に確認)

実 telemetry (gRPC for collector → NR) は operator runbook 任せ。

### Supersedes

- #36 (opentelemetry-exporter-otlp >=1.42.0)
- #39 (opentelemetry-instrumentation-asyncpg >=0.63b0)

両 PR は本 PR merge 後に close する (個別 close コメント済み)。

## Spec / Plan

- spec: \`docs/superpowers/specs/2026-05-22-otel-train-bump-design.md\`
- plan: \`docs/superpowers/plans/2026-05-22-otel-train-bump.md\`
EOF
)"
```

`gh pr create` が sandbox に弾かれた場合は同コマンドをユーザに依頼。

- [ ] **Step 3: PR 番号をメモ**

`gh pr create` の出力 URL (例: `https://github.com/takuta77/ec/pull/49`) から PR 番号を控える。Task 8 で使う。

---

## Task 8: CI 確認 + dependabot PR の整理

**Files:** なし — operations only。

- [ ] **Step 1: CI green を待つ**

```bash
# 5-10 分で settle する想定
gh pr checks <PR-番号> -R takuta77/ec --watch
```

期待: `ci`、`security`、`semgrep` 等が全 green。失敗が出たら原因を確認 (Task 5 で漏れた API 追従の可能性)。

- [ ] **Step 2: PR を squash merge**

CI green を確認後:

```bash
gh pr merge <PR-番号> -R takuta77/ec --squash --delete-branch
```

- [ ] **Step 3: dependabot PR #36, #39 を close**

```bash
gh pr close 36 -R takuta77/ec --comment "Superseded by #<PR-番号>: OTel ecosystem coordinated bump (1.37 → 1.42 / 0.58b0 → 0.63b0)"
gh pr close 39 -R takuta77/ec --comment "Superseded by #<PR-番号>: OTel ecosystem coordinated bump (1.37 → 1.42 / 0.58b0 → 0.63b0)"
```

- [ ] **Step 4: 状態確認**

```bash
gh pr list -R takuta77/ec --state open
```

期待: dependabot の OTel 関連 PR (#36, #39) が消えている。残っているのは無関係な他の PR のみ。

---

## Self-Review Checklist (run by plan author before handoff)

**Spec coverage:**
- §3 主要決定:
  - target floor 1.42 / 0.63b0 → Task 1
  - 上限ピンなし → Task 1 で `>=` のみ
  - 直接依存 9 件 → Task 1 (具体的に列挙)
  - transitive lock 再生成 → Task 2
  - static 検証のみ → Task 3, 4
  - instrumentation API 追従条件付き → Task 5
  - dependabot PR close → Task 8
  - ブランチ名・worktree → header
- §5 ファイル変更マップ: Task 1 (pyproject), Task 2 (lock), Task 5 (telemetry conditional) — 全カバー
- §6.1 specifier 更新詳細: Task 1 Step 2-3 で before/after 明示
- §6.2 lock 再生成: Task 2
- §6.3 検証: Task 3, 4
- §6.4 API 追従: Task 5
- §7 失敗モード: Task 2 Step 1 と Task 5 Step 5 で言及
- §8 dependabot 整理: Task 8 Step 3
- §9 DoD: Task 1-8 で全項目カバー
- §10 やらないこと: 上限ピンなし、instrumentation 追加なし、collector config 触らない、ECS task def 触らない、実 telemetry 確認しない — 全て plan 内に記述なし (= 含めていない、つまりやらない)

**Placeholder scan:**
- "TBD" / "TODO" / "implement later": なし
- "Add appropriate error handling": なし
- "Write tests for the above" (without code): なし
- "Similar to Task N" (without repeat): なし
- 残っている placeholder: `<PR-番号>` (Task 7-8、PR 作成時に決まる)、`<new-API>` / `<symbol>` (Task 6 Step 3 commit message、Task 5 走った場合のみ実コンテンツに置換) — どちらも intentional runtime placeholder

**Type / naming consistency:**
- パッケージ specifier 命名: pyproject.toml と plan 内表記が一致 (`opentelemetry-instrumentation-aio-pika` のハイフン区切り、`>=0.63b0` の pre-release marker)
- branch / worktree 名: 全 Task で `feature/otel-train-bump` / `.worktrees/otel-train-bump/` 統一
- spec 参照パス: `docs/superpowers/specs/2026-05-22-otel-train-bump-design.md` 統一

No issues. Plan ready for implementation.
