"""AWS cloud provider shared session/client factory."""

import logging
from typing import Any, Dict

import boto3

logger = logging.getLogger(__name__)


def create_aws_session_and_client(config: Dict[str, Any], service_name: str) -> Any:
    """
    Create and return an AWS boto3 client for the specified service.

    Handles credential resolution (explicit credentials, IAM role, or environment).
    Supports custom endpoint URLs for LocalStack, MinIO, etc.

    Args:
        config: Configuration dict with:
            - region: AWS region (required)
            - access_key_id: Optional AWS access key ID
            - secret_access_key: Optional AWS secret access key
            - session_token: Optional AWS session token
            - endpoint_url: Optional custom endpoint URL
        service_name: boto3 service name ('secretsmanager', 'kms', etc.)

    Returns:
        boto3 client instance

    Raises:
        Exception: If client initialization fails
    """
    try:
        session_params = {"region_name": config["region"]}

        # Add credentials if provided (otherwise use IAM role/instance profile)
        if "access_key_id" in config and "secret_access_key" in config:
            session_params["aws_access_key_id"] = config["access_key_id"]
            session_params["aws_secret_access_key"] = config["secret_access_key"]

            if "session_token" in config:
                session_params["aws_session_token"] = config["session_token"]

        session = boto3.session.Session(**session_params)

        client_params = {}
        if "endpoint_url" in config:
            client_params["endpoint_url"] = config["endpoint_url"]

        client = session.client(service_name, **client_params)

        logger.info(
            f"Initialized AWS {service_name} client for region {config['region']}"
        )
        return client

    except Exception as e:
        logger.error(f"Failed to initialize AWS {service_name} client: {str(e)}")
        raise Exception(f"Failed to initialize AWS {service_name} client: {str(e)}")
