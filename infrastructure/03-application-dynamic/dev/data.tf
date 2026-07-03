# ============================================================================
# data.tf — 03-application-dynamic (Dev)
#
# Queries SSM parameters published by both 01-core-static and
# 02-platform-medium to build the full resource graph without any direct
# Terraform state references across lifecycle boundaries.
# ============================================================================

# ── From 01-core-static (networking + storage) ─────────────────────────

data "aws_ssm_parameter" "vpc_id" {
  name = "/dev/core-static/vpc-id"
}

data "aws_ssm_parameter" "private_subnet_ids" {
  name = "/dev/core-static/private-subnet-ids"
}

data "aws_ssm_parameter" "bronze_bucket_id" {
  name = "/dev/core-static/bronze-bucket-id"
}

data "aws_ssm_parameter" "silver_bucket_id" {
  name = "/dev/core-static/silver-bucket-id"
}

data "aws_ssm_parameter" "gold_bucket_id" {
  name = "/dev/core-static/gold-bucket-id"
}

data "aws_ssm_parameter" "kms_key_arn" {
  name = "/dev/core-static/kms-key-arn"
}

# ── From 02-platform-medium (compute + database) ────────────────────────

data "aws_ssm_parameter" "ecs_cluster_name" {
  name = "/dev/platform-medium/ecs-cluster-name"
}

data "aws_ssm_parameter" "ecs_task_execution_role_arn" {
  name = "/dev/platform-medium/ecs-task-execution-role-arn"
}

data "aws_ssm_parameter" "db_host" {
  name = "/dev/platform-medium/db-host"
}

data "aws_ssm_parameter" "db_name" {
  name = "/dev/platform-medium/db-name"
}

data "aws_ssm_parameter" "db_secret_arn" {
  name = "/dev/platform-medium/db-secret-arn"
}

data "aws_ssm_parameter" "db_security_group_id" {
  name = "/dev/platform-medium/db-security-group-id"
}

# ---------------------------------------------------------------------------
# Data transforms
# ---------------------------------------------------------------------------

locals {
  vpc_id                 = data.aws_ssm_parameter.vpc_id.value
  private_subnet_ids     = split(",", data.aws_ssm_parameter.private_subnet_ids.value)
  bronze_bucket_id       = data.aws_ssm_parameter.bronze_bucket_id.value
  silver_bucket_id       = data.aws_ssm_parameter.silver_bucket_id.value
  gold_bucket_id         = data.aws_ssm_parameter.gold_bucket_id.value
  ecs_cluster_name       = data.aws_ssm_parameter.ecs_cluster_name.value
  ecs_execution_role_arn = data.aws_ssm_parameter.ecs_task_execution_role_arn.value
  db_host                = data.aws_ssm_parameter.db_host.value
  db_name                = data.aws_ssm_parameter.db_name.value
  db_secret_arn          = data.aws_ssm_parameter.db_secret_arn.value
}