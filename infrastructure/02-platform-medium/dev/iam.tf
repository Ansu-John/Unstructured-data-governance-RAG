# ============================================================================
# iam.tf — 02-platform-medium IAM Permissions for CI/CD
#
# Attaches least-privilege ECR push permissions to the GitHub Actions
# Terraform IAM role. This role is a Day 0 bootstrap prerequisite (created
# outside Terraform; see Runbook.md Day 1 section).
#
# The ECS task execution role's ECR pull permissions are managed in
# ../../modules/compute_base/main.tf via the ecs_execution_extra policy.
# ============================================================================

# ---------------------------------------------------------------------------
# Data: Reference the bootstrap GitHub Actions IAM role
# ---------------------------------------------------------------------------

data "aws_iam_role" "github_actions_terraform" {
  name = "github-actions-terraform-role"
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
  role       = data.aws_iam_role.github_actions_terraform.name
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
  role       = data.aws_iam_role.github_actions_terraform.name
  policy_arn = aws_iam_policy.ecs_deploy.arn
}