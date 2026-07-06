# ============================================================================
# ecr.tf — 02-platform-medium ECR Repository
#
# Provisions the ECR repository used by the CI/CD pipeline to store and
# distribute the application Docker image. Publishing to SSM Parameter Store
# enables the 03-application-dynamic tier to reference the repository URL
# when rendering ECS task definitions.
#
# Resources created:
#   - ECR repository (ai-catalog-agent) with lifecycle & scan-on-push
#   - Lifecycle policy (14-day untagged image expiry)
#   - Repository policy (least-privilege, CI/CD + ECS task execution roles)
# ============================================================================

# ---------------------------------------------------------------------------
# Module: ECR Repository
# ---------------------------------------------------------------------------

module "ecr_repository" {
  source = "../../modules/repository"

  name        = var.ecr_repository_name
  environment = local.environment

  # IMMUTABLE in non-dev environments; MUTABLE is acceptable in dev for
  # rapid iteration but should never be used in staging or production.
  image_tag_mutability  = local.environment == "dev" ? "MUTABLE" : "IMMUTABLE"
  scan_on_push          = true

  # If a KMS key was published by 01-core-static, use it; otherwise AES-256
  kms_key_arn           = local.kms_key_arn

  # Lifecycle: expire untagged images after 14 days, cap total at 1000
  untagged_image_expire_days = 14
  max_image_count            = 1000

  tags = local.tags
}

# ---------------------------------------------------------------------------
# SSM Parameter Store handoff (consumed by 03-application-dynamic)
# ---------------------------------------------------------------------------

resource "aws_ssm_parameter" "ecr_repository_url" {
  name  = "/${local.environment}/platform-medium/ecr-repository-url"
  type  = "String"
  value = module.ecr_repository.repository_url

  tags = local.tags
}

resource "aws_ssm_parameter" "ecr_repository_arn" {
  name  = "/${local.environment}/platform-medium/ecr-repository-arn"
  type  = "String"
  value = module.ecr_repository.repository_arn

  tags = local.tags
}

resource "aws_ssm_parameter" "ecr_repository_name" {
  name  = "/${local.environment}/platform-medium/ecr-repository-name"
  type  = "String"
  value = module.ecr_repository.repository_name

  tags = local.tags
}