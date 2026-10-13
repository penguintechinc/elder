"""Base client interface for secret providers."""

# flake8: noqa: E501

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class SecretValue:
    """Represents a secret value retrieved from a provider."""

    name: str
    value: str | None = None  # None if masked
    is_masked: bool = True
    is_kv: bool = False
    kv_pairs: dict[str, str] | None = None
    version: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    metadata: dict[str, Any] | None = None

    def mask(self) -> "SecretValue":
        """Return a masked version of this secret."""
        return SecretValue(
            name=self.name,
            value="***MASKED***" if self.value else None,
            is_masked=True,
            is_kv=self.is_kv,
            kv_pairs=(
                {k: "***MASKED***" for k in self.kv_pairs.keys()}
                if self.kv_pairs
                else None
            ),
            version=self.version,
            created_at=self.created_at,
            updated_at=self.updated_at,
            metadata=self.metadata,
        )


@dataclass
class SecretMetadata:
    """Metadata about a secret without the actual value."""

    name: str
    path: str
    is_kv: bool
    version: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    metadata: dict[str, Any] | None = None


class SecretProviderClient(ABC):
    """Abstract base class for secret provider clients."""

    def __init__(self, config: dict[str, Any]):
        """
        Initialize the secret provider client.

        Args:
            config: Provider-specific configuration dictionary
        """
        self.config = config
        self._validate_config()

    @abstractmethod
    def _validate_config(self) -> None:
        """
        Validate the provider configuration.

        Raises:
            ValueError: If configuration is invalid
        """

    @abstractmethod
    def test_connection(self) -> bool:
        """
        Test the connection to the secret provider.

        Returns:
            True if connection is successful, False otherwise
        """

    @abstractmethod
    def get_secret(self, path: str, version: str | None = None) -> SecretValue:
        """
        Retrieve a secret from the provider.

        Args:
            path: Path or identifier of the secret
            version: Optional specific version to retrieve

        Returns:
            SecretValue object with the secret data

        Raises:
            SecretNotFoundException: If secret doesn't exist
            SecretAccessDeniedException: If access is denied
            SecretProviderException: For other provider errors
        """

    @abstractmethod
    def list_secrets(self, prefix: str | None = None) -> list[SecretMetadata]:
        """
        List secrets available in the provider.

        Args:
            prefix: Optional prefix to filter secrets

        Returns:
            List of SecretMetadata objects

        Raises:
            SecretProviderException: For provider errors
        """

    @abstractmethod
    def create_secret(
        self, path: str, value: str, metadata: dict[str, Any] | None = None
    ) -> SecretMetadata:
        """
        Create a new secret in the provider.

        Args:
            path: Path or identifier for the secret
            value: Secret value to store
            metadata: Optional metadata to attach

        Returns:
            SecretMetadata of the created secret

        Raises:
            SecretAlreadyExistsException: If secret already exists
            SecretProviderException: For other provider errors
        """

    @abstractmethod
    def update_secret(self, path: str, value: str) -> SecretMetadata:
        """
        Update an existing secret in the provider.

        Args:
            path: Path or identifier of the secret
            value: New secret value

        Returns:
            SecretMetadata of the updated secret

        Raises:
            SecretNotFoundException: If secret doesn't exist
            SecretProviderException: For provider errors
        """

    @abstractmethod
    def delete_secret(self, path: str, force: bool = False) -> bool:
        """
        Delete a secret from the provider.

        Args:
            path: Path or identifier of the secret
            force: Force immediate deletion (skip recovery window if available)

        Returns:
            True if deletion was successful

        Raises:
            SecretNotFoundException: If secret doesn't exist
            SecretProviderException: For provider errors
        """

    @abstractmethod
    def get_secret_versions(self, path: str) -> list[str]:
        """
        Get all versions of a secret.

        Args:
            path: Path or identifier of the secret

        Returns:
            List of version identifiers

        Raises:
            SecretNotFoundException: If secret doesn't exist
            SecretProviderException: For provider errors
        """


class SecretProviderException(Exception):
    """Base exception for secret provider errors."""


class SecretNotFoundException(SecretProviderException):
    """Exception raised when a secret is not found."""


class SecretAccessDeniedException(SecretProviderException):
    """Exception raised when access to a secret is denied."""


class SecretAlreadyExistsException(SecretProviderException):
    """Exception raised when trying to create a secret that already exists."""


class InvalidSecretConfigException(SecretProviderException):
    """Exception raised when secret provider configuration is invalid."""
