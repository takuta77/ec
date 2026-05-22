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

output "alb_target_group_green_arn" {
  value = aws_lb_target_group.api_green.arn
}

output "api_5xx_alarm_names" {
  value = [for k, v in aws_cloudwatch_metric_alarm.api_5xx : v.alarm_name]
}

output "api_unhealthy_alarm_names" {
  value = [for k, v in aws_cloudwatch_metric_alarm.api_unhealthy : v.alarm_name]
}

output "otel_collector_ecr_repository_url" {
  value = aws_ecr_repository.otel_collector.repository_url
}
