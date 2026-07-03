# ============================================================================
# outputs.tf — 01-core-static (Dev)
#
# While outputs are published to SSM Parameter Store for cross-tier sharing,
# we also emit them as Terraform outputs for CLI and CI/CD convenience.
# ============================================================================

output "vpc_id" {
  description = "VPC ID"
  value       = module.networking.vpc_id
}

output "vpc_cidr_block" {
  description = "VPC CIDR block"
  value       = module.networking.vpc_cidr_block
}

output "private_subnet_ids" {
  description = "Private subnet IDs"
  value       = module.networking.private_subnet_ids
}

output "public_subnet_ids" {
  description = "Public subnet IDs"
  value       = module.networking.public_subnet_ids
}

output "bronze_bucket_id" {
  description = "Bronze S3 bucket"
  value       = module.storage_lake.bronze_bucket_id
}

output "silver_bucket_id" {
  description = "Silver S3 bucket"
  value       = module.storage_lake.silver_bucket_id
}

output "gold_bucket_id" {
  description = "Gold S3 bucket"
  value       = module.storage_lake.gold_bucket_id
}

output "kms_key_arn" {
  description = "KMS CMK ARN for lake encryption"
  value       = module.storage_lake.kms_key_arn
}

output "ssm_parameters" {
  description = "Map of SSM parameter names published by this tier"
  value = {
    vpc_id             = "/${local.environment}/core-static/vpc-id"
    private_subnet_ids = "/${local.environment}/core-static/private-subnet-ids"
    public_subnet_ids  = "/${local.environment}/core-static/public-subnet-ids"
    bronze_bucket_id   = "/${local.environment}/core-static/bronze-bucket-id"
    bronze_bucket_arn  = "/${local.environment}/core-static/bronze-bucket-arn"
    silver_bucket_id   = "/${local.environment}/core-static/silver-bucket-id"
    silver_bucket_arn  = "/${local.environment}/core-static/silver-bucket-arn"
    gold_bucket_id     = "/${local.environment}/core-static/gold-bucket-id"
    gold_bucket_arn    = "/${local.environment}/core-static/gold-bucket-arn"
    kms_key_arn        = "/${local.environment}/core-static/kms-key-arn"
  }
}