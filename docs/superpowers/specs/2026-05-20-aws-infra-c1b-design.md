# C-1b: AWS CD パイプライン (App + Infra)

**Date:** 2026-05-20
**Status:** Approved (designing)
**Predecessor:** `2026-05-19-aws-infra-c1a-design.md`
**Successors:** C-1c (prod 設定整合: JWT env / OTel / NR)

## 1. 目的

C-1a で揃えた静的 IaC アーティファクト (Terraform + ecspresso) を、GitHub Actions から **OIDC で AWS に push** できる CD パイプラインに仕上げる。

スコープは **本番環境 (`prod`) への自動デプロイ** に限定する。staging などのマルチ環境化は spec §11 で follow-up。

## 2. テスタビリティの制約

C-1a と同じ。AWS への live apply は本セッションでは行わない:

- 追加した workflow ファイルは `actionlint` で構文検証
- Terraform 変更は `terraform fmt -check` / `terraform validate` / `tflint`
- ecspresso JSON は `jq empty` での構文検証
- IAM policy / ALB / B/G 切替の正当性は **レビューと spec 上の根拠** で担保し、実 trigger は operator 作業

## 3. 主要決定 (Approval 済み)

| 項目 | 決定 | 根拠 |
|---|---|---|
| 環境スコープ | `prod` のみ | C-1a が env=prod 固定。マルチ環境は spec §11 follow-up |
| トリガー | main merge + manual approval | GitHub Environment `production` の protection rule |
| Migration | `ecspresso run` で deploy 前に自動実行 (`alembic upgrade head`) | C-1a runbook §4 と同じ流れ |
| api deploy 戦略 | **ECS Native Blue/Green** (2025-07 リリース) | Listener Rule 切替、bake time 5 分 |
| worker deploy 戦略 | rolling + ECS deployment circuit breaker | ALB なしで B/G 不可。3 worker は独立 |
| Bake time | 5 分 | alarm 点灯 (1–2 分) + スモークテストの余裕 |
| Abort alarm | ALB 5xx **OR** target unhealthy | CloudWatch metric alarm 2 個 |
| Test traffic listener | **設けない** | Listener Rule の forward 先切替のみ |
| Image tag | git short SHA のみ | IMMUTABLE ECR と整合、`latest` 不使用 |
| Terraform CD | アプリと独立した workflow / PR で `terraform plan` コメント / main merge + manual approval で `terraform apply` | インフラ事故が app に波及しないように分離 |
| OIDC sub 制限 | `repo:takuta77/ec:ref:refs/heads/main` または `repo:takuta77/ec:environment:production` を StringEquals で許可 (§7.6 参照) | feature branch / PR からデプロイ不可。manual approval と二重防護 |
| Tool | **HashiCorp Terraform 1.10+** (OpenTofu から乗り換え) | ユーザ判断 |
| Terraform state | S3 + S3 native locking (`use_lockfile = true`) | DynamoDB lock table 不使用 (Terraform 1.10+ 機能) |

## 4. アーキテクチャ

```
                ┌────────────────────────────────────────┐
                │  GitHub repo (main branch)             │
                └────────────────────────────────────────┘
                    │                            │
                    │ infra/terraform/** 含む    │ app コード変更
                    ▼                            ▼
        ┌─────────────────────────┐  ┌──────────────────────────┐
        │ terraform.yml           │  │ cd.yml                   │
        │                         │  │                          │
        │ PR:   plan + コメント    │  │ PR:   no-op (既存 CI のみ) │
        │ main: plan → approval   │  │ main: build → push →     │
        │       → apply           │  │       migrate → approval │
        │                         │  │       → deploy           │
        └─────────────────────────┘  └──────────────────────────┘
                    │                            │
                    ▼                            ▼
            AWS (Terraform state)        AWS (ECR / ECS / RDS / ALB)
```

両 workflow は別ファイル・別 concurrency group。インフラ変更失敗が app deploy を止めない (逆も同様)。

## 5. ファイル構成

```
.github/workflows/
├── ci.yml                # 既存 — terraform job は削除
├── cd.yml                # NEW (app build → migrate → deploy)
├── terraform.yml         # NEW (infra plan/apply)
├── _security-reusable.yml  # 既存
├── security.yml          # 既存
└── nightly-security.yml  # 既存

infra/terraform/
├── providers.tf          # required_version >= 1.10
├── backend.tf            # S3 + use_lockfile = true (DynamoDB 削除)
├── variables.tf          # tflock_table 削除 / tfstate_bucket 残存
├── network.tf            # 既存
├── ecr.tf, ecs.tf        # 既存
├── rds.tf, mq.tf         # 既存
├── secrets.tf            # 既存
├── iam.tf                # OIDC sub 絞り込み + B/G & state 権限追加
├── alb.tf                # ★ TG 2 個 + listener.lifecycle.ignore_changes + alarms
├── outputs.tf            # ★ green TG ARN / alarm name / B/G role を追加
├── README.md             # ★ tofu → terraform、DynamoDB 削除、CD/手動境界
└── .terraform.lock.hcl   # ★ HashiCorp Terraform で再生成

infra/ecspresso/
├── api/
│   └── ecs-service-def.json    # ★ BLUE_GREEN strategy + alarms
├── outbox-relay/
│   └── ecs-service-def.json    # ★ rolling + circuit breaker 明示
├── order-consumer/
│   └── ecs-service-def.json    # ★ 同上
└── checkout-sweeper/
    └── ecs-service-def.json    # ★ 同上
```

## 6. GitHub Actions Workflow

### 6.1 `cd.yml` (App CD)

```yaml
name: cd
on:
  push:
    branches: [main]
    paths-ignore:
      - 'infra/terraform/**'
      - 'docs/**'
      - '*.md'
  workflow_dispatch:
    inputs:
      ref:
        description: "Git SHA to deploy (omit for HEAD)"
        required: false
      skip_migrate:
        description: "Skip alembic migration step (hotfix only)"
        type: boolean
        default: false

permissions:
  id-token: write   # OIDC
  contents: read

concurrency:
  group: cd-prod
  cancel-in-progress: false
```

| Job | Needs | Environment | 概要 |
|---|---|---|---|
| `build-and-push` | — | — | OIDC assume → ECR login → docker buildx build/push (`tag = ${{ github.sha }}` の short 7 桁) → SBOM artifact |
| `migrate` | `build-and-push` | — | ecspresso run で `alembic upgrade head` を ECS one-off task。`skip_migrate=true` なら no-op |
| `approval` | `migrate` | **production** | `echo OK` のみ。GitHub Environment protection rule で人間承認 |
| `deploy-api` | `approval` | production | ecspresso deploy api (B/G、bake 5 分) |
| `deploy-workers` | `approval` | production | `strategy.matrix: [outbox-relay, order-consumer, checkout-sweeper]`、3 並列 (rolling + CB) |

deploy-api と deploy-workers は並列起動。どちらかの fail は他に波及しない (workflow 全体としては fail 表示)。

### 6.2 `terraform.yml` (Infra CD)

```yaml
name: terraform
on:
  pull_request:
    paths: ['infra/terraform/**', '.github/workflows/terraform.yml']
  push:
    branches: [main]
    paths: ['infra/terraform/**', '.github/workflows/terraform.yml']

permissions:
  id-token: write
  contents: read
  pull-requests: write   # plan コメント

concurrency:
  group: terraform-prod
  cancel-in-progress: false
```

| Job | 起動条件 | Environment | 概要 |
|---|---|---|---|
| `plan` | PR + push to main | — | OIDC assume → `terraform init` → `terraform plan -out=tfplan` → PR の場合は `tfcmt plan` でコメント、main の場合は tfplan を artifact 化 |
| `apply` | push to main のみ | **production** | `needs: plan` + manual approval → `actions/download-artifact` で tfplan を取得 → `terraform apply tfplan` |

**plan artifact 引き継ぎ**: plan で見たもの === apply されるもの、を保証 (race / drift 防止)。

**既存 `ci.yml` の terraform job (warn-only) は削除**。terraform.yml が required check になる。

### 6.3 SHA-pinned Action 一覧

| 用途 | Action |
|---|---|
| Checkout | `actions/checkout@<sha>` (既存) |
| AWS OIDC | `aws-actions/configure-aws-credentials@<sha>` |
| ECR login | `aws-actions/amazon-ecr-login@<sha>` |
| Docker buildx | `docker/setup-buildx-action@<sha>` |
| Docker build/push | `docker/build-push-action@<sha>` |
| ecspresso setup | `kayac/ecspresso@<sha>` (v2.5+ pin) |
| Terraform setup | `hashicorp/setup-terraform@<sha>` |
| tfcmt | `shmokmt/actions-setup-tfcmt@<sha>` |
| SBOM | `anchore/sbom-action@<sha>` |
| Artifact up/down | `actions/upload-artifact@<sha>` / `actions/download-artifact@<sha>` |

全 SHA は実装時に最新リリースから採取して pin、`renovate.json5` or `dependabot.yml` で自動更新対象に。

## 7. Terraform 変更点

### 7.1 ツール乗り換え (OpenTofu → HashiCorp Terraform)

- `.terraform.lock.hcl` を **削除** → Terraform 1.10+ で `terraform init` 実行して再生成 (provider hash registry が `registry.terraform.io` に変わる)
- `infra/terraform/README.md` 内の `tofu` を **全て** `terraform` に書き換え
- HashiCorp BSL ライセンスへの同意が必要。インストール経路は (a) HashiCorp 公式 tarball / (b) tfenv / (c) GitHub Actions の `setup-terraform`。CI/本書では (c) が主、ローカルは operator 判断

### 7.2 `providers.tf`

```hcl
terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.60" }
  }
}
```

### 7.3 `backend.tf`

```hcl
terraform {
  backend "s3" {
    bucket       = "REPLACE_ME_ec-api-tfstate"     # operator が書き換え
    key          = "ec-api/terraform.tfstate"
    region       = "ap-northeast-1"
    encrypt      = true
    use_lockfile = true                             # ← S3 native locking
  }
}
```

DynamoDB lock table は **使わない**。state の履歴は S3 versioning で取る (operator が bootstrap 時に `s3api put-bucket-versioning` で ON)。

### 7.4 `variables.tf`

- 削除: `tflock_table`
- 既存: `tfstate_bucket`、project, env, region など

### 7.5 `alb.tf`

```hcl
# 既存 aws_lb_target_group.api を BLUE 側に位置付け (リネームなし)

resource "aws_lb_target_group" "api_green" {
  name        = "${var.project}-${var.env}-api-green"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    path                = "/healthz"
    matcher             = "200"
    interval            = 30
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

# Production listener — ECS B/G が default_action を listener rule 単位で書き換える。
# Terraform は両 TG の存在のみ管理し、active な TG 選択は ECS に委ねる。
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  # nosemgrep: terraform.aws.security.insecure-load-balancer-tls-version.insecure-load-balancer-tls-version
  protocol = "HTTP"  # TLS は C-1c
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn   # 初期値、ECS が更新
  }
  lifecycle {
    ignore_changes = [default_action]  # ECS B/G が変更する
  }
}

# CloudWatch alarms (BLUE / GREEN 各 TG に対して同じ閾値で 2 個ずつ作る)。
# api service の alarms に両 TG のものを並べることで、active 側がどちらでも abort 可能。
locals {
  api_target_groups = {
    blue  = aws_lb_target_group.api.arn_suffix
    green = aws_lb_target_group.api_green.arn_suffix
  }
}

resource "aws_cloudwatch_metric_alarm" "api_5xx" {
  for_each            = local.api_target_groups
  alarm_name          = "${var.project}-${var.env}-api-5xx-${each.key}"
  metric_name         = "HTTPCode_Target_5XX_Count"
  namespace           = "AWS/ApplicationELB"
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 5
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  dimensions = {
    LoadBalancer = aws_lb.main.arn_suffix
    TargetGroup  = each.value
  }
}

resource "aws_cloudwatch_metric_alarm" "api_unhealthy" {
  for_each            = local.api_target_groups
  alarm_name          = "${var.project}-${var.env}-api-unhealthy-${each.key}"
  metric_name         = "UnHealthyHostCount"
  namespace           = "AWS/ApplicationELB"
  statistic           = "Maximum"
  period              = 30
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  dimensions = {
    LoadBalancer = aws_lb.main.arn_suffix
    TargetGroup  = each.value
  }
}
```

### 7.6 `iam.tf` — OIDC sub 絞り込み + 権限追加

```hcl
# 旧: StringLike sub = "repo:takuta77/ec:*"
# 新:
condition {
  test     = "StringEquals"
  variable = "token.actions.githubusercontent.com:sub"
  values = [
    "repo:takuta77/ec:ref:refs/heads/main",
    "repo:takuta77/ec:environment:production",
  ]
}
```

> GitHub OIDC token の `sub` は context により形式が変わる:
> - branch context: `repo:OWNER/REPO:ref:refs/heads/main` (e.g. terraform.yml の plan job)
> - environment context: `repo:OWNER/REPO:environment:production` (e.g. cd.yml の deploy-api job)
>
> 両方許可することで、plan/build (env なし) と deploy/apply (env あり) の両方が assume 可能。

**追加 statement (B/G 用):**

```hcl
statement {
  sid = "ElbForBlueGreen"
  actions = [
    "elasticloadbalancing:DescribeTargetGroups",
    "elasticloadbalancing:DescribeListeners",
    "elasticloadbalancing:DescribeRules",
    "elasticloadbalancing:ModifyListener",
    "elasticloadbalancing:ModifyRule",
  ]
  resources = ["*"]
}

statement {
  sid       = "CloudWatchForAlarms"
  actions   = ["cloudwatch:DescribeAlarms"]
  resources = ["*"]
}
```

**追加 statement (Terraform state 用):**

```hcl
statement {
  sid = "TerraformState"
  actions = [
    "s3:GetObject", "s3:PutObject", "s3:DeleteObject",
    "s3:ListBucket",
  ]
  resources = [
    "arn:aws:s3:::${var.tfstate_bucket}/*",
    "arn:aws:s3:::${var.tfstate_bucket}",
  ]
}
```

`use_lockfile = true` の lock オブジェクトも同じ bucket 内に置かれる (`<key>.tflock`) ため、上記 S3 権限でカバー。DynamoDB statement は **削除**。

**Terraform apply 自体に必要な広めの権限** (ECS / RDS / ALB / IAM / Secrets / VPC ... をすべて create/modify する): 別途 `aws_iam_role` `github_terraform` を作るか、`github_deploy` 役割に追加するか。本 spec では **シンプルさ優先で同一 role に admin 相当を持たせる** とする (理由: 最小権限化はチーム規模次第で大幅な追加作業 — follow-up 候補)。**ただし `iam:PassRole` は exec role / task role に限定** (B/G は AWS service-linked role `AWSServiceRoleForECS` を使うため PassRole 不要、§7.7)。

### 7.7 ECS B/G 用 service-linked role

ECS Native B/G は内部で ALB の listener rule を書き換えるため、ECS service にアタッチする `roleArn` (advancedConfiguration.roleArn) が必要。AWS の service-linked role `AWSServiceRoleForECS` で標準対応するため、追加 IAM 不要 (確認: AWS Docs)。spec 上は明示的な追加リソースなし。

### 7.8 `outputs.tf`

追加 outputs (ecspresso `tfstate` 参照用):

```hcl
output "alb_target_group_green_arn" { value = aws_lb_target_group.api_green.arn }
output "api_5xx_alarm_names"        { value = [for k, v in aws_cloudwatch_metric_alarm.api_5xx : v.alarm_name] }
output "api_unhealthy_alarm_names"  { value = [for k, v in aws_cloudwatch_metric_alarm.api_unhealthy : v.alarm_name] }
```

## 8. ecspresso 変更点

### 8.1 `api/ecs-service-def.json` (Blue/Green)

```jsonc
{
  "deploymentController": { "type": "ECS" },
  "deploymentConfiguration": {
    "strategy": "BLUE_GREEN",
    "bakeTimeInMinutes": 5
  },
  "loadBalancers": [
    {
      "containerName": "app",
      "containerPort": 8000,
      "targetGroupArn": "{{ tfstate `aws_lb_target_group.api.arn` }}",
      "advancedConfiguration": {
        "alternateTargetGroupArn": "{{ tfstate `aws_lb_target_group.api_green.arn` }}",
        "productionListenerRule":  "{{ tfstate `aws_lb_listener.http.arn` }}"
      }
    }
  ],
  "alarms": {
    "enable":   true,
    "rollback": true,
    "alarmNames": [
      "{{ tfstate `aws_cloudwatch_metric_alarm.api_5xx[\"blue\"].alarm_name` }}",
      "{{ tfstate `aws_cloudwatch_metric_alarm.api_5xx[\"green\"].alarm_name` }}",
      "{{ tfstate `aws_cloudwatch_metric_alarm.api_unhealthy[\"blue\"].alarm_name` }}",
      "{{ tfstate `aws_cloudwatch_metric_alarm.api_unhealthy[\"green\"].alarm_name` }}"
    ]
  },
  "healthCheckGracePeriodSeconds": 60
}
```

> ecspresso v2.5+ が必要。CI / runbook で version pin。

### 8.2 `{outbox-relay, order-consumer, checkout-sweeper}/ecs-service-def.json`

```jsonc
{
  "deploymentController": { "type": "ECS" },
  "deploymentConfiguration": {
    "maximumPercent": 200,
    "minimumHealthyPercent": 100,
    "deploymentCircuitBreaker": {
      "enable":   true,
      "rollback": true
    }
  }
}
```

(loadBalancers / alarms は含めない — worker は ALB なし)

## 9. Failure modes

| シナリオ | 検知 | 自動挙動 | 人間アクション |
|---|---|---|---|
| build-and-push 失敗 | job exit ≠ 0 | 以降 skip | 修正 PR |
| migrate 失敗 | job exit ≠ 0 | approval / deploy skip。旧コード + 旧 schema が継続稼働 | DB 状態確認 → 修正 PR |
| api B/G abort (alarm 発火) | CloudWatch alarm | ECS が listener rule を BLUE に戻し GREEN task 停止 | logs / metrics 確認 → 修正 PR |
| api B/G timeout (bake 経過しても steady-state 到達せず) | ECS deployment timeout | 同 abort | 同上 |
| worker CB 発火 | ECS deployment circuit breaker | その worker のみ前 task def に rollback、api 無影響 | logs 確認 → 修正 PR |
| terraform apply 失敗 | job exit ≠ 0 | state 中途半端 | `terraform plan` で確認、手動修復 or `git revert` |
| manual approval 放置 | GitHub Environment の wait timer / 自動 fail はデフォルトなし。pending state が 30 日経過すると workflow_run が自動 cancel | pending のまま | 承認 or キャンセル |

## 10. Migration 安全策

- **expand-contract pattern を README で要求**: schema 変更は 2 段階。CD では強制しないがレビュー時に確認
- ecspresso run の timeout はデフォルト 30 分。それを超える migration は operator が事前に手で実行する前提
- `skip_migrate: true` 入力で migration を skip 可能 (hotfix で schema 変更なしの場合)
- migration 失敗時は alembic state を確認、`alembic downgrade -1` 等は **CD では行わない** — operator 判断

## 11. オープン項目 (follow-up)

- TLS / Route53 / ACM (C-1c または独立 PR)
- JWT を env-based に切り替え (C-1c)
- staging 環境追加 (Terraform workspace or ディレクトリ分離)
- Multi-AZ RDS / HA NAT / WAF
- canary / weighted shift (50% → 100%) for api B/G
- terraform plan の policy check (OPA / Sentinel)
- Slack / Teams 通知
- Secret rotation 自動化
- `github_terraform` admin role を `github_deploy` から分離 (最小権限化)

## 12. 非ゴール

- 実 AWS への live apply
- canary / weighted shift
- staging 環境
- アプリコード改修 (JWT env / OTel など → C-1c)

## 13. 完了条件 (Definition of Done)

- [ ] `cd.yml` / `terraform.yml` が main に存在、両方とも `actionlint` で構文チェック pass
- [ ] Terraform 変更:
  - [ ] `.terraform.lock.hcl` を HashiCorp Terraform 1.10+ で再生成
  - [ ] `backend.tf` が `use_lockfile = true`、DynamoDB 参照なし
  - [ ] `alb.tf` に green TG + listener `ignore_changes = [default_action]` + alarms 4 個 (BLUE/GREEN × 5xx/unhealthy)
  - [ ] `iam.tf` の OIDC sub が `ref:refs/heads/main` と `environment:production` の StringEquals + B/G & S3 state 権限追加
  - [ ] `variables.tf` から `tflock_table` 削除
  - [ ] `outputs.tf` に green TG / alarm 名を追加
- [ ] ecspresso:
  - [ ] api service-def が `strategy: BLUE_GREEN` + alarms 4 個参照
  - [ ] worker 3 個が `deploymentCircuitBreaker: enable+rollback`
- [ ] README runbook が `terraform` 統一 + S3 native locking + CD/手動境界 + rollback / migration skip 手順
- [ ] 既存 `ci.yml` の terraform job (warn-only) を削除
- [ ] `terraform fmt -check` / `terraform validate` / `tflint` / ecspresso JSON `jq empty` / `actionlint` がすべて pass
- [ ] live AWS への apply は実施しない (operator runbook 化)
