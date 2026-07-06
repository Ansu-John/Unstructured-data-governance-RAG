# ============================================================================
# data.tf — 02-platform-medium (Dev)
#
# Dynamically queries the outputs published by 01-core-static via SSM
# Parameter Store. This is the only cross-tier coupling point — no direct
# state references between tiers.
#
# NOTE: Only the SSM parameters actually consumed by this tier are queried.
# Unused parameters (bronze/silver/gold bucket ARNs, public subnet IDs, etc.)
# are intentionally excluded to reduce the SSM permission surface area.
# ============================================================================

# ---------------------------------------------------------------------------
# SSM Parameters from 01-core-static (cross-tier data handoff)
# ---------------------------------------------------------------------------

data "aws_ssm_parameter" "vpc_id" {
  name = "/dev/core-static/vpc-id"
}

data "aws_ssm_parameter" "private_subnet_ids" {
  name = "/dev/core-static/private-subnet-ids"
}

data "aws_ssm_parameter" "kms_key_arn" {
  name = "/dev/core-static/kms-key-arn"
}

# ---------------------------------------------------------------------------
# AWS context data sources (no permissions needed — these are metadata)
# ---------------------------------------------------------------------------

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

# ---------------------------------------------------------------------------
# Local transforms
# ---------------------------------------------------------------------------

locals {
  vpc_id             = data.aws_ssm_parameter.vpc_id.value
  private_subnet_ids = split(",", data.aws_ssm_parameter.private_subnet_ids.value)
  kms_key_arn        = data.aws_ssm_parameter.kms_key_arn.value
}