# Provision the Secure Landing Zone (Amazon S3)

# Create a Customer Managed KMS Key for S3 Encryption
resource "aws_kms_key" "s3_key" {
  description             = "KMS key for Bronze PDF ingestion bucket"
  deletion_window_in_days = 30
  enable_key_rotation     = true
}

# The Bronze Landing Bucket
resource "aws_s3_bucket" "bronze_pdfs" {
  bucket = "enterprise-data-gov-bronze-pdfs-${var.environment}"
}

# Enforce KMS Encryption
resource "aws_s3_bucket_server_side_encryption_configuration" "bronze_encryption" {
  bucket = aws_s3_bucket.bronze_pdfs.id
  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.s3_key.arn
      sse_algorithm     = "aws:kms"
    }
  }
}

# Enable EventBridge Routing on the Bucket
resource "aws_s3_bucket_notification" "bucket_notification" {
  bucket      = aws_s3_bucket.bronze_pdfs.id
  eventbridge = true
}

# Cost Optimization: Move old raw PDFs to Glacier
resource "aws_s3_bucket_lifecycle_configuration" "bronze_lifecycle" {
  bucket = aws_s3_bucket.bronze_pdfs.id
  rule {
    id     = "archive_old_raw_files"
    status = "Enabled"
    transition {
      days          = 30
      storage_class = "GLACIER_IR" # Instant Retrieval
    }
  }
}
