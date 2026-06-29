# Configure the Event Router (Amazon EventBridge)

# The Event Rule: Listen for S3 ObjectCreated events specifically for PDFs
resource "aws_cloudwatch_event_rule" "pdf_upload_rule" {
  name        = "capture-pdf-uploads-${var.environment}"
  description = "Routes S3 PDF uploads to SQS"

  event_pattern = jsonencode({
    source      = ["aws.s3"]
    detail-type = ["Object Created"]
    detail = {
      bucket = { name = [aws_s3_bucket.bronze_pdfs.id] }
      object = { key = [{ suffix = ".pdf" }] } # Filter out non-PDFs natively
    }
  })
}

# The Target: Send the matching events to our SQS Queue
resource "aws_cloudwatch_event_target" "sqs_target" {
  rule      = aws_cloudwatch_event_rule.pdf_upload_rule.name
  target_id = "SendToSQS"
  arn       = aws_sqs_queue.pdf_queue.arn
}