"""Hashicorp Vault secrets client implementation."""

# flake8: noqa: E501


import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

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


class HashicorpVaultClient(SecretProviderClient):
    """Hashicorp Vault implementation of SecretProviderClient."""

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize Hashicorp Vault client.

        Expected config:
        {
            "url": "https://vault.example.com:8200",  # Required
            "token": "s.XXXXXXXXXXXX",  # Required (or use auth method)
            "namespace": "admin/namespace",  # Optional (Vault Enterprise)
            "mount_point": "secret",  # Optional, default: "secret"
            "kv_version": "2",  # Optional, default: "2" (KV v1 or v2)
            "verify_tls": true,  # Optional, default: true
            "ca_cert": "/path/to/ca.pem",  # Optional
            "client_cert": "/path/to/client.pem",  # Optional (for mTLS)
            "client_key": "/path/to/client-key.pem"  # Optional (for mTLS)
        }
        """
        super().__init__(config)
        self._init_client()

    def _validate_config(self) -> None:
        """Validate Vault configuration."""
        required_fields = ["url", "token"]
        missing_fields = [
            field for field in required_fields if field not in self.config
        ]

        if missing_fields:
            raise InvalidSecretConfigException(
                f"Missing required Vault config fields: {', '.join(missing_fields)}"
            )

        # Validate URL format
        if not self.config["url"].startswith(("http://", "https://")):
            raise InvalidSecretConfigException(
                "Vault URL must start with http:// or https://"
            )

    def _init_client(self) -> None:
        """Initialize Vault client session."""
        from apps.api.common.providers.vault import create_vault_session

        self.base_url = self.config["url"].rstrip("/")
        self.mount_point = self.config.get("mount_point", "secret")
        self.kv_version = self.config.get("kv_version", "2")

        self.session = create_vault_session(self.config)
        logger.info(f"Initialized Hashicorp Vault client for {self.base_url}")

    def _make_request(self, method: str, path: str, **kwargs) -> Dict[str, Any]:
        """Make a request to Vault API."""
        url = f"{self.base_url}{path}"

        try:
            response = self.session.request(method, url, **kwargs)

            # Handle specific HTTP status codes
            if response.status_code == 404:
                raise SecretNotFoundException(f"Secret not found at path: {path}")
            elif response.status_code == 403:
                raise SecretAccessDeniedException(f"Access denied to path: {path}")
            elif response.status_code == 409:
                raise SecretAlreadyExistsException(
                    f"Secret already exists at path: {path}"
                )
            elif response.status_code >= 400:
                error_msg = (
                    response.json().get("errors", [response.text])[0]
                    if response.text
                    else "Unknown error"
                )
                raise SecretProviderException(f"Vault API error: {error_msg}")

            return response.json() if response.text else {}

        except requests.exceptions.RequestException as e:
            logger.error(f"Vault request failed: {str(e)}")
            raise SecretProviderException(f"Vault request failed: {str(e)}")

    def test_connection(self) -> bool:
        """Test connection to Vault."""
        try:
            # Check health endpoint
            response = self._make_request("GET", "/v1/sys/health")
            logger.info("Vault connection test successful")
            return True
        except Exception as e:
            logger.error(f"Vault connection test failed: {str(e)}")
            return False

    def _get_kv_path(self, path: str, operation: str = "data") -> str:
        """Get the correct KV path based on version."""
        path = path.lstrip("/")

        if self.kv_version == "2":
            # KV v2 has different paths for operations
            if operation == "data":
                return f"/v1/{self.mount_point}/data/{path}"
            elif operation == "metadata":
                return f"/v1/{self.mount_point}/metadata/{path}"
            elif operation == "delete":
                return f"/v1/{self.mount_point}/data/{path}"
            elif operation == "destroy":
                return f"/v1/{self.mount_point}/destroy/{path}"
        else:
            # KV v1 uses simple paths
            return f"/v1/{self.mount_point}/{path}"

        return f"/v1/{self.mount_point}/{path}"

    def get_secret(self, path: str, version: Optional[str] = None) -> SecretValue:
        """Retrieve a secret from Vault."""
        try:
            api_path = self._get_kv_path(path, "data")
            params = {}

            if self.kv_version == "2" and version:
                params["version"] = version

            response = self._make_request("GET", api_path, params=params)

            # Parse response based on KV version
            if self.kv_version == "2":
                data = response.get("data", {}).get("data", {})
                metadata = response.get("data", {}).get("metadata", {})

                created_at = None
                if metadata.get("created_time"):
                    try:
                        created_at = datetime.fromisoformat(
                            metadata["created_time"].replace("Z", "+00:00")
                        )
                    except Exception:
                        pass

                return SecretValue(
                    name=path,
                    value=None,
                    is_masked=True,
                    is_kv=True,
                    kv_pairs=data,
                    version=str(metadata.get("version", "1")),
                    created_at=created_at,
                    updated_at=created_at,
                    metadata={
                        "deletion_time": metadata.get("deletion_time"),
                        "destroyed": metadata.get("destroyed", False),
                        "custom_metadata": metadata.get("custom_metadata", {}),
                    },
                )
            else:
                # KV v1
                data = response.get("data", {})
                return SecretValue(
                    name=path,
                    value=None,
                    is_masked=True,
                    is_kv=True,
                    kv_pairs=data,
                    version="1",
                    created_at=None,
                    updated_at=None,
                    metadata={},
                )

        except (SecretNotFoundException, SecretAccessDeniedException):
            raise
        except Exception as e:
            logger.error(f"Failed to get secret '{path}': {str(e)}")
            raise SecretProviderException(f"Failed to get secret: {str(e)}")

    def list_secrets(self, prefix: Optional[str] = None) -> List[SecretMetadata]:
        """List secrets in Vault."""
        try:
            path = prefix or ""
            if self.kv_version == "2":
                api_path = f"/v1/{self.mount_point}/metadata/{path}"
            else:
                api_path = f"/v1/{self.mount_point}/{path}"

            response = self._make_request("LIST", api_path)

            keys = response.get("data", {}).get("keys", [])

            secrets = []
            for key in keys:
                # Skip directories (end with /)
                if key.endswith("/"):
                    continue

                full_path = f"{path}/{key}".strip("/")

                try:
                    # Get metadata for each secret
                    if self.kv_version == "2":
                        metadata_path = f"/v1/{self.mount_point}/metadata/{full_path}"
                        meta_response = self._make_request("GET", metadata_path)
                        current_version = meta_response.get("data", {}).get(
                            "current_version"
                        )
                        created_time = meta_response.get("data", {}).get("created_time")

                        created_at = None
                        if created_time:
                            try:
                                created_at = datetime.fromisoformat(
                                    created_time.replace("Z", "+00:00")
                                )
                            except Exception:
                                pass

                        secrets.append(
                            SecretMetadata(
                                name=key,
                                path=full_path,
                                is_kv=True,
                                version=(
                                    str(current_version) if current_version else "1"
                                ),
                                created_at=created_at,
                                updated_at=created_at,
                                metadata=meta_response.get("data", {}),
                            )
                        )
                    else:
                        # KV v1 doesn't have metadata endpoint
                        secrets.append(
                            SecretMetadata(
                                name=key,
                                path=full_path,
                                is_kv=True,
                                version="1",
                                created_at=None,
                                updated_at=None,
                                metadata={},
                            )
                        )
                except Exception as e:
                    logger.warning(
                        f"Failed to get metadata for secret '{full_path}': {str(e)}"
                    )
                    continue

            logger.info(f"Listed {len(secrets)} secrets with prefix '{prefix or ''}'")
            return secrets

        except SecretNotFoundException:
            # Empty path
            return []
        except Exception as e:
            logger.error(f"Failed to list secrets: {str(e)}")
            raise SecretProviderException(f"Failed to list secrets: {str(e)}")

    def create_secret(
        self, path: str, value: str, metadata: Optional[Dict[str, Any]] = None
    ) -> SecretMetadata:
        """Create a new secret in Vault."""
        try:
            # Parse value as JSON if possible
            try:
                data = json.loads(value) if isinstance(value, str) else value
            except json.JSONDecodeError:
                data = {"value": value}

            api_path = self._get_kv_path(path, "data")

            if self.kv_version == "2":
                # KV v2 wraps data in 'data' key
                payload = {"data": data, "options": {}}

                # Add Check-And-Set (CAS) to ensure creation only
                payload["options"][
                    "cas"
                ] = 0  # cas=0 means only create if doesn't exist
            else:
                # KV v1
                payload = data

            response = self._make_request("POST", api_path, json=payload)

            created_at = None
            version = "1"

            if self.kv_version == "2":
                version_data = response.get("data", {})
                version = str(version_data.get("version", "1"))

                if version_data.get("created_time"):
                    try:
                        created_at = datetime.fromisoformat(
                            version_data["created_time"].replace("Z", "+00:00")
                        )
                    except Exception:
                        pass

            logger.info(f"Created secret '{path}' (version: {version})")

            return SecretMetadata(
                name=path.split("/")[-1],
                path=path,
                is_kv=True,
                version=version,
                created_at=created_at,
                updated_at=created_at,
                metadata=metadata or {},
            )

        except SecretAlreadyExistsException:
            raise
        except Exception as e:
            logger.error(f"Failed to create secret '{path}': {str(e)}")
            raise SecretProviderException(f"Failed to create secret: {str(e)}")

    def update_secret(self, path: str, value: str) -> SecretMetadata:
        """Update an existing secret in Vault."""
        try:
            # Parse value as JSON if possible
            try:
                data = json.loads(value) if isinstance(value, str) else value
            except json.JSONDecodeError:
                data = {"value": value}

            api_path = self._get_kv_path(path, "data")

            if self.kv_version == "2":
                payload = {"data": data}
            else:
                payload = data

            response = self._make_request("POST", api_path, json=payload)

            updated_at = None
            version = "1"

            if self.kv_version == "2":
                version_data = response.get("data", {})
                version = str(version_data.get("version", "1"))

                if version_data.get("created_time"):
                    try:
                        updated_at = datetime.fromisoformat(
                            version_data["created_time"].replace("Z", "+00:00")
                        )
                    except Exception:
                        pass

            logger.info(f"Updated secret '{path}' (version: {version})")

            return SecretMetadata(
                name=path.split("/")[-1],
                path=path,
                is_kv=True,
                version=version,
                created_at=None,
                updated_at=updated_at,
                metadata={},
            )

        except Exception as e:
            logger.error(f"Failed to update secret '{path}': {str(e)}")
            raise SecretProviderException(f"Failed to update secret: {str(e)}")

    def delete_secret(self, path: str, force: bool = False) -> bool:
        """
        Delete a secret from Vault.

        For KV v2:
        - force=False: Soft delete (latest version)
        - force=True: Permanent destruction (all versions)

        For KV v1:
        - Always permanent deletion
        """
        try:
            if self.kv_version == "2":
                if force:
                    # Permanent destruction
                    api_path = self._get_kv_path(path, "destroy")
                    # Get all versions first
                    versions = self.get_secret_versions(path)
                    payload = {"versions": [int(v) for v in versions]}
                    self._make_request("POST", api_path, json=payload)
                else:
                    # Soft delete
                    api_path = f"/v1/{self.mount_point}/delete/{path.lstrip('/')}"
                    # Get latest version
                    versions = self.get_secret_versions(path)
                    if versions:
                        payload = {"versions": [int(versions[0])]}
                        self._make_request("POST", api_path, json=payload)
            else:
                # KV v1: Always permanent
                api_path = self._get_kv_path(path, "delete")
                self._make_request("DELETE", api_path)

            logger.info(f"Deleted secret '{path}' (force: {force})")
            return True

        except Exception as e:
            logger.error(f"Failed to delete secret '{path}': {str(e)}")
            raise SecretProviderException(f"Failed to delete secret: {str(e)}")

    def get_secret_versions(self, path: str) -> List[str]:
        """
        Get all versions of a secret.

        Only supported for KV v2. Returns ['1'] for KV v1.
        """
        if self.kv_version == "1":
            return ["1"]

        try:
            api_path = f"/v1/{self.mount_point}/metadata/{path.lstrip('/')}"
            response = self._make_request("GET", api_path)

            versions_data = response.get("data", {}).get("versions", {})
            versions = []

            for version_num, version_info in versions_data.items():
                if not version_info.get("destroyed", False) and not version_info.get(
                    "deletion_time"
                ):
                    versions.append(version_num)

            # Sort versions in descending order (latest first)
            versions.sort(key=int, reverse=True)

            return versions

        except Exception as e:
            logger.error(f"Failed to get versions for secret '{path}': {str(e)}")
            raise SecretProviderException(f"Failed to get secret versions: {str(e)}")
