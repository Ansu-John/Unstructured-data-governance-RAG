# ============================================================================
# repository/variables.tf — ECR Repository Module Variables
# ============================================================================

variable "name" {
  description = "Name of the ECR repository"
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev/staging/prod)"
  type        = string
}

variable "image_tag_mutability" {
  description = "Tag mutability setting. IMMUTABLE prevents tag overwrites. Use MUTABLE for dev only."
  type        = string
  default     = "IMMUTABLE"

  validation {
    condition     = contains(["MUTABLE", "IMMUTABLE"], var.image_tag_mutability)
    error_message = "image_tag_mutability must be either 'MUTABLE' or 'IMMUTABLE'."
  }
}

variable "scan_on_push" {
  description = "Enable automatic image scanning on push"
  type        = bool
  default     = true
}

variable "kms_key_arn" {
  description = "KMS key ARN for encryption. If empty, uses AES-256 default encryption."
  type        = string
  default     = ""
}

variable "untagged_image_expire_days" {
  description = "Number of days to retain untagged images before expiring them"
  type        = number
  default     = 14
}

variable "max_image_count" {
  description = "Maximum number of images to retain per repository (applies to both tagged and untagged via lifecycle rules)"
  type        = number
  default     = 1000
}

variable "tags" {
  description = "Common tags applied to all resources"
  type        = map(string)
  default     = {}
}