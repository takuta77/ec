# C-1a: AWS インフラ基盤 (Terraform + ecspresso) 設計

**Date:** 2026-05-19
**Status:** Draft → ユーザレビュー待ち
**Scope:** EC API を AWS ECS Fargate 上で本番運用するための静的インフラを Terraform で、ECS サービス/タスク定義を ecspresso で記述する。C-1 (本番デプロイ) の最初のサブプロジェクト。

**C-1 サブプロジェクト分割:**
- **C-1a (本 spec)** — Terraform 静的インフラ + ecspresso 定義ファイル。静的検証まで。
- **C-1b (後続)** — CD パイプライン (GitHub Actions: build→push→migrate→`ecspresso deploy`)。
- **C-1c (後続)** — 本番 config / JWT キー env 化 / OTel→New Relic 本番経路 / healthcheck。

---

## 1. 目的と背景

`docs/superpowers/specs/2026-05-12-ec-api-design.md` §13 のオープン項目「本番デプロイ先」。アプリは 1 イメージから 4 プロセス (api / outbox-relay / order-consumer / checkout-sweeper) + Postgres / RabbitMQ / otel-collector 依存。これを AWS ECS Fargate で動かす。

ツール選定 (ユーザ決定): デプロイ先 = AWS ECS Fargate、IaC = Terraform + ecspresso。Terraform が「滅多に変わらない基盤」、ecspresso が「アプリのデプロイ単位 (task def revision / service 更新)」を担当する責務分離。

## 2. テスタビリティの制約 (重要・前提)

本セッションでは実 AWS provisioning ができない (AWS アカウント・認証情報・課金が必要)。さらにローカルに `terraform` / `tflint` / `ecspresso` 未導入 (`jq` のみ有)。したがって:

- ✅ **生産物**: Terraform HCL、ecspresso 定義 (yml + task/service JSON)、CI の terraform ジョブ、operator runbook
- ✅ **このセッションで可能な検証**: ecspresso task/service def JSON の `jq empty` 構文チェック、HCL の目視 + 構造レビュー
- ✅ **CI で可能な検証** (実装に含める): `hashicorp/setup-terraform` で `terraform fmt -check` + `terraform validate` (`-backend=false`)、`tflint` (action 経由)
- ❌ **不可**: `terraform apply` / 実 provisioning / `ecspresso verify`/`deploy` (AWS API 必須) → operator runbook として手順記述
- ローカル静的検証をやる場合は `brew install terraform tflint` が前提 (plan に optional ステップとして記載、CI が一次ゲート)

## 3. 責務分担

| ツール | 管理対象 | 変更頻度 |
|---|---|---|
| Terraform | VPC/subnet/SG, ECR, RDS Postgres 16, Amazon MQ (RabbitMQ), Secrets Manager, ALB+TG+listener, IAM (ECS exec/task role + GitHub OIDC deploy role), CloudWatch log groups, ECS cluster, state backend (S3+DynamoDB) | 低 (基盤) |
| ecspresso | 4 サービスの task definition + service definition、`tfstate://` プラグインで Terraform outputs を参照 | 中 (デプロイ毎) |

実際の `ecspresso deploy` 実行は C-1b の CD。C-1a は両者の定義ファイル生産 + 静的検証まで。

## 4. ディレクトリ構成

```
infra/
├── terraform/
│   ├── backend.tf        # S3+DynamoDB state backend (bootstrap は README 手順)
│   ├── providers.tf      # aws provider, region 変数, required_version/required_providers
│   ├── variables.tf      # region, project, env, db_instance_class, mq_instance_type, image_tag 等
│   ├── outputs.tf        # cluster_name, ecr_repository_url, private_subnet_ids, app_security_group_id, secret ARNs, ecs_task_execution_role_arn, ecs_task_role_arn, github_oidc_role_arn, alb_target_group_arn, log_group_name
│   ├── network.tf        # VPC, public×2/private×2 subnet (2 AZ), IGW, NAT GW, route tables
│   ├── ecr.tf            # ECR repository (単一イメージ) + lifecycle policy (untagged 失効)
│   ├── rds.tf            # RDS Postgres 16, db subnet group, parameter group, SG (app SG からのみ 5432)
│   ├── mq.tf             # Amazon MQ RabbitMQ broker (single-instance), SG (app SG からのみ 5671)
│   ├── secrets.tf        # Secrets Manager: database_url, rabbitmq_url, jwt_private_key, jwt_public_key, new_relic_license_key (値は手動投入前提、resource は空 or placeholder + ignore_changes)
│   ├── alb.tf            # ALB(public), target group(api:8000, health /healthz), HTTP listener:80 (TLS/ACM は follow-up)
│   ├── iam.tf            # ECS task execution role(ECR pull+logs+secrets read), task role(最小), GitHub OIDC provider + deploy role(ECR push, ecs:UpdateService/RegisterTaskDefinition, iam:PassRole 限定)
│   ├── ecs.tf            # ECS cluster (Fargate + Fargate Spot capacity provider), CloudWatch log group /ecs/ec-api
│   └── README.md         # operator runbook (bootstrap → apply 順 → ecspresso 初回 → migration)
└── ecspresso/
    ├── api/
    │   ├── ecspresso.yml          # cluster/service/region + tfstate plugin
    │   ├── ecs-task-def.json      # 4 container 共通の image、command=uvicorn、secrets 参照、log config
    │   └── ecs-service-def.json   # desired_count, network(private subnet/app SG), ALB target group 紐付け
    ├── outbox-relay/
    │   ├── ecspresso.yml
    │   ├── ecs-task-def.json      # command=python -m app.workers.outbox_relay
    │   └── ecs-service-def.json   # ALB なし
    ├── order-consumer/
    │   ├── ecspresso.yml
    │   ├── ecs-task-def.json      # command=python -m app.workers.order_consumer
    │   └── ecs-service-def.json   # ALB なし
    └── checkout-sweeper/
        ├── ecspresso.yml
        ├── ecs-task-def.json      # command=python -m app.workers.checkout_sweeper
        └── ecs-service-def.json   # ALB なし
```

## 5. Terraform 詳細

### state backend
`backend.tf` は S3 bucket + DynamoDB lock table を参照。bucket/table 自体は chicken-and-egg のため **bootstrap 手順 (README)** で手動 or 別 mini-terraform で先に作る。`backend.tf` には bucket/table/region/key を変数化せずハードコード相当 (Terraform backend は変数不可) — README に「ここを自分の bucket 名に書き換えてから init」と明記。

### network
- VPC 1 個、2 AZ、public subnet×2 (ALB/NAT)、private subnet×2 (ECS tasks/RDS/MQ)
- IGW + 単一 NAT GW (コスト優先、HA NAT は follow-up)
- ECS タスクは private subnet、ALB のみ public

### RDS Postgres 16
- engine `postgres`, version 16, インスタンスクラスは変数 (`db_instance_class`, default `db.t4g.micro`)
- single-AZ (Multi-AZ は follow-up)、自動バックアップ 7 日
- SG: app security group からの 5432 のみ許可
- credentials: master password は Secrets Manager 管理 (`manage_master_user_password = true`)。接続文字列 `database_url` は別途 secret に手動投入 (RDS endpoint 確定後)

### Amazon MQ (RabbitMQ)
- engine `RABBITMQ`, single-instance broker, インスタンスタイプ変数 (`mq_instance_type`, default `mq.t3.micro`)
- private subnet 配置、SG: app SG からの 5671 (AMQPS) のみ
- credentials は Secrets Manager、`rabbitmq_url` secret に手動投入

### Secrets Manager
- 作成する secret (名前のみ確定、値は operator が後で投入。`ignore_changes = [secret_string]` で drift 無視):
  - `ec-api/database_url`
  - `ec-api/rabbitmq_url`
  - `ec-api/jwt_private_key` (PEM 内容)
  - `ec-api/jwt_public_key` (PEM 内容)
  - `ec-api/new_relic_license_key`
- ARN を outputs に出し、ecspresso task def の `secrets` で参照

### ALB
- public ALB、target group: protocol HTTP port 8000、health check path `/healthz` (既存エンドポイント)
- listener: HTTP:80 のみ (HTTPS/ACM/Route53 は follow-up、§オープン項目)
- api service のみ target group 紐付け、worker 3 つは ALB なし

### IAM
- **ECS task execution role**: ECR pull, CloudWatch logs 書き込み, Secrets Manager 読み取り (作成した secret ARN に限定)
- **ECS task role**: アプリ実行時の AWS API 権限 (現状アプリは AWS SDK を使わないので最小 = ほぼ空。将来 S3 等使うならここに追加)
- **GitHub OIDC**: `token.actions.githubusercontent.com` provider + deploy role。信頼ポリシーは `repo:takuta77/ec:*` (環境制限は follow-up)。権限: ECR push, `ecs:RegisterTaskDefinition`, `ecs:UpdateService`, `ecs:DescribeServices/Tasks`, `iam:PassRole` (exec/task role に限定)。C-1b の CD がこれを assume

### ECS cluster
- Fargate + Fargate Spot capacity provider (コスト優先、worker は Spot 許容、api は follow-up で on-demand 比率調整可)
- CloudWatch log group `/ecs/ec-api` retention 30 日

## 6. ecspresso 詳細

### ecspresso.yml (各サービス共通形)
```yaml
region: "{{ must_env `AWS_REGION` }}"
cluster: ec-api
service: ec-api-<svc>
service_definition: ecs-service-def.json
task_definition: ecs-task-def.json
plugins:
  - name: tfstate
    config:
      url: s3://<state-bucket>/ec-api/terraform.tfstate
```
`<state-bucket>` は README で各自書き換え (backend.tf と一致させる)。

### task def テンプレート参照例
`ecs-task-def.json` で Terraform outputs を `{{ tfstate "<address>" }}` で解決:
- `executionRoleArn`: `{{ tfstate "aws_iam_role.ecs_task_execution.arn" }}`
- `taskRoleArn`: `{{ tfstate "aws_iam_role.ecs_task.arn" }}`
- `secrets`: `[{name: "DATABASE_URL", valueFrom: "{{ tfstate \"aws_secretsmanager_secret.database_url.arn\" }}"}, ...]`
- `logConfiguration`: awslogs group `{{ tfstate "aws_cloudwatch_log_group.ecs.name" }}`
- `image`: `{{ tfstate "aws_ecr_repository.app.repository_url" }}:{{ must_env "IMAGE_TAG" }}` (タグは C-1b の CD が渡す)
- 4 サービスの差分は `command` のみ (api=uvicorn、workers=python -m ...)

### service def テンプレート参照例
- `networkConfiguration.awsvpcConfiguration`: subnets/securityGroups を tfstate から
- api のみ `loadBalancers: [{targetGroupArn: {{ tfstate "aws_lb_target_group.api.arn" }}, containerName: "app", containerPort: 8000}]`
- workers は `loadBalancers` 無し、`desiredCount` 1

### migration
api task def を `command` override (`alembic upgrade head`) して `ecspresso run` で one-off 実行。C-1a では task def が override 可能であることだけ担保 (実行は C-1b)。

## 7. CI 連携 (C-1a 分)

`.github/workflows/ci.yml` に `terraform` ジョブ追加:
- `hashicorp/setup-terraform` で terraform CLI 用意
- `terraform fmt -check -recursive infra/terraform`
- `cd infra/terraform && terraform init -backend=false && terraform validate`
- `tflint` (`terraform-linters/setup-tflint` action、`tflint --recursive` or single dir)
- ecspresso JSON: `find infra/ecspresso -name '*.json' -exec jq empty {} \;`

実 `terraform plan/apply` は CI でやらない (認証・課金)。C-1b の CD が OIDC で実行する設計。本ジョブは required check には**しない** (warn-only から開始、安定後に必須化を C-1b で判断)。

## 8. 静的検証戦略 (本セッション)

実装中に subagent が行う検証:
- ecspresso 全 JSON を `jq empty <file>` で構文チェック (ローカル jq 利用可)
- HCL は terraform CLI 不在のため目視 + 構造レビュー (CI が一次ゲート)。`brew install terraform` できる場合は optional で `terraform fmt`/`validate` も実施し結果記録
- README runbook の手順整合性を self-review
- 既存テスト (`pytest`/`ruff`/`mypy`) に影響なし (infra/ 配下はアプリ対象外)

## 9. operator runbook (README に記載する内容)

1. **bootstrap**: state 用 S3 bucket + DynamoDB table を手動 (or 添付 bootstrap snippet) で作成 → `backend.tf` の bucket/table 名を書き換え
2. **terraform apply**: `cd infra/terraform && terraform init && terraform apply` (VPC→RDS→MQ→ALB→IAM→ECS 順は依存解決で自動)
3. **secret 値投入**: RDS/MQ endpoint 確定後、`aws secretsmanager put-secret-value` で `database_url` `rabbitmq_url` `jwt_*` `new_relic_license_key` を投入
4. **イメージ push**: (C-1b の CD で自動化されるが手動手順も記載) ECR login → `docker build`/`push`
5. **初回 ecspresso deploy**: 各サービスで `IMAGE_TAG=... ecspresso deploy --config infra/ecspresso/<svc>/ecspresso.yml`
6. **migration**: `IMAGE_TAG=... ecspresso run --config infra/ecspresso/api/ecspresso.yml --overrides '{"containerOverrides":[{"name":"app","command":["alembic","upgrade","head"]}]}'`
7. **検証**: ALB DNS で `/healthz` 200

## 10. ロールアウト計画

1. C-1a PR: `infra/terraform/*`, `infra/ecspresso/*`, `ci.yml` terraform ジョブ, README runbook
2. CI: terraform fmt/validate/tflint + ecspresso JSON 構文 green
3. main マージ後、operator が runbook に従い実 provisioning (このセッション外)
4. 続いて C-1b (CD), C-1c (本番 config) を別サイクル

## 11. オープン項目 (将来検討)

### C-1 続きのサブプロジェクト (確定済み後続)
- **C-1b**: CD パイプライン (OIDC → build/push → migration `ecspresso run` → `ecspresso deploy`×4 ローリング)
- **C-1c**: 本番 config / JWT キーの env 化 (アプリ改修 or entrypoint shim) / OTel→New Relic 本番経路 / readiness probe

### インフラ強化 (follow-up)
- TLS: ACM 証明書 + ALB HTTPS listener + Route53 DNS
- RDS Multi-AZ / リードレプリカ / バックアップ詳細
- NAT GW の AZ 冗長化
- マルチ環境 (staging/prod、Terraform workspace or ディレクトリ分離)
- WAF / CloudFront
- Fargate on-demand/Spot 比率チューニング、RDS/MQ サイジング最適化
- terraform plan を PR にコメントする仕組み (Atlantis / tfcmt) — C-1b 範疇
- Secrets ローテーション自動化

## 12. 非ゴール (本 spec で扱わない)

- 実 AWS provisioning (operator runbook 化)
- アプリコードの改修 (JWT キー読み取り方式変更は C-1c)
- CD 自動化 (C-1b)
- DNS / TLS 終端 (follow-up)
- マルチリージョン / DR
