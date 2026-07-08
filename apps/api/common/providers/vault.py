"""Hashicorp Vault cloud provider shared session factory."""

import logging
from typing import Any, Dict

import requests

logger = logging.getLogger(__name__)


def create_vault_session(config: Dict[str, Any]) -> requests.Session:
    """
    Create and configure a Vault HTTP session.

    Handles:
    - Token authentication
    - Namespace headers (Vault Enterprise)
    - TLS/mTLS certificate configuration
    - CA certificate validation

    Args:
        config: Configuration dict with:
            - url: Vault server URL (required)
            - token: Vault auth token (required)
            - namespace: Optional Vault Enterprise namespace
            - verify_tls: TLS verification (bool or CA cert path), default True
            - ca_cert: Optional CA certificate path
            - client_cert: Optional client certificate path (for mTLS)
            - client_key: Optional client key path (for mTLS)

    Returns:
        Configured requests.Session

    Raises:
        Exception: If session initialization fails
    """
    try:
        session = requests.Session()

        # Set Vault auth headers
        session.headers.update(
            {
                "X-Vault-Token": config.get("token", ""),
                "Content-Type": "application/json",
            }
        )

        # Add namespace header if provided (Vault Enterprise)
        if config.get("namespace"):
            session.headers.update({"X-Vault-Namespace": config["namespace"]})

        # Configure TLS verification
        verify_tls = config.get("verify_tls", True)
        if isinstance(verify_tls, str):
            session.verify = verify_tls
        elif verify_tls and config.get("ca_cert"):
            session.verify = config["ca_cert"]
        else:
            session.verify = verify_tls

        # Configure mTLS if provided
        if config.get("client_cert") and config.get("client_key"):
            session.cert = (config["client_cert"], config["client_key"])

        logger.info(f"Initialized Vault session for {config.get('url')}")
        return session

    except Exception as e:
        logger.error(f"Failed to initialize Vault session: {str(e)}")
        raise Exception(f"Failed to initialize Vault session: {str(e)}")
