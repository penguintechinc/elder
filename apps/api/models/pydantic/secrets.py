"""
Pydantic 2 response DTOs for built-in secrets management endpoints.

Scopes `apps.api.services.secrets.base.SecretMetadata` / `SecretValue` down to
the exact fields these endpoints intend to expose — security-audit fix for
raw `__dict__` serialization (see apps/api/modules/secrets/routes/builtin_secrets.py).
"""

from datetime import datetime
from typing import Any

from penguin_libs.pydantic.base import ImmutableModel


class SecretMetadataResponse(ImmutableModel):
    """Metadata about a built-in secret — never includes the secret value."""

    name: str
    path: str
    is_kv: bool
    version: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    metadata: dict[str, Any] | None = None


class SecretMetadataListResponse(ImmutableModel):
    """List of built-in secret metadata entries."""

    secrets: list[SecretMetadataResponse]


class SecretValueResponse(ImmutableModel):
    """
    A retrieved built-in secret value.

    NOTE: `value`/`kv_pairs` masking is enforced by the provider client
    (BuiltinSecretsClient), not this DTO — see security note in
    builtin_secrets.py's get_secret handler.
    """

    name: str
    value: str | None = None
    is_masked: bool
    is_kv: bool
    kv_pairs: dict[str, str] | None = None
    version: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    metadata: dict[str, Any] | None = None


class DeleteSecretResponse(ImmutableModel):
    """Result of a built-in secret deletion."""

    success: bool
    path: str


class SecretConnectionTestResponse(ImmutableModel):
    """Result of testing a secrets provider connection."""

    success: bool
    provider: str
