"""Built-in secrets storage client implementation."""

# flake8: noqa: E501


import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from flask import current_app

from .base import (
    InvalidSecretConfigException,
    SecretAlreadyExistsException,
    SecretMetadata,
    SecretNotFoundException,
    SecretProviderClient,
    SecretProviderException,
    SecretValue,
)

logger = logging.getLogger(__name__)


class BuiltinSecretsClient(SecretProviderClient):
    """Built-in secrets storage implementation using PyDAL."""

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize Built-in secrets client.

        Expected config:
        {
            "organization_id": 1,  # Required: organization ID for secret scoping
            "user_id": 1  # Optional: for audit logging
        }
        """
        super().__init__(config)
        # Get db from Flask app context
        self.db = current_app.db
        self.organization_id = config.get("organization_id")
        self.user_id = config.get("user_id")

    def _validate_config(self) -> None:
        """Validate configuration."""
        if "organization_id" not in self.config:
            raise InvalidSecretConfigException(
                "Missing required config field: organization_id"
            )

    def test_connection(self) -> bool:
        """Test connection to database."""
        try:
            # Try a simple query to test database connectivity
            self.db(self.db.builtin_secrets.id > 0).count()
            logger.info("Built-in secrets database connection test successful")
            return True
        except Exception as e:
            logger.error(f"Built-in secrets database connection test failed: {str(e)}")
            return False

    def _get_secret_row(self, path: str):
        """Helper to get secret row by path (name)."""
        return (
            self.db(
                (self.db.builtin_secrets.name == path)
                & (self.db.builtin_secrets.organization_id == self.organization_id)
                & (self.db.builtin_secrets.is_active is True)
            )
            .select()
            .first()
        )

    def get_secret(self, path: str, version: Optional[str] = None) -> SecretValue:
        """
        Retrieve a secret from built-in storage.

        Note: version parameter is ignored as builtin secrets don't support versioning.
        """
        try:
            row = self._get_secret_row(path)

            if not row:
                raise SecretNotFoundException(f"Secret '{path}' not found")

            # Determine if it's a key-value secret
            is_kv = row.secret_json is not None

            if is_kv:
                # Parse JSON secret
                try:
                    kv_pairs = (
                        json.loads(row.secret_json)
                        if isinstance(row.secret_json, str)
                        else row.secret_json
                    )
                except json.JSONDecodeError:
                    logger.error(f"Failed to parse JSON for secret '{path}'")
                    kv_pairs = {}

                return SecretValue(
                    name=row.name,
                    value=None,
                    is_masked=True,
                    is_kv=True,
                    kv_pairs=kv_pairs,
                    version="1",  # Built-in secrets don't support versioning
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                    metadata={
                        "secret_type": row.secret_type,
                        "description": row.description,
                        "tags": row.tags,
                        "expires_at": (
                            row.expires_at.isoformat() if row.expires_at else None
                        ),
                    },
                )
            else:
                # Simple string secret (password field is encrypted by PyDAL)
                # Note: We can't retrieve the actual password value, it's hashed
                return SecretValue(
                    name=row.name,
                    value="***ENCRYPTED***",  # PyDAL password fields are one-way hashed
                    is_masked=True,
                    is_kv=False,
                    kv_pairs=None,
                    version="1",
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                    metadata={
                        "secret_type": row.secret_type,
                        "description": row.description,
                        "tags": row.tags,
                        "expires_at": (
                            row.expires_at.isoformat() if row.expires_at else None
                        ),
                    },
                )

        except SecretNotFoundException:
            raise
        except Exception as e:
            logger.error(f"Failed to get secret '{path}': {str(e)}")
            raise SecretProviderException(f"Failed to get secret: {str(e)}")

    def list_secrets(self, prefix: Optional[str] = None) -> List[SecretMetadata]:
        """List secrets in built-in storage."""
        try:
            query = (
                self.db.builtin_secrets.organization_id == self.organization_id
            ) & (self.db.builtin_secrets.is_active is True)

            if prefix:
                query &= self.db.builtin_secrets.name.like(f"{prefix}%")

            rows = self.db(query).select(
                self.db.builtin_secrets.name,
                self.db.builtin_secrets.secret_type,
                self.db.builtin_secrets.description,
                self.db.builtin_secrets.secret_json,
                self.db.builtin_secrets.created_at,
                self.db.builtin_secrets.updated_at,
                self.db.builtin_secrets.tags,
                self.db.builtin_secrets.expires_at,
                orderby=self.db.builtin_secrets.name,
            )

            secrets = []
            for row in rows:
                secrets.append(
                    SecretMetadata(
                        name=row.name,
                        path=row.name,
                        is_kv=row.secret_json is not None,
                        version="1",
                        created_at=row.created_at,
                        updated_at=row.updated_at,
                        metadata={
                            "secret_type": row.secret_type,
                            "description": row.description,
                            "tags": row.tags,
                            "expires_at": (
                                row.expires_at.isoformat() if row.expires_at else None
                            ),
                        },
                    )
                )

            logger.info(f"Listed {len(secrets)} secrets with prefix '{prefix or ''}'")
            return secrets

        except Exception as e:
            logger.error(f"Failed to list secrets: {str(e)}")
            raise SecretProviderException(f"Failed to list secrets: {str(e)}")

    def create_secret(
        self, path: str, value: str, metadata: Optional[Dict[str, Any]] = None
    ) -> SecretMetadata:
        """Create a new secret in built-in storage."""
        try:
            # Check if secret already exists
            existing = self._get_secret_row(path)
            if existing:
                raise SecretAlreadyExistsException(f"Secret '{path}' already exists")

            metadata = metadata or {}

            # Determine if value is JSON (key-value) or simple string
            is_json = False
            json_value = None
            string_value = None

            try:
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    is_json = True
                    json_value = value
                else:
                    string_value = value
            except (json.JSONDecodeError, TypeError):
                string_value = value

            # Create secret
            secret_id = self.db.builtin_secrets.insert(
                name=path,
                description=metadata.get("description", ""),
                organization_id=self.organization_id,
                secret_value=string_value,
                secret_json=json_value,
                secret_type=metadata.get("secret_type", "password"),
                tags=metadata.get("tags", []),
                expires_at=metadata.get("expires_at"),
                is_active=True,
            )

            self.db.commit()

            # Retrieve created secret
            row = self.db.builtin_secrets[secret_id]

            logger.info(f"Created secret '{path}' (ID: {secret_id})")

            return SecretMetadata(
                name=row.name,
                path=row.name,
                is_kv=is_json,
                version="1",
                created_at=row.created_at,
                updated_at=row.updated_at,
                metadata={
                    "secret_type": row.secret_type,
                    "description": row.description,
                    "tags": row.tags,
                    "expires_at": (
                        row.expires_at.isoformat() if row.expires_at else None
                    ),
                },
            )

        except SecretAlreadyExistsException:
            raise
        except Exception as e:
            self.db.rollback()
            logger.error(f"Failed to create secret '{path}': {str(e)}")
            raise SecretProviderException(f"Failed to create secret: {str(e)}")

    def update_secret(self, path: str, value: str) -> SecretMetadata:
        """Update an existing secret in built-in storage."""
        try:
            row = self._get_secret_row(path)
            if not row:
                raise SecretNotFoundException(f"Secret '{path}' not found")

            # Determine if value is JSON or string
            is_json = False
            json_value = None
            string_value = None

            try:
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    is_json = True
                    json_value = value
                else:
                    string_value = value
            except (json.JSONDecodeError, TypeError):
                string_value = value

            # Update secret
            self.db(self.db.builtin_secrets.id == row.id).update(
                secret_value=string_value,
                secret_json=json_value,
                updated_at=datetime.now(),
            )

            logger.info(f"Updated secret '{path}' (ID: {row.id})")

            return SecretMetadata(
                name=row.name,
                path=row.name,
                is_kv=is_json,
                version="1",
                created_at=row.created_at,
                updated_at=row.updated_at,
                metadata={
                    "secret_type": row.secret_type,
                    "description": row.description,
                    "tags": row.tags,
                    "expires_at": (
                        row.expires_at.isoformat() if row.expires_at else None
                    ),
                },
            )

        except SecretNotFoundException:
            raise
        except Exception as e:
            self.db.rollback()
            logger.error(f"Failed to update secret '{path}': {str(e)}")
            raise SecretProviderException(f"Failed to update secret: {str(e)}")

    def delete_secret(self, path: str, force: bool = False) -> bool:
        """
        Delete a secret from built-in storage.

        Note: force parameter is ignored. Deletion is always soft (is_active=False).
        """
        try:
            row = self._get_secret_row(path)
            if not row:
                raise SecretNotFoundException(f"Secret '{path}' not found")

            # Soft delete (set is_active to False)
            self.db(self.db.builtin_secrets.id == row.id).update(
                is_active=False, updated_at=datetime.now()
            )

            logger.info(f"Deleted secret '{path}' (ID: {row.id})")
            return True

        except SecretNotFoundException:
            raise
        except Exception as e:
            self.db.rollback()
            logger.error(f"Failed to delete secret '{path}': {str(e)}")
            raise SecretProviderException(f"Failed to delete secret: {str(e)}")

    def get_secret_versions(self, path: str) -> List[str]:
        """
        Get all versions of a secret.

        Note: Built-in secrets don't support versioning, always returns ['1'].
        """
        row = self._get_secret_row(path)
        if not row:
            raise SecretNotFoundException(f"Secret '{path}' not found")

        return ["1"]  # Built-in secrets don't support versioning
