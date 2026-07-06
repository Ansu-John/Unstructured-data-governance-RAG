# ============================================================================
# outputs.tf — 02-platform-medium (Dev)
# ============================================================================

output "ecr_repository_url" {
  description = "ECR repository URL for the application Docker image"
  value       = module.ecr_repository.repository_url
}

output "ecr_repository_arn" {
  description = "ECR repository ARN"
  value       = module.ecr_repository.repository_arn
}

output "ecs_cluster_name" {
  description = "ECS cluster name"
  value       = module.compute_base.ecs_cluster_name
}

output "ecs_task_execution_role_arn" {
  description = "ECS task execution role ARN"
  value       = module.compute_base.ecs_task_execution_role_arn
}

output "emr_application_id" {
  description = "EMR Serverless application ID"
  value       = module.compute_base.emr_application_id
}

output "db_endpoint" {
  description = "Aurora PostgreSQL writer endpoint"
  value       = module.database_aurora.endpoint
}

output "db_secret_arn" {
  description = "ARN of Secrets Manager entry with DB credentials"
  value       = module.database_aurora.secrets_manager_arn
}

output "log_group_names" {
  description = "CloudWatch log group names"
  value       = module.observability.log_group_names
}

output "sns_topic_arn" {
  description = "SNS topic ARN for alarms"
  value       = module.observability.sns_topic_arn
}