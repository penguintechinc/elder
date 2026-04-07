"""AWS Secrets Manager client implementation."""

# flake8: noqa: E501


import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError

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


class AWSSecretsManagerClient(SecretProviderClient):
    """AWS Secrets Manager implementation of SecretProviderClient."""

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize AWS Secrets Manager client.

        Expected config:
        {
            "region": "us-east-1",
            "access_key_id": "AKIAIOSFODNN7EXAMPLE",  # Optional if using IAM role
            "secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",  # Optional
            "endpoint_url": "https://secretsmanager.us-east-1.amazonaws.com"  # Optional
        }
        """
        super().__init__(config)
        self._init_client()

    def _validate_config(self) -> None:
        """Validate AWS configuration."""
        required_fields = ["region"]
        missing_fields = [
            field for field in required_fields if field not in self.config
        ]

        if missing_fields:
            raise InvalidSecretConfigException(
                f"Missing required AWS config fields: {', '.join(missing_fields)}"
            )

    def _init_client(self) -> None:
        """Initialize the boto3 Secrets Manager client."""
        try:
            session_params = {"region_name": self.config["region"]}

            # Add credentials if provided (otherwise use IAM role/instance profile)
            if "access_key_id" in self.config and "secret_access_key" in self.config:
                session_params["aws_access_key_id"] = self.config["access_key_id"]
                session_params["aws_secret_access_key"] = self.config[
                    "secret_access_key"
                ]

            session = boto3.session.Session(**session_params)

            client_params = {}
            if "endpoint_url" in self.config:
                client_params["endpoint_url"] = self.config["endpoint_url"]

            self.client = session.client("secretsmanager", **client_params)

            logger.info(
                f"Initialized AWS Secrets Manager client for region {self.config['region']}"
            )

        except Exception as e:
            logger.error(f"Failed to initialize AWS Secrets Manager client: {str(e)}")
            raise SecretProviderException(f"Failed to initialize AWS client: {str(e)}")

    def test_connection(self) -> bool:
        """Test connection to AWS Secrets Manager."""
        try:
            # Try to list secrets with max results of 1 to test connectivity
            self.client.list_secrets(MaxResults=1)
            logger.info("AWS Secrets Manager connection test successful")
            return True
        except ClientError as e:
            logger.error(
                f"AWS Secrets Manager connection test failed: {e.response['Error']['Message']}"
            )
            return False
        except Exception as e:
            logger.error(f"AWS Secrets Manager connection test failed: {str(e)}")
            return False

    def get_secret(self, path: str, version: Optional[str] = None) -> SecretValue:
        """Retrieve a secret from AWS Secrets Manager."""
        try:
            params = {"SecretId": path}
            if version:
                params["VersionId"] = version

            response = self.client.get_secret_value(**params)

            # Parse secret value
            secret_string = response.get("SecretString")
            is_kv = False
            kv_pairs = None

            if secret_string:
                # Try to parse as JSON (KV store)
                try:
                    parsed = json.loads(secret_string)
                    if isinstance(parsed, dict):
                        is_kv = True
                        kv_pairs = parsed
                        secret_string = None  # Don't expose raw JSON
                except json.JSONDecodeError:
                    # Not JSON, treat as plain string
                    pass

            return SecretValue(
                name=response["Name"],
                value=secret_string,
                is_masked=False,
                is_kv=is_kv,
                kv_pairs=kv_pairs,
                version=response.get("VersionId"),
                created_at=response.get("CreatedDate"),
                updated_at=response.get(
                    "CreatedDate"
                ),  # AWS uses CreatedDate for last update
                metadata={
                    "arn": response["ARN"],
                    "version_stages": response.get("VersionStages", []),
                },
            )

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            error_msg = e.response["Error"]["Message"]

            if error_code == "ResourceNotFoundException":
                raise SecretNotFoundException(f"Secret '{path}' not found: {error_msg}")
            elif error_code == "AccessDeniedException":
                raise SecretAccessDeniedException(
                    f"Access denied to secret '{path}': {error_msg}"
                )
            else:
                raise SecretProviderException(
                    f"AWS error retrieving secret '{path}': {error_msg}"
                )

        except Exception as e:
            raise SecretProviderException(
                f"Unexpected error retrieving secret '{path}': {str(e)}"
            )

    def list_secrets(self, prefix: Optional[str] = None) -> List[SecretMetadata]:
        """List secrets in AWS Secrets Manager."""
        try:
            secrets = []
            paginator = self.client.get_paginator("list_secrets")

            params = {}
            if prefix:
                params["Filters"] = [{"Key": "name", "Values": [prefix]}]

            for page in paginator.paginate(**params):
                for secret in page["SecretList"]:
                    # Determine if it's a KV store by checking if it has a JSON structure
                    is_kv = False
                    if "SecretString" in secret:
                        try:
                            parsed = json.loads(secret["SecretString"])
                            is_kv = isinstance(parsed, dict)
                        except (json.JSONDecodeError, KeyError):
                            pass

                    secrets.append(
                        SecretMetadata(
                            name=secret["Name"],
                            path=secret["Name"],
                            is_kv=is_kv,
                            version=secret.get("LastAccessedDate"),
                            created_at=secret.get("CreatedDate"),
                            updated_at=secret.get("LastChangedDate"),
                            metadata={
                                "arn": secret["ARN"],
                                "description": secret.get("Description", ""),
                                "tags": secret.get("Tags", []),
                            },
                        )
                    )

            logger.info(f"Listed {len(secrets)} secrets from AWS Secrets Manager")
            return secrets

        except ClientError as e:
            error_msg = e.response["Error"]["Message"]
            raise SecretProviderException(f"AWS error listing secrets: {error_msg}")
        except Exception as e:
            raise SecretProviderException(f"Unexpected error listing secrets: {str(e)}")

    def create_secret(
        self, path: str, value: str, metadata: Optional[Dict[str, Any]] = None
    ) -> SecretMetadata:
        """Create a new secret in AWS Secrets Manager."""
        try:
            params = {
                "Name": path,
                "SecretString": value,
            }

            if metadata:
                if "description" in metadata:
                    params["Description"] = metadata["description"]
                if "tags" in metadata:
                    params["Tags"] = [
                        {"Key": k, "Value": v} for k, v in metadata["tags"].items()
                    ]
                if "kms_key_id" in metadata:
                    params["KmsKeyId"] = metadata["kms_key_id"]

            response = self.client.create_secret(**params)

            logger.info(f"Created secret '{path}' in AWS Secrets Manager")

            return SecretMetadata(
                name=path,
                path=path,
                is_kv=self._is_json_dict(value),
                version=response["VersionId"],
                created_at=datetime.now(timezone.utc),
                metadata={"arn": response["ARN"]},
            )

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            error_msg = e.response["Error"]["Message"]

            if error_code == "ResourceExistsException":
                raise SecretAlreadyExistsException(
                    f"Secret '{path}' already exists: {error_msg}"
                )
            elif error_code == "AccessDeniedException":
                raise SecretAccessDeniedException(
                    f"Access denied creating secret '{path}': {error_msg}"
                )
            else:
                raise SecretProviderException(
                    f"AWS error creating secret '{path}': {error_msg}"
                )

        except Exception as e:
            raise SecretProviderException(
                f"Unexpected error creating secret '{path}': {str(e)}"
            )

    def update_secret(self, path: str, value: str) -> SecretMetadata:
        """Update an existing secret in AWS Secrets Manager."""
        try:
            response = self.client.put_secret_value(SecretId=path, SecretString=value)

            logger.info(f"Updated secret '{path}' in AWS Secrets Manager")

            return SecretMetadata(
                name=path,
                path=path,
                is_kv=self._is_json_dict(value),
                version=response["VersionId"],
                updated_at=datetime.now(timezone.utc),
                metadata={
                    "arn": response["ARN"],
                    "version_stages": response.get("VersionStages", []),
                },
            )

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            error_msg = e.response["Error"]["Message"]

            if error_code == "ResourceNotFoundException":
                raise SecretNotFoundException(f"Secret '{path}' not found: {error_msg}")
            elif error_code == "AccessDeniedException":
                raise SecretAccessDeniedException(
                    f"Access denied updating secret '{path}': {error_msg}"
                )
            else:
                raise SecretProviderException(
                    f"AWS error updating secret '{path}': {error_msg}"
                )

        except Exception as e:
            raise SecretProviderException(
                f"Unexpected error updating secret '{path}': {str(e)}"
            )

    def delete_secret(self, path: str, force: bool = False) -> bool:
        """Delete a secret from AWS Secrets Manager."""
        try:
            params = {"SecretId": path}

            if force:
                # Force immediate deletion (cannot be recovered)
                params["ForceDeleteWithoutRecovery"] = True
            else:
                # Schedule deletion with recovery window (default 30 days)
                params["RecoveryWindowInDays"] = 30

            self.client.delete_secret(**params)

            action = "immediately deleted" if force else "scheduled for deletion"
            logger.info(f"Secret '{path}' {action} in AWS Secrets Manager")

            return True

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            error_msg = e.response["Error"]["Message"]

            if error_code == "ResourceNotFoundException":
                raise SecretNotFoundException(f"Secret '{path}' not found: {error_msg}")
            elif error_code == "AccessDeniedException":
                raise SecretAccessDeniedException(
                    f"Access denied deleting secret '{path}': {error_msg}"
                )
            else:
                raise SecretProviderException(
                    f"AWS error deleting secret '{path}': {error_msg}"
                )

        except Exception as e:
            raise SecretProviderException(
                f"Unexpected error deleting secret '{path}': {str(e)}"
            )

    def get_secret_versions(self, path: str) -> List[str]:
        """Get all versions of a secret."""
        try:
            response = self.client.list_secret_version_ids(SecretId=path)

            versions = []
            for version_data in response.get("Versions", []):
                versions.append(version_data["VersionId"])

            logger.info(f"Found {len(versions)} versions for secret '{path}'")
            return versions

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            error_msg = e.response["Error"]["Message"]

            if error_code == "ResourceNotFoundException":
                raise SecretNotFoundException(f"Secret '{path}' not found: {error_msg}")
            elif error_code == "AccessDeniedException":
                raise SecretAccessDeniedException(
                    f"Access denied accessing secret '{path}': {error_msg}"
                )
            else:
                raise SecretProviderException(
                    f"AWS error listing versions for '{path}': {error_msg}"
                )

        except Exception as e:
            raise SecretProviderException(
                f"Unexpected error listing versions for '{path}': {str(e)}"
            )

    @staticmethod
    def _is_json_dict(value: str) -> bool:
        """Check if a string value is a JSON dictionary."""
        try:
            parsed = json.loads(value)
            return isinstance(parsed, dict)
        except (json.JSONDecodeError, TypeError):
            return False
