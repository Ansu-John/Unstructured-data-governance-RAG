# backend.tf
terraform {
  backend "s3" {
    bucket         = "enterprise-data-gov-tfstate-prod"
    key            = "ingestion-layer/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "terraform-state-locking" # Prevents concurrent applies
    encrypt        = true
  }
}