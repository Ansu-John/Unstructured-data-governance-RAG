# ============================================================================
# variables.tf — 03-application-dynamic (Dev)
# ============================================================================

variable "environment" {
  description = "Deployment environment"
  type        = string
  default     = "dev"
}

variable "container_image_uri" {
  description = "ECR image URI for the agent orchestrator container"
  type        = string
  default     = ""
}

variable "dbt_image_uri" {
  description = "ECR image URI for the dbt runner container"
  type        = string
  default     = ""
}

variable "create_ecs_service" {
  description = "Create ECS service (vs. only task definition)"
  type        = bool
  default     = true
}

variable "tags" {
  description = "Common tags applied to all resources"
  type        = map(string)
  default = {
    Project     = "ai-data-catalog-agent"
    Environment = "dev"
    ManagedBy   = "terraform/03-application-dynamic"
  }
}