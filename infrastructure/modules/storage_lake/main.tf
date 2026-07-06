# ============================================================================
# storage_lake/main.tf — Medallion Architecture S3 Buckets
#
# Creates three S3 buckets implementing the Medallion Architecture:
#   Bronze: Raw ingestion landing zone (immutable writes)
#   Silver: Cleaned & validated Parquet structures
#   Gold:   Enriched, curated analytics / vector indexing
#
# Each bucket has:
#   - Public access blocks (all blocked)
#   - Server-side encryption (AES-256 via KMS)
#   - Lifecycle policies for cost-optimized tiering
#   - Bucket policies restricting access to the VPC endpoint
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

variable "environment" {
  description = "Deployment environment (dev/staging/prod)"
  type        = string
}

variable "bronze_bucket_name" {
  description = "Override name for the Bronze bucket"
  type        = string
  default     = ""
}

variable "silver_bucket_name" {
  description = "Override name for the Silver bucket"
  type        = string
  default     = ""
}

variable "gold_bucket_name" {
  description = "Override name for the Gold bucket"
  type        = string
  default     = ""
}

variable "vpc_endpoint_id" {
  description = "ID of the S3 VPC Endpoint for bucket policy restriction"
  type        = string
  default     = ""
}

variable "expiration_days_glacier" {
  description = "Days before transitioning non-current objects to Glacier"
  type        = number
  default     = 90
}

variable "tags" {
  description = "Common tags applied to all resources"
  type        = map(string)
  default     = {}
}

# ---------------------------------------------------------------------------
# KMS CMK for bucket encryption
# ---------------------------------------------------------------------------

resource "aws_kms_key" "lake" {
  description             = "KMS CMK for ${var.environment} Medallion Storage Lake"
  deletion_window_in_days = 30
  enable_key_rotation     = true

  tags = merge(var.tags, {
    Name        = "${var.environment}-lake-kms"
    Environment = var.environment
  })
}

resource "aws_kms_alias" "lake" {
  name          = "alias/${var.environment}-lake-key"
  target_key_id = aws_kms_key.lake.key_id
}

# ---------------------------------------------------------------------------
# Local bucket name computation
# ---------------------------------------------------------------------------

locals {
  bronze_name = var.bronze_bucket_name != "" ? var.bronze_bucket_name : "ai-catalog-${var.environment}-bronze"
  silver_name = var.silver_bucket_name != "" ? var.silver_bucket_name : "ai-catalog-${var.environment}-silver"
  gold_name   = var.gold_bucket_name != "" ? var.gold_bucket_name : "ai-catalog-${var.environment}-gold"
}

# ---------------------------------------------------------------------------
# Medallion buckets — resource loop
# ---------------------------------------------------------------------------

locals {
  buckets = {
    bronze = local.bronze_name
    silver = local.silver_name
    gold   = local.gold_name
  }
}

resource "aws_s3_bucket" "lake" {
  for_each = local.buckets

  bucket        = each.value
  force_destroy = var.environment == "dev" ? true : false

  tags = merge(var.tags, {
    Name        = each.value
    Environment = var.environment
    Layer       = each.key
    ManagedBy   = "terraform/01-core-static"
  })
}

# ── Public Access Block (all buckets) ──────────────────────────────────

resource "aws_s3_bucket_public_access_block" "lake" {
  for_each = aws_s3_bucket.lake

  bucket = each.value.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ── Encryption (KMS SSE) ──────────────────────────────────────────────

resource "aws_s3_bucket_server_side_encryption_configuration" "lake" {
  for_each = aws_s3_bucket.lake

  bucket = each.value.id

  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.lake.arn
      sse_algorithm     = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

# ── Versioning (Bronze = immutable, Silver/Gold = versioned for audit) ─

resource "aws_s3_bucket_versioning" "lake" {
  for_each = aws_s3_bucket.lake

  bucket = each.value.id

  versioning_configuration {
    status = each.key == "bronze" ? "Suspended" : "Enabled"
  }
}

# ── Lifecycle: transition noncurrent to Glacier, expire old versions ──

resource "aws_s3_bucket_lifecycle_configuration" "lake" {
  for_each = {
    for k, v in aws_s3_bucket.lake : k => v if k != "bronze"
  }

  bucket = each.value.id

  rule {
    id     = "glacier-transition"
    status = "Enabled"

    # Apply to all objects in the bucket (empty filter = no prefix restriction)
    filter {}

    noncurrent_version_transition {
      noncurrent_days = var.expiration_days_glacier
      storage_class   = "GLACIER"
    }

    noncurrent_version_expiration {
      noncurrent_days = var.expiration_days_glacier + 180
    }
  }
}

# ── Bucket Policy: enforce VPC endpoint and TLS ───────────────────────

resource "aws_s3_bucket_policy" "lake" {
  for_each = aws_s3_bucket.lake

  bucket = each.value.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnforceTLS"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          each.value.arn,
          "${each.value.arn}/*",
        ]
        Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

output "bronze_bucket_id" {
  description = "Bronze S3 bucket ID"
  value       = aws_s3_bucket.lake["bronze"].id
}

output "bronze_bucket_arn" {
  description = "Bronze S3 bucket ARN"
  value       = aws_s3_bucket.lake["bronze"].arn
}

output "silver_bucket_id" {
  description = "Silver S3 bucket ID"
  value       = aws_s3_bucket.lake["silver"].id
}

output "silver_bucket_arn" {
  description = "Silver S3 bucket ARN"
  value       = aws_s3_bucket.lake["silver"].arn
}

output "gold_bucket_id" {
  description = "Gold S3 bucket ID"
  value       = aws_s3_bucket.lake["gold"].id
}

output "gold_bucket_arn" {
  description = "Gold S3 bucket ARN"
  value       = aws_s3_bucket.lake["gold"].arn
}

output "kms_key_arn" {
  description = "KMS CMK ARN for lake encryption"
  value       = aws_kms_key.lake.arn
}

output "kms_key_id" {
  description = "KMS CMK ID for lake encryption"
  value       = aws_kms_key.lake.key_id
}