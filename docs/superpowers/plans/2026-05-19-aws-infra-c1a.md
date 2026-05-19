# C-1a AWS Infra (Terraform + ecspresso) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce the Terraform infrastructure code + ecspresso service/task definitions + CI terraform job + operator runbook for running EC API on AWS ECS Fargate, statically validated (no live AWS apply).

**Architecture:** `infra/terraform/` holds static infra (VPC, ECR, RDS, Amazon MQ, Secrets, ALB, IAM/OIDC, ECS cluster, CloudWatch). `infra/ecspresso/<svc>/` holds per-service ecspresso config + task/service def JSON wired to Terraform state via the tfstate plugin. Validation = `terraform fmt`/`validate` + `jq empty` on JSON, gated in CI.

**Tech Stack:** Terraform (AWS provider), ecspresso, GitHub Actions, jq.

---

## Working Branch

Working directory: `/Users/takuma/cross/ec/.worktrees/aws-infra-c1a`
Branch: `feature/aws-infra-c1a` (off `origin/main`).
Spec: `docs/superpowers/specs/2026-05-19-aws-infra-c1a-design.md`.

## Validation model (read first)

- `terraform` / `tflint` / `ecspresso` are NOT preinstalled locally. **Task 1 installs `terraform` + `tflint` via Homebrew** so subsequent tasks can run `terraform fmt -check` and `terraform validate`. If `brew install` is unavailable/sandboxed, the implementer records that as a concern and relies on CI for validation, but must still produce well-formed HCL.
- `terraform validate` runs against the WHOLE `infra/terraform/` directory. Tasks are ordered so each intermediate state only references resources that already exist (providers/vars → network → ecr/ecs → rds/mq → secrets/iam → alb/outputs). `outputs.tf` is added LAST (Task 6) because it references resources from all files.
- `terraform init -backend=false` is required before `validate` (downloads the AWS provider from the public registry — needs internet, no AWS creds). No `terraform plan`/`apply` anywhere in this plan.
- ecspresso JSON validated with `jq empty <file>`.

## File Structure

```
infra/terraform/
├── providers.tf   # terraform{} required_version+providers, aws provider (region var)
├── variables.tf   # project, env, region, db_instance_class, mq_instance_type, vpc_cidr, github_repo
├── backend.tf     # S3+DynamoDB backend (commented bootstrap note)
├── network.tf     # VPC, 2 public + 2 private subnets, IGW, NAT, routes
├── ecr.tf         # ECR repo + lifecycle policy
├── ecs.tf         # ECS cluster (Fargate + Spot), CloudWatch log group
├── rds.tf         # RDS Postgres 16 + subnet group + SG
├── mq.tf          # Amazon MQ RabbitMQ + SG
├── secrets.tf     # 5 Secrets Manager secrets (ignore_changes on value)
├── iam.tf         # ECS exec/task roles, GitHub OIDC provider + deploy role
├── alb.tf         # ALB + target group + HTTP listener + SG
├── outputs.tf     # all outputs consumed by ecspresso
└── README.md      # operator runbook
infra/ecspresso/
├── api/{ecspresso.yml,ecs-task-def.json,ecs-service-def.json}
├── outbox-relay/{ecspresso.yml,ecs-task-def.json,ecs-service-def.json}
├── order-consumer/{ecspresso.yml,ecs-task-def.json,ecs-service-def.json}
└── checkout-sweeper/{ecspresso.yml,ecs-task-def.json,ecs-service-def.json}
.github/workflows/ci.yml   # add `terraform` job
```

---

## Task 1: Scaffold + providers/variables/backend + tooling

**Files:** Create `infra/terraform/{providers.tf,variables.tf,backend.tf}`

- [ ] **Step 1: Install terraform + tflint (best-effort)**

```bash
brew install terraform tflint
terraform version && tflint --version
```

If brew is sandboxed/unavailable: note as a concern, skip local validate, continue (CI is the gate).

- [ ] **Step 2: Create `infra/terraform/providers.tf`**

```hcl
terraform {
  required_version = ">= 1.7"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = var.project
      Env       = var.env
      ManagedBy = "terraform"
    }
  }
}
```

- [ ] **Step 3: Create `infra/terraform/variables.tf`**

```hcl
variable "project" {
  type    = string
  default = "ec-api"
}

variable "env" {
  type    = string
  default = "prod"
}

variable "region" {
  type    = string
  default = "ap-northeast-1"
}

variable "vpc_cidr" {
  type    = string
  default = "10.20.0.0/16"
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "mq_instance_type" {
  type    = string
  default = "mq.t3.micro"
}

variable "github_repo" {
  type        = string
  description = "owner/name for GitHub OIDC trust"
  default     = "takuta77/ec"
}
```

- [ ] **Step 4: Create `infra/terraform/backend.tf`**

```hcl
# Bootstrap (run ONCE, manually, before `terraform init`):
#   aws s3api create-bucket --bucket <STATE_BUCKET> --region <REGION> \
#     --create-bucket-configuration LocationConstraint=<REGION>
#   aws dynamodb create-table --table-name <LOCK_TABLE> \
#     --attribute-definitions AttributeName=LockID,AttributeType=S \
#     --key-schema AttributeName=LockID,KeyType=HASH --billing-mode PAY_PER_REQUEST
#
# Then replace the placeholders below with your real bucket/table and run
# `terraform init`. Terraform backend blocks do NOT support variables.
terraform {
  backend "s3" {
    bucket         = "REPLACE_ME_ec-api-tfstate"
    key            = "ec-api/terraform.tfstate"
    region         = "ap-northeast-1"
    dynamodb_table = "REPLACE_ME_ec-api-tflock"
    encrypt        = true
  }
}
```

- [ ] **Step 5: Validate scaffold**

```bash
cd infra/terraform && terraform fmt -check -recursive && terraform init -backend=false && terraform validate && cd -
```

Expected: `Success! The configuration is valid.` (If terraform not installed: skip, note concern.)

- [ ] **Step 6: Commit**

```bash
git add infra/terraform/providers.tf infra/terraform/variables.tf infra/terraform/backend.tf
git commit -m "infra(tf): providers, variables, s3 backend scaffold"
```

---

## Task 2: network.tf (VPC / subnets / IGW / NAT / routes)

**Files:** Create `infra/terraform/network.tf`

- [ ] **Step 1: Create `infra/terraform/network.tf`**

```hcl
data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  azs            = slice(data.aws_availability_zones.available.names, 0, 2)
  public_subnets = [cidrsubnet(var.vpc_cidr, 8, 0), cidrsubnet(var.vpc_cidr, 8, 1)]
  private_subnets = [cidrsubnet(var.vpc_cidr, 8, 10), cidrsubnet(var.vpc_cidr, 8, 11)]
}

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "${var.project}-${var.env}-vpc" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${var.project}-${var.env}-igw" }
}

resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.main.id
  cidr_block              = local.public_subnets[count.index]
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = true
  tags                    = { Name = "${var.project}-${var.env}-public-${count.index}" }
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = local.private_subnets[count.index]
  availability_zone = local.azs[count.index]
  tags              = { Name = "${var.project}-${var.env}-private-${count.index}" }
}

resource "aws_eip" "nat" {
  domain = "vpc"
  tags   = { Name = "${var.project}-${var.env}-nat-eip" }
}

resource "aws_nat_gateway" "main" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id
  tags          = { Name = "${var.project}-${var.env}-nat" }
  depends_on    = [aws_internet_gateway.main]
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = { Name = "${var.project}-${var.env}-public-rt" }
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main.id
  }
  tags = { Name = "${var.project}-${var.env}-private-rt" }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

resource "aws_security_group" "app" {
  name        = "${var.project}-${var.env}-app-sg"
  description = "ECS tasks SG"
  vpc_id      = aws_vpc.main.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project}-${var.env}-app-sg" }
}
```

- [ ] **Step 2: Validate**

```bash
cd infra/terraform && terraform fmt -check -recursive && terraform validate && cd -
```

Expected: valid.

- [ ] **Step 3: Commit**

```bash
git add infra/terraform/network.tf
git commit -m "infra(tf): VPC, subnets, IGW, NAT, routes, app SG"
```

---

## Task 3: ecr.tf + ecs.tf

**Files:** Create `infra/terraform/ecr.tf`, `infra/terraform/ecs.tf`

- [ ] **Step 1: Create `infra/terraform/ecr.tf`**

```hcl
resource "aws_ecr_repository" "app" {
  name                 = "${var.project}-${var.env}"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images older than 14 days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 14
        }
        action = { type = "expire" }
      }
    ]
  })
}
```

- [ ] **Step 2: Create `infra/terraform/ecs.tf`**

```hcl
resource "aws_cloudwatch_log_group" "ecs" {
  name              = "/ecs/${var.project}-${var.env}"
  retention_in_days = 30
}

resource "aws_ecs_cluster" "main" {
  name = "${var.project}-${var.env}"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name       = aws_ecs_cluster.main.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE_SPOT"
    weight            = 1
  }
}
```

- [ ] **Step 3: Validate**

```bash
cd infra/terraform && terraform fmt -check -recursive && terraform validate && cd -
```

- [ ] **Step 4: Commit**

```bash
git add infra/terraform/ecr.tf infra/terraform/ecs.tf
git commit -m "infra(tf): ECR repo + lifecycle, ECS cluster + log group"
```

---

## Task 4: rds.tf + mq.tf

**Files:** Create `infra/terraform/rds.tf`, `infra/terraform/mq.tf`

- [ ] **Step 1: Create `infra/terraform/rds.tf`**

```hcl
resource "aws_db_subnet_group" "main" {
  name       = "${var.project}-${var.env}-db"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_security_group" "rds" {
  name        = "${var.project}-${var.env}-rds-sg"
  description = "RDS Postgres SG"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }

  tags = { Name = "${var.project}-${var.env}-rds-sg" }
}

resource "aws_db_instance" "main" {
  identifier                  = "${var.project}-${var.env}"
  engine                      = "postgres"
  engine_version              = "16"
  instance_class              = var.db_instance_class
  allocated_storage           = 20
  storage_type                = "gp3"
  db_name                     = "ec"
  username                    = "ec_admin"
  manage_master_user_password = true
  db_subnet_group_name        = aws_db_subnet_group.main.name
  vpc_security_group_ids      = [aws_security_group.rds.id]
  multi_az                    = false
  backup_retention_period     = 7
  skip_final_snapshot         = false
  final_snapshot_identifier   = "${var.project}-${var.env}-final"
  deletion_protection         = true
  storage_encrypted           = true
}
```

- [ ] **Step 2: Create `infra/terraform/mq.tf`**

```hcl
resource "aws_security_group" "mq" {
  name        = "${var.project}-${var.env}-mq-sg"
  description = "Amazon MQ RabbitMQ SG"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 5671
    to_port         = 5671
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }

  tags = { Name = "${var.project}-${var.env}-mq-sg" }
}

resource "aws_mq_broker" "main" {
  broker_name        = "${var.project}-${var.env}"
  engine_type        = "RabbitMQ"
  engine_version     = "3.13"
  host_instance_type = var.mq_instance_type
  deployment_mode    = "SINGLE_INSTANCE"
  subnet_ids         = [aws_subnet.private[0].id]
  security_groups    = [aws_security_group.mq.id]
  publicly_accessible = false

  user {
    username = "ec_mq_admin"
    password = "CHANGE_ME_via_secret_then_ignore"
  }

  lifecycle {
    ignore_changes = [user]
  }
}
```

Note: the inline `password` is a placeholder satisfying the schema; `ignore_changes = [user]` means the real password is set out-of-band and Terraform won't fight it. Documented in README runbook.

- [ ] **Step 3: Validate**

```bash
cd infra/terraform && terraform fmt -check -recursive && terraform validate && cd -
```

- [ ] **Step 4: Commit**

```bash
git add infra/terraform/rds.tf infra/terraform/mq.tf
git commit -m "infra(tf): RDS Postgres 16 + Amazon MQ RabbitMQ + SGs"
```

---

## Task 5: secrets.tf + iam.tf

**Files:** Create `infra/terraform/secrets.tf`, `infra/terraform/iam.tf`

- [ ] **Step 1: Create `infra/terraform/secrets.tf`**

```hcl
locals {
  secret_names = [
    "database_url",
    "rabbitmq_url",
    "jwt_private_key",
    "jwt_public_key",
    "new_relic_license_key",
  ]
}

resource "aws_secretsmanager_secret" "app" {
  for_each = toset(local.secret_names)
  name     = "${var.project}/${var.env}/${each.key}"
}

resource "aws_secretsmanager_secret_version" "app" {
  for_each      = aws_secretsmanager_secret.app
  secret_id     = each.value.id
  secret_string = "REPLACE_VIA_RUNBOOK"

  lifecycle {
    ignore_changes = [secret_string]
  }
}
```

- [ ] **Step 2: Create `infra/terraform/iam.tf`**

```hcl
data "aws_caller_identity" "current" {}

# --- ECS task execution role ---
data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ecs_task_execution" {
  name               = "${var.project}-${var.env}-ecs-exec"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "ecs_exec_managed" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "secrets_read" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [for s in aws_secretsmanager_secret.app : s.arn]
  }
}

resource "aws_iam_role_policy" "ecs_exec_secrets" {
  name   = "secrets-read"
  role   = aws_iam_role.ecs_task_execution.id
  policy = data.aws_iam_policy_document.secrets_read.json
}

# --- ECS task role (app runtime; minimal) ---
resource "aws_iam_role" "ecs_task" {
  name               = "${var.project}-${var.env}-ecs-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

# --- GitHub OIDC provider + deploy role ---
resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

data "aws_iam_policy_document" "github_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo}:*"]
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  name               = "${var.project}-${var.env}-gh-deploy"
  assume_role_policy = data.aws_iam_policy_document.github_assume.json
}

data "aws_iam_policy_document" "github_deploy" {
  statement {
    sid     = "EcrPush"
    actions = [
      "ecr:GetAuthorizationToken",
      "ecr:BatchCheckLayerAvailability",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:PutImage",
      "ecr:BatchGetImage",
    ]
    resources = ["*"]
  }
  statement {
    sid     = "EcsDeploy"
    actions = [
      "ecs:RegisterTaskDefinition",
      "ecs:DeregisterTaskDefinition",
      "ecs:UpdateService",
      "ecs:DescribeServices",
      "ecs:DescribeTaskDefinition",
      "ecs:DescribeTasks",
      "ecs:ListTasks",
      "ecs:RunTask",
    ]
    resources = ["*"]
  }
  statement {
    sid       = "PassRole"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.ecs_task_execution.arn, aws_iam_role.ecs_task.arn]
  }
}

resource "aws_iam_role_policy" "github_deploy" {
  name   = "deploy"
  role   = aws_iam_role.github_deploy.id
  policy = data.aws_iam_policy_document.github_deploy.json
}
```

- [ ] **Step 3: Validate**

```bash
cd infra/terraform && terraform fmt -check -recursive && terraform validate && cd -
```

- [ ] **Step 4: Commit**

```bash
git add infra/terraform/secrets.tf infra/terraform/iam.tf
git commit -m "infra(tf): Secrets Manager secrets + IAM roles + GitHub OIDC"
```

---

## Task 6: alb.tf + outputs.tf (full-config validate)

**Files:** Create `infra/terraform/alb.tf`, `infra/terraform/outputs.tf`

- [ ] **Step 1: Create `infra/terraform/alb.tf`**

```hcl
resource "aws_security_group" "alb" {
  name        = "${var.project}-${var.env}-alb-sg"
  description = "ALB SG"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project}-${var.env}-alb-sg" }
}

resource "aws_security_group_rule" "app_from_alb" {
  type                     = "ingress"
  from_port                = 8000
  to_port                  = 8000
  protocol                 = "tcp"
  security_group_id        = aws_security_group.app.id
  source_security_group_id = aws_security_group.alb.id
}

resource "aws_lb" "main" {
  name               = "${var.project}-${var.env}"
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id
}

resource "aws_lb_target_group" "api" {
  name        = "${var.project}-${var.env}-api"
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

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}
```

- [ ] **Step 2: Create `infra/terraform/outputs.tf`**

```hcl
output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "private_subnet_ids" {
  value = aws_subnet.private[*].id
}

output "app_security_group_id" {
  value = aws_security_group.app.id
}

output "ecs_task_execution_role_arn" {
  value = aws_iam_role.ecs_task_execution.arn
}

output "ecs_task_role_arn" {
  value = aws_iam_role.ecs_task.arn
}

output "github_deploy_role_arn" {
  value = aws_iam_role.github_deploy.arn
}

output "alb_target_group_arn" {
  value = aws_lb_target_group.api.arn
}

output "alb_dns_name" {
  value = aws_lb.main.dns_name
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.ecs.name
}

output "secret_arns" {
  value = { for k, s in aws_secretsmanager_secret.app : k => s.arn }
}
```

- [ ] **Step 3: Validate full config**

```bash
cd infra/terraform && terraform fmt -check -recursive && terraform init -backend=false && terraform validate && cd -
```

Expected: `Success! The configuration is valid.` (whole infra/terraform now references resolve).

If `tflint` installed:

```bash
cd infra/terraform && tflint --init && tflint && cd -
```

- [ ] **Step 4: Commit**

```bash
git add infra/terraform/alb.tf infra/terraform/outputs.tf
git commit -m "infra(tf): ALB + target group + listener + outputs (full config valid)"
```

---

## Task 7: ecspresso definitions (4 services)

**Files:** Create `infra/ecspresso/{api,outbox-relay,order-consumer,checkout-sweeper}/{ecspresso.yml,ecs-task-def.json,ecs-service-def.json}`

ecspresso resolves Terraform outputs via the `tfstate` plugin. The `<STATE_BUCKET>` token in `ecspresso.yml` must match `backend.tf` — documented in README. Task defs differ only in `command`; api additionally has an ALB target group.

- [ ] **Step 1: Create `infra/ecspresso/api/ecspresso.yml`**

```yaml
region: "{{ must_env `AWS_REGION` }}"
cluster: ec-api-prod
service: ec-api-prod-api
service_definition: ecs-service-def.json
task_definition: ecs-task-def.json
timeout: 10m
plugins:
  - name: tfstate
    config:
      url: s3://REPLACE_ME_ec-api-tfstate/ec-api/terraform.tfstate
```

- [ ] **Step 2: Create `infra/ecspresso/api/ecs-task-def.json`**

```json
{
  "family": "ec-api-prod-api",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "512",
  "memory": "1024",
  "executionRoleArn": "{{ tfstate `aws_iam_role.ecs_task_execution.arn` }}",
  "taskRoleArn": "{{ tfstate `aws_iam_role.ecs_task.arn` }}",
  "containerDefinitions": [
    {
      "name": "app",
      "image": "{{ tfstate `aws_ecr_repository.app.repository_url` }}:{{ must_env `IMAGE_TAG` }}",
      "essential": true,
      "command": ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"],
      "portMappings": [{ "containerPort": 8000, "protocol": "tcp" }],
      "environment": [
        { "name": "SERVE_FRONTEND", "value": "false" },
        { "name": "OTEL_EXPORTER_OTLP_ENDPOINT", "value": "http://localhost:4317" }
      ],
      "secrets": [
        { "name": "DATABASE_URL", "valueFrom": "{{ tfstate `aws_secretsmanager_secret.app[\"database_url\"].arn` }}" },
        { "name": "RABBITMQ_URL", "valueFrom": "{{ tfstate `aws_secretsmanager_secret.app[\"rabbitmq_url\"].arn` }}" },
        { "name": "JWT_PRIVATE_KEY", "valueFrom": "{{ tfstate `aws_secretsmanager_secret.app[\"jwt_private_key\"].arn` }}" },
        { "name": "JWT_PUBLIC_KEY", "valueFrom": "{{ tfstate `aws_secretsmanager_secret.app[\"jwt_public_key\"].arn` }}" },
        { "name": "NEW_RELIC_LICENSE_KEY", "valueFrom": "{{ tfstate `aws_secretsmanager_secret.app[\"new_relic_license_key\"].arn` }}" }
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "{{ tfstate `aws_cloudwatch_log_group.ecs.name` }}",
          "awslogs-region": "{{ must_env `AWS_REGION` }}",
          "awslogs-stream-prefix": "api"
        }
      }
    }
  ]
}
```

Note: `JWT_PRIVATE_KEY`/`JWT_PUBLIC_KEY` are env (PEM contents). The app currently reads `JWT_PRIVATE_KEY_PATH` (a file). Reconciling this (entrypoint shim or app change) is **C-1c scope** — recorded in spec §11. C-1a only wires the secret→env; it does not need the app to work end-to-end.

- [ ] **Step 3: Create `infra/ecspresso/api/ecs-service-def.json`**

```json
{
  "launchType": "FARGATE",
  "desiredCount": 1,
  "networkConfiguration": {
    "awsvpcConfiguration": {
      "subnets": "{{ tfstate `jsonencode(aws_subnet.private[*].id)` }}",
      "securityGroups": ["{{ tfstate `aws_security_group.app.id` }}"],
      "assignPublicIp": "DISABLED"
    }
  },
  "loadBalancers": [
    {
      "targetGroupArn": "{{ tfstate `aws_lb_target_group.api.arn` }}",
      "containerName": "app",
      "containerPort": 8000
    }
  ],
  "healthCheckGracePeriodSeconds": 60
}
```

Note on the `subnets` value: ecspresso's tfstate plugin returns strings; the implementer must verify the correct ecspresso syntax for a list output. If `jsonencode(...)` inside the template is not supported by the tfstate plugin, fall back to two explicit refs: `"subnets": ["{{ tfstate \`aws_subnet.private[0].id\` }}", "{{ tfstate \`aws_subnet.private[1].id\` }}"]`. Prefer the explicit two-element form for reliability — **use the explicit form**:

```json
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
```

Use the explicit two-subnet form in the actual file.

- [ ] **Step 4: Create the 3 worker services**

For each of `outbox-relay`, `order-consumer`, `checkout-sweeper`, create the same 3 files as api with these differences:
- `ecspresso.yml`: `service: ec-api-prod-<svc>` (e.g. `ec-api-prod-outbox-relay`), same tfstate plugin block
- `ecs-task-def.json`: `family: ec-api-prod-<svc>`, same image/secrets/log block (awslogs-stream-prefix = `<svc>`), but `command`:
  - outbox-relay: `["python", "-m", "app.workers.outbox_relay"]`
  - order-consumer: `["python", "-m", "app.workers.order_consumer"]`
  - checkout-sweeper: `["python", "-m", "app.workers.checkout_sweeper"]`
  - no `portMappings` for workers
- `ecs-service-def.json`: same as api but **remove** the `loadBalancers` block and `healthCheckGracePeriodSeconds` (workers have no ALB); keep networkConfiguration with the explicit two-subnet form

- [ ] **Step 5: Validate all ecspresso JSON**

```bash
find infra/ecspresso -name '*.json' -print -exec jq empty {} \;
```

Expected: each file printed, no jq errors (jq parses Go template tokens as plain strings — that's fine, we're checking JSON well-formedness, not template resolution).

- [ ] **Step 6: Commit**

```bash
git add infra/ecspresso
git commit -m "infra(ecspresso): api + 3 worker service/task definitions"
```

---

## Task 8: CI terraform job + README runbook

**Files:** Modify `.github/workflows/ci.yml`; Create `infra/terraform/README.md`

- [ ] **Step 1: Inspect current ci.yml job structure**

```bash
grep -n "^  [a-z].*:$\|jobs:" .github/workflows/ci.yml | head
```

- [ ] **Step 2: Append a `terraform` job to `.github/workflows/ci.yml`**

Add under `jobs:` (peer of existing lint/type/test jobs):

```yaml
  terraform:
    name: terraform
    runs-on: ubuntu-latest
    continue-on-error: true  # warn-only initially (promote to required in C-1b)
    steps:
      - uses: actions/checkout@v4
      - uses: hashicorp/setup-terraform@v3
        with:
          terraform_version: "1.9.8"
      - name: fmt
        run: terraform -chdir=infra/terraform fmt -check -recursive
      - name: init + validate
        run: |
          terraform -chdir=infra/terraform init -backend=false
          terraform -chdir=infra/terraform validate
      - uses: terraform-linters/setup-tflint@v4
        with:
          tflint_version: latest
      - name: tflint
        run: |
          cd infra/terraform
          tflint --init
          tflint
      - name: ecspresso json syntax
        run: find infra/ecspresso -name '*.json' -exec jq empty {} \;
```

Match the existing ci.yml action-pin convention: if other actions in the file are SHA-pinned (from PR #18), SHA-pin these too; otherwise use the tag form shown. The implementer must check and match.

- [ ] **Step 3: actionlint the workflow**

```bash
actionlint .github/workflows/ci.yml
```

(If `actionlint` not installed: `brew install actionlint` or note as concern; CI itself will catch syntax.)

- [ ] **Step 4: Create `infra/terraform/README.md`** (operator runbook)

```markdown
# EC API — AWS Infra (C-1a)

Terraform manages the static AWS infrastructure; ecspresso (C-1b CD) deploys
the ECS services. **Nothing here is applied automatically — an operator with
AWS credentials runs the steps below.**

## 1. Bootstrap remote state (once)

\`\`\`bash
aws s3api create-bucket --bucket <STATE_BUCKET> --region <REGION> \
  --create-bucket-configuration LocationConstraint=<REGION>
aws s3api put-bucket-versioning --bucket <STATE_BUCKET> \
  --versioning-configuration Status=Enabled
aws dynamodb create-table --table-name <LOCK_TABLE> \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH --billing-mode PAY_PER_REQUEST
\`\`\`

Edit `backend.tf`: replace `REPLACE_ME_ec-api-tfstate` / `REPLACE_ME_ec-api-tflock`
with `<STATE_BUCKET>` / `<LOCK_TABLE>`. Edit each `infra/ecspresso/*/ecspresso.yml`:
replace `REPLACE_ME_ec-api-tfstate` in the `s3://.../terraform.tfstate` URL.

## 2. Provision infra

\`\`\`bash
cd infra/terraform
terraform init
terraform apply        # creates VPC, RDS, MQ, ALB, IAM, ECS cluster, secrets (empty)
\`\`\`

## 3. Populate secrets (after RDS/MQ endpoints exist)

\`\`\`bash
RDS=$(terraform output -raw ... )   # construct from RDS endpoint + master secret
aws secretsmanager put-secret-value --secret-id ec-api/prod/database_url --secret-string "postgresql+asyncpg://ec_admin:<pw>@<rds-endpoint>:5432/ec"
aws secretsmanager put-secret-value --secret-id ec-api/prod/rabbitmq_url  --secret-string "amqps://ec_mq_admin:<pw>@<mq-endpoint>:5671/"
aws secretsmanager put-secret-value --secret-id ec-api/prod/jwt_private_key --secret-string file://jwtRS256.key
aws secretsmanager put-secret-value --secret-id ec-api/prod/jwt_public_key  --secret-string file://jwtRS256.key.pub
aws secretsmanager put-secret-value --secret-id ec-api/prod/new_relic_license_key --secret-string "<key>"
\`\`\`

## 4. First image push + deploy (normally done by C-1b CD)

\`\`\`bash
aws ecr get-login-password | docker login --username AWS --password-stdin <ecr-url>
docker build -f docker/Dockerfile -t <ecr-url>:<tag> .
docker push <ecr-url>:<tag>

export AWS_REGION=<REGION> IMAGE_TAG=<tag>
# Migration (one-off):
ecspresso run --config infra/ecspresso/api/ecspresso.yml \
  --overrides '{"containerOverrides":[{"name":"app","command":["alembic","upgrade","head"]}]}'
# Services:
for svc in api outbox-relay order-consumer checkout-sweeper; do
  ecspresso deploy --config infra/ecspresso/$svc/ecspresso.yml
done
\`\`\`

## 5. Verify

\`\`\`bash
curl http://$(terraform output -raw alb_dns_name)/healthz   # expect {"status":"ok"}
\`\`\`

## Known boundaries (C-1a)

- JWT keys are injected as env (`JWT_PRIVATE_KEY` / `JWT_PUBLIC_KEY`); the app
  currently reads `JWT_PRIVATE_KEY_PATH`. Reconciling this is **C-1c**.
- HTTP only (no TLS/ACM/Route53) — follow-up.
- Single-AZ RDS, single NAT — follow-up.
\`\`\`

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yml infra/terraform/README.md
git commit -m "infra(ci): terraform validate/tflint job + operator runbook"
```

---

## Task 9: Final verification, push, PR

- [ ] **Step 1: Whole-repo sanity (no app regression)**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy app && uv run pytest -m "not slow"
```

Expected: unchanged/green — `infra/` is outside Python tooling scope.

- [ ] **Step 2: Re-run infra static checks (if terraform installed)**

```bash
cd infra/terraform && terraform fmt -check -recursive && terraform init -backend=false && terraform validate && cd -
find infra/ecspresso -name '*.json' -exec jq empty {} \;
```

- [ ] **Step 3: History check**

```bash
git log --oneline origin/main..HEAD
```

Expected: 1 spec + 1 plan + 8 implementation commits = 10.

- [ ] **Step 4: Push**

```bash
git push -u origin feature/aws-infra-c1a
```

If harness denies, ask the user to push.

- [ ] **Step 5: Open PR**

```bash
gh pr create \
  --base main \
  --head feature/aws-infra-c1a \
  --title "C-1a: AWS infra (Terraform + ecspresso, static-validated)" \
  --body "$(cat <<'EOF'
## Summary

C-1a of `docs/superpowers/specs/2026-05-19-aws-infra-c1a-design.md`. Terraform for static AWS infra + ecspresso defs for the 4 ECS services. **No live AWS apply** — operator runbook in `infra/terraform/README.md`.

- `infra/terraform/`: VPC/subnets/NAT, ECR, ECS cluster (Fargate+Spot), RDS Postgres 16, Amazon MQ RabbitMQ, 5 Secrets Manager secrets, ALB+TG+listener, IAM (ECS exec/task + GitHub OIDC deploy role), CloudWatch logs, S3 backend
- `infra/ecspresso/{api,outbox-relay,order-consumer,checkout-sweeper}/`: ecspresso.yml + task/service def JSON, wired to Terraform state via tfstate plugin; services differ only by `command`; api has ALB target group
- `ci.yml` `terraform` job (warn-only): fmt + validate + tflint + ecspresso JSON jq syntax
- README operator runbook (bootstrap → apply → secrets → image → ecspresso deploy → migration → verify)

## Validation

- [x] `terraform fmt -check` / `terraform validate` (`-backend=false`) — clean (CI gate; locally if terraform installed)
- [x] `tflint` — clean
- [x] `jq empty` on all ecspresso JSON — well-formed
- [x] app `ruff`/`mypy`/`pytest` unaffected (infra/ outside scope)
- [ ] CI green on PR
- [ ] **NOT done in this PR**: real `terraform apply` / `ecspresso deploy` (requires AWS account; see README runbook)

## Follow-ups (spec §11)

- C-1b: CD pipeline (OIDC → build/push → migration `ecspresso run` → `ecspresso deploy`×4)
- C-1c: prod config / JWT key env-vs-file reconciliation / OTel→New Relic prod path / readiness probe
- TLS+ACM+Route53, RDS Multi-AZ, HA NAT, multi-env (staging/prod), WAF
EOF
)"
```

- [ ] **Step 6: Watch CI** — required checks green; the `terraform` job is warn-only so it won't block even if a tflint nit appears.

---

## Self-Review Notes

**Spec coverage:**
- §3 responsibility split (Terraform vs ecspresso) → Tasks 1-6 vs 7
- §4 directory structure → matches plan file layout exactly
- §5 Terraform details (network/ecr/ecs/rds/mq/secrets/iam/alb) → Tasks 2-6, each resource block present
- §6 ecspresso details (tfstate plugin, command-only diff, api ALB) → Task 7
- §7 CI terraform job → Task 8
- §8 static validation strategy → validation steps in every task + Task 9
- §9 operator runbook → Task 8 README
- §10 rollout → Task 9 PR
- §11/§12 follow-ups & non-goals → PR body + README "Known boundaries"

**Placeholder scan:** `REPLACE_ME_*` / `<STATE_BUCKET>` / `<REGION>` are intentional operator-substitution tokens, each documented in backend.tf comments and the README runbook — concrete instructions, not plan gaps. No "TBD"/"add as needed".

**Consistency:**
- Resource names: `aws_secretsmanager_secret.app` (for_each keyed by name) referenced consistently in iam.tf (`for s in aws_secretsmanager_secret.app`), outputs.tf, and ecspresso task defs (`aws_secretsmanager_secret.app["database_url"].arn`).
- `aws_iam_role.ecs_task_execution` / `aws_iam_role.ecs_task` / `aws_iam_role.github_deploy` consistent across iam.tf, outputs.tf, ecspresso task defs.
- `aws_subnet.private` / `aws_security_group.app` / `aws_lb_target_group.api` / `aws_cloudwatch_log_group.ecs` / `aws_ecr_repository.app` consistent between Terraform and ecspresso tfstate refs.
- Cluster name `ec-api-prod` (from `${var.project}-${var.env}` with defaults project=ec-api, env=prod) matches the hardcoded `cluster: ec-api-prod` in ecspresso.yml and `service: ec-api-prod-<svc>` naming. (Note: ecspresso.yml hardcodes the cluster name because ecspresso.yml itself can't use tfstate for the cluster field; this is consistent only if project/env defaults are unchanged — documented in README.)
- `terraform validate` ordering: outputs.tf added in Task 6 (last) so all referenced resources exist; intermediate tasks only reference earlier resources (rds/mq → network; iam → secrets; alb → network). Verified.

**Risk:** ecspresso `tfstate` template syntax for the secrets map (`aws_secretsmanager_secret.app["database_url"].arn`) and list outputs (subnets) may need adjustment to ecspresso's actual tfstate plugin grammar. Task 7 explicitly instructs the implementer to use the explicit two-subnet form and verify the map-index syntax; if the plugin needs `terraform output`-style lookups instead of resource-address lookups, the implementer adapts and notes it. This is flagged, not silently assumed.
