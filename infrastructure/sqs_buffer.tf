# Establish the Decoupling Buffer (Amazon SQS)

# The Dead Letter Queue (DLQ)
resource "aws_sqs_queue" "pdf_dlq" {
  name                      = "pdf-ingestion-dlq-${var.environment}"
  message_retention_seconds = 1209600 # 14 days to allow for manual inspection
}

# The Main Ingestion Queue
resource "aws_sqs_queue" "pdf_queue" {
  name                       = "pdf-ingestion-queue-${var.environment}"
  visibility_timeout_seconds = 7200 # 2 hours (must be longer than max EMR job time)

  # Route poison pills to the DLQ after 3 failed attempts
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.pdf_dlq.arn
    maxReceiveCount     = 3
  })
}

# Allow EventBridge to write to this SQS Queue
resource "aws_sqs_queue_policy" "eventbridge_sqs_policy" {
  queue_url = aws_sqs_queue.pdf_queue.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "events.amazonaws.com" }
        Action    = "sqs:SendMessage"
        Resource  = aws_sqs_queue.pdf_queue.arn
      }
    ]
  })
}