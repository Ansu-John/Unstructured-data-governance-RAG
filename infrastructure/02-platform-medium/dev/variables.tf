# ============================================================================
# variables.tf — 02-platform-medium (Dev)
#
# Input variables for the platform medium lifecycle tier.
# ============================================================================

variable "ecr_repository_name" {
  description = "Name of the ECR repository for the application Docker image"
  type        = string
  default     = "ai-catalog-agent"
}