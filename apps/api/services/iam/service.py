"""IAM management service - business logic layer for IAM operations."""

# flake8: noqa: E501


from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from apps.api.services.iam.aws_client import AWSIAMClient
from apps.api.services.iam.base import BaseIAMProvider
from apps.api.services.iam.gcp_client import GCPIAMClient
from apps.api.services.iam.k8s_client import KubernetesRBACClient


class IAMService:
    """Service layer for IAM management operations."""

    def __init__(self, db):
        """
        Initialize IAMService.

        Args:
            db: PyDAL database instance
        """
        self.db = db

    def _get_provider_client(self, provider_id: int) -> BaseIAMProvider:
        """
        Get configured provider client.

        Args:
            provider_id: Provider ID from database

        Returns:
            Configured provider client instance

        Raises:
            Exception: If provider not found or invalid type
        """
        provider = self.db.iam_providers[provider_id]

        if not provider:
            raise Exception(f"IAM provider not found: {provider_id}")

        config = provider.as_dict()
        config["provider_type"] = provider.provider_type

        # Add config_json fields to config
        if provider.config_json:
            config.update(provider.config_json)

        # Create appropriate client
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

    def list_providers(self, enabled_only: bool = False) -> List[Dict[str, Any]]:
        """List all IAM providers."""
        query = self.db.iam_providers.id > 0

        if enabled_only:
            query &= self.db.iam_providers.enabled is True

        providers = self.db(query).select(orderby=self.db.iam_providers.name)

        return [self._sanitize_provider(p.as_dict()) for p in providers]

    def get_provider(self, provider_id: int) -> Dict[str, Any]:
        """Get provider details."""
        provider = self.db.iam_providers[provider_id]

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
        """Create a new IAM provider."""
        # Validate provider type
        valid_types = ["aws_iam", "gcp_iam", "kubernetes"]
        if provider_type.lower() not in valid_types:
            raise Exception(
                f"Invalid provider type: {provider_type}. Must be one of {valid_types}"
            )

        # Create provider
        now = datetime.now(timezone.utc)
        provider_id = self.db.iam_providers.insert(
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
        """Update provider configuration."""
        provider = self.db.iam_providers[provider_id]

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
            self.db(self.db.iam_providers.id == provider_id).update(**update_data)
            self.db.commit()

        return self.get_provider(provider_id)

    def delete_provider(self, provider_id: int) -> Dict[str, Any]:
        """Delete an IAM provider."""
        provider = self.db.iam_providers[provider_id]

        if not provider:
            raise Exception(f"Provider not found: {provider_id}")

        # Check if provider has registered resources
        users_count = self.db(self.db.iam_users.iam_provider_id == provider_id).count()
        roles_count = self.db(self.db.iam_roles.iam_provider_id == provider_id).count()

        if users_count > 0 or roles_count > 0:
            raise Exception(
                f"Cannot delete provider with {users_count} users and {roles_count} roles. "
                "Delete resources first or use force delete."
            )

        self.db(self.db.iam_providers.id == provider_id).delete()
        self.db.commit()

        return {"message": "Provider deleted successfully"}

    def test_provider(self, provider_id: int) -> Dict[str, Any]:
        """Test provider connectivity."""
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

    def sync_provider(self, provider_id: int) -> Dict[str, Any]:
        """Sync resources from provider to database."""
        try:
            client = self._get_provider_client(provider_id)
            result = client.sync_from_provider()

            # Store sync results in database
            now = datetime.now(timezone.utc)
            self.db.iam_sync_log.insert(
                iam_provider_id=provider_id,
                synced_at=now,
                users_synced=result.get("users_synced", 0),
                roles_synced=result.get("roles_synced", 0),
                policies_synced=result.get("policies_synced", 0),
                errors_json=result.get("errors", []),
                created_at=now,
                updated_at=now,
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

    def list_users(self, provider_id: int, **kwargs) -> List[Dict[str, Any]]:
        """List users from provider."""
        client = self._get_provider_client(provider_id)
        return client.list_users(**kwargs)

    def get_user(self, provider_id: int, user_id: str) -> Dict[str, Any]:
        """Get user details."""
        client = self._get_provider_client(provider_id)
        return client.get_user(user_id)

    def create_user(self, provider_id: int, **kwargs) -> Dict[str, Any]:
        """Create a new user."""
        client = self._get_provider_client(provider_id)
        return client.create_user(**kwargs)

    def update_user(self, provider_id: int, user_id: str, **kwargs) -> Dict[str, Any]:
        """Update user."""
        client = self._get_provider_client(provider_id)
        return client.update_user(user_id, **kwargs)

    def delete_user(self, provider_id: int, user_id: str) -> Dict[str, Any]:
        """Delete user."""
        client = self._get_provider_client(provider_id)
        return client.delete_user(user_id)

    # Role Management

    def list_roles(self, provider_id: int, **kwargs) -> List[Dict[str, Any]]:
        """List roles from provider."""
        client = self._get_provider_client(provider_id)
        return client.list_roles(**kwargs)

    def get_role(self, provider_id: int, role_id: str) -> Dict[str, Any]:
        """Get role details."""
        client = self._get_provider_client(provider_id)
        return client.get_role(role_id)

    def create_role(self, provider_id: int, **kwargs) -> Dict[str, Any]:
        """Create a new role."""
        client = self._get_provider_client(provider_id)
        return client.create_role(**kwargs)

    def update_role(self, provider_id: int, role_id: str, **kwargs) -> Dict[str, Any]:
        """Update role."""
        client = self._get_provider_client(provider_id)
        return client.update_role(role_id, **kwargs)

    def delete_role(self, provider_id: int, role_id: str) -> Dict[str, Any]:
        """Delete role."""
        client = self._get_provider_client(provider_id)
        return client.delete_role(role_id)

    # Policy Management

    def list_policies(self, provider_id: int, **kwargs) -> List[Dict[str, Any]]:
        """List policies from provider."""
        client = self._get_provider_client(provider_id)
        return client.list_policies(**kwargs)

    def get_policy(self, provider_id: int, policy_id: str) -> Dict[str, Any]:
        """Get policy details."""
        client = self._get_provider_client(provider_id)
        return client.get_policy(policy_id)

    def create_policy(self, provider_id: int, **kwargs) -> Dict[str, Any]:
        """Create a new policy."""
        client = self._get_provider_client(provider_id)
        return client.create_policy(**kwargs)

    def delete_policy(self, provider_id: int, policy_id: str) -> Dict[str, Any]:
        """Delete policy."""
        client = self._get_provider_client(provider_id)
        return client.delete_policy(policy_id)

    # Policy Attachments

    def attach_policy_to_user(
        self, provider_id: int, user_id: str, policy_id: str
    ) -> Dict[str, Any]:
        """Attach policy to user."""
        client = self._get_provider_client(provider_id)
        return client.attach_policy_to_user(user_id, policy_id)

    def detach_policy_from_user(
        self, provider_id: int, user_id: str, policy_id: str
    ) -> Dict[str, Any]:
        """Detach policy from user."""
        client = self._get_provider_client(provider_id)
        return client.detach_policy_from_user(user_id, policy_id)

    def attach_policy_to_role(
        self, provider_id: int, role_id: str, policy_id: str
    ) -> Dict[str, Any]:
        """Attach policy to role."""
        client = self._get_provider_client(provider_id)
        return client.attach_policy_to_role(role_id, policy_id)

    def detach_policy_from_role(
        self, provider_id: int, role_id: str, policy_id: str
    ) -> Dict[str, Any]:
        """Detach policy from role."""
        client = self._get_provider_client(provider_id)
        return client.detach_policy_from_role(role_id, policy_id)

    def list_user_policies(
        self, provider_id: int, user_id: str
    ) -> List[Dict[str, Any]]:
        """List policies attached to user."""
        client = self._get_provider_client(provider_id)
        return client.list_user_policies(user_id)

    def list_role_policies(
        self, provider_id: int, role_id: str
    ) -> List[Dict[str, Any]]:
        """List policies attached to role."""
        client = self._get_provider_client(provider_id)
        return client.list_role_policies(role_id)

    # Access Keys

    def create_access_key(self, provider_id: int, user_id: str) -> Dict[str, Any]:
        """Create access key for user."""
        client = self._get_provider_client(provider_id)
        return client.create_access_key(user_id)

    def list_access_keys(self, provider_id: int, user_id: str) -> List[Dict[str, Any]]:
        """List access keys for user."""
        client = self._get_provider_client(provider_id)
        return client.list_access_keys(user_id)

    def delete_access_key(
        self, provider_id: int, user_id: str, key_id: str
    ) -> Dict[str, Any]:
        """Delete access key."""
        client = self._get_provider_client(provider_id)
        return client.delete_access_key(user_id, key_id)

    # Group Management

    def list_groups(self, provider_id: int, **kwargs) -> List[Dict[str, Any]]:
        """List groups from provider."""
        client = self._get_provider_client(provider_id)
        return client.list_groups(**kwargs)

    def create_group(self, provider_id: int, **kwargs) -> Dict[str, Any]:
        """Create a new group."""
        client = self._get_provider_client(provider_id)
        return client.create_group(**kwargs)

    def delete_group(self, provider_id: int, group_id: str) -> Dict[str, Any]:
        """Delete group."""
        client = self._get_provider_client(provider_id)
        return client.delete_group(group_id)

    def add_user_to_group(
        self, provider_id: int, user_id: str, group_id: str
    ) -> Dict[str, Any]:
        """Add user to group."""
        client = self._get_provider_client(provider_id)
        return client.add_user_to_group(user_id, group_id)

    def remove_user_from_group(
        self, provider_id: int, user_id: str, group_id: str
    ) -> Dict[str, Any]:
        """Remove user from group."""
        client = self._get_provider_client(provider_id)
        return client.remove_user_from_group(user_id, group_id)

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
                "session_token",
                "kubeconfig",
            ]

            for field in sensitive_fields:
                if field in config:
                    config[field] = "***REDACTED***"

            sanitized["config_json"] = config

        return sanitized
