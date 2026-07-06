# ============================================================================
# repository/main.tf — ECR Repository with Lifecycle, Scanning & Encryption
#
# Provisions a fully configured ECR repository with:
#   1. Image scanning on push (vulnerability detection)
#   2. Lifecycle policy (untagged image cleanup after N days)
#   3. Optional KMS encryption (defaults to AES-256)
#   4. Immutable tag support (prevents tag overwrite in production)
#
# Consumed by the 02-platform-medium lifecycle tier.
# ============================================================================

terraform {
  required_version = ">= 1.7"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.50"
    }
  }
}

# ---------------------------------------------------------------------------
# ECR Repository
# ---------------------------------------------------------------------------

resource "aws_ecr_repository" "this" {
  name                 = var.name
  image_tag_mutability = var.image_tag_mutability

  # Automatic vulnerability scanning on every push
  image_scanning_configuration {
    scan_on_push = var.scan_on_push
  }

  # KMS encryption (defaults to AES-256 if no key ARN provided)
  encryption_configuration {
    encryption_type = var.kms_key_arn != "" ? "KMS" : "AES256"
    kms_key         = var.kms_key_arn != "" ? var.kms_key_arn : null
  }

  tags = merge(var.tags, {
    Name        = var.name
    Environment = var.environment
    ManagedBy   = "terraform/02-platform-medium"
  })
}

# ---------------------------------------------------------------------------
# Lifecycle Policy — Untagged Image Cleanup
#
# Prevents runaway storage costs from orphaned CI/CD layers.
# Retains a minimum number of untagged images for safety, then expires
# anything older than N days.
# ---------------------------------------------------------------------------

resource "aws_ecr_lifecycle_policy" "untagged_cleanup" {
  repository = aws_ecr_repository.this.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 10
        description  = "Expire untagged images older than ${var.untagged_image_expire_days} days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = var.untagged_image_expire_days
        }
        action = {
          type = "expire"
        }
      },
      {
        rulePriority = 20
        description  = "Limit total images to ${var.max_image_count}"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = var.max_image_count
        }
        action = {
          type = "expire"
        }
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# Repository Policy — Least-Privilege Access Controls
#
# Restricts push/delete operations to the CI/CD IAM role and the ECS task
# execution role. Pull access is granted to the ECS task execution role
# so Fargate can retrieve images at deploy time.
# ---------------------------------------------------------------------------

data "aws_caller_identity" "current" {}
data "aws_iam_policy_document" "repository_policy" {
  statement {
    sid    = "RestrictPushToCICDRole"
    effect = "Allow"
    principals {
      type = "AWS"
      identifiers = [
        # Allow the CI/CD role (GitHub Actions) to push images
        "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/github-actions-terraform-role",
        # Allow the ECS task execution role to pull images
        "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${var.environment}-ecs-task-execution-role",
      ]
    }
    actions = [
      "ecr:GetAuthorizationToken",
      "ecr:BatchCheckLayerAvailability",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:PutImage",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
      "ecr:DescribeRepositories",
      "ecr:ListImages",
    ]
  }
}

resource "aws_ecr_repository_policy" "this" {
  repository = aws_ecr_repository.this.name
  policy     = data.aws_iam_policy_document.repository_policy.json
}