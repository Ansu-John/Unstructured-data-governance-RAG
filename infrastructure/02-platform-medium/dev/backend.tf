# ============================================================================
# backend.tf — 02-platform-medium (Dev)
#
# Separate state path from 01-core-static to isolate change blast radius.
# ============================================================================

terraform {
  backend "s3" {
    bucket         = "ai-catalog-terraform-state"
    key            = "dev/platform-medium/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "ai-catalog-terraform-locks"
  }
}