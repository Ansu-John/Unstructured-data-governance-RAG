# Establish the MWAA Execution Role

# Trust Policy allowing MWAA to assume this role
resource "aws_iam_role" "mwaa_execution_role" {
  name = "mwaa-execution-role-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = [
            "airflow.amazonaws.com",
            "airflow-env.amazonaws.com"
          ]
        }
        Action = "sts:AssumeRole"
      }
    ]
  })
}

# The actual permissions policy
resource "aws_iam_role_policy" "mwaa_policy" {
  name = "mwaa-execution-policy-${var.environment}"
  role = aws_iam_role.mwaa_execution_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Allow MWAA to read DAGs from its bucket
        Effect = "Allow"
        Action = [
          "s3:GetObject*",
          "s3:GetBucket*",
          "s3:List*"
        ]
        Resource = [
          aws_s3_bucket.mwaa_dags.arn,
          "${aws_s3_bucket.mwaa_dags.arn}/*"
        ]
      },
      {
        # Allow MWAA to write logs to CloudWatch
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:CreateLogGroup",
          "logs:PutLogEvents",
          "logs:GetLogEvents",
          "logs:GetLogRecord",
          "logs:GetLogGroupFields",
          "logs:GetQueryResults"
        ]
        Resource = "arn:aws:logs:*:*:log-group:airflow-*-*"
      },
      {
        # Allow MWAA to poll SQS and trigger EMR (The core pipeline permissions)
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
          "elasticmapreduce:RunJobFlow",
          "elasticmapreduce:DescribeCluster",
          "elasticmapreduce:TerminateJobFlows",
          "elasticmapreduce:AddJobFlowSteps"
        ]
        Resource = "*"
      },
      {
        # Allow MWAA to pass roles to EMR instances
        Effect   = "Allow"
        Action   = "iam:PassRole"
        Resource = "*"
        Condition = {
          StringLike = {
            "iam:PassedToService" : "elasticmapreduce.amazonaws.com"
          }
        }
      }
    ]
  })
}