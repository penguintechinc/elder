"""Infisical client implementation."""

# flake8: noqa: E501


import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
from requests.exceptions import HTTPError, RequestException

from .base import (
    InvalidSecretConfigException,
    SecretAccessDeniedException,
    SecretAlreadyExistsException,
    SecretMetadata,
    SecretNotFoundException,
    SecretProviderClient,
    SecretProviderException,
    SecretValue,
)

logger = logging.getLogger(__name__)


class InfisicalClient(SecretProviderClient):
    """Infisical implementation of SecretProviderClient."""

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize Infisical client.

        Expected config:
        {
            "host": "https://app.infisical.com",  # or self-hosted URL
            "service_token": "st.xxx.yyy.zzz",  # Service token for authentication
            "workspace_id": "workspace-id",  # Optional workspace ID
            "environment": "prod",  # Environment (dev, staging, prod, etc.)
            "secret_path": "/"  # Optional secret path (default: root)
        }
        """
        super().__init__(config)
        self._init_client()

    def _validate_config(self) -> None:
        """Validate Infisical configuration."""
        required_fields = ["host", "service_token", "environment"]
        missing_fields = [
            field for field in required_fields if field not in self.config
        ]

        if missing_fields:
            raise InvalidSecretConfigException(
                f"Missing required Infisical config fields: {', '.join(missing_fields)}"
            )

    def _init_client(self) -> None:
        """Initialize the Infisical client."""
        from apps.api.common.providers.infisical import create_infisical_session

        try:
            self.environment = self.config["environment"]
            self.secret_path = self.config.get("secret_path", "/")
            self.workspace_id = self.config.get("workspace_id")

            session, api_base = create_infisical_session(self.config)
            self.session = session
            self.api_base = api_base
            self.headers = dict(session.headers)

            host = self.config["host"].rstrip("/")
            logger.info(
                f"Initialized Infisical client for {host} (environment: {self.environment})"
            )

        except Exception as e:
            logger.error(f"Failed to initialize Infisical client: {str(e)}")
            raise SecretProviderException(
                f"Failed to initialize Infisical client: {str(e)}"
            )

    def test_connection(self) -> bool:
        """Test connection to Infisical."""
        try:
            # Try to get workspace info or list secrets to test connectivity
            url = f"{self.api_base}/secrets/raw"
            params = {
                "environment": self.environment,
                "secretPath": self.secret_path,
            }
            if self.workspace_id:
                params["workspaceId"] = self.workspace_id

            response = requests.get(
                url, headers=self.headers, params=params, timeout=10
            )
            response.raise_for_status()

            logger.info("Infisical connection test successful")
            return True

        except HTTPError as e:
            if e.response.status_code == 401:
                logger.error("Infisical connection test failed: Invalid service token")
            else:
                logger.error(
                    f"Infisical connection test failed: {e.response.status_code} - {e.response.text}"
                )
            return False
        except Exception as e:
            logger.error(f"Infisical connection test failed: {str(e)}")
            return False

    def get_secret(self, path: str, version: Optional[str] = None) -> SecretValue:
        """Retrieve a secret from Infisical."""
        try:
            # Infisical uses secret names within a path
            secret_key = path.split("/")[-1]
            secret_path = "/".join(path.split("/")[:-1]) or self.secret_path

            url = f"{self.api_base}/secrets/raw/{secret_key}"
            params = {
                "environment": self.environment,
                "secretPath": secret_path,
            }
            if self.workspace_id:
                params["workspaceId"] = self.workspace_id
            if version:
                params["version"] = version

            response = requests.get(
                url, headers=self.headers, params=params, timeout=10
            )
            response.raise_for_status()

            data = response.json()
            secret_data = data.get("secret", {})

            # Infisical stores secrets as key-value pairs
            secret_value = secret_data.get("secretValue", "")
            is_kv = False
            kv_pairs = None

            # Try to parse as JSON for KV support
            try:
                parsed = json.loads(secret_value)
                if isinstance(parsed, dict):
                    is_kv = True
                    kv_pairs = parsed
                    secret_value = None
            except (json.JSONDecodeError, TypeError):
                pass

            return SecretValue(
                name=secret_data.get("secretKey", path),
                value=secret_value,
                is_masked=False,
                is_kv=is_kv,
                kv_pairs=kv_pairs,
                version=str(secret_data.get("version", "1")),
                created_at=self._parse_timestamp(secret_data.get("createdAt")),
                updated_at=self._parse_timestamp(secret_data.get("updatedAt")),
                metadata={
                    "workspace_id": secret_data.get("workspace"),
                    "environment": secret_data.get("environment"),
                    "secret_path": secret_path,
                    "type": secret_data.get("type", "shared"),
                    "comment": secret_data.get("secretComment", ""),
                },
            )

        except HTTPError as e:
            if e.response.status_code == 404:
                raise SecretNotFoundException(f"Secret '{path}' not found in Infisical")
            elif e.response.status_code == 401:
                raise SecretAccessDeniedException(
                    f"Access denied to secret '{path}': Invalid authentication"
                )
            elif e.response.status_code == 403:
                raise SecretAccessDeniedException(
                    f"Access denied to secret '{path}': Insufficient permissions"
                )
            else:
                raise SecretProviderException(
                    f"Infisical HTTP error {e.response.status_code}: {e.response.text}"
                )
        except RequestException as e:
            raise SecretProviderException(
                f"Infisical request error retrieving secret '{path}': {str(e)}"
            )
        except Exception as e:
            raise SecretProviderException(
                f"Unexpected error retrieving secret '{path}': {str(e)}"
            )

    def list_secrets(self, prefix: Optional[str] = None) -> List[SecretMetadata]:
        """List secrets in Infisical."""
        try:
            url = f"{self.api_base}/secrets/raw"
            params = {
                "environment": self.environment,
                "secretPath": self.secret_path,
            }
            if self.workspace_id:
                params["workspaceId"] = self.workspace_id

            response = requests.get(
                url, headers=self.headers, params=params, timeout=10
            )
            response.raise_for_status()

            data = response.json()
            secrets_list = data.get("secrets", [])

            secrets = []
            for secret in secrets_list:
                secret_key = secret.get("secretKey", "")

                # Filter by prefix if provided
                if prefix and not secret_key.startswith(prefix):
                    continue

                # Check if it's a KV store
                is_kv = False
                secret_value = secret.get("secretValue", "")
                try:
                    parsed = json.loads(secret_value)
                    is_kv = isinstance(parsed, dict)
                except (json.JSONDecodeError, TypeError):
                    pass

                full_path = f"{self.secret_path.rstrip('/')}/{secret_key}"

                secrets.append(
                    SecretMetadata(
                        name=secret_key,
                        path=full_path,
                        is_kv=is_kv,
                        version=str(secret.get("version", "1")),
                        created_at=self._parse_timestamp(secret.get("createdAt")),
                        updated_at=self._parse_timestamp(secret.get("updatedAt")),
                        metadata={
                            "workspace_id": secret.get("workspace"),
                            "environment": secret.get("environment"),
                            "type": secret.get("type", "shared"),
                            "comment": secret.get("secretComment", ""),
                        },
                    )
                )

            logger.info(f"Listed {len(secrets)} secrets from Infisical")
            return secrets

        except HTTPError as e:
            if e.response.status_code == 401:
                raise SecretAccessDeniedException(
                    "Access denied: Invalid authentication"
                )
            elif e.response.status_code == 403:
                raise SecretAccessDeniedException(
                    "Access denied: Insufficient permissions"
                )
            else:
                raise SecretProviderException(
                    f"Infisical HTTP error {e.response.status_code}: {e.response.text}"
                )
        except RequestException as e:
            raise SecretProviderException(
                f"Infisical request error listing secrets: {str(e)}"
            )
        except Exception as e:
            raise SecretProviderException(f"Unexpected error listing secrets: {str(e)}")

    def create_secret(
        self, path: str, value: str, metadata: Optional[Dict[str, Any]] = None
    ) -> SecretMetadata:
        """Create a new secret in Infisical."""
        try:
            secret_key = path.split("/")[-1]
            secret_path = "/".join(path.split("/")[:-1]) or self.secret_path

            url = f"{self.api_base}/secrets/raw/{secret_key}"

            payload = {
                "environment": self.environment,
                "secretPath": secret_path,
                "secretValue": value,
                "type": "shared",
            }

            if self.workspace_id:
                payload["workspaceId"] = self.workspace_id

            if metadata:
                if "comment" in metadata:
                    payload["secretComment"] = metadata["comment"]
                if "type" in metadata:
                    payload["type"] = metadata["type"]

            response = requests.post(
                url, headers=self.headers, json=payload, timeout=10
            )
            response.raise_for_status()

            data = response.json()
            secret_data = data.get("secret", {})

            logger.info(f"Created secret '{path}' in Infisical")

            return SecretMetadata(
                name=secret_key,
                path=path,
                is_kv=self._is_json_dict(value),
                version=str(secret_data.get("version", "1")),
                created_at=self._parse_timestamp(secret_data.get("createdAt")),
                metadata={
                    "workspace_id": secret_data.get("workspace"),
                    "environment": secret_data.get("environment"),
                },
            )

        except HTTPError as e:
            if e.response.status_code == 409:
                raise SecretAlreadyExistsException(
                    f"Secret '{path}' already exists in Infisical"
                )
            elif e.response.status_code == 401:
                raise SecretAccessDeniedException(
                    f"Access denied creating secret '{path}': Invalid authentication"
                )
            elif e.response.status_code == 403:
                raise SecretAccessDeniedException(
                    f"Access denied creating secret '{path}': Insufficient permissions"
                )
            else:
                raise SecretProviderException(
                    f"Infisical HTTP error {e.response.status_code}: {e.response.text}"
                )
        except RequestException as e:
            raise SecretProviderException(
                f"Infisical request error creating secret '{path}': {str(e)}"
            )
        except Exception as e:
            raise SecretProviderException(
                f"Unexpected error creating secret '{path}': {str(e)}"
            )

    def update_secret(self, path: str, value: str) -> SecretMetadata:
        """Update an existing secret in Infisical."""
        try:
            secret_key = path.split("/")[-1]
            secret_path = "/".join(path.split("/")[:-1]) or self.secret_path

            url = f"{self.api_base}/secrets/raw/{secret_key}"

            payload = {
                "environment": self.environment,
                "secretPath": secret_path,
                "secretValue": value,
            }

            if self.workspace_id:
                payload["workspaceId"] = self.workspace_id

            response = requests.patch(
                url, headers=self.headers, json=payload, timeout=10
            )
            response.raise_for_status()

            data = response.json()
            secret_data = data.get("secret", {})

            logger.info(f"Updated secret '{path}' in Infisical")

            return SecretMetadata(
                name=secret_key,
                path=path,
                is_kv=self._is_json_dict(value),
                version=str(secret_data.get("version", "1")),
                updated_at=self._parse_timestamp(secret_data.get("updatedAt")),
                metadata={
                    "workspace_id": secret_data.get("workspace"),
                    "environment": secret_data.get("environment"),
                },
            )

        except HTTPError as e:
            if e.response.status_code == 404:
                raise SecretNotFoundException(f"Secret '{path}' not found in Infisical")
            elif e.response.status_code == 401:
                raise SecretAccessDeniedException(
                    f"Access denied updating secret '{path}': Invalid authentication"
                )
            elif e.response.status_code == 403:
                raise SecretAccessDeniedException(
                    f"Access denied updating secret '{path}': Insufficient permissions"
                )
            else:
                raise SecretProviderException(
                    f"Infisical HTTP error {e.response.status_code}: {e.response.text}"
                )
        except RequestException as e:
            raise SecretProviderException(
                f"Infisical request error updating secret '{path}': {str(e)}"
            )
        except Exception as e:
            raise SecretProviderException(
                f"Unexpected error updating secret '{path}': {str(e)}"
            )

    def delete_secret(self, path: str, force: bool = False) -> bool:
        """Delete a secret from Infisical."""
        try:
            secret_key = path.split("/")[-1]
            secret_path = "/".join(path.split("/")[:-1]) or self.secret_path

            url = f"{self.api_base}/secrets/raw/{secret_key}"

            params = {
                "environment": self.environment,
                "secretPath": secret_path,
            }

            if self.workspace_id:
                params["workspaceId"] = self.workspace_id

            response = requests.delete(
                url, headers=self.headers, params=params, timeout=10
            )
            response.raise_for_status()

            logger.info(f"Deleted secret '{path}' from Infisical")
            return True

        except HTTPError as e:
            if e.response.status_code == 404:
                raise SecretNotFoundException(f"Secret '{path}' not found in Infisical")
            elif e.response.status_code == 401:
                raise SecretAccessDeniedException(
                    f"Access denied deleting secret '{path}': Invalid authentication"
                )
            elif e.response.status_code == 403:
                raise SecretAccessDeniedException(
                    f"Access denied deleting secret '{path}': Insufficient permissions"
                )
            else:
                raise SecretProviderException(
                    f"Infisical HTTP error {e.response.status_code}: {e.response.text}"
                )
        except RequestException as e:
            raise SecretProviderException(
                f"Infisical request error deleting secret '{path}': {str(e)}"
            )
        except Exception as e:
            raise SecretProviderException(
                f"Unexpected error deleting secret '{path}': {str(e)}"
            )

    def get_secret_versions(self, path: str) -> List[str]:
        """
        Get all versions of a secret.

        Note: Infisical API v3 doesn't directly support version history listing.
        This returns the current version only.
        """
        try:
            # Get current secret to retrieve version
            secret = self.get_secret(path)
            return [secret.version] if secret.version else ["1"]

        except Exception as e:
            raise SecretProviderException(
                f"Error getting versions for secret '{path}': {str(e)}"
            )

    @staticmethod
    def _parse_timestamp(timestamp_str: Optional[str]) -> Optional[datetime]:
        """Parse ISO 8601 timestamp string to datetime."""
        if not timestamp_str:
            return None
        try:
            return datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None

    @staticmethod
    def _is_json_dict(value: str) -> bool:
        """Check if a string value is a JSON dictionary."""
        try:
            parsed = json.loads(value)
            return isinstance(parsed, dict)
        except (json.JSONDecodeError, TypeError):
            return False
