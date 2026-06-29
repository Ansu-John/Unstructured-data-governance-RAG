# infrastructure/variables.tf

variable "environment" {
  description = "The deployment environment (e.g., dev, staging, prod)"
  type        = string
}