# ============================================================================
# dynamic_alerts.tf — Application-Layer Alerting & Anomaly Notifications
#
# Provisions CloudWatch alarms specifically for the agent application layer:
# loop stalls, quality degradation spikes, and ECS service health.
#
# Infrastructure-level alerts (DB connections, cluster CPU) live in the
# 02-platform-medium observability module. Application-level alerts live
# here because they change with the application code and event patterns.
# ============================================================================

# ── Alarm: Agent Loop Stall (no successful cataloging in N hours) ────────

resource "aws_cloudwatch_metric_alarm" "agent_stall" {
  alarm_name          = "${var.environment}-agent-loop-stall"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = "6"
  metric_name         = "CatalogSuccessCount"
  namespace           = "AICatalog/Agent"
  period              = "600"
  statistic           = "Sum"
  threshold           = "1"
  treat_missing_data  = "breaching"
  datapoints_to_alarm = "6"

  alarm_description = "Agent loop may be stalled — no successful catalog events in 60 minutes"
  alarm_actions     = [aws_sns_topic.app_alarms.arn]
  ok_actions        = [aws_sns_topic.app_alarms.arn]
  insufficient_data_actions = [aws_sns_topic.app_alarms.arn]

  tags = var.tags
}

# ── Alarm: Quality Failure Spike ────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "quality_spike" {
  alarm_name          = "${var.environment}-quality-failure-spike"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "3"
  metric_name         = "QualityFailureCount"
  namespace           = "AICatalog/Agent"
  period              = "300"
  statistic           = "Sum"
  threshold           = "10"
  treat_missing_data  = "notBreaching"

  alarm_description = "Quality failures spiked above 10 in 15 minutes — possible upstream data issue"
  alarm_actions     = [aws_sns_topic.app_alarms.arn]

  tags = var.tags
}

# ── Alarm: Quarantine Rate Anomaly ──────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "quarantine_anomaly" {
  alarm_name          = "${var.environment}-quarantine-anomaly"
  comparison_operator = "GreaterThanUpperThreshold"
  evaluation_periods  = "2"
  metric_name         = "QuarantineEventCount"
  namespace           = "AICatalog/Quality"
  period              = "3600"

  statistic           = "Sum"
  threshold_metric_id = "m1"

  alarm_description = "Quarantine rate is anomalously high — investigate Bronze data quality"

  tags = var.tags
}

# ── Alarm: ECS Service Health ───────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "ecs_service_health" {
  alarm_name          = "${var.environment}-ecs-service-health"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = "3"
  metric_name         = "MemoryUtilization"
  namespace           = "AWS/ECS"
  dimensions = {
    ClusterName = local.ecs_cluster_name
    ServiceName = "${var.environment}-ai-catalog-agent-svc"
  }
  period    = "300"
  statistic = "Average"
  threshold = "20.0"
  treat_missing_data = "breaching"

  alarm_description = "ECS service health anomaly — check task logs"
  alarm_actions     = [aws_sns_topic.app_alarms.arn]

  tags = var.tags
}

# ── SNS Topic for Application Alarms ────────────────────────────────────

resource "aws_sns_topic" "app_alarms" {
  name = "${var.environment}-ai-catalog-app-alarms"

  tags = merge(var.tags, {
    Name = "${var.environment}-ai-catalog-app-alarms"
  })
}

# ── Composite Alarm: System Degraded ─────────────────────────────────────

resource "aws_cloudwatch_composite_alarm" "system_degraded" {
  alarm_name        = "${var.environment}-system-degraded"
  alarm_rule        = "ALARM(\"${aws_cloudwatch_metric_alarm.agent_stall.alarm_name}\") OR ALARM(\"${aws_cloudwatch_metric_alarm.quality_spike.alarm_name}\")"
  alarm_description = "SYSTEM DEGRADED — multiple subsystem alarms firing"

  alarm_actions = [aws_sns_topic.app_alarms.arn]

  tags = var.tags
}