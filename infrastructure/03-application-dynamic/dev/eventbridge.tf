# ============================================================================
# eventbridge.tf — Event-Driven Agent Trigger Rules
#
# Establishes an EventBridge rule that watches for new objects landing in
# the Bronze S3 bucket and triggers the ECS agent orchestrator to process
# them. This implements the "fire-and-forget" ingestion pattern: data lands
# in Bronze, EventBridge fires → ECS runs the LangGraph pipeline.
# ============================================================================

# ── EventBridge Rule: S3 Object Created in Bronze ───────────────────────

resource "aws_cloudwatch_event_rule" "bronze_landing" {
  name        = "${var.environment}-bronze-landing-trigger"
  description = "Trigger agent orchestration when new data lands in Bronze"

  event_pattern = jsonencode({
    source      = ["aws.s3"]
    detail-type = ["Object Created"]
    detail = {
      bucket = {
        name = [local.bronze_bucket_id]
      }
      object = {
        key = [
          { prefix = "" },
          { suffix = ".json" },
          { suffix = ".csv" },
          { suffix = ".parquet" },
        ]
      }
    }
  })

  tags = var.tags
}

# ── Target: ECS Task (Run Agent Orchestrator) ───────────────────────────

resource "aws_cloudwatch_event_target" "run_agent" {
  rule      = aws_cloudwatch_event_rule.bronze_landing.name
  event_bus_name = "default"
  arn       = data.aws_ecs_cluster.main.arn
  role_arn  = aws_iam_role.eventbridge_ecs.arn

  ecs_target {
    task_count          = 1
    task_definition_arn = aws_ecs_task_definition.agent_orchestrator.arn
    launch_type         = "FARGATE"

    network_configuration {
      subnets         = local.private_subnet_ids
      security_groups = [aws_security_group.ecs_tasks.id]
      assign_public_ip = false
    }
  }

  input_transformer {
    input_paths = {
      bucket_name = "$.detail.bucket.name"
      object_key  = "$.detail.object.key"
      size        = "$.detail.object.size"
    }

    input_template = jsonencode({
      environment = var.environment
      event_source = "EventBridge"
      bronze_path = "s3://<bucket_name>/<object_key>"
      file_size   = "<size>"
      triggered_at = "NOW"
    })
  }
}

# ── IAM Role for EventBridge → ECS ──────────────────────────────────────

resource "aws_iam_role" "eventbridge_ecs" {
  name = "${var.environment}-eventbridge-ecs-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "events.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })

  tags = var.tags
}

resource "aws_iam_role_policy" "eventbridge_ecs" {
  name = "${var.environment}-eventbridge-ecs-policy"
  role = aws_iam_role.eventbridge_ecs.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ecs:RunTask",
          "ecs:StopTask",
          "ecs:DescribeTasks",
        ]
        Resource = [
          aws_ecs_task_definition.agent_orchestrator.arn,
          "${replace(aws_ecs_task_definition.agent_orchestrator.arn, "/:\\d+$/", ":*")}",
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "iam:PassRole"
        ]
        Resource = [local.ecs_execution_role_arn]
      },
    ]
  })
}

# ── Schedule Rule: Periodic Quality Check ────────────────────────────────

resource "aws_cloudwatch_event_rule" "periodic_quality" {
  name        = "${var.environment}-periodic-quality-check"
  description = "Trigger quality re-validation on a schedule"

  schedule_expression = "rate(6 hours)"

  tags = var.tags
}

resource "aws_cloudwatch_event_target" "periodic_quality" {
  rule      = aws_cloudwatch_event_rule.periodic_quality.name
  arn       = data.aws_ecs_cluster.main.arn
  role_arn  = aws_iam_role.eventbridge_ecs.arn

  ecs_target {
    task_count          = 1
    task_definition_arn = aws_ecs_task_definition.agent_orchestrator.arn
    launch_type         = "FARGATE"

    network_configuration {
      subnets         = local.private_subnet_ids
      security_groups = [aws_security_group.ecs_tasks.id]
      assign_public_ip = false
    }
  }
}