# ============================================================================
# 02-platform-medium/dev/main.tf — Platform Infrastructure Layer
#
# SECOND lifecycle tier to deploy. Provisions stateful, medium-change-rate
# resources: the Aurora PostgreSQL cluster (with pgvector), ECS cluster,
# EMR Serverless application, and observability (CloudWatch log groups,
# dashboards, alarms).
#
# These resources change less frequently than the application code but more
# frequently than the core network/storage layer. Isolating them in their
# own state prevents unnecessary plan noise on the static layer.
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

provider "aws" {
  region = "us-east-1"
}

# ---------------------------------------------------------------------------
# Local variables
# ---------------------------------------------------------------------------

locals {
  environment = "dev"
  tags = {
    Project     = "ai-data-catalog-agent"
    Environment = "dev"
    ManagedBy   = "terraform/02-platform-medium"
  }
}

# ---------------------------------------------------------------------------
# Module: Compute Base
# ---------------------------------------------------------------------------

module "compute_base" {
  source = "../../modules/compute_base"

  environment = local.environment
  enable_emr_serverless = true

  tags = local.tags
}

# ---------------------------------------------------------------------------
# Module: Database Aurora + pgvector
# ---------------------------------------------------------------------------

module "database_aurora" {
  source = "../../modules/database_aurora"

  environment    = local.environment
  vpc_id         = local.vpc_id
  subnet_ids     = local.private_subnet_ids
  allowed_security_group_ids = []

  database_name           = "postgres"
  master_username         = "postgres"
  serverless_min_capacity = 0.5
  serverless_max_capacity = 4.0
  deletion_protection     = false  # Disabled in dev for easier cleanup

  tags = local.tags
}

# ---------------------------------------------------------------------------
# Module: Observability
# ---------------------------------------------------------------------------

module "observability" {
  source = "../../modules/observability"

  environment = local.environment
  log_retention_days = 7

  tags = local.tags
}

# ---------------------------------------------------------------------------
# Publish outputs to SSM Parameter Store (for 03-application-dynamic)
# ---------------------------------------------------------------------------

resource "aws_ssm_parameter" "ecs_cluster_name" {
  name  = "/${local.environment}/platform-medium/ecs-cluster-name"
  type  = "String"
  value = module.compute_base.ecs_cluster_name

  tags = local.tags
}

resource "aws_ssm_parameter" "ecs_task_execution_role_arn" {
  name  = "/${local.environment}/platform-medium/ecs-task-execution-role-arn"
  type  = "String"
  value = module.compute_base.ecs_task_execution_role_arn

  tags = local.tags
}

resource "aws_ssm_parameter" "emr_application_id" {
  name  = "/${local.environment}/platform-medium/emr-application-id"
  type  = "String"
  value = module.compute_base.emr_application_id

  tags = local.tags
}

resource "aws_ssm_parameter" "db_host" {
  name  = "/${local.environment}/platform-medium/db-host"
  type  = "String"
  value = module.database_aurora.endpoint

  tags = local.tags
}

resource "aws_ssm_parameter" "db_name" {
  name  = "/${local.environment}/platform-medium/db-name"
  type  = "String"
  value = module.database_aurora.database_name

  tags = local.tags
}

resource "aws_ssm_parameter" "db_secret_arn" {
  name  = "/${local.environment}/platform-medium/db-secret-arn"
  type  = "String"
  value = module.database_aurora.secrets_manager_arn

  tags = local.tags
}

resource "aws_ssm_parameter" "db_security_group_id" {
  name  = "/${local.environment}/platform-medium/db-security-group-id"
  type  = "String"
  value = module.database_aurora.security_group_id

  tags = local.tags
}