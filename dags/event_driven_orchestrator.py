"""
Path: dags/event_driven_orchestrator.py
Description: Production orchestrator utilizing centralized AWS system parameters.
"""
 
from datetime import datetime, timedelta
import json
import logging
import boto3

from airflow.decorators import dag, task
from airflow.exceptions import AirflowException

# Import your custom shared utility function cleanly
from utils.aws_utils import get_ssm_parameter

logger = logging.getLogger("airflow.task")

AWS_REGION = "us-east-1"
SSM_QUEUE_PATH = "/enterprise-data-gov/prod/sqs_queue_url"

DEFAULT_ARGS = {
    "owner": "data-engineering",
    "depends_on_past": False,
    "start_date": datetime(2026, 1, 1),
    "retries": 3,
    "retry_delay": timedelta(minutes=2),
    "execution_timeout": timedelta(minutes=15),
}

@dag(
    dag_id="event_driven_orchestrator_trigger",
    default_args=DEFAULT_ARGS,
    description="Decoupled production pipeline using modularized AWS parameters.",
    schedule=None, # Use 'schedule' instead of 'schedule_interval'
    catchup=False,
    max_active_runs=1,
    tags=["production", "ingestion", "modular"],
)
def orchestrator_trigger_pipeline():

    @task(task_id="fetch_ssm_configuration")
    def fetch_config():
        # Call the reusable utility function safely
        queue_url = get_ssm_parameter(parameter_path=SSM_QUEUE_PATH, region_name=AWS_REGION)
        return {"sqs_queue_url": queue_url}

    @task(task_id="poll_and_parse_sqs_payload")
    def poll_and_parse_payload(config):
        queue_url = config["sqs_queue_url"]
        sqs_client = boto3.client("sqs", region_name=AWS_REGION)
        
        try:
            response = sqs_client.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=10,
                WaitTimeSeconds=20,
                VisibilityTimeout=300
            )
        except Exception as e:
            logger.error(f"SQS Communication Failure: {e}")
            raise AirflowException(e)
            
        messages = response.get("Messages", [])
        valid_records = []
        
        for msg in messages:
            try:
                body = json.loads(msg.get("Body", "{}"))
                if "Records" in body:
                    for record in body["Records"]:
                        s3_info = record.get("s3", {})
                        bucket_name = s3_info.get("bucket", {}).get("name")
                        object_key = s3_info.get("object", {}).get("key")
                        
                        if bucket_name and object_key:
                            valid_records.append({
                                "s3_uri": f"s3://{bucket_name}/{object_key}",
                                "receipt_handle": msg.get("ReceiptHandle"),
                                "queue_url": queue_url
                            })
            except json.JSONDecodeError:
                continue
                
        return valid_records

    @task(task_id="trigger_compute_fabric")
    def trigger_compute(staged_records):
        if not staged_records:
            logger.info("No new files found to process.")
            return

        sqs_client = boto3.client("sqs", region_name=AWS_REGION)

        for record in staged_records:
            logger.info(f"🚀 Launching distributed processing worker for target: {record['s3_uri']}")
            
            # Post-Success cleanup loop
            try:
                sqs_client.delete_message(
                    QueueUrl=record["queue_url"],
                    ReceiptHandle=record["receipt_handle"]
                )
            except Exception as e:
                logger.error(f"Failed to purge cleared message token: {e}")

    # Pipeline Topology
    runtime_config = fetch_config()
    staged_payloads = poll_and_parse_payload(runtime_config)
    trigger_compute(staged_payloads)

orchestrator_trigger_pipeline()