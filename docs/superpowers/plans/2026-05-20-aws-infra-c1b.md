# C-1b AWS CD Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire up GitHub Actions CD for the ec-api stack on AWS (App: ECS Native Blue/Green + worker rolling; Infra: Terraform plan/apply pipeline) and switch the toolchain from OpenTofu to HashiCorp Terraform 1.10+ with S3 native locking.

**Architecture:** Two independent workflows — `cd.yml` (build → push → migrate → approval → deploy api B/G + 3 workers rolling) and `terraform.yml` (PR plan w/ comment → main merge approval → apply). Both use OIDC with `sub` restricted to `ref:refs/heads/main` OR `environment:production`. Terraform alb.tf gets a second target group + 4 CloudWatch alarms for B/G abort; ecspresso api service-def switches to `strategy: BLUE_GREEN`. State management drops DynamoDB in favour of S3 native locking (`use_lockfile = true`).

**Tech Stack:** GitHub Actions, AWS OIDC, ECR, ECS Fargate (Native B/G + rolling), ALB, CloudWatch alarms, Secrets Manager, HashiCorp Terraform 1.10+, ecspresso v2.5+, actionlint, tflint.

**Scope boundary:** Static validation only — no live AWS apply. Operator runbook documents the manual bootstrap (state bucket creation).

**Spec:** `docs/superpowers/specs/2026-05-20-aws-infra-c1b-design.md`

**Working directory:** worktree `.worktrees/aws-infra-c1b/`, branch `feature/aws-infra-c1b` (already created, spec already committed).

---

## File map

| File | Action | Owns |
|---|---|---|
| `infra/terraform/providers.tf` | Modify | `required_version` bump to `>= 1.10` |
| `infra/terraform/backend.tf` | Modify | drop `dynamodb_table`, add `use_lockfile = true` |
| `infra/terraform/variables.tf` | (no change for now — `tfstate_bucket` is referenced only in iam.tf via `var.tfstate_bucket`; add it here) | bucket var |
| `infra/terraform/iam.tf` | Modify | OIDC sub `StringEquals` (2 values), B/G + CloudWatch + S3 state + admin statements |
| `infra/terraform/alb.tf` | Modify | green TG, listener `lifecycle.ignore_changes`, 4 CloudWatch alarms |
| `infra/terraform/outputs.tf` | Modify | green TG ARN + alarm names |
| `infra/terraform/.terraform.lock.hcl` | Delete + regenerate | Terraform 1.10 provider hashes |
| `infra/terraform/README.md` | Modify | `tofu`→`terraform`, drop DynamoDB, CD/manual boundary, rollback, skip_migrate |
| `infra/ecspresso/api/ecs-service-def.json` | Modify | `strategy: BLUE_GREEN` + alarms |
| `infra/ecspresso/outbox-relay/ecs-service-def.json` | Modify | `deploymentCircuitBreaker` |
| `infra/ecspresso/order-consumer/ecs-service-def.json` | Modify | `deploymentCircuitBreaker` |
| `infra/ecspresso/checkout-sweeper/ecs-service-def.json` | Modify | `deploymentCircuitBreaker` |
| `.github/workflows/ci.yml` | Modify | remove `terraform:` job (lines 82–107) |
| `.github/workflows/cd.yml` | Create | App CD workflow |
| `.github/workflows/terraform.yml` | Create | Infra plan/apply workflow |

---

## Pre-flight: tooling check

Before Task 1, verify these are available in your shell:

```bash
# Required
terraform -version    # need >= 1.10
jq --version
actionlint --version  # via `brew install actionlint`

# Optional (used in CI; local nice-to-have)
tflint --version
```

If `terraform` is below 1.10:
- macOS: `brew install hashicorp/tap/terraform` (HashiCorp BSL — confirm with user before installing)
- Alternative: download official binary from https://developer.hashicorp.com/terraform/install
- Fallback: `tofu` (>= 1.10) **can be used for `validate` only**, but the lockfile must be regenerated with `terraform` so do NOT skip the regenerate step.

If `actionlint` missing: `brew install actionlint`.

---

## Task 1: Tooling switch — providers.tf + backend.tf + lockfile

**Files:**
- Modify: `infra/terraform/providers.tf:2`
- Modify: `infra/terraform/backend.tf`
- Delete + regenerate: `infra/terraform/.terraform.lock.hcl`

- [ ] **Step 1: Bump `required_version` to 1.10**

Edit `infra/terraform/providers.tf` line 2:

```hcl
terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
  }
}
```

- [ ] **Step 2: Switch backend to S3 native locking**

Replace entire contents of `infra/terraform/backend.tf` with:

```hcl
# Bootstrap (run ONCE, manually, before `terraform init`):
#   aws s3api create-bucket --bucket <STATE_BUCKET> --region <REGION> \
#     --create-bucket-configuration LocationConstraint=<REGION>
#   aws s3api put-bucket-versioning --bucket <STATE_BUCKET> \
#     --versioning-configuration Status=Enabled
#
# Then replace the placeholder below with your real bucket and run
# `terraform init`. Terraform backend blocks do NOT support variables.
#
# State locking uses S3 native locking (Terraform 1.10+), no DynamoDB.
terraform {
  backend "s3" {
    bucket       = "REPLACE_ME_ec-api-tfstate"
    key          = "ec-api/terraform.tfstate"
    region       = "ap-northeast-1"
    encrypt      = true
    use_lockfile = true
  }
}
```

- [ ] **Step 3: Delete old lockfile**

```bash
rm -f infra/terraform/.terraform.lock.hcl
```

- [ ] **Step 4: Regenerate lockfile with HashiCorp Terraform**

```bash
cd infra/terraform
terraform init -backend=false
```

Expected: `Terraform has been successfully initialized!` and a new `.terraform.lock.hcl` appears with `registry.terraform.io` provider hashes.

If `terraform init` warns about a missing backend bucket — that's expected for `-backend=false`; the lock file is the only thing we want.

- [ ] **Step 5: Validate**

```bash
cd infra/terraform
terraform fmt -check
terraform validate
```

Expected: `Success! The configuration is valid.` and no fmt diffs.

- [ ] **Step 6: Commit**

```bash
git add infra/terraform/providers.tf \
        infra/terraform/backend.tf \
        infra/terraform/.terraform.lock.hcl
git commit -m "infra(tf): switch to HashiCorp Terraform 1.10 + S3 native locking"
```

---

## Task 2: Variables — add tfstate_bucket

**Files:**
- Modify: `infra/terraform/variables.tf` (append)

- [ ] **Step 1: Append the variable**

Append to `infra/terraform/variables.tf`:

```hcl
variable "tfstate_bucket" {
  type        = string
  description = "S3 bucket holding terraform state (granted to GitHub deploy role)"
}
```

No `default` — operator must provide via `terraform.tfvars` or `-var` flag.

- [ ] **Step 2: Validate**

```bash
cd infra/terraform
terraform fmt -check
terraform validate
```

Expected: `terraform validate` will say `Success!` (variable without default is fine; required-at-apply behaviour).

- [ ] **Step 3: Commit**

```bash
git add infra/terraform/variables.tf
git commit -m "infra(tf): add tfstate_bucket variable for deploy role IAM"
```

---

## Task 3: iam.tf — OIDC sub restriction + B/G & state permissions

**Files:**
- Modify: `infra/terraform/iam.tf:57-61` (sub condition)
- Modify: `infra/terraform/iam.tf:70-103` (policy document, append statements)

- [ ] **Step 1: Tighten OIDC sub condition**

In `infra/terraform/iam.tf`, replace lines 57–61 (the `StringLike` sub condition block) with:

```hcl
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "repo:${var.github_repo}:ref:refs/heads/main",
        "repo:${var.github_repo}:environment:production",
      ]
    }
```

- [ ] **Step 2: Append B/G + CloudWatch + state + admin statements**

In `infra/terraform/iam.tf`, the `data "aws_iam_policy_document" "github_deploy"` block (lines 70–103) currently has 3 statements (EcrPush, EcsDeploy, PassRole). Add these statements **inside** that data block, before the closing `}` on line 103:

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

  statement {
    sid = "TerraformState"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:ListBucket",
    ]
    resources = [
      "arn:aws:s3:::${var.tfstate_bucket}",
      "arn:aws:s3:::${var.tfstate_bucket}/*",
    ]
  }

  # Admin-equivalent for Terraform apply. spec §7.6 — splitting into a
  # separate `github_terraform` role is a follow-up.
  statement {
    sid       = "TerraformApplyAdmin"
    actions   = ["*"]
    resources = ["*"]
  }
```

- [ ] **Step 3: Validate**

```bash
cd infra/terraform
terraform fmt -check
terraform validate
```

Expected: `Success!`.

- [ ] **Step 4: Commit**

```bash
git add infra/terraform/iam.tf
git commit -m "infra(tf): OIDC sub restriction + B/G & state IAM permissions"
```

---

## Task 4: alb.tf — green TG, listener ignore_changes, alarms

**Files:**
- Modify: `infra/terraform/alb.tf` (append green TG, modify listener, append alarms)

- [ ] **Step 1: Append green target group after the existing `aws_lb_target_group.api`**

Insert this after line 53 (the closing `}` of `aws_lb_target_group.api`):

```hcl

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
```

- [ ] **Step 2: Add `lifecycle.ignore_changes` to the listener**

Modify the `aws_lb_listener.http` block (currently lines 57–67). After the `default_action {...}` block and before the closing `}`, add:

```hcl

  lifecycle {
    # ECS Native B/G rewrites the listener forward target group during deploys;
    # let Terraform ignore that drift.
    ignore_changes = [default_action]
  }
```

So the listener block becomes:

```hcl
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  # nosemgrep: terraform.aws.security.insecure-load-balancer-tls-version.insecure-load-balancer-tls-version
  protocol = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }

  lifecycle {
    # ECS Native B/G rewrites the listener forward target group during deploys;
    # let Terraform ignore that drift.
    ignore_changes = [default_action]
  }
}
```

- [ ] **Step 3: Append CloudWatch alarms at the bottom of the file**

Append to `infra/terraform/alb.tf`:

```hcl

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
  treat_missing_data  = "notBreaching"
  dimensions = {
    LoadBalancer = aws_lb.main.arn_suffix
    TargetGroup  = each.value
  }
}
```

- [ ] **Step 4: Validate**

```bash
cd infra/terraform
terraform fmt -check
terraform validate
```

Expected: `Success!`.

- [ ] **Step 5: Commit**

```bash
git add infra/terraform/alb.tf
git commit -m "infra(tf): ALB green target group + listener ignore_changes + B/G alarms"
```

---

## Task 5: outputs.tf — green TG + alarm names

**Files:**
- Modify: `infra/terraform/outputs.tf` (append)

- [ ] **Step 1: Append outputs**

Append to `infra/terraform/outputs.tf`:

```hcl

output "alb_target_group_green_arn" {
  value = aws_lb_target_group.api_green.arn
}

output "api_5xx_alarm_names" {
  value = [for k, v in aws_cloudwatch_metric_alarm.api_5xx : v.alarm_name]
}

output "api_unhealthy_alarm_names" {
  value = [for k, v in aws_cloudwatch_metric_alarm.api_unhealthy : v.alarm_name]
}
```

- [ ] **Step 2: Validate**

```bash
cd infra/terraform
terraform fmt -check
terraform validate
```

Expected: `Success!`.

- [ ] **Step 3: Commit**

```bash
git add infra/terraform/outputs.tf
git commit -m "infra(tf): outputs for green TG ARN + B/G alarm names"
```

---

## Task 6: ecspresso api — Blue/Green strategy

**Files:**
- Modify: `infra/ecspresso/api/ecs-service-def.json`

- [ ] **Step 1: Replace the file contents**

Replace the entire contents of `infra/ecspresso/api/ecs-service-def.json` with:

```json
{
  "launchType": "FARGATE",
  "desiredCount": 1,
  "deploymentController": { "type": "ECS" },
  "deploymentConfiguration": {
    "strategy": "BLUE_GREEN",
    "bakeTimeInMinutes": 5
  },
  "networkConfiguration": {
    "awsvpcConfiguration": {
      "subnets": [
        "{{ tfstate `aws_subnet.private[0].id` }}",
        "{{ tfstate `aws_subnet.private[1].id` }}"
      ],
      "securityGroups": ["{{ tfstate `aws_security_group.app.id` }}"],
      "assignPublicIp": "DISABLED"
    }
  },
  "loadBalancers": [
    {
      "containerName": "app",
      "containerPort": 8000,
      "targetGroupArn": "{{ tfstate `aws_lb_target_group.api.arn` }}",
      "advancedConfiguration": {
        "alternateTargetGroupArn": "{{ tfstate `aws_lb_target_group.api_green.arn` }}",
        "productionListenerRule": "{{ tfstate `aws_lb_listener.http.arn` }}"
      }
    }
  ],
  "alarms": {
    "enable": true,
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

- [ ] **Step 2: Verify JSON syntax**

```bash
jq empty infra/ecspresso/api/ecs-service-def.json
```

Expected: no output (success).

- [ ] **Step 3: Commit**

```bash
git add infra/ecspresso/api/ecs-service-def.json
git commit -m "infra(ecspresso): api service-def -> Blue/Green strategy with alarms"
```

---

## Task 7: ecspresso workers — rolling + circuit breaker

**Files:**
- Modify: `infra/ecspresso/outbox-relay/ecs-service-def.json`
- Modify: `infra/ecspresso/order-consumer/ecs-service-def.json`
- Modify: `infra/ecspresso/checkout-sweeper/ecs-service-def.json`

- [ ] **Step 1: outbox-relay**

Replace the entire contents of `infra/ecspresso/outbox-relay/ecs-service-def.json` with:

```json
{
  "launchType": "FARGATE",
  "desiredCount": 1,
  "deploymentController": { "type": "ECS" },
  "deploymentConfiguration": {
    "maximumPercent": 200,
    "minimumHealthyPercent": 100,
    "deploymentCircuitBreaker": {
      "enable": true,
      "rollback": true
    }
  },
  "networkConfiguration": {
    "awsvpcConfiguration": {
      "subnets": [
        "{{ tfstate `aws_subnet.private[0].id` }}",
        "{{ tfstate `aws_subnet.private[1].id` }}"
      ],
      "securityGroups": ["{{ tfstate `aws_security_group.app.id` }}"],
      "assignPublicIp": "DISABLED"
    }
  }
}
```

- [ ] **Step 2: order-consumer**

Replace the entire contents of `infra/ecspresso/order-consumer/ecs-service-def.json` with the same JSON as Step 1 (identical content — the file pathway is what differs).

- [ ] **Step 3: checkout-sweeper**

Replace the entire contents of `infra/ecspresso/checkout-sweeper/ecs-service-def.json` with the same JSON as Step 1.

- [ ] **Step 4: Verify JSON syntax of all three**

```bash
for svc in outbox-relay order-consumer checkout-sweeper; do
  jq empty "infra/ecspresso/$svc/ecs-service-def.json"
done
```

Expected: no output (success).

- [ ] **Step 5: Commit**

```bash
git add infra/ecspresso/outbox-relay/ecs-service-def.json \
        infra/ecspresso/order-consumer/ecs-service-def.json \
        infra/ecspresso/checkout-sweeper/ecs-service-def.json
git commit -m "infra(ecspresso): workers -> rolling + deployment circuit breaker"
```

---

## Task 8: Remove warn-only terraform job from ci.yml

**Files:**
- Modify: `.github/workflows/ci.yml:82-107`

- [ ] **Step 1: Delete lines 82-107**

In `.github/workflows/ci.yml`, the `terraform:` job block (currently lines 83–107) is replaced by `terraform.yml` (Task 10). Delete it including the blank line above (line 82).

After deletion, the file should end with the previous job (`test-slow`). Verify with:

```bash
grep -n "^  terraform:" .github/workflows/ci.yml || echo "removed OK"
```

Expected: `removed OK`.

- [ ] **Step 2: Lint the workflow**

```bash
actionlint .github/workflows/ci.yml
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: drop warn-only terraform job (moved to terraform.yml)"
```

---

## Task 9: Create cd.yml — App CD workflow

**Files:**
- Create: `.github/workflows/cd.yml`

This task pins several action SHAs. The SHAs below are placeholders — replace each `<SHA-FROM-LATEST-RELEASE>` with a real SHA looked up at implementation time (e.g. `gh api repos/<owner>/<repo>/git/refs/tags/<tag>`). Existing project convention: PR #18 introduced SHA pinning across workflows.

| Action | Where to look up SHA |
|---|---|
| `actions/checkout@<sha>` | Reuse existing SHA from `ci.yml` (`de0fac2e4500dabe0009e67214ff5f5447ce83dd`) |
| `aws-actions/configure-aws-credentials@<sha>` | https://github.com/aws-actions/configure-aws-credentials/releases (latest v4.x) |
| `aws-actions/amazon-ecr-login@<sha>` | https://github.com/aws-actions/amazon-ecr-login/releases (latest v2.x) |
| `docker/setup-buildx-action@<sha>` | https://github.com/docker/setup-buildx-action/releases (latest v3.x) |
| `docker/build-push-action@<sha>` | https://github.com/docker/build-push-action/releases (latest v6.x) |
| `kayac/ecspresso@<sha>` | https://github.com/kayac/ecspresso/releases (v2.5+ tag) |

- [ ] **Step 1: Write `.github/workflows/cd.yml`**

```yaml
name: cd

on:
  push:
    branches: [main]
    paths-ignore:
      - 'infra/terraform/**'
      - 'docs/**'
      - '*.md'
      - '.github/workflows/terraform.yml'
  workflow_dispatch:
    inputs:
      ref:
        description: "Git SHA to deploy (default: HEAD of main)"
        required: false
        type: string
      skip_migrate:
        description: "Skip alembic migration step (hotfix only)"
        required: false
        type: boolean
        default: false

permissions:
  id-token: write   # OIDC
  contents: read

concurrency:
  group: cd-prod
  cancel-in-progress: false

env:
  AWS_REGION: ap-northeast-1
  IMAGE_TAG: ${{ github.event.inputs.ref || github.sha }}

jobs:
  build-and-push:
    name: build-and-push
    runs-on: ubuntu-latest
    outputs:
      image_tag: ${{ steps.tag.outputs.tag }}
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd  # v6.0.2
        with:
          ref: ${{ env.IMAGE_TAG }}
      - id: tag
        run: |
          # 7-char short SHA, matches IMMUTABLE ECR convention
          SHORT=$(git rev-parse --short=7 HEAD)
          echo "tag=$SHORT" >> "$GITHUB_OUTPUT"
      - uses: aws-actions/configure-aws-credentials@<SHA-FROM-LATEST-RELEASE>  # v4.x
        with:
          role-to-assume: arn:aws:iam::${{ vars.AWS_ACCOUNT_ID }}:role/ec-api-prod-gh-deploy
          aws-region: ${{ env.AWS_REGION }}
      - uses: aws-actions/amazon-ecr-login@<SHA-FROM-LATEST-RELEASE>  # v2.x
        id: ecr
      - uses: docker/setup-buildx-action@<SHA-FROM-LATEST-RELEASE>  # v3.x
      - uses: docker/build-push-action@<SHA-FROM-LATEST-RELEASE>  # v6.x
        with:
          context: .
          push: true
          tags: ${{ steps.ecr.outputs.registry }}/ec-api:${{ steps.tag.outputs.tag }}
          provenance: false   # ECR does not support provenance v1 attestations

  migrate:
    name: migrate
    needs: build-and-push
    runs-on: ubuntu-latest
    if: ${{ github.event.inputs.skip_migrate != 'true' }}
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd  # v6.0.2
        with:
          ref: ${{ env.IMAGE_TAG }}
      - uses: aws-actions/configure-aws-credentials@<SHA-FROM-LATEST-RELEASE>  # v4.x
        with:
          role-to-assume: arn:aws:iam::${{ vars.AWS_ACCOUNT_ID }}:role/ec-api-prod-gh-deploy
          aws-region: ${{ env.AWS_REGION }}
      - uses: kayac/ecspresso@<SHA-FROM-LATEST-RELEASE>  # v2.5+
        with:
          version: v2.5.0
      - name: alembic upgrade head
        env:
          IMAGE_TAG: ${{ needs.build-and-push.outputs.image_tag }}
        run: |
          ecspresso run \
            --config infra/ecspresso/api/ecspresso.yml \
            --overrides '{"containerOverrides":[{"name":"app","command":["alembic","upgrade","head"]}]}' \
            --wait-until=stopped

  approval:
    name: approval
    needs: migrate
    if: ${{ always() && (needs.migrate.result == 'success' || needs.migrate.result == 'skipped') }}
    runs-on: ubuntu-latest
    environment: production
    steps:
      - run: echo "Approved by ${{ github.actor }} - proceeding to deploy"

  deploy-api:
    name: deploy-api
    needs: approval
    runs-on: ubuntu-latest
    environment: production
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd  # v6.0.2
        with:
          ref: ${{ env.IMAGE_TAG }}
      - uses: aws-actions/configure-aws-credentials@<SHA-FROM-LATEST-RELEASE>  # v4.x
        with:
          role-to-assume: arn:aws:iam::${{ vars.AWS_ACCOUNT_ID }}:role/ec-api-prod-gh-deploy
          aws-region: ${{ env.AWS_REGION }}
      - uses: kayac/ecspresso@<SHA-FROM-LATEST-RELEASE>  # v2.5+
        with:
          version: v2.5.0
      - name: deploy api (Blue/Green)
        env:
          IMAGE_TAG: ${{ needs.build-and-push.outputs.image_tag }}
        run: |
          ecspresso deploy --config infra/ecspresso/api/ecspresso.yml \
            --wait-until=service-stable

  deploy-workers:
    name: deploy-workers
    needs: approval
    runs-on: ubuntu-latest
    environment: production
    strategy:
      fail-fast: false
      matrix:
        service: [outbox-relay, order-consumer, checkout-sweeper]
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd  # v6.0.2
        with:
          ref: ${{ env.IMAGE_TAG }}
      - uses: aws-actions/configure-aws-credentials@<SHA-FROM-LATEST-RELEASE>  # v4.x
        with:
          role-to-assume: arn:aws:iam::${{ vars.AWS_ACCOUNT_ID }}:role/ec-api-prod-gh-deploy
          aws-region: ${{ env.AWS_REGION }}
      - uses: kayac/ecspresso@<SHA-FROM-LATEST-RELEASE>  # v2.5+
        with:
          version: v2.5.0
      - name: deploy ${{ matrix.service }}
        env:
          IMAGE_TAG: ${{ needs.build-and-push.outputs.image_tag }}
        run: |
          ecspresso deploy --config infra/ecspresso/${{ matrix.service }}/ecspresso.yml \
            --wait-until=service-stable
```

> **Note on `vars.AWS_ACCOUNT_ID`**: a repository variable set in GitHub repo settings (Settings → Variables → Actions). Documented in the runbook (Task 11).

- [ ] **Step 2: Resolve SHA placeholders**

Replace every `<SHA-FROM-LATEST-RELEASE>` with the actual SHA from the action's latest stable release. Tools:

```bash
# Example for aws-actions/configure-aws-credentials latest v4.x:
gh api repos/aws-actions/configure-aws-credentials/releases | \
  jq -r '.[] | select(.tag_name | startswith("v4")) | "\(.tag_name) \(.target_commitish)"' | head -3
# Then resolve the tag to a commit SHA:
gh api repos/aws-actions/configure-aws-credentials/git/refs/tags/v4.X.Y \
  | jq -r .object.sha
```

Apply the same pattern to each placeholder.

- [ ] **Step 3: Lint**

```bash
actionlint .github/workflows/cd.yml
```

Expected: no errors. If `actionlint` flags the `vars.AWS_ACCOUNT_ID` reference, this is fine — it does not validate org/repo vars.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/cd.yml
git commit -m "ci: add cd.yml (App CD with Blue/Green deploy)"
```

---

## Task 10: Create terraform.yml — Infra plan/apply workflow

**Files:**
- Create: `.github/workflows/terraform.yml`

Additional action SHA needed:

| Action | Where to look up SHA |
|---|---|
| `hashicorp/setup-terraform@<sha>` | https://github.com/hashicorp/setup-terraform/releases (latest v3.x) |
| `shmokmt/actions-setup-tfcmt@<sha>` | https://github.com/shmokmt/actions-setup-tfcmt/releases (latest v2.x) — or any tfcmt installer |
| `actions/upload-artifact@<sha>` | Reuse from ci.yml (`043fb46d1a93c77aae656e7c1c64a875d1fc6a0a` v7.0.1) |
| `actions/download-artifact@<sha>` | Look up matching v7 release |

- [ ] **Step 1: Write `.github/workflows/terraform.yml`**

```yaml
name: terraform

on:
  pull_request:
    paths:
      - 'infra/terraform/**'
      - '.github/workflows/terraform.yml'
  push:
    branches: [main]
    paths:
      - 'infra/terraform/**'
      - '.github/workflows/terraform.yml'
  workflow_dispatch:

permissions:
  id-token: write
  contents: read
  pull-requests: write   # for tfcmt plan comment

concurrency:
  group: terraform-prod
  cancel-in-progress: false

env:
  AWS_REGION: ap-northeast-1
  TF_VERSION: "1.10.4"

jobs:
  plan:
    name: plan
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: infra/terraform
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd  # v6.0.2
      - uses: aws-actions/configure-aws-credentials@<SHA-FROM-LATEST-RELEASE>  # v4.x
        with:
          role-to-assume: arn:aws:iam::${{ vars.AWS_ACCOUNT_ID }}:role/ec-api-prod-gh-deploy
          aws-region: ${{ env.AWS_REGION }}
      - uses: hashicorp/setup-terraform@<SHA-FROM-LATEST-RELEASE>  # v3.x
        with:
          terraform_version: ${{ env.TF_VERSION }}
      - name: terraform init
        run: terraform init
      - name: terraform plan
        run: |
          terraform plan -out=tfplan -var "tfstate_bucket=${{ vars.TFSTATE_BUCKET }}"
      - name: upload plan artifact
        if: github.event_name == 'push' && github.ref == 'refs/heads/main'
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a  # v7.0.1
        with:
          name: tfplan
          path: infra/terraform/tfplan
          retention-days: 7
      - name: install tfcmt
        if: github.event_name == 'pull_request'
        uses: shmokmt/actions-setup-tfcmt@<SHA-FROM-LATEST-RELEASE>  # v2.x
      - name: comment plan on PR
        if: github.event_name == 'pull_request'
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          terraform show -no-color tfplan | tfcmt plan -patch

  apply:
    name: apply
    needs: plan
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    environment: production
    defaults:
      run:
        working-directory: infra/terraform
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd  # v6.0.2
      - uses: aws-actions/configure-aws-credentials@<SHA-FROM-LATEST-RELEASE>  # v4.x
        with:
          role-to-assume: arn:aws:iam::${{ vars.AWS_ACCOUNT_ID }}:role/ec-api-prod-gh-deploy
          aws-region: ${{ env.AWS_REGION }}
      - uses: hashicorp/setup-terraform@<SHA-FROM-LATEST-RELEASE>  # v3.x
        with:
          terraform_version: ${{ env.TF_VERSION }}
      - uses: actions/download-artifact@<SHA-FROM-LATEST-RELEASE>  # v7.x
        with:
          name: tfplan
          path: infra/terraform
      - name: terraform init
        run: terraform init
      - name: terraform apply
        run: terraform apply tfplan
```

> **Notes:**
> - `vars.TFSTATE_BUCKET` and `vars.AWS_ACCOUNT_ID` are GitHub repo variables (documented in runbook, Task 11)
> - The download-artifact path is `infra/terraform` (defaults to the file going there). Adjust if it puts it in a subdir; in that case use `path: infra/terraform/` and possibly `mv` the file.
> - `terraform init` before `apply` is needed for the apply job's runner (separate from plan's). Both use the same S3 backend so state is shared.

- [ ] **Step 2: Resolve SHA placeholders**

Same pattern as Task 9 Step 2. Look up each `<SHA-FROM-LATEST-RELEASE>` and substitute.

- [ ] **Step 3: Lint**

```bash
actionlint .github/workflows/terraform.yml
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/terraform.yml
git commit -m "ci: add terraform.yml (Infra plan/apply with PR plan comment)"
```

---

## Task 11: README — terraform commands, drop DynamoDB, CD/manual boundary

**Files:**
- Modify: `infra/terraform/README.md` (full rewrite of bootstrap section + add CD section)

- [ ] **Step 1: Replace the entire file contents**

Replace the entire contents of `infra/terraform/README.md` with:

````markdown
# infra/terraform — Operator Runbook

> This project uses [HashiCorp Terraform](https://developer.hashicorp.com/terraform) (>= 1.10).
> State locking uses S3 native locking (`use_lockfile = true`) — no DynamoDB lock table required.
> The `.terraform.lock.hcl` is generated by Terraform and contains `registry.terraform.io` provider hashes.

---

## 1. Bootstrap remote state (once, before first `terraform init`)

Create the S3 bucket for Terraform state. Choose a name and record it — you will substitute it into `backend.tf` and the ecspresso configs, and register it as a GitHub Actions variable.

```bash
REGION=ap-northeast-1
STATE_BUCKET=<your-bucket-name>      # e.g. myco-ec-api-tfstate

# Create S3 bucket (ap-northeast-1 requires LocationConstraint)
aws s3api create-bucket \
  --bucket "$STATE_BUCKET" \
  --region "$REGION" \
  --create-bucket-configuration LocationConstraint="$REGION"

# Enable versioning on the bucket (used for state history + native lock object)
aws s3api put-bucket-versioning \
  --bucket "$STATE_BUCKET" \
  --versioning-configuration Status=Enabled
```

After the bucket exists, replace the placeholder in two places:

**`infra/terraform/backend.tf`** — replace `REPLACE_ME_ec-api-tfstate`:
```hcl
terraform {
  backend "s3" {
    bucket       = "<your-bucket-name>"
    key          = "ec-api/terraform.tfstate"
    region       = "ap-northeast-1"
    encrypt      = true
    use_lockfile = true
  }
}
```

**`infra/ecspresso/*/ecspresso.yml`** (all four services) — replace `REPLACE_ME_ec-api-tfstate` in the `tfstate` plugin URL:
```yaml
plugins:
  - name: tfstate
    config:
      url: s3://<your-bucket-name>/ec-api/terraform.tfstate
```

---

## 2. Register GitHub Actions variables

The CD workflows (`cd.yml`, `terraform.yml`) read two repository variables. Set them once via:

```bash
gh variable set AWS_ACCOUNT_ID --body "<your-12-digit-aws-account-id>"
gh variable set TFSTATE_BUCKET --body "<your-bucket-name>"
```

Or via Settings → Variables → Actions in the GitHub UI.

You also need to create the `production` GitHub Environment (Settings → Environments → New) and configure **Required reviewers**. Both `cd.yml` and `terraform.yml` use this environment for manual approval gates.

---

## 3. Provision infrastructure

```bash
cd infra/terraform

terraform init              # downloads providers, configures S3 backend
terraform plan \
  -var "tfstate_bucket=<your-bucket-name>"     # required input
terraform apply \
  -var "tfstate_bucket=<your-bucket-name>"
```

Tip: put recurring vars into `terraform.tfvars` (gitignored if not already):
```hcl
tfstate_bucket = "<your-bucket-name>"
```

Key resources created: VPC + subnets, ECS cluster, ALB + BLUE/GREEN target groups + 4 CloudWatch alarms (for B/G abort), RDS (PostgreSQL), Amazon MQ (RabbitMQ), ECR repository, Secrets Manager secrets (empty — populate in step 4), IAM roles.

---

## 4. Populate secrets

After `terraform apply`, the Secrets Manager secrets exist but are empty. Populate each one with the real values.

Secret paths use the env-qualified prefix `ec-api/prod/<key>`.

```bash
# Database URL
aws secretsmanager put-secret-value \
  --secret-id ec-api/prod/database_url \
  --secret-string "postgresql+asyncpg://ec_admin:<password>@<rds-endpoint>:5432/ec"

# RabbitMQ URL (Amazon MQ broker uses AMQPS on port 5671)
aws secretsmanager put-secret-value \
  --secret-id ec-api/prod/rabbitmq_url \
  --secret-string "amqps://<user>:<password>@<mq-endpoint>:5671/"

# JWT private key (PEM file)
aws secretsmanager put-secret-value \
  --secret-id ec-api/prod/jwt_private_key \
  --secret-string file://path/to/jwt_private_key.pem

# JWT public key (PEM file)
aws secretsmanager put-secret-value \
  --secret-id ec-api/prod/jwt_public_key \
  --secret-string file://path/to/jwt_public_key.pem

# New Relic license key
aws secretsmanager put-secret-value \
  --secret-id ec-api/prod/new_relic_license_key \
  --secret-string "<new-relic-license-key>"
```

Retrieve endpoint values:
```bash
terraform output
```

---

## 5. Deploys

### 5.1 Automated (default — via `cd.yml`)

Push or merge to `main` (excluding pure docs/infra-only changes) triggers `cd.yml`:

1. **build-and-push**: builds the docker image, tags with the short git SHA, pushes to ECR
2. **migrate**: runs `alembic upgrade head` as a one-off ECS task via `ecspresso run`
3. **approval**: pauses on the `production` GitHub Environment until a reviewer approves
4. **deploy-api** (parallel): `ecspresso deploy` — ECS Native Blue/Green, 5-min bake, abort on `api-5xx-*` or `api-unhealthy-*` alarms
5. **deploy-workers** (parallel matrix): rolling deploy with circuit breaker, per-worker

### 5.2 Manual deploy (rollback, hotfix, debugging)

Use `gh workflow run`:

```bash
# Deploy a specific past SHA (e.g. roll back to last-good)
gh workflow run cd.yml -f ref=<past-short-sha>

# Hotfix without migration
gh workflow run cd.yml -f skip_migrate=true
```

### 5.3 Bootstrap deploy (first time, before any CD)

For the very first deploy (before `cd.yml` has produced any image in ECR), run manually:

```bash
REGION=ap-northeast-1
ECR_URL=$(terraform output -raw ecr_repository_url)
IMAGE_TAG=<short-sha>

aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$ECR_URL"

docker build -t "$ECR_URL:$IMAGE_TAG" .
docker push "$ECR_URL:$IMAGE_TAG"

export AWS_REGION="$REGION"
export IMAGE_TAG="$IMAGE_TAG"

ecspresso run \
  --config infra/ecspresso/api/ecspresso.yml \
  --overrides '{"containerOverrides":[{"name":"app","command":["alembic","upgrade","head"]}]}' \
  --wait-until=stopped

for svc in api outbox-relay order-consumer checkout-sweeper; do
  ecspresso deploy --config "infra/ecspresso/$svc/ecspresso.yml" --wait-until=service-stable
done
```

---

## 6. Verify

```bash
curl http://$(terraform output -raw alb_dns_name)/healthz
```

Expected response: HTTP 200 (`{"status":"ok"}` or similar).

---

## 7. Rollback

### 7.1 Automatic

- **api**: ECS Native B/G abort triggers if any `api-5xx-*` or `api-unhealthy-*` alarm fires during the 5-minute bake window. Listener forward target group reverts to BLUE; GREEN tasks are stopped.
- **worker**: ECS deployment circuit breaker reverts to the previous task definition on repeated task launch failure.

### 7.2 Manual (roll back to a known-good SHA)

```bash
gh workflow run cd.yml -f ref=<last-known-good-short-sha>
```

This re-builds (or re-uses) the image for that SHA, re-runs migration (or skip if needed), then redeploys.

### 7.3 Migration rollback

`cd.yml` does **not** automate `alembic downgrade`. If a migration goes bad:

1. Stop further deploys (let the failed migration job stay in failed state — approval gate prevents subsequent deploys)
2. Operator decides: forward fix (new migration on top) or backward (`alembic downgrade -1` manually against the prod DB)
3. Once DB is in the desired state, re-run `cd.yml` with `skip_migrate=true` to deploy the corrected code

---

## 8. Known boundaries (C-1a + C-1b)

| Item | Status | Follow-up |
|------|--------|-----------|
| JWT key injection | Secrets Manager injects keys as env vars but the app currently reads `JWT_PRIVATE_KEY_PATH` | **C-1c** |
| TLS / HTTPS | ALB is HTTP-only; no ACM certificate or Route 53 record | C-1c or independent PR |
| RDS availability | Single-AZ (cost-optimised) | Multi-AZ promotion as follow-up |
| NAT Gateway | Single NAT (one AZ) | Per-AZ NATs for HA |
| Multi-environment (staging) | env=prod hardcoded in `variables.tf` | Terraform workspace or directory split |
| OIDC: Terraform admin permissions | `github_deploy` role currently has `*:*` for the apply job (admin-equivalent) | Split into `github_terraform` admin role + minimal app deploy role (spec §7.6) |
| ECS B/G canary / weighted shift | All-at-once shift after bake; no incremental traffic ramp | ECS supports this; opt-in if needed |
| Plan policy check | No OPA / Sentinel guardrail on `terraform plan` | Optional follow-up |
| Notifications | No Slack/Teams hook on deploy success/failure | Optional follow-up |
| Secret rotation | Manual `put-secret-value` only | AWS Secrets Manager rotation lambda (follow-up) |
````

- [ ] **Step 2: Verify no `tofu` references remain**

```bash
grep -n "tofu" infra/terraform/README.md
```

Expected: no output (zero matches).

- [ ] **Step 3: Verify no DynamoDB references remain**

```bash
grep -n -i "dynamodb" infra/terraform/README.md
```

Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add infra/terraform/README.md
git commit -m "infra(tf): README -> terraform CLI, S3 native locking, CD/manual boundary"
```

---

## Task 12: Verify all static checks + push + PR

**Files:** (none — verification only)

- [ ] **Step 1: Format check**

```bash
terraform -chdir=infra/terraform fmt -check -recursive
```

Expected: no output.

If there are diffs, run `terraform -chdir=infra/terraform fmt -recursive` and add the result as an amend or separate fixup commit.

- [ ] **Step 2: Validate**

```bash
terraform -chdir=infra/terraform validate
```

Expected: `Success! The configuration is valid.`

- [ ] **Step 3: tflint**

```bash
cd infra/terraform
tflint --init
tflint
```

Expected: no errors. (`tflint` may need a fresh `--init` after lockfile regen.)

- [ ] **Step 4: ecspresso JSON syntax**

```bash
find infra/ecspresso -name '*.json' -exec jq empty {} \;
```

Expected: no output.

- [ ] **Step 5: actionlint on all workflows**

```bash
actionlint .github/workflows/*.yml
```

Expected: no errors.

- [ ] **Step 6: app static checks (sanity)**

```bash
uv run ruff check
uv run pytest -m "not slow" -q
```

Expected: ruff `All checks passed!`, pytest all green. (These should be untouched by C-1b, but verify in case of accidental edits.)

- [ ] **Step 7: Inspect commit history**

```bash
git -C . log --oneline feature/aws-infra-c1b ^origin/main
```

Expected: ~12 commits (spec + plan + 10 implementation tasks).

- [ ] **Step 8: Push**

```bash
git push -u origin feature/aws-infra-c1b
```

If the sandbox blocks `git push`, ask the user to run it from their shell:
> `! git -C /Users/takuma/cross/ec/.worktrees/aws-infra-c1b push -u origin feature/aws-infra-c1b`

- [ ] **Step 9: Open PR**

```bash
gh pr create --base main --head feature/aws-infra-c1b \
  --title "C-1b: AWS CD pipeline (App B/G + Infra plan/apply)" \
  --body "$(cat <<'EOF'
## Summary

C-1a で揃えた静的 IaC (Terraform + ecspresso) を **CD パイプライン** に仕上げる。
本 PR のスコープは static-validation only — 実 AWS apply は operator runbook 化。

### 追加・変更したもの

**Workflows:**
- `cd.yml` (新規): build → push → migrate → approval (production env) → deploy-api (B/G) + deploy-workers (rolling matrix)
- `terraform.yml` (新規): PR plan + tfcmt コメント / main merge で approval → apply (plan artifact 引き継ぎ)
- `ci.yml` (修正): warn-only terraform job 削除 (terraform.yml に統合)

**Terraform:**
- `providers.tf`: `required_version >= 1.10`
- `backend.tf`: S3 native locking (`use_lockfile = true`)、DynamoDB 不使用
- `variables.tf`: `tfstate_bucket` 追加
- `iam.tf`: OIDC sub を `ref:refs/heads/main` + `environment:production` の StringEquals に絞り込み、ELB B/G + CloudWatch alarms + S3 state + admin (apply 用) statements 追加
- `alb.tf`: green TG、listener `lifecycle.ignore_changes = [default_action]`、CloudWatch alarms 4 個 (5xx / unhealthy × blue / green)
- `outputs.tf`: green TG ARN + alarm 名群
- `.terraform.lock.hcl`: HashiCorp Terraform 1.10 で再生成

**ecspresso:**
- api service-def: `strategy: BLUE_GREEN`、bake 5min、alarms 4 個参照
- worker 3 個: `deploymentCircuitBreaker: enable+rollback`

**README:**
- 全コマンドを `terraform` に統一、DynamoDB セクション削除、CD/手動運用境界、rollback 手順、`skip_migrate` 入力など

## Spec

`docs/superpowers/specs/2026-05-20-aws-infra-c1b-design.md`

## Test Plan

- [x] `terraform fmt -check -recursive`
- [x] `terraform validate` → Success
- [x] `tflint` → no issues
- [x] `find infra/ecspresso -name '*.json' -exec jq empty {} \;` → ok
- [x] `actionlint .github/workflows/*.yml` → no errors
- [x] app `uv run ruff check` → All checks passed
- [x] app `uv run pytest -m "not slow"` → green
- [ ] CI 全 green
- [ ] Operator が runbook の bootstrap → apply → secrets 投入 → 初回 deploy → `/healthz` 検証手順を再現可能であること (人手レビュー)

## Follow-ups (本 PR スコープ外)

- C-1c: JWT を env-based に切り替え / OTel prod endpoint / NR key 流通
- staging 環境追加 (Terraform workspace or ディレクトリ分離)
- TLS / Route53 / ACM
- canary / weighted shift for api B/G
- `github_terraform` admin role を `github_deploy` から分離 (最小権限化)
- terraform plan policy check (OPA / Sentinel)
- Slack 通知

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

If `gh pr create` is blocked by the sandbox, ask the user to run it manually with the same command.

---

## Self-Review Checklist (already run by author)

**Spec coverage:**
- §3 OIDC sub restriction → Task 3
- §4 architecture (independent workflows) → Tasks 9 + 10
- §5 file structure → matches Tasks 1–11
- §6 workflow details → Tasks 9 (cd.yml) + 10 (terraform.yml)
- §7 Terraform changes (lockfile / backend / iam / alb / outputs) → Tasks 1, 2, 3, 4, 5
- §8 ecspresso (api B/G + workers rolling) → Tasks 6 + 7
- §9 failure modes → documented in Task 11 (README) §7
- §10 migration safety → Task 11 (README §7.3) + Task 9 `skip_migrate` input
- §13 DoD → Task 12 covers all checks

**Type / naming consistency:**
- `aws_lb_target_group.api_green` — defined in Task 4, referenced in Tasks 5, 6
- `aws_cloudwatch_metric_alarm.api_5xx` / `api_unhealthy` — defined in Task 4, referenced in Tasks 5, 6
- `var.tfstate_bucket` — defined in Task 2, used in Task 3 (`iam.tf`) and Task 10 (`terraform.yml` `-var`)
- ecspresso B/G alarm names — Task 6 references the Task 4 alarm resources via `tfstate` plugin
- GitHub vars `AWS_ACCOUNT_ID`, `TFSTATE_BUCKET` — referenced in Tasks 9 + 10, documented in Task 11

No placeholders, every step has concrete code.
