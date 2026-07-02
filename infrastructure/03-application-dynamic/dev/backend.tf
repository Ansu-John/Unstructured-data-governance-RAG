# ============================================================================
# backend.tf — 03-application-dynamic (Dev)
#
# Third segregated state — highest change frequency.
# ============================================================================

terraform {
  backend "s3" {
    bucket         = "ai-catalog-terraform-state"
    key            = "dev/app-dynamic/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "ai-catalog-terraform-locks"
  }
}