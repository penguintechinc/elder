"""Azure AD (Microsoft Entra ID) client for identity and access management."""

# flake8: noqa: E501

import logging
from typing import Any, Dict, Optional

try:
    from azure.identity import ClientSecretCredential
    from msgraph import GraphServiceClient
except ImportError:
    GraphServiceClient = None

from apps.api.services.iam.base import BaseIAMProvider

logger = logging.getLogger(__name__)


class AzureADClient(BaseIAMProvider):
    """Azure AD (Microsoft Entra ID) IAM implementation."""

    def __init__(self, config: dict[str, Any]):
        """
        Initialize Azure AD client.

        Args:
            config: Configuration dictionary with:
                - tenant_id: Azure AD tenant ID
                - client_id: Application (client) ID
                - client_secret: Client secret
                - authority: Optional authority URL (default: login.microsoftonline.com)
        """
        super().__init__(config)

        if GraphServiceClient is None:
            raise ImportError(
                "Microsoft Graph SDK required for Azure AD. Install with: pip install msgraph-sdk azure-identity"
            )

        self.tenant_id = config.get("tenant_id")
        self.client_id = config.get("client_id")
        self.client_secret = config.get("client_secret")

        if not all([self.tenant_id, self.client_id, self.client_secret]):
            raise ValueError(
                "Azure AD requires tenant_id, client_id, and client_secret"
            )

        # Create credential and Graph client
        self.credential = ClientSecretCredential(
            tenant_id=self.tenant_id,
            client_id=self.client_id,
            client_secret=self.client_secret,
        )

        self.client = GraphServiceClient(credentials=self.credential)

        logger.info(f"Initialized Azure AD client for tenant {self.tenant_id}")

    # User Management

    def list_users(
        self, limit: int | None = None, next_token: str | None = None
    ) -> dict[str, Any]:
        """List all Azure AD users."""
        try:
            request = self.client.users.get()

            if limit:
                request.top = min(limit, 999)  # Microsoft Graph max is 999

            if next_token:
                request.skip_token = next_token

            result = request

            users = [self._normalize_user(user) for user in result.value or []]

            return {
                "users": users,
                "next_token": (
                    result.odata_next_link
                    if hasattr(result, "odata_next_link")
                    else None
                ),
            }

        except Exception as e:
            logger.error(f"Azure AD list users error: {str(e)}")
            raise Exception(f"Azure AD list users error: {str(e)}")

    def get_user(self, user_identifier: str) -> dict[str, Any]:
        """Get Azure AD user details."""
        try:
            user = self.client.users.by_user_id(user_identifier).get()

            return self._normalize_user(user)

        except Exception as e:
            logger.error(f"Azure AD get user error: {str(e)}")
            raise Exception(f"Azure AD get user error: {str(e)}")

    def create_user(
        self,
        username: str,
        display_name: str | None = None,
        tags: dict[str, str] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Create a new Azure AD user."""
        try:
            from msgraph.generated.models.password_profile import PasswordProfile
            from msgraph.generated.models.user import User

            user = User()
            user.user_principal_name = username
            user.display_name = display_name or username
            user.mail_nickname = username.split("@")[0]

            # Set temporary password
            password_profile = PasswordProfile()
            password_profile.force_change_password_next_sign_in = True
            password_profile.password = kwargs.get("password", "TempPass123!")
            user.password_profile = password_profile

            user.account_enabled = kwargs.get("enabled", True)

            created_user = self.client.users.post(user)

            logger.info(f"Created Azure AD user: {username}")

            return self._normalize_user(created_user)

        except Exception as e:
            logger.error(f"Azure AD create user error: {str(e)}")
            raise Exception(f"Azure AD create user error: {str(e)}")

    def delete_user(self, user_identifier: str) -> dict[str, Any]:
        """Delete an Azure AD user."""
        try:
            self.client.users.by_user_id(user_identifier).delete()

            logger.info(f"Deleted Azure AD user: {user_identifier}")

            return {"success": True, "user_id": user_identifier}

        except Exception as e:
            logger.error(f"Azure AD delete user error: {str(e)}")
            raise Exception(f"Azure AD delete user error: {str(e)}")

    def update_user(
        self,
        user_identifier: str,
        display_name: str | None = None,
        tags: dict[str, str] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Update Azure AD user."""
        try:
            from msgraph.generated.models.user import User

            user = User()

            if display_name:
                user.display_name = display_name

            if kwargs.get("enabled") is not None:
                user.account_enabled = kwargs["enabled"]

            self.client.users.by_user_id(user_identifier).patch(user)

            logger.info(f"Updated Azure AD user: {user_identifier}")

            return self.get_user(user_identifier)

        except Exception as e:
            logger.error(f"Azure AD update user error: {str(e)}")
            raise Exception(f"Azure AD update user error: {str(e)}")

    # Role Management (Groups in Azure AD)

    def list_roles(
        self, limit: int | None = None, next_token: str | None = None
    ) -> dict[str, Any]:
        """List all Azure AD groups (roles)."""
        try:
            request = self.client.groups.get()

            if limit:
                request.top = min(limit, 999)

            if next_token:
                request.skip_token = next_token

            result = request

            roles = [self._normalize_role(group) for group in result.value or []]

            return {
                "roles": roles,
                "next_token": (
                    result.odata_next_link
                    if hasattr(result, "odata_next_link")
                    else None
                ),
            }

        except Exception as e:
            logger.error(f"Azure AD list roles error: {str(e)}")
            raise Exception(f"Azure AD list roles error: {str(e)}")

    def get_role(self, role_identifier: str) -> dict[str, Any]:
        """Get Azure AD group (role) details."""
        try:
            group = self.client.groups.by_group_id(role_identifier).get()

            return self._normalize_role(group)

        except Exception as e:
            logger.error(f"Azure AD get role error: {str(e)}")
            raise Exception(f"Azure AD get role error: {str(e)}")

    def create_role(
        self,
        role_name: str,
        description: str | None = None,
        tags: dict[str, str] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Create a new Azure AD group (role)."""
        try:
            from msgraph.generated.models.group import Group

            group = Group()
            group.display_name = role_name
            group.mail_nickname = role_name.replace(" ", "_").lower()
            group.mail_enabled = False
            group.security_enabled = True
            group.description = description

            created_group = self.client.groups.post(group)

            logger.info(f"Created Azure AD group: {role_name}")

            return self._normalize_role(created_group)

        except Exception as e:
            logger.error(f"Azure AD create role error: {str(e)}")
            raise Exception(f"Azure AD create role error: {str(e)}")

    def delete_role(self, role_identifier: str) -> dict[str, Any]:
        """Delete an Azure AD group (role)."""
        try:
            self.client.groups.by_group_id(role_identifier).delete()

            logger.info(f"Deleted Azure AD group: {role_identifier}")

            return {"success": True, "role_id": role_identifier}

        except Exception as e:
            logger.error(f"Azure AD delete role error: {str(e)}")
            raise Exception(f"Azure AD delete role error: {str(e)}")

    # Role Assignment

    def assign_role_to_user(
        self, user_identifier: str, role_identifier: str
    ) -> dict[str, Any]:
        """Add user to Azure AD group (role assignment)."""
        try:
            from msgraph.generated.models.reference_create import ReferenceCreate

            reference = ReferenceCreate()
            reference.odata_id = (
                f"https://graph.microsoft.com/v1.0/users/{user_identifier}"
            )

            self.client.groups.by_group_id(role_identifier).members.ref.post(reference)

            logger.info(
                f"Assigned Azure AD user {user_identifier} to group {role_identifier}"
            )

            return {
                "success": True,
                "user_id": user_identifier,
                "role_id": role_identifier,
            }

        except Exception as e:
            logger.error(f"Azure AD assign role error: {str(e)}")
            raise Exception(f"Azure AD assign role error: {str(e)}")

    def remove_role_from_user(
        self, user_identifier: str, role_identifier: str
    ) -> dict[str, Any]:
        """Remove user from Azure AD group."""
        try:
            self.client.groups.by_group_id(
                role_identifier
            ).members.by_directory_object_id(user_identifier).ref.delete()

            logger.info(
                f"Removed Azure AD user {user_identifier} from group {role_identifier}"
            )

            return {
                "success": True,
                "user_id": user_identifier,
                "role_id": role_identifier,
            }

        except Exception as e:
            logger.error(f"Azure AD remove role error: {str(e)}")
            raise Exception(f"Azure AD remove role error: {str(e)}")

    def list_user_roles(self, user_identifier: str) -> dict[str, Any]:
        """List groups (roles) for a user."""
        try:
            groups = self.client.users.by_user_id(user_identifier).member_of.get()

            roles = [self._normalize_role(group) for group in groups.value or []]

            return {"roles": roles}

        except Exception as e:
            logger.error(f"Azure AD list user roles error: {str(e)}")
            raise Exception(f"Azure AD list user roles error: {str(e)}")

    # Helper methods

    def _normalize_user(self, user: Any) -> dict[str, Any]:
        """Normalize Azure AD user to common format."""
        return {
            "id": user.id,
            "username": user.user_principal_name,
            "display_name": user.display_name,
            "email": user.mail or user.user_principal_name,
            "enabled": user.account_enabled,
            "created_at": (
                user.created_date_time.isoformat() if user.created_date_time else None
            ),
            "provider": "azure_ad",
        }

    def _normalize_role(self, group: Any) -> dict[str, Any]:
        """Normalize Azure AD group to role format."""
        return {
            "id": group.id,
            "name": group.display_name,
            "description": group.description or "",
            "created_at": (
                group.created_date_time.isoformat() if group.created_date_time else None
            ),
            "provider": "azure_ad",
        }

    def test_connection(self) -> bool:
        """Test Azure AD connection."""
        try:
            # Try to list users with limit 1
            self.list_users(limit=1)
            logger.info("Azure AD connection test successful")
            return True
        except Exception as e:
            logger.error(f"Azure AD connection test failed: {str(e)}")
            return False
