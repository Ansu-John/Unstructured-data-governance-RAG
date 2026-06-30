# infrastructure/variables.tf

variable "environment" {
  description = "The deployment environment (e.g., dev, staging, prod)"
  type        = string
}

variable "region" {
  description = "The AWS region"
  type        = string
  default     = "us-east-1"
}

variable "account_id" {
  description = "The AWS account id"
  type        = string
  default     = "932021957888"
}

variable "vpc_subnet_ids" {
  description = "List of private subnet IDs for MWAA (Must have NAT Gateway access)"
  type        = list(string)
  default     = ["subnet-065564f33d3fa2a8e", "subnet-039fed859e511d835"]
}

variable "mwaa_security_group_ids" {
  description = "List of security group IDs for the MWAA environment"
  type        = list(string)
  default     = ["sg-aa8cd19e"]
}