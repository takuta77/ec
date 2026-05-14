# EC API CI + Security Pipeline 設計

**Date:** 2026-05-14
**Status:** Draft → ユーザレビュー待ち
**Scope:** EC API リポジトリ (`takuta77/ec`) の継続的インテグレーション (CI) と、CI に統合するセキュリティチェック群。CD (デプロイ) は対象外で、次フェーズの「本番デプロイ先」スペックで扱う。

---

## 1. 目的と背景

EC API は FastAPI / SQLAlchemy / RabbitMQ を組み合わせた複数プロセス構成で、認証付き JWT・MQ 連携・コンテナイメージなど **攻撃面が広い**。さらに開発はマルチブランチ / マルチ PR で進んでおり、人手レビューだけでは下記を見逃すリスクが高い:

- 依存ライブラリの既知 CVE
- ソースコードに潜む典型的セキュリティアンチパターン (SQL インジェクション、不安全な YAML/JWT/暗号、SSRF など)
- リポジトリへの秘密情報コミット
- コンテナベースイメージ脆弱性
- Dockerfile / docker-compose のミス設定 (root 実行、過剰な権限など)

本パイプラインは **PR を main にマージする時点でこれらが自動検査・ブロックされ、 nightly で main の継続的な健全性を保つ** ことを目的とする。

## 2. ゴール / 非ゴール

### ゴール
- 全 PR でアプリケーション品質ゲート (lint / type / test) を強制する
- 全 PR でセキュリティチェック (deps / SAST / secrets / container / dockerfile / IaC / SBOM) を実行し、HIGH 以上の検知で **マージをブロック**する
- 検知結果を GitHub Code Scanning UI に集約し、開発者が PR 画面で内容を確認できる
- 依存ライブラリと GitHub Actions の自動アップデート PR を週次で受け取る
- nightly で main の脆弱性スキャンを再実行し、新規 CVE 発覚時に Issue を自動作成する

### 非ゴール (本スペック外)
- アプリケーションのデプロイ (CD) — 「本番デプロイ先」スペックで扱う
- DAST (ZAP 等の動的解析) — デプロイ環境が決まってから別途
- ライセンスコンプライアンスの厳格運用 — 現時点で要件無し、YAGNI
- パフォーマンステスト / ロードテスト
- Renovate への移行 — Dependabot で足りる間は不要
- ブランチ保護設定の自動化 (Terraform 等) — 手動設定 + README の手順で運用開始

## 3. 全体構成

```
                ┌────────────────────────── GitHub ──────────────────────────┐
                │                                                            │
PR open/sync ──►│  ci.yml          (lint / type / test-unit / test-slow)     │
                │  security.yml    (deps / sast / secrets / image / docker   │
                │                    / iac / sbom)                           │
                │                                                            │
nightly 03:00 ──►│  nightly-security.yml → security.yml の reusable 部分を呼出│
JST            │                                                            │
                │  Dependabot.yml  (pip / github-actions / docker, weekly)   │
                │                                                            │
                │  SARIF results → GitHub Code Scanning UI                   │
                │  SBOM artifacts → Workflow Artifacts (90 日保持)            │
                └────────────────────────────────────────────────────────────┘
```

## 4. ファイル / 構成

```
.github/
├── workflows/
│   ├── ci.yml                    # lint, type, test  — 必須 status check
│   ├── security.yml              # 上記セキュリティジョブ群 — 一部必須
│   ├── nightly-security.yml      # cron + workflow_dispatch、security.yml を再利用
│   └── _security-reusable.yml    # security.yml と nightly から呼ばれる reusable workflow
├── dependabot.yml                # pip, github-actions, docker
└── pull_request_template.md      # チェック項目 (Security review 必要か等)
.gitleaks.toml                    # 偽陽性 allowlist (テストフィクスチャ用)
.semgrepignore                    # 同上 (テスト/マイグレーション特定行)
.security/
└── pip-audit-ignore.yaml         # 既知だが未修正の CVE と期限・理由
README.md                         # 「ローカルでの再現」と「ブランチ保護設定手順」セクション追加
```

## 5. CI ワークフロー (`ci.yml`)

### トリガ
- `pull_request` (opened, synchronize, reopened) - すべてのブランチ
- `push` to `main`

### 共通セットアップ (composite action または各ジョブで重複)
1. `actions/checkout@v4` (full history — gitleaks 用、SAST baseline 比較用)
2. `astral-sh/setup-uv@v3` (Python 3.12)
3. `uv sync --frozen`

### ジョブ

| job_id     | 内容                                                          | 失敗条件                       |
|------------|---------------------------------------------------------------|--------------------------------|
| `lint`     | `uv run ruff check . && uv run ruff format --check .`         | ruff 違反                      |
| `type`     | `uv run mypy app`                                             | mypy strict エラー             |
| `test-unit`| `uv run pytest -m "not slow" --cov=app --cov-report=xml`      | テスト失敗 / カバレッジ XML 生成失敗 |
| `test-slow`| Docker 必須。 `uv run pytest -m slow --maxfail=3`              | テスト失敗                     |

- `lint` / `type` / `test-unit` は並列、 `test-slow` は単独 (Docker リソース競合回避)
- カバレッジ XML は artifact (`name: coverage-xml`) に上げる。閾値判定や Codecov 連携は今回は **行わない** (YAGNI)
- `test-slow` は Testcontainers が runner の Docker をそのまま使うため、`services:` ブロックは不要

## 6. Security ワークフロー (`security.yml` / `_security-reusable.yml`)

### トリガ
- `security.yml`: `pull_request` + `push` to `main`
- `nightly-security.yml`: `schedule: cron '0 18 * * *'` (UTC 18:00 = JST 03:00) + `workflow_dispatch`

### ジョブ

| job_id        | ツール                | 入力                              | 失敗条件                                   | SARIF |
|---------------|-----------------------|-----------------------------------|--------------------------------------------|-------|
| `deps`        | pip-audit             | `uv export --no-hashes` の出力     | `HIGH`/`CRITICAL` の CVE                   | yes   |
| `sast`        | Semgrep CI            | `p/python` + `p/security-audit` + `p/owasp-top-ten` + `p/jwt` | severity `ERROR` 検出 | yes   |
| `secrets`     | gitleaks              | PR 時は `git diff`、nightly は full history | いかなる検知 (allowlist 適用後) | yes   |
| `image`       | Trivy (image)         | `docker build` で作る `ec-api:ci` | `HIGH`/`CRITICAL` の脆弱性                  | yes   |
| `dockerfile`  | hadolint              | `docker/Dockerfile.*` 全て         | severity `error` 以上                       | yes   |
| `iac`         | Checkov               | `docker-compose.yml`              | severity `HIGH` 以上                        | yes   |
| `sbom`        | Syft                  | リポジトリ全体                     | (常に成功)                                  | no — artifact |

- SARIF は `github/codeql-action/upload-sarif@v3` で Code Scanning にアップロード
- SBOM は CycloneDX JSON 形式で artifact (リテンション 90 日)
- 必須 = `deps`, `sast`, `secrets`。`image`, `dockerfile`, `iac` は **warn-only** (現状の Dockerfile/compose にどれくらい指摘が出るか未知のため、最初は警告のみで運用 → 安定後に必須化)
- `sbom` は常に成功 (証跡保管用)

### Allowlist 戦略
- `.gitleaks.toml`: テスト用 RSA 鍵 (`tests/fixtures/jwt_test_keys/*.pem`) は明示的に除外。プロダクションコードへの allowlist は禁止
- `.semgrepignore`: マイグレーション (Alembic) の自動生成箇所のみ。アプリコードの allowlist は別途レビュー
- `.security/pip-audit-ignore.yaml`: 各エントリ `{vuln_id, package, reason, expires_at}` を必須化。`expires_at` は最大 60 日先まで。期限切れエントリがあれば `deps` ジョブは失敗

例:
```yaml
ignores:
  - vuln_id: GHSA-xxxx-yyyy-zzzz
    package: cryptography
    reason: "未パッチ、本機能では affected コードパス未使用 (DoS only)"
    expires_at: 2026-07-01
```

### Nightly 専用挙動
- 失敗時に `gh issue create --title "[security] nightly scan failure $(date)" --label security,nightly --body @summary.md` を呼び自動 Issue 化
- Issue body には失敗したジョブと SARIF のリンクを含める

## 7. Dependabot 設定 (`dependabot.yml`)

```yaml
version: 2
updates:
  - package-ecosystem: "pip"
    directory: "/"
    schedule: { interval: "weekly", day: "monday", time: "03:00", timezone: "Asia/Tokyo" }
    open-pull-requests-limit: 5
    groups:
      python-minor-patch:
        update-types: ["minor", "patch"]
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule: { interval: "weekly" }
  - package-ecosystem: "docker"
    directory: "/docker"
    schedule: { interval: "weekly" }
```

- Python は minor/patch をグループ化して PR を 1 本にまとめる (CI コスト削減)
- Major バージョン更新は個別 PR
- Dependabot PR にも CI + security が必ず走るため、安全に自動マージ判断できる材料が揃う (自動マージ自体は導入しない)

## 8. ブランチ保護 (手動設定 + README 手順)

設定対象ブランチ: `main`, `feature/*` の主要長期ブランチ (例 `feature/ec-api-impl`)

| 設定項目                                     | 値                                                            |
|----------------------------------------------|---------------------------------------------------------------|
| Require pull request before merging          | ON, 最低 1 approval                                            |
| Dismiss stale reviews                        | ON                                                            |
| Require status checks to pass                | ON                                                            |
| Required checks                              | `ci / lint`, `ci / type`, `ci / test-unit`, `ci / test-slow`, `security / deps`, `security / sast`, `security / secrets` |
| Require branches to be up to date            | ON                                                            |
| Require linear history                       | ON                                                            |
| Require signed commits                       | OFF (将来検討)                                                 |
| Allow force pushes                           | OFF                                                           |
| Allow deletions                              | OFF                                                           |

README に GitHub UI でのスクリーンショット手順を記載。Terraform / `gh api` での自動化は対象外。

## 9. ローカル再現 (README に手順を集約)

Makefile / タスクランナーは導入せず、FastAPI / uv のネイティブコマンドを README に列挙する。CI の各ステップもこのコマンドをそのまま使う。

### 開発サーバ・本番風サーバ
```bash
# 開発 (auto-reload)
uv run fastapi dev app/main.py

# 本番風 (multi-worker)
uv run fastapi run app/main.py --workers 4
```

### CI 同等チェック (PR 作成前)
```bash
# Lint + format
uv run ruff check .
uv run ruff format --check .

# 型チェック
uv run mypy app

# テスト (Docker 必要)
uv run pytest -m "not slow"
uv run pytest -m slow
```

### Security チェック
```bash
# Python 依存脆弱性
uv export --no-hashes --no-dev=false > /tmp/req.txt
uv run pip-audit -r /tmp/req.txt

# SAST
uv run semgrep ci \
  --config p/python \
  --config p/security-audit \
  --config p/owasp-top-ten \
  --config p/jwt

# 秘密情報スキャン
gitleaks detect --redact --no-banner

# Dockerfile lint
docker run --rm -i hadolint/hadolint < docker/Dockerfile.api

# Container image スキャン
docker build -f docker/Dockerfile.api -t ec-api:dev .
trivy image --severity HIGH,CRITICAL ec-api:dev
```

### 依存
- `pip-audit`, `semgrep` は `pyproject.toml` の `[dependency-groups] dev` に追加 (uv で同梱インストール)
- `gitleaks`, `hadolint`, `trivy` はバイナリ前提 (`brew install gitleaks hadolint aquasecurity/trivy/trivy`)。CI では公式 GitHub Action を使うので、ローカル必須ではない

README にこれらをコピペ可能なブロックとして掲載する。ショートカット (`poe ci` 等) が将来欲しくなった場合は `poethepoet` を別 PR で追加する余地を残すが、本スコープでは導入しない。

## 10. 失敗時のフロー

| 失敗パターン                                      | 対応                                                                                   |
|---------------------------------------------------|----------------------------------------------------------------------------------------|
| `lint` / `type`                                   | 開発者がローカル `uv run ruff check . && uv run mypy app` で修正                        |
| `test-unit` / `test-slow`                         | 通常のテスト修正フロー                                                                  |
| `deps` で HIGH/CRITICAL                           | ① 依存更新 PR、② Allowlist (`.security/pip-audit-ignore.yaml`) に期限付きで追加     |
| `sast` 検知                                       | コード修正。誤検知は `.semgrepignore` ではなく **コードレベルの `# nosemgrep` コメント** を優先 (理由をコメント) |
| `secrets` 検知                                    | **即座にコミットから除去・履歴クリーンアップを判断**、鍵をローテーション、allowlist は最小限    |
| `image` warn (HIGH)                               | ベースイメージ更新 (Dependabot 経由)、緩和不能なら nightly Issue で追跡                 |
| `dockerfile` / `iac` warn                         | 修正タスクをチケット化                                                                  |
| `nightly` 失敗                                    | 自動作成された Issue を週次トリアージ                                                    |

## 11. 観測性 / 監視

- CI 失敗率の傾向は GitHub Actions の Insights タブで確認 (現状は OK)
- Code Scanning Alerts は週次トリアージ
- Dependabot Security Updates は別途オートで通知される (GitHub 標準)
- Nightly Issue は label `security,nightly` でフィルタ可能

## 12. テスト戦略 (本パイプライン自体の検証)

CI/CD 自体には自動テストを書きづらいので、以下で検証する:

1. **意図的に失敗するシナリオを一度ずつ通す**: 設定後 1 回だけ、以下のテストを **専用 PR を立てて実証**:
   - 既知 CVE のあるパッケージを一時的に pin → `deps` ジョブが赤になることを確認
   - `tests/fixtures/jwt_test_keys` 以外の場所に偽の API キーを置く → `secrets` ジョブが赤になる
   - わざと `os.system(user_input)` を書く → `sast` が赤になる
   - 終わったら revert
2. **正常系**: 既存コードベース (`feature/ec-api-impl` 相当) に対して **すべて緑** であることを確認
3. **再現性**: §9 のローカルコマンドが CI と同じ結果を出す

## 13. ロールアウト計画

1. PR-A (本実装): `.github/workflows/*`, `.github/dependabot.yml`, allowlist 雛形, README 追記 (§9 コマンド集 + ブランチ保護設定手順)
2. PR-A マージ前: `feature/ci-security` ブランチで意図的失敗 PR をテスト
3. PR-A マージ後: main にブランチ保護を手動設定
4. 1 週間運用観察。 `image` / `dockerfile` / `iac` のノイズを評価し、必要なら必須化判断
5. 開発者向けに「CI/Security 失敗時のフロー」ドキュメントを README に追記

## 14. オープン項目 (将来検討)

- 既存 `feature/ec-api-impl` の `Makefile` 削除 (本 spec は新規追加しないだけ。retroactive 削除は別 PR で対応)
- CodeQL の追加 (Python は Semgrep と重複するが SARIF カバレッジ向上)
- DAST 統合 (`本番デプロイ先` 確定後)
- Dependabot 自動マージ (minor/patch のみ、security label 付きのみ)
- ライセンスコンプライアンス (FOSSA / scancode)
- ブランチ保護を Terraform で IaC 化
- SLSA Level 2 / 3 対応 (build provenance)
- 署名付きコンテナイメージ (cosign)
- ショートカット用 `poethepoet` 導入 (`poe ci`, `poe dev` 等)
