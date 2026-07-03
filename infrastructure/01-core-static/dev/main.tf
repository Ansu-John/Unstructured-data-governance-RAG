# ============================================================================
# 01-core-static/dev/main.tf — Static Infrastructure Layer (Networking + Storage)
#
# This is the FIRST lifecycle tier to deploy. It provisions foundational
# infrastructure that rarely changes: VPC, subnets, NAT gateways, S3 buckets,
# and KMS keys. These resources have long-lived state and broad blast radius,
# so they are isolated in their own Terraform state.
#
# Outputs are published to SSM Parameter Store so the 02-platform-medium and
# 03-application-dynamic tiers can consume them without cross-state references.
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
    ManagedBy   = "terraform/01-core-static"
  }
}

# ---------------------------------------------------------------------------
# Module: Networking
# ---------------------------------------------------------------------------

module "networking" {
  source = "../../modules/networking"

  environment         = local.environment
  vpc_cidr            = "10.0.0.0/16"
  availability_zones  = ["us-east-1a", "us-east-1b"]
  enable_nat_gateway  = true
  enable_vpc_endpoints = true

  tags = local.tags
}

# ---------------------------------------------------------------------------
# Module: Storage Lake (Medallion S3 Buckets)
# ---------------------------------------------------------------------------

module "storage_lake" {
  source = "../../modules/storage_lake"

  environment = local.environment

  tags = local.tags

  # In dev, allow faster cleanup
  expiration_days_glacier = 30
}

# ---------------------------------------------------------------------------
# Publish outputs to SSM Parameter Store (for downstream tiers)
# ---------------------------------------------------------------------------

resource "aws_ssm_parameter" "vpc_id" {
  name  = "/${local.environment}/core-static/vpc-id"
  type  = "String"
  value = module.networking.vpc_id

  tags = local.tags
}

resource "aws_ssm_parameter" "private_subnet_ids" {
  name  = "/${local.environment}/core-static/private-subnet-ids"
  type  = "StringList"
  value = join(",", module.networking.private_subnet_ids)

  tags = local.tags
}

resource "aws_ssm_parameter" "public_subnet_ids" {
  name  = "/${local.environment}/core-static/public-subnet-ids"
  type  = "StringList"
  value = join(",", module.networking.public_subnet_ids)

  tags = local.tags
}

resource "aws_ssm_parameter" "bronze_bucket_id" {
  name  = "/${local.environment}/core-static/bronze-bucket-id"
  type  = "String"
  value = module.storage_lake.bronze_bucket_id

  tags = local.tags
}

resource "aws_ssm_parameter" "bronze_bucket_arn" {
  name  = "/${local.environment}/core-static/bronze-bucket-arn"
  type  = "String"
  value = module.storage_lake.bronze_bucket_arn

  tags = local.tags
}

resource "aws_ssm_parameter" "silver_bucket_id" {
  name  = "/${local.environment}/core-static/silver-bucket-id"
  type  = "String"
  value = module.storage_lake.silver_bucket_id

  tags = local.tags
}

resource "aws_ssm_parameter" "silver_bucket_arn" {
  name  = "/${local.environment}/core-static/silver-bucket-arn"
  type  = "String"
  value = module.storage_lake.silver_bucket_arn

  tags = local.tags
}

resource "aws_ssm_parameter" "gold_bucket_id" {
  name  = "/${local.environment}/core-static/gold-bucket-id"
  type  = "String"
  value = module.storage_lake.gold_bucket_id

  tags = local.tags
}

resource "aws_ssm_parameter" "gold_bucket_arn" {
  name  = "/${local.environment}/core-static/gold-bucket-arn"
  type  = "String"
  value = module.storage_lake.gold_bucket_arn

  tags = local.tags
}

resource "aws_ssm_parameter" "kms_key_arn" {
  name  = "/${local.environment}/core-static/kms-key-arn"
  type  = "String"
  value = module.storage_lake.kms_key_arn

  tags = local.tags
}