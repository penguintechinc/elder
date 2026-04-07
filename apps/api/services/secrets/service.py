"""Secrets Management service layer."""

# flake8: noqa: E501


import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from flask import current_app

from .aws_client import AWSSecretsManagerClient
from .base import SecretNotFoundException, SecretProviderClient, SecretProviderException
from .gcp_client import GCPSecretManagerClient
from .infisical_client import InfisicalClient

logger = logging.getLogger(__name__)


class SecretsService:
    """Service layer for secrets management with provider abstraction."""

    # Provider type to client class mapping
    PROVIDER_CLIENTS = {
        "aws_secrets_manager": AWSSecretsManagerClient,
        "gcp_secret_manager": GCPSecretManagerClient,
        "infisical": InfisicalClient,
    }

    def __init__(self, db_instance=None):
        """
        Initialize secrets service.

        Args:
            db_instance: Database instance (optional, uses current_app.db if not provided)
        """
        self.db = db_instance or current_app.db

    def get_provider_client(self, provider_id: int) -> SecretProviderClient:
        """
        Get initialized client for a secret provider.

        Args:
            provider_id: Provider database ID

        Returns:
            Initialized SecretProviderClient instance

        Raises:
            ValueError: If provider not found or invalid type
            SecretProviderException: If client initialization fails
        """
        provider = self.db.secret_providers[provider_id]
        if not provider:
            raise ValueError(f"Secret provider {provider_id} not found")

        if not provider.enabled:
            raise ValueError(f"Secret provider {provider_id} is disabled")

        provider_type = provider.provider
        if provider_type not in self.PROVIDER_CLIENTS:
            raise ValueError(f"Unknown provider type: {provider_type}")

        client_class = self.PROVIDER_CLIENTS[provider_type]

        try:
            client = client_class(provider.config_json)
            logger.info(
                f"Initialized {provider_type} client for provider {provider_id}"
            )
            return client
        except Exception as e:
            logger.error(
                f"Failed to initialize client for provider {provider_id}: {str(e)}"
            )
            raise SecretProviderException(
                f"Failed to initialize provider client: {str(e)}"
            )

    def get_secret(
        self, secret_id: int, unmask: bool = False, identity_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Retrieve a secret, masked or unmasked.

        Args:
            secret_id: Secret database ID
            unmask: Whether to retrieve unmasked value
            identity_id: Identity ID for access logging

        Returns:
            Dictionary with secret data

        Raises:
            ValueError: If secret not found
            SecretProviderException: If provider error occurs
        """
        secret = self.db.secrets[secret_id]
        if not secret:
            raise ValueError(f"Secret {secret_id} not found")

        try:
            client = self.get_provider_client(secret.provider_id)
            secret_value = client.get_secret(secret.provider_path)

            # Log access
            if identity_id:
                self._log_secret_access(
                    secret_id=secret_id,
                    identity_id=identity_id,
                    action="view_unmasked" if unmask else "view_masked",
                    masked=not unmask,
                )

            # Mask if requested
            if not unmask:
                secret_value = secret_value.mask()

            return {
                "id": secret_id,
                "name": secret.name,
                "provider_path": secret.provider_path,
                "secret_type": secret.secret_type,
                "is_kv": secret.is_kv,
                "organization_id": secret.organization_id,
                "value": secret_value.value if not secret_value.is_kv else None,
                "kv_pairs": secret_value.kv_pairs if secret_value.is_kv else None,
                "is_masked": secret_value.is_masked,
                "version": secret_value.version,
                "provider_metadata": secret_value.metadata,
                "last_synced_at": secret.last_synced_at,
                "created_at": secret.created_at,
                "updated_at": secret.updated_at,
            }

        except Exception as e:
            logger.error(f"Error retrieving secret {secret_id}: {str(e)}")
            raise

    def list_secrets(
        self,
        organization_id: Optional[int] = None,
        provider_id: Optional[int] = None,
        secret_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        List secrets with optional filters.

        Args:
            organization_id: Filter by organization
            provider_id: Filter by provider
            secret_type: Filter by secret type

        Returns:
            List of secret dictionaries (masked)
        """
        query = self.db.secrets.id > 0

        if organization_id:
            query &= self.db.secrets.organization_id == organization_id
        if provider_id:
            query &= self.db.secrets.provider_id == provider_id
        if secret_type:
            query &= self.db.secrets.secret_type == secret_type

        secrets = self.db(query).select()

        return [
            {
                "id": s.id,
                "name": s.name,
                "provider_path": s.provider_path,
                "secret_type": s.secret_type,
                "is_kv": s.is_kv,
                "organization_id": s.organization_id,
                "provider_id": s.provider_id,
                "is_masked": True,
                "last_synced_at": s.last_synced_at,
                "created_at": s.created_at,
                "updated_at": s.updated_at,
            }
            for s in secrets
        ]

    def create_secret(
        self,
        name: str,
        provider_id: int,
        provider_path: str,
        secret_type: str,
        organization_id: int,
        is_kv: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
        identity_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Register a new secret.

        Args:
            name: Secret name
            provider_id: Provider ID
            provider_path: Path in provider
            secret_type: Secret type
            organization_id: Organization ID
            is_kv: Whether secret is key-value store
            metadata: Additional metadata
            identity_id: Identity ID for logging

        Returns:
            Created secret dictionary

        Raises:
            ValueError: If validation fails
            SecretProviderException: If provider error occurs
        """
        # Validate provider exists
        provider = self.db.secret_providers[provider_id]
        if not provider:
            raise ValueError(f"Provider {provider_id} not found")

        # Test that we can access the secret
        try:
            client = self.get_provider_client(provider_id)
            client.get_secret(provider_path)
        except SecretNotFoundException:
            raise ValueError(f"Secret at path '{provider_path}' not found in provider")

        # Create secret record
        secret_id = self.db.secrets.insert(
            name=name,
            provider_id=provider_id,
            provider_path=provider_path,
            secret_type=secret_type,
            is_kv=is_kv,
            organization_id=organization_id,
            parent_id=None,
            metadata=metadata or {},
            last_synced_at=datetime.utcnow(),
        )
        self.db.commit()

        # Log creation
        if identity_id:
            self._log_secret_access(
                secret_id=secret_id,
                identity_id=identity_id,
                action="create",
                masked=True,
            )

        logger.info(f"Created secret {secret_id} (name: {name})")

        secret = self.db.secrets[secret_id]
        return {
            "id": secret_id,
            "name": secret.name,
            "provider_path": secret.provider_path,
            "secret_type": secret.secret_type,
            "is_kv": secret.is_kv,
            "organization_id": secret.organization_id,
            "created_at": secret.created_at,
        }

    def update_secret_metadata(
        self,
        secret_id: int,
        name: Optional[str] = None,
        secret_type: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        identity_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Update secret metadata (not the actual value in provider).

        Args:
            secret_id: Secret ID
            name: New name (optional)
            secret_type: New type (optional)
            metadata: New metadata (optional)
            identity_id: Identity ID for logging

        Returns:
            Updated secret dictionary

        Raises:
            ValueError: If secret not found
        """
        secret = self.db.secrets[secret_id]
        if not secret:
            raise ValueError(f"Secret {secret_id} not found")

        update_data = {}
        if name:
            update_data["name"] = name
        if secret_type:
            update_data["secret_type"] = secret_type
        if metadata is not None:
            update_data["metadata"] = metadata

        if update_data:
            self.db(self.db.secrets.id == secret_id).update(**update_data)

            # Log update
            if identity_id:
                self._log_secret_access(
                    secret_id=secret_id,
                    identity_id=identity_id,
                    action="update",
                    masked=True,
                )

            logger.info(f"Updated metadata for secret {secret_id}")

        return self.get_secret(secret_id, unmask=False)

    def delete_secret(self, secret_id: int, identity_id: Optional[int] = None) -> bool:
        """
        Delete a secret registration (does not delete from provider).

        Args:
            secret_id: Secret ID
            identity_id: Identity ID for logging

        Returns:
            True if deleted

        Raises:
            ValueError: If secret not found
        """
        secret = self.db.secrets[secret_id]
        if not secret:
            raise ValueError(f"Secret {secret_id} not found")

        # Log deletion before removing
        if identity_id:
            self._log_secret_access(
                secret_id=secret_id,
                identity_id=identity_id,
                action="delete",
                masked=True,
            )

        del self.db.secrets[secret_id]
        self.db.commit()

        logger.info(f"Deleted secret {secret_id}")
        return True

    def sync_secret(
        self, secret_id: int, identity_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Force sync secret metadata from provider.

        Args:
            secret_id: Secret ID
            identity_id: Identity ID for logging

        Returns:
            Updated secret dictionary

        Raises:
            ValueError: If secret not found
            SecretProviderException: If provider error occurs
        """
        secret = self.db.secrets[secret_id]
        if not secret:
            raise ValueError(f"Secret {secret_id} not found")

        try:
            client = self.get_provider_client(secret.provider_id)
            secret_value = client.get_secret(secret.provider_path)

            # Update sync timestamp and KV status
            self.db(self.db.secrets.id == secret_id).update(
                is_kv=secret_value.is_kv, last_synced_at=datetime.utcnow()
            )

            logger.info(f"Synced secret {secret_id} from provider")

            return self.get_secret(secret_id, unmask=False, identity_id=identity_id)

        except Exception as e:
            logger.error(f"Error syncing secret {secret_id}: {str(e)}")
            raise

    def get_secret_access_log(
        self, secret_id: int, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get access log for a secret.

        Args:
            secret_id: Secret ID
            limit: Maximum number of entries

        Returns:
            List of access log entries
        """
        logs = self.db(self.db.secret_access_log.secret_id == secret_id).select(
            orderby=~self.db.secret_access_log.accessed_at, limitby=(0, limit)
        )

        return [
            {
                "id": log.id,
                "secret_id": log.secret_id,
                "identity_id": log.identity_id,
                "action": log.action,
                "masked": log.masked,
                "accessed_at": log.accessed_at,
            }
            for log in logs
        ]

    def _log_secret_access(
        self, secret_id: int, identity_id: int, action: str, masked: bool
    ) -> None:
        """Log secret access for audit trail."""
        try:
            self.db.secret_access_log.insert(
                secret_id=secret_id,
                identity_id=identity_id,
                action=action,
                masked=masked,
                accessed_at=datetime.utcnow(),
            )
            self.db.commit()
        except Exception as e:
            logger.error(f"Failed to log secret access: {str(e)}")
            # Don't fail the operation if logging fails

    # Provider Management Methods

    def list_providers(
        self, organization_id: Optional[int] = None, provider_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """List secret providers."""
        query = self.db.secret_providers.id > 0

        if organization_id:
            query &= self.db.secret_providers.organization_id == organization_id
        if provider_type:
            query &= self.db.secret_providers.provider == provider_type

        providers = self.db(query).select()

        return [
            {
                "id": p.id,
                "name": p.name,
                "provider": p.provider,
                "organization_id": p.organization_id,
                "enabled": p.enabled,
                "last_sync_at": p.last_sync_at,
                "created_at": p.created_at,
                "updated_at": p.updated_at,
            }
            for p in providers
        ]

    def get_provider(self, provider_id: int) -> Dict[str, Any]:
        """Get provider details."""
        provider = self.db.secret_providers[provider_id]
        if not provider:
            raise ValueError(f"Provider {provider_id} not found")

        return {
            "id": provider.id,
            "name": provider.name,
            "provider": provider.provider,
            "organization_id": provider.organization_id,
            "enabled": provider.enabled,
            "last_sync_at": provider.last_sync_at,
            "created_at": provider.created_at,
            "updated_at": provider.updated_at,
        }

    def create_provider(
        self,
        name: str,
        provider_type: str,
        config_json: Dict[str, Any],
        organization_id: int,
    ) -> Dict[str, Any]:
        """Create a new secret provider."""
        if provider_type not in self.PROVIDER_CLIENTS:
            raise ValueError(f"Unknown provider type: {provider_type}")

        # Test provider connection
        try:
            client_class = self.PROVIDER_CLIENTS[provider_type]
            client = client_class(config_json)
            if not client.test_connection():
                raise ValueError("Provider connection test failed")
        except Exception as e:
            raise ValueError(f"Provider validation failed: {str(e)}")

        provider_id = self.db.secret_providers.insert(
            name=name,
            provider=provider_type,
            config_json=config_json,
            organization_id=organization_id,
            enabled=True,
        )
        self.db.commit()

        logger.info(f"Created provider {provider_id} (type: {provider_type})")

        return self.get_provider(provider_id)

    def update_provider(
        self,
        provider_id: int,
        name: Optional[str] = None,
        config_json: Optional[Dict[str, Any]] = None,
        enabled: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Update provider configuration."""
        provider = self.db.secret_providers[provider_id]
        if not provider:
            raise ValueError(f"Provider {provider_id} not found")

        update_data = {}
        if name:
            update_data["name"] = name
        if config_json is not None:
            # Test new config
            try:
                client_class = self.PROVIDER_CLIENTS[provider.provider]
                client = client_class(config_json)
                if not client.test_connection():
                    raise ValueError("Provider connection test failed with new config")
            except Exception as e:
                raise ValueError(f"Provider config validation failed: {str(e)}")

            update_data["config_json"] = config_json
        if enabled is not None:
            update_data["enabled"] = enabled

        if update_data:
            self.db(self.db.secret_providers.id == provider_id).update(**update_data)

            logger.info(f"Updated provider {provider_id}")

        return self.get_provider(provider_id)

    def delete_provider(self, provider_id: int) -> bool:
        """Delete a provider."""
        provider = self.db.secret_providers[provider_id]
        if not provider:
            raise ValueError(f"Provider {provider_id} not found")

        # Check if any secrets are using this provider
        secrets_count = self.db(self.db.secrets.provider_id == provider_id).count()
        if secrets_count > 0:
            raise ValueError(
                f"Cannot delete provider {provider_id}: {secrets_count} secrets are still using it"
            )

        del self.db.secret_providers[provider_id]
        self.db.commit()

        logger.info(f"Deleted provider {provider_id}")
        return True

    def sync_provider(self, provider_id: int) -> Dict[str, Any]:
        """Sync all secrets from a provider."""
        provider = self.db.secret_providers[provider_id]
        if not provider:
            raise ValueError(f"Provider {provider_id} not found")

        try:
            client = self.get_provider_client(provider_id)
            provider_secrets = client.list_secrets()

            synced_count = 0
            for provider_secret in provider_secrets:
                # Check if secret already exists
                existing = (
                    self.db(
                        (self.db.secrets.provider_id == provider_id)
                        & (self.db.secrets.provider_path == provider_secret.path)
                    )
                    .select()
                    .first()
                )

                if existing:
                    # Update sync timestamp
                    self.db(self.db.secrets.id == existing.id).update(
                        is_kv=provider_secret.is_kv, last_synced_at=datetime.utcnow()
                    )
                    synced_count += 1

            # Update provider sync timestamp
            self.db(self.db.secret_providers.id == provider_id).update(
                last_sync_at=datetime.utcnow()
            )

            logger.info(f"Synced {synced_count} secrets from provider {provider_id}")

            return {
                "provider_id": provider_id,
                "secrets_synced": synced_count,
                "total_provider_secrets": len(provider_secrets),
                "synced_at": datetime.utcnow(),
            }

        except Exception as e:
            logger.error(f"Error syncing provider {provider_id}: {str(e)}")
            raise

    def test_provider_connection(self, provider_id: int) -> bool:
        """Test provider connection."""
        try:
            client = self.get_provider_client(provider_id)
            return client.test_connection()
        except Exception as e:
            logger.error(f"Provider {provider_id} connection test failed: {str(e)}")
            return False
