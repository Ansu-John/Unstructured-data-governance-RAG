# ============================================================================
# iam.tf — 02-platform-medium IAM Permissions for CI/CD
#
# Attaches least-privilege ECR push, ECS deploy, and SSM parameter read
# permissions to the GitHub Actions Terraform IAM role.
#
# IMPORTANT — Day 0 bootstrap prerequisite:
# The `github-actions-terraform-role` must exist with minimum permissions
# BEFORE this Terraform can run. Minimum required bootstrap permissions:
#   - ssm:GetParameter, ssm:PutParameter, ssm:DeleteParameter on /dev/*
#   - iam:CreatePolicy, iam:CreatePolicyVersion, iam:DeletePolicy
#   - iam:AttachRolePolicy, iam:DetachRolePolicy on itself
#   - iam:GetRole on itself
#   - ec2:Describe*, elasticloadbalancing:Describe*
#   - rds:Describe*, ecs:Describe*, emr:Describe*
# See Runbook.md Day 1 section for the full policy document.
#
# The ECS task execution role's ECR pull permissions are managed in
# ../../modules/compute_base/main.tf via the ecs_execution_extra policy.
# ============================================================================

# ---------------------------------------------------------------------------
# Local: GitHub Actions role name (hardcoded — it's a Day 0 bootstrap resource)
# Using a local instead of data.aws_iam_role avoids the chicken-and-egg
# problem where Terraform needs iam:GetRole before it can attach policies.
# ---------------------------------------------------------------------------

locals {
  github_actions_role_name = "github-actions-terraform-role"
}

# ---------------------------------------------------------------------------
# Policy: SSM Parameter Read (cross-tier handoff from 01-core-static)
#
# The 02-platform-medium tier reads SSM parameters published by the
# 01-core-static tier (VPC ID, subnet IDs, bucket ARNs, KMS key ARN).
# Terraform also writes new SSM parameters for the 03-application-dynamic tier.
# ---------------------------------------------------------------------------

resource "aws_iam_policy" "ssm_parameter_access" {
  name        = "${local.environment}-ssm-parameter-access"
  description = "Read/write SSM parameters for cross-tier Terraform state handoff"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadCoreStaticParameters"
        Effect = "Allow"
        Action = [
          "ssm:GetParameter",
          "ssm:GetParameters",
          "ssm:GetParametersByPath",
        ]
        Resource = [
          "arn:aws:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter/dev/core-static/*",
          "arn:aws:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter/dev/platform-medium/*",
        ]
      },
      {
        Sid    = "WritePlatformMediumParameters"
        Effect = "Allow"
        Action = [
          "ssm:PutParameter",
          "ssm:DeleteParameter",
          "ssm:AddTagsToResource",
        ]
        Resource = [
          "arn:aws:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter/dev/platform-medium/*",
        ]
      },
    ]
  })

  tags = merge(local.tags, {
    Name = "${local.environment}-ssm-parameter-access"
  })
}

resource "aws_iam_role_policy_attachment" "github_actions_ssm" {
  role       = local.github_actions_role_name
  policy_arn = aws_iam_policy.ssm_parameter_access.arn
}

# ---------------------------------------------------------------------------
# Policy: Terraform State & Locking
#
# Permissions to read/write the Terraform state in S3 and manage the
# DynamoDB lock table. These are typically already on the bootstrap role,
# but we manage them here as well to ensure consistency.
# ---------------------------------------------------------------------------

resource "aws_iam_policy" "terraform_state_access" {
  name        = "${local.environment}-terraform-state-access"
  description = "Access to Terraform state backend (S3) and lock table (DynamoDB)"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "TerraformStateAccess"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket",
        ]
        Resource = [
          "arn:aws:s3:::ai-catalog-terraform-state-*",
          "arn:aws:s3:::ai-catalog-terraform-state-*/*",
        ]
      },
      {
        Sid    = "TerraformLockAccess"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:DeleteItem",
          "dynamodb:DescribeTable",
        ]
        Resource = ["arn:aws:dynamodb:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:table/ai-catalog-terraform-locks"]
      },
    ]
  })

  tags = merge(local.tags, {
    Name = "${local.environment}-terraform-state-access"
  })
}

resource "aws_iam_role_policy_attachment" "github_actions_state" {
  role       = local.github_actions_role_name
  policy_arn = aws_iam_policy.terraform_state_access.arn
}

# ---------------------------------------------------------------------------
# Policy: IAM Self-Management
#
# Terraform needs to create IAM policies and attach them to the GitHub
# Actions role. This is self-referential but necessary. The bootstrap role
# must have these permissions at Day 0; after the first apply, Terraform
# manages them going forward.
# ---------------------------------------------------------------------------

resource "aws_iam_policy" "iam_self_management" {
  name        = "${local.environment}-iam-self-management"
  description = "Allow Terraform to manage IAM policies attached to the CI/CD role"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "CreateAndAttachPolicies"
        Effect = "Allow"
        Action = [
          "iam:CreatePolicy",
          "iam:CreatePolicyVersion",
          "iam:DeletePolicy",
          "iam:DeletePolicyVersion",
          "iam:GetPolicy",
          "iam:GetPolicyVersion",
          "iam:ListPolicyVersions",
          "iam:AttachRolePolicy",
          "iam:DetachRolePolicy",
          "iam:ListAttachedRolePolicies",
          "iam:GetRole",
        ]
        Resource = [
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:policy/${local.environment}-*",
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${local.github_actions_role_name}",
        ]
      },
    ]
  })

  tags = merge(local.tags, {
    Name = "${local.environment}-iam-self-management"
  })
}

resource "aws_iam_role_policy_attachment" "github_actions_iam" {
  role       = local.github_actions_role_name
  policy_arn = aws_iam_policy.iam_self_management.arn
}

# ---------------------------------------------------------------------------
# Policy: ECR Push Permissions for CI/CD Pipeline
#
# These are the exact actions the docker/build-push-action step in
# app-python-cicd.yml needs to authenticate, push, and verify images.
# No wildcards — every action is explicitly enumerated.
# ---------------------------------------------------------------------------

resource "aws_iam_policy" "ecr_push" {
  name        = "${local.environment}-ecr-push-policy"
  description = "Least-privilege ECR push permissions for GitHub Actions CI/CD pipeline"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ECRAuthAndPush"
        Effect = "Allow"
        Action = [
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
        Resource = ["*"]
      },
    ]
  })

  tags = merge(local.tags, {
    Name = "${local.environment}-ecr-push-policy"
  })
}

resource "aws_iam_role_policy_attachment" "github_actions_ecr_push" {
  role       = local.github_actions_role_name
  policy_arn = aws_iam_policy.ecr_push.arn
}

# ---------------------------------------------------------------------------
# Policy: ECS Deploy Permissions for CI/CD Pipeline
#
# Required by the amazon-ecs-deploy-task-definition action to render task
# definitions, register revisions, and update the ECS service.
# ---------------------------------------------------------------------------

resource "aws_iam_policy" "ecs_deploy" {
  name        = "${local.environment}-ecs-deploy-policy"
  description = "Least-privilege ECS deploy permissions for GitHub Actions CI/CD pipeline"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ECSDescribeAndDeploy"
        Effect = "Allow"
        Action = [
          "ecs:DescribeTaskDefinition",
          "ecs:RegisterTaskDefinition",
          "ecs:DescribeServices",
          "ecs:UpdateService",
          "ecs:DescribeClusters",
          "ecs:ListTasks",
          "ecs:DescribeTasks",
          "ecs:WaitUntilServicesStable",
        ]
        Resource = ["*"]
      },
      {
        Sid    = "PassRoleForECS"
        Effect = "Allow"
        Action = "iam:PassRole"
        Resource = [
          module.compute_base.ecs_task_execution_role_arn,
        ]
      },
    ]
  })

  tags = merge(local.tags, {
    Name = "${local.environment}-ecs-deploy-policy"
  })
}

resource "aws_iam_role_policy_attachment" "github_actions_ecs_deploy" {
  role       = local.github_actions_role_name
  policy_arn = aws_iam_policy.ecs_deploy.arn
}