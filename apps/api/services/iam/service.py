"""IAM management service - business logic layer for IAM operations."""

# flake8: noqa: E501

import json
from datetime import UTC, datetime
from typing import Any

from apps.api.services.iam.aws_client import AWSIAMClient
from apps.api.services.iam.base import BaseIAMProvider
from apps.api.services.iam.gcp_client import GCPIAMClient
from apps.api.services.iam.k8s_client import KubernetesRBACClient

_IAM_SENSITIVE_FIELDS = (
    "access_key_id",
    "secret_access_key",
    "credentials_json",
    "token",
    "session_token",
    "kubeconfig",
    "client_secret",
    "private_key",
    "api_token",
    "api_key",
)
_REDACTED_SENTINELS = frozenset({"", "***REDACTED***", "***masked***"})


def _parse_json_dict(raw: Any) -> dict[str, Any]:
    """Parse a JSON column value (dict or JSON string) into a fresh dict."""
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return dict(parsed)
        except (ValueError, TypeError):
            pass
    return {}


def _is_blank_or_redacted_secret(value: Any) -> bool:
    """Return True when a secret value is empty, None, or a redaction marker."""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() in _REDACTED_SENTINELS
    if isinstance(value, dict) and not value:
        return True
    return False


class IAMTenantRequiredError(PermissionError):
    """Raised when an IAM operation is attempted without a verified tenant id."""


class IAMProviderNotFoundError(LookupError):
    """Raised when a provider/organization does not exist in the caller's tenant.

    Rows owned by another tenant are reported exactly like missing rows so
    callers cannot enumerate other tenants' ids.
    """


def _require_tenant(tenant_id: int | None) -> int:
    """Return ``tenant_id`` or raise ``IAMTenantRequiredError`` (fail closed)."""
    if not tenant_id:
        raise IAMTenantRequiredError("A verified tenant id is required")
    return int(tenant_id)


class IAMService:
    """Service layer for IAM management operations.

    Every public method takes the caller's verified ``tenant_id`` first.
    ``iam_providers`` has no ``tenant_id`` column; tenancy is derived from
    ``iam_providers.organization_id -> organizations.tenant_id``.
    """

    def __init__(self, db):
        """
        Initialize IAMService.

        Args:
            db: penguin-dal database instance
        """
        self.db = db

    def _tenant_org_ids(self, tenant_id: int) -> list[int]:
        """Return the ids of all organizations owned by ``tenant_id``."""
        db = self.db
        rows = db(db.organizations.tenant_id == tenant_id).select(db.organizations.id)
        return [r.id for r in rows]

    def _require_tenant_org(self, tenant_id: int, organization_id: int) -> int:
        """Return ``organization_id`` if it belongs to ``tenant_id``, else raise."""
        db = self.db
        org = (
            db(
                (db.organizations.id == organization_id)
                & (db.organizations.tenant_id == tenant_id)
            )
            .select(db.organizations.id)
            .first()
        )
        if not org:
            raise IAMProviderNotFoundError(f"Organization not found: {organization_id}")
        return org.id

    def _get_scoped_provider(
        self, tenant_id: int | None, provider_id: int, label: str = "Provider"
    ) -> Any:
        """Load a provider row only if its organization belongs to ``tenant_id``."""
        tid = _require_tenant(tenant_id)
        org_ids = self._tenant_org_ids(tid)
        provider = None
        if provider_id and org_ids:
            db = self.db
            provider = (
                db(
                    (db.iam_providers.id == provider_id)
                    & (db.iam_providers.organization_id.belongs(org_ids))
                )
                .select()
                .first()
            )
        if not provider:
            raise IAMProviderNotFoundError(f"{label} not found: {provider_id}")
        return provider

    def _get_provider_client(
        self, tenant_id: int | None, provider_id: int
    ) -> BaseIAMProvider:
        """
        Get configured provider client for a provider in the caller's tenant.

        Raises:
            IAMTenantRequiredError: If no tenant id is supplied
            IAMProviderNotFoundError: If the provider is missing or foreign
            Exception: If the provider type is unsupported
        """
        provider = self._get_scoped_provider(tenant_id, provider_id, "IAM provider")

        config = provider.as_dict()
        config["provider_type"] = provider.provider_type

        if provider.config_json:
            config.update(_parse_json_dict(provider.config_json))

        provider_type = provider.provider_type.lower()

        if provider_type == "aws_iam":
            return AWSIAMClient(config)
        elif provider_type == "gcp_iam":
            return GCPIAMClient(config)
        elif provider_type == "kubernetes":
            return KubernetesRBACClient(config)
        else:
            raise Exception(f"Unsupported provider type: {provider_type}")

    # Provider Management

    def list_providers(
        self,
        tenant_id: int | None,
        enabled_only: bool = False,
        organization_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """List IAM providers in the caller's tenant."""
        tid = _require_tenant(tenant_id)
        org_ids = self._tenant_org_ids(tid)
        if organization_id is not None:
            org_ids = [o for o in org_ids if o == organization_id]
        if not org_ids:
            return []

        query = self.db.iam_providers.organization_id.belongs(org_ids)
        if enabled_only:
            query &= self.db.iam_providers.enabled == True  # noqa: E712

        providers = self.db(query).select(orderby=self.db.iam_providers.name)

        return [self._sanitize_provider(p.as_dict()) for p in providers]

    def get_provider(self, tenant_id: int | None, provider_id: int) -> dict[str, Any]:
        """Get provider details."""
        provider = self._get_scoped_provider(tenant_id, provider_id)
        return self._sanitize_provider(provider.as_dict())

    def create_provider(
        self,
        tenant_id: int | None,
        name: str,
        provider_type: str,
        config: dict[str, Any],
        description: str | None = None,
        organization_id: int | None = None,
    ) -> dict[str, Any]:
        """Create a new IAM provider in an organization of the caller's tenant."""
        tid = _require_tenant(tenant_id)
        valid_types = ["aws_iam", "gcp_iam", "kubernetes"]
        if provider_type.lower() not in valid_types:
            raise Exception(
                f"Invalid provider type: {provider_type}. Must be one of {valid_types}"
            )

        if organization_id is not None:
            resolved_org_id = self._require_tenant_org(tid, organization_id)
        else:
            org_ids = sorted(self._tenant_org_ids(tid))
            if not org_ids:
                raise ValueError(
                    "organization_id is required (no default organization exists)"
                )
            resolved_org_id = org_ids[0]

        provider_config = dict(config or {})
        if description is not None:
            provider_config["_description"] = description

        from apps.api.utils.village_id import mint_village_id

        now = datetime.now(UTC)
        provider_id = self.db.iam_providers.insert(
            name=name,
            provider_type=provider_type.lower(),
            organization_id=resolved_org_id,
            enabled=True,
            config_json=provider_config,
            village_id=mint_village_id(tid),
            created_at=now,
            updated_at=now,
        )

        self.db.commit()

        return self.get_provider(tid, provider_id)

    def update_provider(
        self,
        tenant_id: int | None,
        provider_id: int,
        name: str | None = None,
        config: dict[str, Any] | None = None,
        description: str | None = None,
        enabled: bool | None = None,
        organization_id: int | None = None,
    ) -> dict[str, Any]:
        """Update provider configuration."""
        provider = self._get_scoped_provider(tenant_id, provider_id)
        tid = int(tenant_id)  # validated by _get_scoped_provider

        existing_cfg = _parse_json_dict(getattr(provider, "config_json", None))
        update_data: dict[str, Any] = {}
        if name is not None:
            update_data["name"] = name
        if enabled is not None:
            update_data["enabled"] = enabled
        if organization_id is not None:
            update_data["organization_id"] = self._require_tenant_org(
                tid, organization_id
            )

        if config is not None or description is not None:
            if config is not None:
                merged_cfg = dict(config)
                if (
                    description is None
                    and "_description" not in merged_cfg
                    and "_description" in existing_cfg
                ):
                    merged_cfg["_description"] = existing_cfg["_description"]
                for secret_key in _IAM_SENSITIVE_FIELDS:
                    if (
                        secret_key in existing_cfg
                        and not _is_blank_or_redacted_secret(existing_cfg[secret_key])
                        and _is_blank_or_redacted_secret(merged_cfg.get(secret_key))
                    ):
                        merged_cfg[secret_key] = existing_cfg[secret_key]
            else:
                merged_cfg = dict(existing_cfg)

            if description is not None:
                merged_cfg["_description"] = description

            update_data["config_json"] = merged_cfg

        if update_data:
            update_data["updated_at"] = datetime.now(UTC)
            self.db(self.db.iam_providers.id == provider_id).update(**update_data)
            self.db.commit()

        return self.get_provider(tid, provider_id)

    def delete_provider(
        self, tenant_id: int | None, provider_id: int
    ) -> dict[str, Any]:
        """Delete an IAM provider.

        Users/roles/policies live in the external provider and are never
        persisted locally, so there are no dependent rows to check.
        """
        provider = self._get_scoped_provider(tenant_id, provider_id)

        self.db(self.db.iam_providers.id == provider.id).delete()
        self.db.commit()

        return {"message": "Provider deleted successfully"}

    def test_provider(self, tenant_id: int | None, provider_id: int) -> dict[str, Any]:
        """Test provider connectivity (provider must be in the caller's tenant)."""
        self._get_scoped_provider(tenant_id, provider_id)
        try:
            client = self._get_provider_client(tenant_id, provider_id)
            success = client.test_connection()

            return {
                "provider_id": provider_id,
                "success": success,
                "tested_at": datetime.now(UTC).isoformat(),
            }

        except Exception as e:
            return {
                "provider_id": provider_id,
                "success": False,
                "error": str(e),
                "tested_at": datetime.now(UTC).isoformat(),
            }

    def sync_provider(self, tenant_id: int | None, provider_id: int) -> dict[str, Any]:
        """Sync from the provider and record the time on ``iam_providers.last_sync_at``."""
        provider = self._get_scoped_provider(tenant_id, provider_id)
        try:
            client = self._get_provider_client(tenant_id, provider_id)
            result = client.sync_from_provider()

            now = datetime.now(UTC)
            self.db(self.db.iam_providers.id == provider.id).update(
                last_sync_at=now, updated_at=now
            )
            self.db.commit()

            return result

        except Exception as e:
            return {
                "users_synced": 0,
                "roles_synced": 0,
                "policies_synced": 0,
                "errors": [str(e)],
            }

    # User Management

    def list_users(
        self, tenant_id: int | None, provider_id: int, **kwargs
    ) -> list[dict[str, Any]]:
        """List users from provider."""
        client = self._get_provider_client(tenant_id, provider_id)
        return client.list_users(**kwargs)

    def get_user(
        self, tenant_id: int | None, provider_id: int, user_id: str
    ) -> dict[str, Any]:
        """Get user details."""
        client = self._get_provider_client(tenant_id, provider_id)
        return client.get_user(user_id)

    def create_user(
        self, tenant_id: int | None, provider_id: int, **kwargs
    ) -> dict[str, Any]:
        """Create a new user."""
        client = self._get_provider_client(tenant_id, provider_id)
        return client.create_user(**kwargs)

    def update_user(
        self, tenant_id: int | None, provider_id: int, user_id: str, **kwargs
    ) -> dict[str, Any]:
        """Update user."""
        client = self._get_provider_client(tenant_id, provider_id)
        return client.update_user(user_id, **kwargs)

    def delete_user(
        self, tenant_id: int | None, provider_id: int, user_id: str
    ) -> dict[str, Any]:
        """Delete user."""
        client = self._get_provider_client(tenant_id, provider_id)
        return client.delete_user(user_id)

    # Role Management

    def list_roles(
        self, tenant_id: int | None, provider_id: int, **kwargs
    ) -> list[dict[str, Any]]:
        """List roles from provider."""
        client = self._get_provider_client(tenant_id, provider_id)
        return client.list_roles(**kwargs)

    def get_role(
        self, tenant_id: int | None, provider_id: int, role_id: str
    ) -> dict[str, Any]:
        """Get role details."""
        client = self._get_provider_client(tenant_id, provider_id)
        return client.get_role(role_id)

    def create_role(
        self, tenant_id: int | None, provider_id: int, **kwargs
    ) -> dict[str, Any]:
        """Create a new role."""
        client = self._get_provider_client(tenant_id, provider_id)
        return client.create_role(**kwargs)

    def update_role(
        self, tenant_id: int | None, provider_id: int, role_id: str, **kwargs
    ) -> dict[str, Any]:
        """Update role."""
        client = self._get_provider_client(tenant_id, provider_id)
        return client.update_role(role_id, **kwargs)

    def delete_role(
        self, tenant_id: int | None, provider_id: int, role_id: str
    ) -> dict[str, Any]:
        """Delete role."""
        client = self._get_provider_client(tenant_id, provider_id)
        return client.delete_role(role_id)

    # Policy Management

    def list_policies(
        self, tenant_id: int | None, provider_id: int, **kwargs
    ) -> list[dict[str, Any]]:
        """List policies from provider."""
        client = self._get_provider_client(tenant_id, provider_id)
        return client.list_policies(**kwargs)

    def get_policy(
        self, tenant_id: int | None, provider_id: int, policy_id: str
    ) -> dict[str, Any]:
        """Get policy details."""
        client = self._get_provider_client(tenant_id, provider_id)
        return client.get_policy(policy_id)

    def create_policy(
        self, tenant_id: int | None, provider_id: int, **kwargs
    ) -> dict[str, Any]:
        """Create a new policy."""
        client = self._get_provider_client(tenant_id, provider_id)
        return client.create_policy(**kwargs)

    def delete_policy(
        self, tenant_id: int | None, provider_id: int, policy_id: str
    ) -> dict[str, Any]:
        """Delete policy."""
        client = self._get_provider_client(tenant_id, provider_id)
        return client.delete_policy(policy_id)

    # Policy Attachments

    def attach_policy_to_user(
        self, tenant_id: int | None, provider_id: int, user_id: str, policy_id: str
    ) -> dict[str, Any]:
        """Attach policy to user."""
        client = self._get_provider_client(tenant_id, provider_id)
        return client.attach_policy_to_user(user_id, policy_id)

    def detach_policy_from_user(
        self, tenant_id: int | None, provider_id: int, user_id: str, policy_id: str
    ) -> dict[str, Any]:
        """Detach policy from user."""
        client = self._get_provider_client(tenant_id, provider_id)
        return client.detach_policy_from_user(user_id, policy_id)

    def attach_policy_to_role(
        self, tenant_id: int | None, provider_id: int, role_id: str, policy_id: str
    ) -> dict[str, Any]:
        """Attach policy to role."""
        client = self._get_provider_client(tenant_id, provider_id)
        return client.attach_policy_to_role(role_id, policy_id)

    def detach_policy_from_role(
        self, tenant_id: int | None, provider_id: int, role_id: str, policy_id: str
    ) -> dict[str, Any]:
        """Detach policy from role."""
        client = self._get_provider_client(tenant_id, provider_id)
        return client.detach_policy_from_role(role_id, policy_id)

    def list_user_policies(
        self, tenant_id: int | None, provider_id: int, user_id: str
    ) -> list[dict[str, Any]]:
        """List policies attached to user."""
        client = self._get_provider_client(tenant_id, provider_id)
        return client.list_user_policies(user_id)

    def list_role_policies(
        self, tenant_id: int | None, provider_id: int, role_id: str
    ) -> list[dict[str, Any]]:
        """List policies attached to role."""
        client = self._get_provider_client(tenant_id, provider_id)
        return client.list_role_policies(role_id)

    # Access Keys

    def create_access_key(
        self, tenant_id: int | None, provider_id: int, user_id: str
    ) -> dict[str, Any]:
        """Create access key for user."""
        client = self._get_provider_client(tenant_id, provider_id)
        return client.create_access_key(user_id)

    def list_access_keys(
        self, tenant_id: int | None, provider_id: int, user_id: str
    ) -> list[dict[str, Any]]:
        """List access keys for user."""
        client = self._get_provider_client(tenant_id, provider_id)
        return client.list_access_keys(user_id)

    def delete_access_key(
        self, tenant_id: int | None, provider_id: int, user_id: str, key_id: str
    ) -> dict[str, Any]:
        """Delete access key."""
        client = self._get_provider_client(tenant_id, provider_id)
        return client.delete_access_key(user_id, key_id)

    # Group Management

    def list_groups(
        self, tenant_id: int | None, provider_id: int, **kwargs
    ) -> list[dict[str, Any]]:
        """List groups from provider."""
        client = self._get_provider_client(tenant_id, provider_id)
        return client.list_groups(**kwargs)

    def create_group(
        self, tenant_id: int | None, provider_id: int, **kwargs
    ) -> dict[str, Any]:
        """Create a new group."""
        client = self._get_provider_client(tenant_id, provider_id)
        return client.create_group(**kwargs)

    def delete_group(
        self, tenant_id: int | None, provider_id: int, group_id: str
    ) -> dict[str, Any]:
        """Delete group."""
        client = self._get_provider_client(tenant_id, provider_id)
        return client.delete_group(group_id)

    def add_user_to_group(
        self, tenant_id: int | None, provider_id: int, user_id: str, group_id: str
    ) -> dict[str, Any]:
        """Add user to group."""
        client = self._get_provider_client(tenant_id, provider_id)
        return client.add_user_to_group(user_id, group_id)

    def remove_user_from_group(
        self, tenant_id: int | None, provider_id: int, user_id: str, group_id: str
    ) -> dict[str, Any]:
        """Remove user from group."""
        client = self._get_provider_client(tenant_id, provider_id)
        return client.remove_user_from_group(user_id, group_id)

    # Utility Methods

    def _sanitize_provider(self, provider: dict[str, Any]) -> dict[str, Any]:
        """Remove sensitive fields from provider data."""
        sanitized = dict(provider)

        if "config_json" in sanitized and sanitized["config_json"] is not None:
            config = _parse_json_dict(sanitized["config_json"])

            for field in _IAM_SENSITIVE_FIELDS:
                if field in config and not _is_blank_or_redacted_secret(config[field]):
                    config[field] = "***REDACTED***"

            if "description" not in sanitized:
                sanitized["description"] = config.get("_description")

            sanitized["config_json"] = config

        return sanitized
