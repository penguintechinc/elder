"""Infisical cloud provider shared session factory."""

import logging
from typing import Any, Dict, Tuple

import requests

logger = logging.getLogger(__name__)


def create_infisical_session(config: dict[str, Any]) -> tuple[requests.Session, str]:
    """
    Create and configure an Infisical HTTP session.

    Handles:
    - Bearer token authentication
    - API base URL construction
    - Content-Type headers

    Args:
        config: Configuration dict with:
            - host: Infisical host URL (required, e.g., 'https://app.infisical.com')
            - service_token or token: Bearer token for authentication (required)
            - api_version: Optional API version (default: 'v3')

    Returns:
        Tuple of (session, api_base_url)

    Raises:
        Exception: If session initialization fails
    """
    try:
        host = config.get("host", "https://app.infisical.com").rstrip("/")
        token = config.get("service_token") or config.get("token")
        api_version = config.get("api_version", "v3")

        if not token:
            raise ValueError("service_token or token is required for Infisical")

        session = requests.Session()
        session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
        )

        api_base = f"{host}/api/{api_version}"

        logger.info(f"Initialized Infisical session for {host}")
        return session, api_base

    except Exception as e:
        logger.error(f"Failed to initialize Infisical session: {str(e)}")
        raise Exception(f"Failed to initialize Infisical session: {str(e)}")
