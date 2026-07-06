# ============================================================================
# observability/main.tf — CloudWatch Log Groups, Metrics, and Alarms
#
# Provides centralized observability for the entire platform:
#   1. CloudWatch Log Groups for ECS, EMR, and agent execution logs.
#   2. Metrics filters for key operational signals.
#   3. Composite alarms for agent loop stalls, quality drops, and DB health.
#   4. Dashboard widgets for the operational overview.
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

variable "log_retention_days" {
  description = "CloudWatch log retention in days"
  type        = number
  default     = 30
}

variable "alarm_email" {
  description = "Email address for SNS alarm notifications"
  type        = string
  default     = ""
}

variable "tags" {
  description = "Common tags applied to all resources"
  type        = map(string)
  default     = {}
}

# ---------------------------------------------------------------------------
# CloudWatch Log Groups
# ---------------------------------------------------------------------------

locals {
  log_groups = {
    ecs_agent      = "/ecs/ai-catalog-agent/${var.environment}"
    emr_pipeline   = "/emr/ai-catalog-pipeline/${var.environment}"
    agent_graph    = "/langgraph/ai-catalog-agent/${var.environment}"
    quality_runs   = "/quality/gx-runs/${var.environment}"
    db_audit       = "/rds/ai-catalog-db/${var.environment}"
  }
}

resource "aws_cloudwatch_log_group" "main" {
  for_each = local.log_groups

  name              = each.value
  retention_in_days = var.log_retention_days

  tags = merge(var.tags, {
    Name        = each.value
    Environment = var.environment
    ManagedBy   = "terraform/02-platform-medium"
  })
}

# ---------------------------------------------------------------------------
# Metric Filters — extract signals from structured logs
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_metric_filter" "quality_failure" {
  name           = "${var.environment}-quality-failure-count"
  pattern        = "\"QUALITY FAIL\""
  log_group_name = aws_cloudwatch_log_group.main["agent_graph"].name

  metric_transformation {
    name          = "QualityFailureCount"
    namespace     = "AICatalog/Agent"
    value         = "1"
    default_value = 0
  }
}

resource "aws_cloudwatch_log_metric_filter" "agent_error" {
  name           = "${var.environment}-agent-error-count"
  pattern        = "\"ERROR\""
  log_group_name = aws_cloudwatch_log_group.main["agent_graph"].name

  metric_transformation {
    name          = "AgentErrorCount"
    namespace     = "AICatalog/Agent"
    value         = "1"
    default_value = 0
  }
}

resource "aws_cloudwatch_log_metric_filter" "quarantine_event" {
  name           = "${var.environment}-quarantine-event"
  pattern        = "\"QUARANTINE\""
  log_group_name = aws_cloudwatch_log_group.main["quality_runs"].name

  metric_transformation {
    name          = "QuarantineEventCount"
    namespace     = "AICatalog/Quality"
    value         = "1"
    default_value = 0
  }
}

resource "aws_cloudwatch_log_metric_filter" "catalog_success" {
  name           = "${var.environment}-catalog-success-count"
  pattern        = "\"Cataloged\""
  log_group_name = aws_cloudwatch_log_group.main["agent_graph"].name

  metric_transformation {
    name          = "CatalogSuccessCount"
    namespace     = "AICatalog/Agent"
    value         = "1"
    default_value = 0
  }
}

# ---------------------------------------------------------------------------
# SNS Topic for Alarm Notifications
# ---------------------------------------------------------------------------

resource "aws_sns_topic" "alarms" {
  name = "${var.environment}-ai-catalog-alarms"

  tags = merge(var.tags, {
    Environment = var.environment
  })
}

# ---------------------------------------------------------------------------
# CloudWatch Alarms
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "agent_loop_stall" {
  alarm_name          = "${var.environment}-agent-loop-stall"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = "3"
  metric_name         = "CatalogSuccessCount"
  namespace           = "AICatalog/Agent"
  period              = "300"
  statistic           = "Sum"
  threshold           = "1"
  treat_missing_data  = "breaching"

  alarm_description = "Agent loop may be stalled — no catalog entries in 15 minutes"
  alarm_actions     = [aws_sns_topic.alarms.arn]

  tags = merge(var.tags, {
    Environment = var.environment
  })
}

resource "aws_cloudwatch_metric_alarm" "quality_drop" {
  alarm_name          = "${var.environment}-quality-drop"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "2"
  metric_name         = "QualityFailureCount"
  namespace           = "AICatalog/Quality"
  period              = "300"
  statistic           = "Sum"
  threshold           = "5"
  treat_missing_data  = "notBreaching"

  alarm_description = "Elevated quality failures detected — check Bronze data quality"
  alarm_actions     = [aws_sns_topic.alarms.arn]

  tags = merge(var.tags, {
    Environment = var.environment
  })
}

resource "aws_cloudwatch_metric_alarm" "agent_error_rate" {
  alarm_name          = "${var.environment}-agent-error-rate"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "2"
  metric_name         = "AgentErrorCount"
  namespace           = "AICatalog/Agent"
  period              = "300"
  statistic           = "Sum"
  threshold           = "10"
  treat_missing_data  = "notBreaching"

  alarm_description = "Agent error rate exceeded threshold — check logs"
  alarm_actions     = [aws_sns_topic.alarms.arn]

  tags = merge(var.tags, {
    Environment = var.environment
  })
}

# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = "${var.environment}-ai-catalog-dashboard"

  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric"
        properties = {
          metrics = [
            ["AICatalog/Agent", "CatalogSuccessCount", { stat = "Sum" }],
            ["AICatalog/Agent", "AgentErrorCount", { stat = "Sum" }],
          ]
          period = 300
          stat   = "Sum"
          region = "us-east-1"
          title  = "${var.environment} — Agent Performance"
        }
      },
      {
        type = "metric"
        properties = {
          metrics = [
            ["AICatalog/Quality", "QualityFailureCount", { stat = "Sum" }],
            ["AICatalog/Quality", "QuarantineEventCount", { stat = "Sum" }],
          ]
          period = 300
          stat   = "Sum"
          region = "us-east-1"
          title  = "${var.environment} — Quality Metrics"
        }
      },
      {
        type = "log"
        properties = {
          query   = "SOURCE '${aws_cloudwatch_log_group.main["agent_graph"].name}' | fields @timestamp, @message | filter @message like /ERROR|FAIL|quarantine/ | sort @timestamp desc | limit 20"
          region  = "us-east-1"
          title   = "${var.environment} — Recent Agent Errors"
        }
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

output "log_group_names" {
  description = "Map of log group names by purpose"
  value = {
    for k, v in aws_cloudwatch_log_group.main : k => v.name
  }
}

output "log_group_arns" {
  description = "Map of log group ARNs by purpose"
  value = {
    for k, v in aws_cloudwatch_log_group.main : k => v.arn
  }
}

output "sns_topic_arn" {
  description = "ARN of the SNS alarm notification topic"
  value       = aws_sns_topic.alarms.arn
}

output "dashboard_name" {
  description = "Name of the CloudWatch dashboard"
  value       = aws_cloudwatch_dashboard.main.dashboard_name
}