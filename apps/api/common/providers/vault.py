"""Hashicorp Vault cloud provider shared session factory."""

import logging
import random
import time
from typing import Any

import requests

from shared.utils.env import env_float, env_int

logger = logging.getLogger(__name__)

#: Methods safe to repeat: Vault reads/lists have no side effects. Writes
#: (POST/PUT/DELETE, including transit encrypt/sign/rotate) are never retried.
_IDEMPOTENT_READ_METHODS = frozenset({"GET", "LIST", "HEAD"})

#: Gateway-style statuses that mean "try again" (sealed/standby/overloaded).
_RETRYABLE_STATUSES = frozenset({502, 503, 504})

_jitter = random.SystemRandom()
_sleep = time.sleep


def vault_timeout() -> tuple[float, float]:
    """Return the ``(connect, read)`` timeout in seconds for Vault calls.

    Env-configurable (``VAULT_CONNECT_TIMEOUT_SECONDS`` default 3,
    ``VAULT_READ_TIMEOUT_SECONDS`` default 10) through clamping parsers that
    cannot raise, so a bad value never crashes import or a request.
    """
    return (
        env_float("VAULT_CONNECT_TIMEOUT_SECONDS", 3.0, minimum=0.1, maximum=60.0),
        env_float("VAULT_READ_TIMEOUT_SECONDS", 10.0, minimum=0.1, maximum=300.0),
    )


def vault_request(
    session: requests.Session, method: str, url: str, **kwargs: Any
) -> requests.Response:
    """Send one Vault API request with an explicit timeout and safe retries.

    Without a timeout a hung Vault pins the calling worker forever. Every
    call gets ``vault_timeout()`` unless the caller passes its own; only
    idempotent reads are retried (bounded attempts, exponential backoff
    with jitter) on connection errors, timeouts and 502/503/504. The final
    failure is raised/returned unchanged for the caller to translate.
    """
    kwargs.setdefault("timeout", vault_timeout())
    attempts = 1
    if method.upper() in _IDEMPOTENT_READ_METHODS:
        attempts = env_int("VAULT_MAX_READ_ATTEMPTS", 3, minimum=1, maximum=6)

    for attempt in range(1, attempts + 1):
        final = attempt >= attempts
        try:
            response = session.request(method, url, **kwargs)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            if final:
                raise
            logger.warning(
                "Vault %s failed (%s), retrying (attempt %d/%d)",
                method.upper(),
                type(e).__name__,
                attempt,
                attempts,
            )
        else:
            if final or response.status_code not in _RETRYABLE_STATUSES:
                return response
            logger.warning(
                "Vault %s returned HTTP %d, retrying (attempt %d/%d)",
                method.upper(),
                response.status_code,
                attempt,
                attempts,
            )
        window = min(2.0, 0.2 * (2 ** (attempt - 1)))
        _sleep(window / 2 + _jitter.uniform(0, window / 2))
    raise RuntimeError(
        "unreachable: vault_request retry loop exited"
    )  # pragma: no cover


def create_vault_session(config: dict[str, Any]) -> requests.Session:
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
