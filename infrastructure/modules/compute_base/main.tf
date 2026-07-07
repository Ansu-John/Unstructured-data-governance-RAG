# ============================================================================
# compute_base/main.tf — ECS Cluster & EMR Serverless Namespace
#
# Provisions the shared compute infrastructure:
#   1. ECS Cluster (Fargate) for containerized agent execution.
#   2. EMR Serverless Application for PySpark batch processing.
#   3. IAM roles and execution policies for both compute planes.
#
# This module is consumed by the 02-platform-medium lifecycle tier.
# ECS task definitions and services are created in 03-application-dynamic.
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

variable "environment" {
  description = "Deployment environment (dev/staging/prod)"
  type        = string
}

variable "ecs_cluster_name" {
  description = "Override name for the ECS cluster"
  type        = string
  default     = ""
}

variable "emr_application_name" {
  description = "Override name for the EMR Serverless application"
  type        = string
  default     = ""
}

variable "enable_emr_serverless" {
  description = "Enable EMR Serverless application"
  type        = bool
  default     = true
}

variable "tags" {
  description = "Common tags applied to all resources"
  type        = map(string)
  default     = {}
}

# ---------------------------------------------------------------------------
# ECS Cluster (Fargate)
# ---------------------------------------------------------------------------

resource "aws_ecs_cluster" "main" {
  name = var.ecs_cluster_name != "" ? var.ecs_cluster_name : "${var.environment}-ai-catalog-ecs"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  configuration {
    execute_command_configuration {
      logging = "DEFAULT"
    }
  }

  tags = merge(var.tags, {
    Name        = "${var.environment}-ai-catalog-ecs"
    Environment = var.environment
    ManagedBy   = "terraform/02-platform-medium"
  })
}

# ---------------------------------------------------------------------------
# ECS Task Execution IAM Role
# ---------------------------------------------------------------------------

resource "aws_iam_role" "ecs_task_execution" {
  name = "${var.environment}-ecs-task-execution-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "ecs-tasks.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })

  tags = merge(var.tags, {
    Environment = var.environment
  })
}

resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "ecs_execution_extra" {
  name = "${var.environment}-ecs-execution-extra"
  role = aws_iam_role.ecs_task_execution.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:ListBucket",
          "s3:DeleteObject",
        ]
        Resource = [
          "arn:aws:s3:::ai-catalog-${var.environment}-*",
          "arn:aws:s3:::ai-catalog-${var.environment}-*/*",
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey",
          "kms:Encrypt",
        ]
        Resource = ["*"]
      },
      {
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream",
        ]
        Resource = [
          "arn:aws:bedrock:${data.aws_region.current.name}::foundation-model/anthropic.claude-3-5-*",
          "arn:aws:bedrock:${data.aws_region.current.name}::foundation-model/amazon.titan-embed-*",
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = [
          "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/ecs/${var.environment}-ai-catalog-*"
        ]
      },
      {
        Sid    = "ECRPullForTaskExecution"
        Effect = "Allow"
        Action = [
          "ecr:GetAuthorizationToken",
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
        ]
        Resource = ["*"]
      },
      # ----------------------------------------------------
      # NEW BLOCK: Allow ECS to pull the database secret
      # ----------------------------------------------------
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = [
          "arn:aws:secretsmanager:${data.aws_region.current.name}:*:secret:${var.environment}-ai-catalog-db-credentials-*"
        ]
      }
    ]
  })
}

# ---------------------------------------------------------------------------
# EMR Serverless Application
# ---------------------------------------------------------------------------

resource "aws_emrserverless_application" "spark" {
  count         = var.enable_emr_serverless ? 1 : 0
  name          = var.emr_application_name != "" ? var.emr_application_name : "${var.environment}-ai-catalog-emr"
  release_label = "emr-7.2.0"
  type          = "SPARK"

  initial_capacity {
    initial_capacity_type = "Driver"
    initial_capacity_config {
      worker_count = 1
      worker_configuration {
        cpu    = "4vCPU"
        memory = "16GB"
      }
    }
  }

  initial_capacity {
    initial_capacity_type = "Executor"
    initial_capacity_config {
      worker_count = 2
      worker_configuration {
        cpu    = "4vCPU"
        memory = "16GB"
      }
    }
  }

  maximum_capacity {
    cpu    = "200vCPU"
    memory = "800GB"
  }

  auto_start_configuration {
    enabled = true
  }

  auto_stop_configuration {
    enabled              = true
    idle_timeout_minutes = 15
  }

  tags = merge(var.tags, {
    Name        = "${var.environment}-ai-catalog-emr"
    Environment = var.environment
    ManagedBy   = "terraform/02-platform-medium"
  })
}

# ---------------------------------------------------------------------------
# Data sources
# ---------------------------------------------------------------------------

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

output "ecs_cluster_name" {
  description = "ECS cluster name for task definitions"
  value       = aws_ecs_cluster.main.name
}

output "ecs_cluster_arn" {
  description = "ECS cluster ARN"
  value       = aws_ecs_cluster.main.arn
}

output "ecs_task_execution_role_arn" {
  description = "ARN of the ECS task execution IAM role"
  value       = aws_iam_role.ecs_task_execution.arn
}

output "ecs_task_execution_role_name" {
  description = "Name of the ECS task execution IAM role"
  value       = aws_iam_role.ecs_task_execution.name
}

output "emr_application_id" {
  description = "EMR Serverless application ID"
  value       = var.enable_emr_serverless ? aws_emrserverless_application.spark[0].id : ""
}

output "emr_application_arn" {
  description = "EMR Serverless application ARN"
  value       = var.enable_emr_serverless ? aws_emrserverless_application.spark[0].arn : ""
}