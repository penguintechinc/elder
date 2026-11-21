"""Base abstract class for key management providers."""

# flake8: noqa: E501

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class BaseKeyProvider(ABC):
    """Abstract base class for key management providers (AWS KMS, GCP KMS, Infisical)."""

    def __init__(self, config: dict[str, Any]):
        """
        Initialize the key provider.

        Args:
            config: Provider-specific configuration dictionary
        """
        self.config = config
        self.provider_type = config.get("provider_type")

    @abstractmethod
    def create_key(
        self,
        key_name: str,
        key_type: str = "symmetric",
        key_spec: str | None = None,
        description: str | None = None,
        tags: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Create a new encryption key.

        Args:
            key_name: Name/identifier for the key
            key_type: Type of key (symmetric, asymmetric, hmac)
            key_spec: Key specification (algorithm, key size)
            description: Optional description
            tags: Optional tags/labels

        Returns:
            Dictionary with key details:
            {
                "key_id": "provider-specific-key-id",
                "key_arn": "full-arn-or-resource-name",
                "key_type": "symmetric",
                "state": "enabled",
                "created_at": "ISO8601 timestamp",
            }
        """

    @abstractmethod
    def get_key(self, key_id: str) -> dict[str, Any]:
        """
        Get key metadata.

        Args:
            key_id: Provider-specific key identifier

        Returns:
            Dictionary with key metadata
        """

    @abstractmethod
    def list_keys(
        self, limit: int | None = None, next_token: str | None = None
    ) -> dict[str, Any]:
        """
        List all keys.

        Args:
            limit: Maximum number of keys to return
            next_token: Pagination token

        Returns:
            Dictionary with:
            {
                "keys": [list of key metadata dicts],
                "next_token": "pagination-token" or None
            }
        """

    @abstractmethod
    def enable_key(self, key_id: str) -> dict[str, Any]:
        """
        Enable a disabled key.

        Args:
            key_id: Provider-specific key identifier

        Returns:
            Dictionary with updated key state
        """

    @abstractmethod
    def disable_key(self, key_id: str) -> dict[str, Any]:
        """
        Disable a key (soft delete).

        Args:
            key_id: Provider-specific key identifier

        Returns:
            Dictionary with updated key state
        """

    @abstractmethod
    def schedule_key_deletion(
        self, key_id: str, pending_days: int = 30
    ) -> dict[str, Any]:
        """
        Schedule key deletion.

        Args:
            key_id: Provider-specific key identifier
            pending_days: Number of days before permanent deletion

        Returns:
            Dictionary with deletion details
        """

    @abstractmethod
    def cancel_key_deletion(self, key_id: str) -> dict[str, Any]:
        """
        Cancel scheduled key deletion.

        Args:
            key_id: Provider-specific key identifier

        Returns:
            Dictionary with updated key state
        """

    @abstractmethod
    def encrypt(
        self, key_id: str, plaintext: str, context: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """
        Encrypt data using a key.

        Args:
            key_id: Provider-specific key identifier
            plaintext: Data to encrypt (string or bytes)
            context: Optional encryption context (additional authenticated data)

        Returns:
            Dictionary with:
            {
                "ciphertext": "base64-encoded-ciphertext",
                "key_id": "key-used-for-encryption",
            }
        """

    @abstractmethod
    def decrypt(
        self, ciphertext: str, context: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """
        Decrypt data.

        Args:
            ciphertext: Base64-encoded ciphertext
            context: Optional encryption context (must match encryption context)

        Returns:
            Dictionary with:
            {
                "plaintext": "decrypted-data",
                "key_id": "key-used-for-decryption",
            }
        """

    @abstractmethod
    def generate_data_key(
        self,
        key_id: str,
        key_spec: str = "AES_256",
        context: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Generate a data encryption key.

        Args:
            key_id: Provider-specific key identifier (master key)
            key_spec: Data key specification (AES_256, AES_128)
            context: Optional encryption context

        Returns:
            Dictionary with:
            {
                "plaintext_key": "base64-plaintext-key",
                "ciphertext_key": "base64-encrypted-key",
                "key_id": "master-key-id",
            }
        """

    @abstractmethod
    def sign(
        self,
        key_id: str,
        message: str,
        signing_algorithm: str = "RSASSA_PSS_SHA_256",
    ) -> dict[str, Any]:
        """
        Sign a message using an asymmetric key.

        Args:
            key_id: Provider-specific key identifier
            message: Message to sign
            signing_algorithm: Signing algorithm

        Returns:
            Dictionary with:
            {
                "signature": "base64-encoded-signature",
                "key_id": "key-used-for-signing",
                "algorithm": "signing-algorithm-used",
            }
        """

    @abstractmethod
    def verify(
        self, key_id: str, message: str, signature: str, signing_algorithm: str
    ) -> dict[str, Any]:
        """
        Verify a message signature.

        Args:
            key_id: Provider-specific key identifier
            message: Original message
            signature: Base64-encoded signature
            signing_algorithm: Signing algorithm used

        Returns:
            Dictionary with:
            {
                "valid": true/false,
                "key_id": "key-used-for-verification",
            }
        """

    @abstractmethod
    def rotate_key(self, key_id: str) -> dict[str, Any]:
        """
        Rotate a key (create new version or rotate key material).

        Args:
            key_id: Provider-specific key identifier

        Returns:
            Dictionary with rotation details
        """

    @abstractmethod
    def test_connection(self) -> bool:
        """
        Test connectivity to the key provider.

        Returns:
            True if connection successful, False otherwise
        """

    def _normalize_key_metadata(self, raw_metadata: dict[str, Any]) -> dict[str, Any]:
        """
        Normalize provider-specific key metadata to common format.

        Args:
            raw_metadata: Provider-specific metadata

        Returns:
            Normalized metadata dictionary
        """
        return {
            "key_id": raw_metadata.get("key_id") or raw_metadata.get("KeyId"),
            "key_arn": raw_metadata.get("key_arn")
            or raw_metadata.get("KeyMetadata", {}).get("Arn"),
            "state": raw_metadata.get("state") or raw_metadata.get("KeyState"),
            "created_at": raw_metadata.get("created_at")
            or raw_metadata.get("CreationDate"),
            "description": raw_metadata.get("description")
            or raw_metadata.get("Description"),
            "enabled": raw_metadata.get("enabled") or raw_metadata.get("Enabled", True),
        }
