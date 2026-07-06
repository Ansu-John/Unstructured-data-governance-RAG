# ============================================================================
# data.tf — 02-platform-medium (Dev)
#
# Dynamically queries the outputs published by 01-core-static via SSM
# Parameter Store. This is the only cross-tier coupling point — no direct
# state references between tiers.
# ============================================================================

data "aws_ssm_parameter" "vpc_id" {
  name = "/dev/core-static/vpc-id"
}

data "aws_ssm_parameter" "private_subnet_ids" {
  name = "/dev/core-static/private-subnet-ids"
}

data "aws_ssm_parameter" "public_subnet_ids" {
  name = "/dev/core-static/public-subnet-ids"
}

data "aws_ssm_parameter" "bronze_bucket_arn" {
  name = "/dev/core-static/bronze-bucket-arn"
}

data "aws_ssm_parameter" "silver_bucket_arn" {
  name = "/dev/core-static/silver-bucket-arn"
}

data "aws_ssm_parameter" "gold_bucket_arn" {
  name = "/dev/core-static/gold-bucket-arn"
}

data "aws_ssm_parameter" "kms_key_arn" {
  name = "/dev/core-static/kms-key-arn"
}

# ---------------------------------------------------------------------------
# Data transforms
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# AWS context data sources
# ---------------------------------------------------------------------------

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

locals {
  vpc_id             = data.aws_ssm_parameter.vpc_id.value
  private_subnet_ids = split(",", data.aws_ssm_parameter.private_subnet_ids.value)
  public_subnet_ids  = split(",", data.aws_ssm_parameter.public_subnet_ids.value)
  bronze_bucket_arn  = data.aws_ssm_parameter.bronze_bucket_arn.value
  silver_bucket_arn  = data.aws_ssm_parameter.silver_bucket_arn.value
  gold_bucket_arn    = data.aws_ssm_parameter.gold_bucket_arn.value
  kms_key_arn        = data.aws_ssm_parameter.kms_key_arn.value
}