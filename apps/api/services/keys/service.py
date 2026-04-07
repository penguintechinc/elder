"""Keys management service - business logic layer for key operations."""

# flake8: noqa: E501


from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from apps.api.services.keys.aws_client import AWSKMSClient
from apps.api.services.keys.base import BaseKeyProvider
from apps.api.services.keys.gcp_client import GCPKMSClient
from apps.api.services.keys.infisical_client import InfisicalClient


class KeysService:
    """Service layer for key management operations."""

    def __init__(self, db):
        """
        Initialize KeysService.

        Args:
            db: PyDAL database instance
        """
        self.db = db

    def _get_provider_client(self, provider_id: int) -> BaseKeyProvider:
        """
        Get configured provider client.

        Args:
            provider_id: Provider ID from database

        Returns:
            Configured provider client instance

        Raises:
            Exception: If provider not found or invalid type
        """
        provider = self.db.key_providers[provider_id]

        if not provider:
            raise Exception(f"Key provider not found: {provider_id}")

        config = provider.as_dict()
        config["provider_type"] = provider.provider_type

        # Add config_json fields to config
        if provider.config_json:
            config.update(provider.config_json)

        # Create appropriate client
        provider_type = provider.provider_type.lower()

        if provider_type == "aws_kms":
            return AWSKMSClient(config)
        elif provider_type == "gcp_kms":
            return GCPKMSClient(config)
        elif provider_type == "infisical":
            return InfisicalClient(config)
        else:
            raise Exception(f"Unsupported provider type: {provider_type}")

    # Provider Management

    def list_providers(self, enabled_only: bool = False) -> List[Dict[str, Any]]:
        """
        List all key providers.

        Args:
            enabled_only: If True, only return enabled providers

        Returns:
            List of provider dictionaries
        """
        query = self.db.key_providers.id > 0

        if enabled_only:
            query &= self.db.key_providers.enabled is True

        providers = self.db(query).select(orderby=self.db.key_providers.name)

        return [self._sanitize_provider(p.as_dict()) for p in providers]

    def get_provider(self, provider_id: int) -> Dict[str, Any]:
        """
        Get provider details.

        Args:
            provider_id: Provider ID

        Returns:
            Provider dictionary

        Raises:
            Exception: If provider not found
        """
        provider = self.db.key_providers[provider_id]

        if not provider:
            raise Exception(f"Provider not found: {provider_id}")

        return self._sanitize_provider(provider.as_dict())

    def create_provider(
        self,
        name: str,
        provider_type: str,
        config: Dict[str, Any],
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a new key provider.

        Args:
            name: Provider name
            provider_type: Provider type (aws_kms, gcp_kms, infisical)
            config: Provider-specific configuration
            description: Optional description

        Returns:
            Created provider dictionary
        """
        # Validate provider type
        valid_types = ["aws_kms", "gcp_kms", "infisical"]
        if provider_type.lower() not in valid_types:
            raise Exception(
                f"Invalid provider type: {provider_type}. Must be one of {valid_types}"
            )

        # Create provider
        now = datetime.now(timezone.utc)
        provider_id = self.db.key_providers.insert(
            name=name,
            provider_type=provider_type.lower(),
            enabled=True,
            config_json=config,
            description=description,
            created_at=now,
            updated_at=now,
        )

        self.db.commit()

        return self.get_provider(provider_id)

    def update_provider(
        self,
        provider_id: int,
        name: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Update provider configuration.

        Args:
            provider_id: Provider ID
            name: New name
            config: New configuration
            description: New description
            enabled: Enable/disable provider

        Returns:
            Updated provider dictionary
        """
        provider = self.db.key_providers[provider_id]

        if not provider:
            raise Exception(f"Provider not found: {provider_id}")

        update_data = {}
        if name is not None:
            update_data["name"] = name
        if config is not None:
            update_data["config_json"] = config
        if description is not None:
            update_data["description"] = description
        if enabled is not None:
            update_data["enabled"] = enabled

        if update_data:
            self.db(self.db.key_providers.id == provider_id).update(**update_data)
            self.db.commit()

        return self.get_provider(provider_id)

    def delete_provider(self, provider_id: int) -> Dict[str, Any]:
        """
        Delete a key provider.

        Args:
            provider_id: Provider ID

        Returns:
            Success message
        """
        provider = self.db.key_providers[provider_id]

        if not provider:
            raise Exception(f"Provider not found: {provider_id}")

        # Check if provider has keys
        keys_count = self.db(self.db.crypto_keys.key_provider_id == provider_id).count()

        if keys_count > 0:
            raise Exception(
                f"Cannot delete provider with {keys_count} registered keys. "
                "Delete keys first or use force delete."
            )

        self.db(self.db.key_providers.id == provider_id).delete()
        self.db.commit()

        return {"message": "Provider deleted successfully"}

    def test_provider(self, provider_id: int) -> Dict[str, Any]:
        """
        Test provider connectivity.

        Args:
            provider_id: Provider ID

        Returns:
            Test result dictionary
        """
        try:
            client = self._get_provider_client(provider_id)
            success = client.test_connection()

            return {
                "provider_id": provider_id,
                "success": success,
                "tested_at": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as e:
            return {
                "provider_id": provider_id,
                "success": False,
                "error": str(e),
                "tested_at": datetime.now(timezone.utc).isoformat(),
            }

    # Key Management

    def create_key(
        self,
        provider_id: int,
        key_name: str,
        key_type: str = "symmetric",
        key_spec: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Create a new encryption key.

        Args:
            provider_id: Provider ID
            key_name: Key name
            key_type: Key type (symmetric, asymmetric, hmac)
            key_spec: Key specification
            description: Key description
            tags: Key tags

        Returns:
            Created key dictionary
        """
        client = self._get_provider_client(provider_id)

        # Create key with provider
        key_data = client.create_key(key_name, key_type, key_spec, description, tags)

        # Register key in database
        now = datetime.now(timezone.utc)
        key_id = self.db.crypto_keys.insert(
            key_provider_id=provider_id,
            provider_key_id=key_data["key_id"],
            provider_key_arn=key_data.get("key_arn"),
            name=key_name,
            key_type=key_type,
            key_state=key_data.get("state", "Enabled"),
            metadata_json={
                "key_spec": key_spec,
                "algorithm": key_data.get("algorithm"),
                "description": description,
                "tags": tags or {},
            },
            created_at=now,
            updated_at=now,
        )

        self.db.commit()

        return self.get_key(key_id)

    def get_key(self, key_id: int) -> Dict[str, Any]:
        """
        Get key details.

        Args:
            key_id: Database key ID

        Returns:
            Key dictionary
        """
        key = self.db.crypto_keys[key_id]

        if not key:
            raise Exception(f"Key not found: {key_id}")

        key_dict = key.as_dict()

        # Add provider name
        if key.key_provider_id:
            provider = self.db.key_providers[key.key_provider_id]
            key_dict["provider_name"] = provider.name if provider else None

        return key_dict

    def list_keys(
        self,
        provider_id: Optional[int] = None,
        key_type: Optional[str] = None,
        enabled_only: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        List all registered keys.

        Args:
            provider_id: Filter by provider ID
            key_type: Filter by key type
            enabled_only: Only return enabled keys

        Returns:
            List of key dictionaries
        """
        query = self.db.crypto_keys.id > 0

        if provider_id:
            query &= self.db.crypto_keys.key_provider_id == provider_id

        if key_type:
            query &= self.db.crypto_keys.key_type == key_type

        if enabled_only:
            query &= self.db.crypto_keys.key_state == "Enabled"

        keys = self.db(query).select(orderby=self.db.crypto_keys.created_at)

        result = []
        for key in keys:
            key_dict = key.as_dict()

            # Add provider name
            if key.key_provider_id:
                provider = self.db.key_providers[key.key_provider_id]
                key_dict["provider_name"] = provider.name if provider else None

            result.append(key_dict)

        return result

    def enable_key(self, key_id: int) -> Dict[str, Any]:
        """Enable a disabled key."""
        key = self.db.crypto_keys[key_id]

        if not key:
            raise Exception(f"Key not found: {key_id}")

        client = self._get_provider_client(key.key_provider_id)
        result = client.enable_key(key.provider_key_id)

        # Update database
        self.db(self.db.crypto_keys.id == key_id).update(
            key_state=result.get("state", "Enabled")
        )
        self.db.commit()

        return self.get_key(key_id)

    def disable_key(self, key_id: int) -> Dict[str, Any]:
        """Disable a key."""
        key = self.db.crypto_keys[key_id]

        if not key:
            raise Exception(f"Key not found: {key_id}")

        client = self._get_provider_client(key.key_provider_id)
        result = client.disable_key(key.provider_key_id)

        # Update database
        self.db(self.db.crypto_keys.id == key_id).update(
            key_state=result.get("state", "Disabled")
        )
        self.db.commit()

        return self.get_key(key_id)

    def delete_key(self, key_id: int, pending_days: int = 30) -> Dict[str, Any]:
        """Schedule key deletion."""
        key = self.db.crypto_keys[key_id]

        if not key:
            raise Exception(f"Key not found: {key_id}")

        client = self._get_provider_client(key.key_provider_id)
        result = client.schedule_key_deletion(key.provider_key_id, pending_days)

        # Update database
        self.db(self.db.crypto_keys.id == key_id).update(
            key_state=result.get("state", "PendingDeletion")
        )
        self.db.commit()

        # Log access
        self._log_access(key_id, "schedule_deletion", pending_days=pending_days)

        return result

    # Cryptographic Operations

    def encrypt(
        self, key_id: int, plaintext: str, context: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """Encrypt data using a key."""
        key = self.db.crypto_keys[key_id]

        if not key:
            raise Exception(f"Key not found: {key_id}")

        if key.key_state != "Enabled":
            raise Exception(f"Key is not enabled (state: {key.key_state})")

        client = self._get_provider_client(key.key_provider_id)
        result = client.encrypt(key.provider_key_id, plaintext, context)

        # Log access
        self._log_access(key_id, "encrypt", plaintext_length=len(plaintext))

        return result

    def decrypt(
        self, key_id: int, ciphertext: str, context: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """Decrypt data using a key."""
        key = self.db.crypto_keys[key_id]

        if not key:
            raise Exception(f"Key not found: {key_id}")

        if key.key_state != "Enabled":
            raise Exception(f"Key is not enabled (state: {key.key_state})")

        client = self._get_provider_client(key.key_provider_id)
        result = client.decrypt(ciphertext, context)

        # Log access
        self._log_access(key_id, "decrypt")

        return result

    def generate_data_key(
        self,
        key_id: int,
        key_spec: str = "AES_256",
        context: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Generate a data encryption key."""
        key = self.db.crypto_keys[key_id]

        if not key:
            raise Exception(f"Key not found: {key_id}")

        if key.key_state != "Enabled":
            raise Exception(f"Key is not enabled (state: {key.key_state})")

        client = self._get_provider_client(key.key_provider_id)
        result = client.generate_data_key(key.provider_key_id, key_spec, context)

        # Log access
        self._log_access(key_id, "generate_data_key", key_spec=key_spec)

        return result

    def sign(
        self, key_id: int, message: str, signing_algorithm: str = "RSASSA_PSS_SHA_256"
    ) -> Dict[str, Any]:
        """Sign a message using an asymmetric key."""
        key = self.db.crypto_keys[key_id]

        if not key:
            raise Exception(f"Key not found: {key_id}")

        if key.key_type != "asymmetric":
            raise Exception(
                f"Key must be asymmetric for signing (type: {key.key_type})"
            )

        if key.key_state != "Enabled":
            raise Exception(f"Key is not enabled (state: {key.key_state})")

        client = self._get_provider_client(key.key_provider_id)
        result = client.sign(key.provider_key_id, message, signing_algorithm)

        # Log access
        self._log_access(key_id, "sign", algorithm=signing_algorithm)

        return result

    def verify(
        self, key_id: int, message: str, signature: str, signing_algorithm: str
    ) -> Dict[str, Any]:
        """Verify a message signature."""
        key = self.db.crypto_keys[key_id]

        if not key:
            raise Exception(f"Key not found: {key_id}")

        if key.key_type != "asymmetric":
            raise Exception(
                f"Key must be asymmetric for verification (type: {key.key_type})"
            )

        client = self._get_provider_client(key.key_provider_id)
        result = client.verify(
            key.provider_key_id, message, signature, signing_algorithm
        )

        # Log access
        self._log_access(key_id, "verify", algorithm=signing_algorithm)

        return result

    def rotate_key(self, key_id: int) -> Dict[str, Any]:
        """Rotate a key."""
        key = self.db.crypto_keys[key_id]

        if not key:
            raise Exception(f"Key not found: {key_id}")

        client = self._get_provider_client(key.key_provider_id)
        result = client.rotate_key(key.provider_key_id)

        # Log access
        self._log_access(key_id, "rotate")

        return result

    # Access Logging

    def _log_access(self, key_id: int, operation: str, **kwargs):
        """Log key access for audit trail."""
        now = datetime.now(timezone.utc)
        self.db.key_access_log.insert(
            key_id=key_id,
            operation=operation,
            accessed_at=now,
            metadata_json=kwargs,
            created_at=now,
            updated_at=now,
        )
        self.db.commit()

    def get_access_log(
        self, key_id: int, limit: int = 100, offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Get access log for a key.

        Args:
            key_id: Key ID
            limit: Maximum number of records
            offset: Pagination offset

        Returns:
            List of access log entries
        """
        logs = self.db(self.db.key_access_log.key_id == key_id).select(
            orderby=~self.db.key_access_log.accessed_at,
            limitby=(offset, offset + limit),
        )

        return [log.as_dict() for log in logs]

    # Utility Methods

    def _sanitize_provider(self, provider: Dict[str, Any]) -> Dict[str, Any]:
        """Remove sensitive fields from provider data."""
        # Create a copy
        sanitized = dict(provider)

        # Remove or mask sensitive config fields
        if "config_json" in sanitized and sanitized["config_json"]:
            config = dict(sanitized["config_json"])

            # Mask sensitive fields
            sensitive_fields = [
                "access_key_id",
                "secret_access_key",
                "credentials_json",
                "token",
                "api_key",
            ]

            for field in sensitive_fields:
                if field in config:
                    config[field] = "***REDACTED***"

            sanitized["config_json"] = config

        return sanitized
