# Provision the MWAA Storage

# The Dedicated MWAA Bucket
resource "aws_s3_bucket" "mwaa_dags" {
  bucket = "enterprise-data-gov-mwaa-dags-${var.environment}"
}

# Versioning is strictly required by MWAA
resource "aws_s3_bucket_versioning" "mwaa_versioning" {
  bucket = aws_s3_bucket.mwaa_dags.id
  versioning_configuration {
    status = "Enabled"
  }
}

# Block all public access for enterprise security
resource "aws_s3_bucket_public_access_block" "mwaa_public_access" {
  bucket                  = aws_s3_bucket.mwaa_dags.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}