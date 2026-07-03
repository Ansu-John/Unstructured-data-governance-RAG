# ============================================================================
# networking/main.tf — VPC, Subnets, NAT Gateways
#
# Provision a dual-AZ VPC with private/public subnets, NAT Gateways for
# outbound internet from private subnets, and VPC Endpoints for S3 and
# DynamoDB (without traversing the internet).
#
# This module is consumed by the 01-core-static lifecycle tier.
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

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "availability_zones" {
  description = "List of Availability Zones to use"
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b"]
}

variable "enable_nat_gateway" {
  description = "Enable NAT Gateway for private subnet outbound traffic"
  type        = bool
  default     = true
}

variable "enable_vpc_endpoints" {
  description = "Enable VPC Endpoints for S3 and DynamoDB"
  type        = bool
  default     = true
}

variable "tags" {
  description = "Common tags applied to all resources"
  type        = map(string)
  default     = {}
}

# ---------------------------------------------------------------------------
# VPC
# ---------------------------------------------------------------------------

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true
  instance_tenancy     = "default"

  tags = merge(var.tags, {
    Name        = "${var.environment}-ai-catalog-vpc"
    Environment = var.environment
    ManagedBy   = "terraform/01-core-static"
  })
}

# ---------------------------------------------------------------------------
# Subnets — public
# ---------------------------------------------------------------------------

resource "aws_subnet" "public" {
  count = length(var.availability_zones)

  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone       = var.availability_zones[count.index]
  map_public_ip_on_launch = true

  tags = merge(var.tags, {
    Name        = "${var.environment}-public-${count.index + 1}"
    Environment = var.environment
    Type        = "public"
    Tier        = "static"
  })
}

# ---------------------------------------------------------------------------
# Subnets — private (for ECS tasks, databases)
# ---------------------------------------------------------------------------

resource "aws_subnet" "private" {
  count = length(var.availability_zones)

  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index + 10)
  availability_zone       = var.availability_zones[count.index]
  map_public_ip_on_launch = false

  tags = merge(var.tags, {
    Name        = "${var.environment}-private-${count.index + 1}"
    Environment = var.environment
    Type        = "private"
    Tier        = "static"
  })
}

# ---------------------------------------------------------------------------
# Internet Gateway
# ---------------------------------------------------------------------------

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = merge(var.tags, {
    Name        = "${var.environment}-igw"
    Environment = var.environment
  })
}

# ---------------------------------------------------------------------------
# NAT Gateway (one per AZ for HA)
# ---------------------------------------------------------------------------

resource "aws_eip" "nat" {
  count  = var.enable_nat_gateway ? 1 : 0
  domain = "vpc"

  tags = merge(var.tags, {
    Name = "${var.environment}-nat-eip"
  })
}

resource "aws_nat_gateway" "main" {
  count         = var.enable_nat_gateway ? 1 : 0
  allocation_id = aws_eip.nat[0].id
  subnet_id     = aws_subnet.public[0].id

  tags = merge(var.tags, {
    Name        = "${var.environment}-nat-gw"
    Environment = var.environment
  })

  depends_on = [aws_internet_gateway.main]
}

# ---------------------------------------------------------------------------
# Route Tables
# ---------------------------------------------------------------------------

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = merge(var.tags, {
    Name        = "${var.environment}-public-rt"
    Environment = var.environment
  })
}

resource "aws_route_table" "private" {
  count  = var.enable_nat_gateway ? 1 : 0
  vpc_id = aws_vpc.main.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main[0].id
  }

  tags = merge(var.tags, {
    Name        = "${var.environment}-private-rt"
    Environment = var.environment
  })
}

# ---------------------------------------------------------------------------
# Route Table Associations
# ---------------------------------------------------------------------------

resource "aws_route_table_association" "public" {
  count          = length(var.availability_zones)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count          = var.enable_nat_gateway ? length(var.availability_zones) : 0
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[0].id
}

# ---------------------------------------------------------------------------
# VPC Endpoints (no-internet access to AWS services)
# ---------------------------------------------------------------------------

resource "aws_vpc_endpoint" "s3" {
  count       = var.enable_vpc_endpoints ? 1 : 0
  vpc_id      = aws_vpc.main.id
  service_name = "com.amazonaws.${data.aws_region.current.name}.s3"
  vpc_endpoint_type = "Gateway"

  route_table_ids = [aws_route_table.private[0].id]

  tags = merge(var.tags, {
    Name = "${var.environment}-s3-vpce"
  })
}

resource "aws_vpc_endpoint" "dynamodb" {
  count       = var.enable_vpc_endpoints ? 1 : 0
  vpc_id      = aws_vpc.main.id
  service_name = "com.amazonaws.${data.aws_region.current.name}.dynamodb"
  vpc_endpoint_type = "Gateway"

  route_table_ids = [aws_route_table.private[0].id]

  tags = merge(var.tags, {
    Name = "${var.environment}-dynamodb-vpce"
  })
}

# ---------------------------------------------------------------------------
# Data sources
# ---------------------------------------------------------------------------

data "aws_region" "current" {}

# ---------------------------------------------------------------------------
# Outputs (consumed by 02-platform-medium and 03-application-dynamic)
# ---------------------------------------------------------------------------

output "vpc_id" {
  description = "The ID of the VPC"
  value       = aws_vpc.main.id
}

output "public_subnet_ids" {
  description = "List of public subnet IDs"
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "List of private subnet IDs"
  value       = aws_subnet.private[*].id
}

output "nat_gateway_ips" {
  description = "List of NAT Gateway public IPs"
  value       = var.enable_nat_gateway ? aws_eip.nat[*].public_ip : []
}

output "vpc_cidr_block" {
  description = "The CIDR block of the VPC"
  value       = aws_vpc.main.cidr_block
}