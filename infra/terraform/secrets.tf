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
