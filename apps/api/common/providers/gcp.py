"""GCP cloud provider shared client factory."""

import json
import logging
from typing import Any, Dict, Optional, Tuple

from google.oauth2 import service_account

logger = logging.getLogger(__name__)


def resolve_gcp_credentials(
    config: dict[str, Any],
) -> service_account.Credentials | None:
    """
    Resolve GCP credentials from config.

    Supports:
    - credentials_json: Service account JSON dict/string
    - credentials_file: Path to service account JSON file
    - Default application credentials (GOOGLE_APPLICATION_CREDENTIALS env var)

    Args:
        config: Configuration dict with optional credentials_json or credentials_file

    Returns:
        service_account.Credentials or None if using default credentials

    Raises:
        Exception: If credentials cannot be resolved
    """
    try:
        if "credentials_json" in config:
            creds_data = config["credentials_json"]
            if isinstance(creds_data, str):
                creds_data = json.loads(creds_data)
            return service_account.Credentials.from_service_account_info(creds_data)

        if "credentials_file" in config:
            return service_account.Credentials.from_service_account_file(
                config["credentials_file"]
            )

        # Use default application credentials
        return None

    except Exception as e:
        logger.error(f"Failed to resolve GCP credentials: {str(e)}")
        raise Exception(f"Failed to resolve GCP credentials: {str(e)}")


def create_gcp_client(
    config: dict[str, Any], client_factory_func: Any
) -> tuple[Any, service_account.Credentials | None]:
    """
    Create a GCP client using the provided factory function.

    Args:
        config: Configuration dict with project_id and optional credentials
        client_factory_func: Function that creates the client
                            (e.g., secretmanager.SecretManagerServiceClient)

    Returns:
        Tuple of (client, credentials)

    Raises:
        Exception: If client initialization fails
    """
    try:
        credentials = resolve_gcp_credentials(config)

        if credentials:
            client = client_factory_func(credentials=credentials)
        else:
            client = client_factory_func()

        project_id = config.get("project_id", "")
        logger.info(f"Initialized GCP client for project {project_id}")
        return client, credentials

    except Exception as e:
        logger.error(f"Failed to initialize GCP client: {str(e)}")
        raise Exception(f"Failed to initialize GCP client: {str(e)}")
