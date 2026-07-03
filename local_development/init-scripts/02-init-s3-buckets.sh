#!/bin/bash
# ============================================================================
# 02-init-s3-buckets.sh
# LocalStack ready.d hook — creates the Medallion Architecture S3 buckets
# with the correct prefix structure for local development.
# ============================================================================

set -euo pipefail

echo "=== Initializing S3 Medallion buckets ==="

BUCKETS=(
  "ai-catalog-bronze-dev"
  "ai-catalog-silver-dev"
  "ai-catalog-gold-dev"
)

for bucket in "${BUCKETS[@]}"; do
  awslocal s3 mb "s3://${bucket}" --region us-east-1
  awslocal s3api put-public-access-block \
    --bucket "${bucket}" \
    --public-access-block-configuration "BlockPublicAcls=true,BlockPublicPolicy=true,IgnorePublicAcls=true,RestrictPublicBuckets=true"
  echo "  ✓ Created bucket: ${bucket}"
done

# Create sample landing data to simulate ingestion
echo "=== Seeding sample data ==="

SAMPLE_JSON='[
  {"id":1,"name":"Alice","email":"alice@example.com","signup_date":"2024-01-15","score":95.5},
  {"id":2,"name":"Bob","email":"bob@example.com","signup_date":"2024-02-20","score":87.3},
  {"id":3,"name":"Charlie","email":"charlie@example.com","signup_date":"2024-03-10","score":null}
]'

echo "${SAMPLE_JSON}" > /tmp/sample_users.json
awslocal s3 cp /tmp/sample_users.json "s3://ai-catalog-bronze-dev/crm/users/year=2024/month=03/day=10/users_20240310.json"
echo "  ✓ Seeded sample data in bronze"

echo "=== S3 initialization complete ==="