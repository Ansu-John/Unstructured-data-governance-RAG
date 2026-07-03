# ============================================================================
# ecs_tasks.tf — 03-application-dynamic (Dev)
#
# ECS Fargate task definitions and services for the agent runtime.
#
# This is the highest-change-frequency layer — task definitions, container
# images, environment variables, and resource limits are updated on every
# application release. Separating this from the cluster (02-platform-medium)
# prevents noise in the cluster's Terraform state.
# ============================================================================

# ── Task Definition: Agent Orchestrator ─────────────────────────────────

resource "aws_ecs_task_definition" "agent_orchestrator" {
  family                   = "${var.environment}-ai-catalog-agent"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "1024"
  memory                   = "3072"
  execution_role_arn       = local.ecs_execution_role_arn
  task_role_arn            = local.ecs_execution_role_arn

  container_definitions = jsonencode([
    {
      name  = "agent-orchestrator"
      image = "${var.container_image_uri != "" ? var.container_image_uri : "${var.environment}-ai-catalog-agent:latest"}"

      essential = true

      environment = [
        { name = "ENVIRONMENT",           value = var.environment },
        { name = "DB_HOST",               value = local.db_host },
        { name = "DB_NAME",               value = local.db_name },
        { name = "DB_USER",               value = "catalog_admin" },
        { name = "DB_PASSWORD",           value = "FROM_SECRETS_MANAGER" },
        { name = "BRONZE_S3_PATHS",       value = "s3://${local.bronze_bucket_id}/" },
        { name = "QUALITY_THRESHOLD",     value = "0.95" },
        { name = "AGENT_MAX_RETRIES",     value = "3" },
        { name = "LOG_LEVEL",            value = "INFO" },
        { name = "AWS_REGION",           value = "us-east-1" },
        { name = "OTEL_EXPORTER_OTLP_ENDPOINT", value = "" },
        { name = "OTEL_SERVICE_NAME",     value = "ai-catalog-agent" },
      ]

      log_configuration = {
        log_driver = "awslogs"
        options = {
          "awslogs-group"         = "/ecs/ai-catalog-agent/${var.environment}"
          "awslogs-region"        = "us-east-1"
          "awslogs-stream-prefix" = "agent-orchestrator"
        }
      }

      resource_requirements = []

      health_check = {
        command     = ["CMD-SHELL", "curl -f http://localhost:8080/health || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        start_period = 60
      }
    }
  ])

  tags = var.tags
}

# ── ECS Service ─────────────────────────────────────────────────────────

resource "aws_ecs_service" "agent_orchestrator" {
  count = var.create_ecs_service ? 1 : 0

  name                 = "${var.environment}-ai-catalog-agent-svc"
  cluster              = local.ecs_cluster_name
  task_definition      = aws_ecs_task_definition.agent_orchestrator.arn
  launch_type          = "FARGATE"
  desired_count        = 1
  enable_execute_command = var.environment == "dev" ? true : false

  network_configuration {
    subnets         = local.private_subnet_ids
    security_groups = [aws_security_group.ecs_tasks.id]
    assign_public_ip = false
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  tags = var.tags
}

# ── Security Group for ECS Tasks ─────────────────────────────────────────

resource "aws_security_group" "ecs_tasks" {
  name        = "${var.environment}-ai-catalog-ecs-tasks-sg"
  description = "Security group for ECS Fargate tasks"
  vpc_id      = local.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, {
    Name = "${var.environment}-ai-catalog-ecs-tasks-sg"
  })
}

# ── Task Definition: dbt Runner (ad-hoc) ─────────────────────────────────

resource "aws_ecs_task_definition" "dbt_runner" {
  family                   = "${var.environment}-ai-catalog-dbt"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = local.ecs_execution_role_arn
  task_role_arn            = local.ecs_execution_role_arn

  container_definitions = jsonencode([
    {
      name  = "dbt-runner"
      image = "${var.dbt_image_uri != "" ? var.dbt_image_uri : "ghcr.io/dbt-labs/dbt-postgres:1.8"}"

      essential = true

      environment = [
        { name = "DBT_TARGET",          value = var.environment },
        { name = "DB_HOST",             value = local.db_host },
        { name = "DB_PORT",             value = "5433" },
        { name = "DB_NAME",             value = local.db_name },
        { name = "DB_USER",             value = "catalog_admin" },
        { name = "DB_PASSWORD",         value = "FROM_SECRETS_MANAGER" },
      ]

      log_configuration = {
        log_driver = "awslogs"
        options = {
          "awslogs-group"         = "/ecs/ai-catalog-agent/${var.environment}"
          "awslogs-region"        = "us-east-1"
          "awslogs-stream-prefix" = "dbt-runner"
        }
      }
    }
  ])

  tags = var.tags
}