"""GCP Secret Manager client implementation."""

# flake8: noqa: E501


import json
import logging
from typing import Any, Dict, List, Optional

from google.api_core import exceptions as google_exceptions
from google.cloud import secretmanager

from apps.api.common.providers.gcp import resolve_gcp_credentials

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


class GCPSecretManagerClient(SecretProviderClient):
    """GCP Secret Manager implementation of SecretProviderClient."""

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize GCP Secret Manager client.

        Expected config:
        {
            "project_id": "my-gcp-project",
            "credentials_json": {...},  # Service account credentials JSON (optional if using default)
            "credentials_file": "/path/to/credentials.json"  # Alternative to credentials_json
        }
        """
        super().__init__(config)
        self._init_client()

    def _validate_config(self) -> None:
        """Validate GCP configuration."""
        if "project_id" not in self.config:
            raise InvalidSecretConfigException(
                "Missing required GCP config field: project_id"
            )

    def _init_client(self) -> None:
        """Initialize the GCP Secret Manager client."""
        try:
            self.project_id = self.config["project_id"]
            credentials = resolve_gcp_credentials(self.config)

            if credentials:
                self.client = secretmanager.SecretManagerServiceClient(
                    credentials=credentials
                )
            else:
                self.client = secretmanager.SecretManagerServiceClient()

            logger.info(
                f"Initialized GCP Secret Manager client for project {self.project_id}"
            )

        except Exception as e:
            logger.error(f"Failed to initialize GCP Secret Manager client: {str(e)}")
            raise SecretProviderException(f"Failed to initialize GCP client: {str(e)}")

    def test_connection(self) -> bool:
        """Test connection to GCP Secret Manager."""
        try:
            # Try to list secrets with max results of 1 to test connectivity
            parent = f"projects/{self.project_id}"
            request = secretmanager.ListSecretsRequest(parent=parent, page_size=1)
            list(self.client.list_secrets(request=request))
            logger.info("GCP Secret Manager connection test successful")
            return True
        except google_exceptions.PermissionDenied as e:
            logger.error(
                f"GCP Secret Manager connection test failed: Permission denied - {str(e)}"
            )
            return False
        except Exception as e:
            logger.error(f"GCP Secret Manager connection test failed: {str(e)}")
            return False

    def get_secret(self, path: str, version: Optional[str] = None) -> SecretValue:
        """Retrieve a secret from GCP Secret Manager."""
        try:
            # GCP secret names are in format: projects/{project}/secrets/{secret}/versions/{version}
            secret_name = self._format_secret_name(path)
            version_name = f"{secret_name}/versions/{version or 'latest'}"

            response = self.client.access_secret_version(request={"name": version_name})

            # Parse secret value
            secret_data = response.payload.data.decode("UTF-8")
            is_kv = False
            kv_pairs = None

            # Try to parse as JSON (KV store)
            try:
                parsed = json.loads(secret_data)
                if isinstance(parsed, dict):
                    is_kv = True
                    kv_pairs = parsed
                    secret_data = None  # Don't expose raw JSON
            except json.JSONDecodeError:
                # Not JSON, treat as plain string
                pass

            # Get secret metadata
            secret_info = self.client.get_secret(request={"name": secret_name})

            return SecretValue(
                name=path,
                value=secret_data,
                is_masked=False,
                is_kv=is_kv,
                kv_pairs=kv_pairs,
                version=response.name.split("/")[-1],
                created_at=secret_info.create_time,
                updated_at=response.create_time,
                metadata={
                    "full_name": response.name,
                    "labels": dict(secret_info.labels) if secret_info.labels else {},
                    "replication": str(secret_info.replication),
                },
            )

        except google_exceptions.NotFound:
            raise SecretNotFoundException(
                f"Secret '{path}' not found in GCP Secret Manager"
            )
        except google_exceptions.PermissionDenied as e:
            raise SecretAccessDeniedException(
                f"Access denied to secret '{path}': {str(e)}"
            )
        except Exception as e:
            raise SecretProviderException(
                f"GCP error retrieving secret '{path}': {str(e)}"
            )

    def list_secrets(self, prefix: Optional[str] = None) -> List[SecretMetadata]:
        """List secrets in GCP Secret Manager."""
        try:
            secrets = []
            parent = f"projects/{self.project_id}"

            request = secretmanager.ListSecretsRequest(parent=parent)
            if prefix:
                request.filter = f"name:{prefix}*"

            for secret in self.client.list_secrets(request=request):
                secret_name = secret.name.split("/")[-1]

                # Skip if doesn't match prefix (additional client-side filtering)
                if prefix and not secret_name.startswith(prefix):
                    continue

                # Try to determine if it's a KV store
                is_kv = False
                try:
                    version_name = f"{secret.name}/versions/latest"
                    response = self.client.access_secret_version(
                        request={"name": version_name}
                    )
                    secret_data = response.payload.data.decode("UTF-8")
                    parsed = json.loads(secret_data)
                    is_kv = isinstance(parsed, dict)
                except Exception:
                    # If we can't access or parse, assume not KV
                    pass

                secrets.append(
                    SecretMetadata(
                        name=secret_name,
                        path=secret_name,
                        is_kv=is_kv,
                        created_at=secret.create_time,
                        metadata={
                            "full_name": secret.name,
                            "labels": dict(secret.labels) if secret.labels else {},
                            "replication": str(secret.replication),
                        },
                    )
                )

            logger.info(f"Listed {len(secrets)} secrets from GCP Secret Manager")
            return secrets

        except google_exceptions.PermissionDenied as e:
            raise SecretAccessDeniedException(
                f"Access denied listing secrets: {str(e)}"
            )
        except Exception as e:
            raise SecretProviderException(f"GCP error listing secrets: {str(e)}")

    def create_secret(
        self, path: str, value: str, metadata: Optional[Dict[str, Any]] = None
    ) -> SecretMetadata:
        """Create a new secret in GCP Secret Manager."""
        try:
            parent = f"projects/{self.project_id}"

            # Create the secret
            secret_request = {
                "parent": parent,
                "secret_id": path,
                "secret": {
                    "replication": {"automatic": {}},
                },
            }

            if metadata and "labels" in metadata:
                secret_request["secret"]["labels"] = metadata["labels"]

            secret = self.client.create_secret(request=secret_request)

            # Add the secret value as the first version
            version_request = {
                "parent": secret.name,
                "payload": {"data": value.encode("UTF-8")},
            }
            version = self.client.add_secret_version(request=version_request)

            logger.info(f"Created secret '{path}' in GCP Secret Manager")

            return SecretMetadata(
                name=path,
                path=path,
                is_kv=self._is_json_dict(value),
                version=version.name.split("/")[-1],
                created_at=secret.create_time,
                metadata={"full_name": secret.name},
            )

        except google_exceptions.AlreadyExists:
            raise SecretAlreadyExistsException(
                f"Secret '{path}' already exists in GCP Secret Manager"
            )
        except google_exceptions.PermissionDenied as e:
            raise SecretAccessDeniedException(
                f"Access denied creating secret '{path}': {str(e)}"
            )
        except Exception as e:
            raise SecretProviderException(
                f"GCP error creating secret '{path}': {str(e)}"
            )

    def update_secret(self, path: str, value: str) -> SecretMetadata:
        """Update an existing secret in GCP Secret Manager (creates new version)."""
        try:
            secret_name = self._format_secret_name(path)

            # Add new version
            request = {
                "parent": secret_name,
                "payload": {"data": value.encode("UTF-8")},
            }
            version = self.client.add_secret_version(request=request)

            logger.info(f"Updated secret '{path}' in GCP Secret Manager (new version)")

            return SecretMetadata(
                name=path,
                path=path,
                is_kv=self._is_json_dict(value),
                version=version.name.split("/")[-1],
                updated_at=version.create_time,
                metadata={"full_name": version.name},
            )

        except google_exceptions.NotFound:
            raise SecretNotFoundException(
                f"Secret '{path}' not found in GCP Secret Manager"
            )
        except google_exceptions.PermissionDenied as e:
            raise SecretAccessDeniedException(
                f"Access denied updating secret '{path}': {str(e)}"
            )
        except Exception as e:
            raise SecretProviderException(
                f"GCP error updating secret '{path}': {str(e)}"
            )

    def delete_secret(self, path: str, force: bool = False) -> bool:
        """Delete a secret from GCP Secret Manager."""
        try:
            secret_name = self._format_secret_name(path)

            # GCP Secret Manager deletes are immediate (no recovery window by default)
            request = {"name": secret_name}
            self.client.delete_secret(request=request)

            logger.info(f"Deleted secret '{path}' from GCP Secret Manager")
            return True

        except google_exceptions.NotFound:
            raise SecretNotFoundException(
                f"Secret '{path}' not found in GCP Secret Manager"
            )
        except google_exceptions.PermissionDenied as e:
            raise SecretAccessDeniedException(
                f"Access denied deleting secret '{path}': {str(e)}"
            )
        except Exception as e:
            raise SecretProviderException(
                f"GCP error deleting secret '{path}': {str(e)}"
            )

    def get_secret_versions(self, path: str) -> List[str]:
        """Get all versions of a secret."""
        try:
            secret_name = self._format_secret_name(path)

            versions = []
            request = secretmanager.ListSecretVersionsRequest(parent=secret_name)

            for version in self.client.list_secret_versions(request=request):
                version_id = version.name.split("/")[-1]
                versions.append(version_id)

            logger.info(f"Found {len(versions)} versions for secret '{path}'")
            return versions

        except google_exceptions.NotFound:
            raise SecretNotFoundException(
                f"Secret '{path}' not found in GCP Secret Manager"
            )
        except google_exceptions.PermissionDenied as e:
            raise SecretAccessDeniedException(
                f"Access denied accessing secret '{path}': {str(e)}"
            )
        except Exception as e:
            raise SecretProviderException(
                f"GCP error listing versions for '{path}': {str(e)}"
            )

    def _format_secret_name(self, secret_id: str) -> str:
        """Format secret ID into full GCP resource name."""
        if secret_id.startswith("projects/"):
            return secret_id
        return f"projects/{self.project_id}/secrets/{secret_id}"

    @staticmethod
    def _is_json_dict(value: str) -> bool:
        """Check if a string value is a JSON dictionary."""
        try:
            parsed = json.loads(value)
            return isinstance(parsed, dict)
        except (json.JSONDecodeError, TypeError):
            return False
