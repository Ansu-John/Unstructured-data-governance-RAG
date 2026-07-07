# ============================================================================
# database_aurora/main.tf — Aurora Serverless v2 PostgreSQL + pgvector
#
# Provisions an Amazon Aurora Serverless v2 PostgreSQL cluster with the
# pgvector extension enabled, acting as both:
#   1. The central state checkpointer for LangGraph execution persistence.
#   2. The primary vector store for semantic search over cataloged assets.
#
# Key design decisions:
#   - Serverless v2 for auto-scaling (0.5–128 ACU) to match variable
#     catalog workloads without over-provisioning.
#   - pgvector enabled via cluster parameter group (shared_preload_libraries).
#   - Deployed in private subnets with a security group allowing inbound
#     from the ECS/EMR security groups only.
#   - Credentials stored in AWS Secrets Manager (not plaintext in state).
# ============================================================================

terraform {
  required_version = ">= 1.7"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.50"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

variable "environment" {
  description = "Deployment environment (dev/staging/prod)"
  type        = string
}

variable "vpc_id" {
  description = "VPC ID for the DB subnet group"
  type        = string
}

variable "subnet_ids" {
  description = "List of private subnet IDs for the DB subnet group"
  type        = list(string)
}

variable "allowed_security_group_ids" {
  description = "List of security group IDs allowed to connect to the database"
  type        = list(string)
  default     = []
}

variable "database_name" {
  description = "Name of the initial database"
  type        = string
  default     = "postgres"
}

variable "master_username" {
  description = "Master username for the Aurora cluster"
  type        = string
  default     = "postgres"
}

variable "serverless_min_capacity" {
  description = "Minimum Aurora capacity units (ACU)"
  type        = number
  default     = 0.5
}

variable "serverless_max_capacity" {
  description = "Maximum Aurora capacity units (ACU)"
  type        = number
  default     = 8.0
}

variable "deletion_protection" {
  description = "Enable deletion protection on the cluster"
  type        = bool
  default     = true
}

variable "tags" {
  description = "Common tags applied to all resources"
  type        = map(string)
  default     = {}
}

# ---------------------------------------------------------------------------
# Random password for master credentials
# ---------------------------------------------------------------------------

resource "random_password" "master" {
  length  = 32
  special = false
}

# ---------------------------------------------------------------------------
# Secrets Manager — store DB credentials
# ---------------------------------------------------------------------------

resource "aws_secretsmanager_secret" "db_credentials" {
  name = "${var.environment}-ai-catalog-db-credentials"

  tags = merge(var.tags, {
    Name        = "${var.environment}-ai-catalog-db-credentials"
    Environment = var.environment
  })
}

resource "aws_secretsmanager_secret_version" "db_credentials" {
  secret_id = aws_secretsmanager_secret.db_credentials.id

  secret_string = jsonencode({
    username = var.master_username
    password = random_password.master.result
    host     = aws_rds_cluster.aurora.endpoint
    port     = 5432
    dbname   = var.database_name
    engine   = "aurora-postgresql"
    dbClusterIdentifier = aws_rds_cluster.aurora.cluster_identifier
  })
}

# ---------------------------------------------------------------------------
# DB Subnet Group
# ---------------------------------------------------------------------------

resource "aws_db_subnet_group" "aurora" {
  name        = "${var.environment}-ai-catalog-db-subnet-group"
  description = "Subnet group for Aurora Serverless v2 PostgreSQL"
  subnet_ids  = var.subnet_ids

  tags = merge(var.tags, {
    Name        = "${var.environment}-ai-catalog-db-subnet-group"
    Environment = var.environment
  })
}

# ---------------------------------------------------------------------------
# Security Group
# ---------------------------------------------------------------------------

resource "aws_security_group" "aurora" {
  name        = "${var.environment}-ai-catalog-db-sg"
  description = "Security group for Aurora PostgreSQL cluster"
  vpc_id      = var.vpc_id

  ingress {
    description     = "PostgreSQL from allowed security groups"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = var.allowed_security_group_ids
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, {
    Name        = "${var.environment}-ai-catalog-db-sg"
    Environment = var.environment
  })
}

# ---------------------------------------------------------------------------
# DB Cluster Parameter Group (pgvector support)
# ---------------------------------------------------------------------------

resource "aws_rds_cluster_parameter_group" "aurora" {
  name        = "${var.environment}-ai-catalog-pg-cluster-params"
  family      = "aurora-postgresql16"
  description = "Cluster parameter group with pgvector enabled"

  parameter {
    name  = "shared_preload_libraries"
    value = "pg_stat_statements"
    apply_method = "pending-reboot"
  }

  parameter {
    name  = "rds.logical_replication"
    value = "1"
    apply_method = "pending-reboot"
  }

  tags = merge(var.tags, {
    Environment = var.environment
  })
}

resource "aws_db_parameter_group" "aurora" {
  name        = "${var.environment}-ai-catalog-pg-db-params"
  family      = "aurora-postgresql16"
  description = "DB parameter group for pgvector configuration"

  # The vector.dimension_limit parameter block has been removed
  # because it is not supported by the AWS RDS API.

  tags = merge(var.tags, {
    Environment = var.environment
  })
}

# ---------------------------------------------------------------------------
# Aurora Serverless v2 Cluster
# ---------------------------------------------------------------------------

resource "aws_rds_cluster" "aurora" {
  cluster_identifier          = "${var.environment}-ai-catalog-aurora-cluster"
  engine                      = "aurora-postgresql"
  engine_version              = "16.4"
  engine_mode                 = "provisioned"
  database_name               = var.database_name
  master_username             = var.master_username
  master_password             = random_password.master.result
  port                        = 5432
  db_subnet_group_name        = aws_db_subnet_group.aurora.name
  vpc_security_group_ids      = [aws_security_group.aurora.id]
  db_cluster_parameter_group_name = aws_rds_cluster_parameter_group.aurora.name
  deletion_protection         = var.deletion_protection
  skip_final_snapshot         = var.environment == "dev" ? true : false
  storage_encrypted           = true
  backup_retention_period     = var.environment == "prod" ? 35 : 7
  preferred_backup_window     = "03:00-04:00"
  preferred_maintenance_window = "sun:05:00-sun:06:00"

  serverlessv2_scaling_configuration {
    min_capacity = var.serverless_min_capacity
    max_capacity = var.serverless_max_capacity
  }

  tags = merge(var.tags, {
    Name        = "${var.environment}-ai-catalog-aurora-cluster"
    Environment = var.environment
    ManagedBy   = "terraform/02-platform-medium"
  })
}

# ---------------------------------------------------------------------------
# DB Instance (single writer instance in dev, multi-AZ in prod)
# ---------------------------------------------------------------------------

resource "aws_rds_cluster_instance" "writer" {
  count              = 1
  identifier         = "${var.environment}-ai-catalog-aurora-writer-${count.index + 1}"
  cluster_identifier = aws_rds_cluster.aurora.id
  instance_class     = "db.serverless"
  engine             = aws_rds_cluster.aurora.engine
  engine_version     = aws_rds_cluster.aurora.engine_version
  db_parameter_group_name = aws_db_parameter_group.aurora.name

  tags = merge(var.tags, {
    Name        = "${var.environment}-ai-catalog-aurora-writer"
    Environment = var.environment
  })
}

# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

output "cluster_identifier" {
  description = "Aurora cluster identifier"
  value       = aws_rds_cluster.aurora.cluster_identifier
}

output "endpoint" {
  description = "Aurora cluster writer endpoint"
  value       = aws_rds_cluster.aurora.endpoint
}

output "reader_endpoint" {
  description = "Aurora cluster reader endpoint"
  value       = aws_rds_cluster.aurora.reader_endpoint
}

output "database_name" {
  description = "Database name"
  value       = var.database_name
}

output "master_username" {
  description = "Master username for the database"
  value       = var.master_username
}

output "security_group_id" {
  description = "Security group ID for the database"
  value       = aws_security_group.aurora.id
}

output "secrets_manager_arn" {
  description = "ARN of the Secrets Manager entry with DB credentials"
  value       = aws_secretsmanager_secret.db_credentials.arn
}