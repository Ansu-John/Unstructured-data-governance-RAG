"""
Path: dags/utils/aws_utils.py
Description: Centralized, reusable AWS utility helpers for enterprise data workflows.
"""

import logging
import boto3
from airflow.exceptions import AirflowException

logger = logging.getLogger("airflow.task")

def get_ssm_parameter(parameter_path: str, region_name: str = "us-east-1") -> str:
    """
    Securely fetches and decrypts a string value from the AWS SSM Parameter Store.
    
    :param parameter_path: The absolute path of the parameter (e.g., '/prod/sqs_url')
    :param region_name: Target AWS region
    :return: Decrypted string value of the parameter
    """
    try:
        logger.info(f"Dynamically pulling parameter context from SSM: {parameter_path}")
        ssm_client = boto3.client("ssm", region_name=region_name)
        
        response = ssm_client.get_parameter(Name=parameter_path, WithDecryption=True)
        return response["Parameter"]["Value"]
        
    except ssm_client.exceptions.ParameterNotFound:
        logger.error(f"SSM Parameter lookup failure. Path not found: {parameter_path}")
        raise AirflowException(f"Missing SSM Configuration: {parameter_path}")
    except Exception as e:
        logger.error(f"Unexpected connectivity error while accessing AWS SSM: {str(e)}")
        raise AirflowException(f"SSM Fetch Exception: {e}")