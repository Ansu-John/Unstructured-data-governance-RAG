# ============================================================================
# backend.tf — 01-core-static (Dev)
#
# State is stored in a shared S3 backend with DynamoDB locking.
# The path prefix /dev/core-static/ enforces state segregation.
#
# This backend bucket must be created before the first apply (bootstrap).
# ============================================================================

terraform {
  backend "s3" {
    bucket         = "ai-catalog-terraform-state"
    key            = "dev/core-static/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "ai-catalog-terraform-locks"
  }
}