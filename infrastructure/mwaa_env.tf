# Provision the MWAA Environment

resource "aws_mwaa_environment" "data_gov_airflow" {
  name               = "data-gov-orchestrator-${var.environment}"
  airflow_version    = "2.8.1"
  environment_class  = "mw1.small" # Use small for dev/testing, scale up for true prod
  execution_role_arn = aws_iam_role.mwaa_execution_role.arn
  
  # Point MWAA to the S3 bucket we just created
  source_bucket_arn  = aws_s3_bucket.mwaa_dags.arn
  dag_s3_path        = "dags/"

  # Enterprise Security Settings
  webserver_access_mode = "PUBLIC_ONLY" # Set to PRIVATE_ONLY for strict enterprise compliance (requires VPN/Bastion to view UI)

  network_configuration {
    security_group_ids = var.mwaa_security_group_ids
    subnet_ids         = var.vpc_subnet_ids
  }

  logging_configuration {
    dag_processing_logs {
      enabled   = true
      log_level = "INFO"
    }
    scheduler_logs {
      enabled   = true
      log_level = "INFO"
    }
    task_logs {
      enabled   = true
      log_level = "INFO"
    }
    webserver_logs {
      enabled   = true
      log_level = "ERROR"
    }
    worker_logs {
      enabled   = true
      log_level = "INFO"
    }
  }

  # Ensure the bucket is fully configured before attempting to build the environment
  depends_on = [
    aws_s3_bucket_public_access_block.mwaa_public_access,
    aws_iam_role_policy.mwaa_policy
  ]
}