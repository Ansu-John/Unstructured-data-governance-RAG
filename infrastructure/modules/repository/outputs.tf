# ============================================================================
# repository/outputs.tf — ECR Repository Module Outputs
# ============================================================================

output "repository_url" {
  description = "URL of the ECR repository (used by CI/CD to push and by ECS task definitions)"
  value       = aws_ecr_repository.this.repository_url
}

output "repository_arn" {
  description = "ARN of the ECR repository"
  value       = aws_ecr_repository.this.arn
}

output "registry_id" {
  description = "AWS account ID of the registry that owns the repository"
  value       = aws_ecr_repository.this.registry_id
}

output "repository_name" {
  description = "Name of the ECR repository"
  value       = aws_ecr_repository.this.name
}