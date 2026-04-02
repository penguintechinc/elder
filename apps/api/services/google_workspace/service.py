"""Google Workspace Integration Service for Elder v1.2.0 (Phase 7)."""

# flake8: noqa: E501


import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from penguin_dal import DAL


class GoogleWorkspaceService:
    """Service for Google Workspace user and group management."""

    def __init__(self, db: DAL):
        """
        Initialize GoogleWorkspaceService.

        Args:
            db: penguin-dal database instance
        """
        self.db = db

    # ===========================
    # Provider Management
    # ===========================

    def list_providers(
        self, organization_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        List Google Workspace providers.

        Args:
            organization_id: Filter by organization

        Returns:
            List of provider dictionaries
        """
        query = self.db.google_workspace_providers.id > 0

        if organization_id is not None:
            query &= (
                self.db.google_workspace_providers.organization_id == organization_id
            )

        providers = self.db(query).select(
            orderby=self.db.google_workspace_providers.created_at
        )

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
        provider = self.db.google_workspace_providers[provider_id]

        if not provider:
            raise Exception(f"Google Workspace provider {provider_id} not found")

        return self._sanitize_provider(provider.as_dict())

    def create_provider(
        self,
        name: str,
        organization_id: int,
        customer_id: str,
        admin_email: str,
        service_account_json: Dict[str, Any],
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create Google Workspace provider.

        Args:
            name: Provider name
            organization_id: Organization ID
            customer_id: Google Workspace customer ID
            admin_email: Admin user email for delegation
            service_account_json: Service account credentials JSON
            description: Optional description

        Returns:
            Created provider dictionary
        """
        # Validate service account JSON
        required_keys = ["type", "project_id", "private_key", "client_email"]
        missing = [k for k in required_keys if k not in service_account_json]
        if missing:
            raise Exception(
                f"Invalid service account JSON. Missing keys: {', '.join(missing)}"
            )

        provider_id = self.db.google_workspace_providers.insert(
            name=name,
            organization_id=organization_id,
            customer_id=customer_id,
            admin_email=admin_email,
            service_account_json=json.dumps(service_account_json),
            description=description,
            enabled=True,
            created_at=datetime.utcnow(),
        )

        self.db.commit()

        provider = self.db.google_workspace_providers[provider_id]
        return self._sanitize_provider(provider.as_dict())

    def update_provider(
        self,
        provider_id: int,
        name: Optional[str] = None,
        customer_id: Optional[str] = None,
        admin_email: Optional[str] = None,
        service_account_json: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Update provider configuration.

        Args:
            provider_id: Provider ID
            name: New name
            customer_id: New customer ID
            admin_email: New admin email
            service_account_json: New service account credentials
            description: New description
            enabled: New enabled status

        Returns:
            Updated provider dictionary

        Raises:
            Exception: If provider not found
        """
        provider = self.db.google_workspace_providers[provider_id]

        if not provider:
            raise Exception(f"Google Workspace provider {provider_id} not found")

        update_data = {"updated_at": datetime.utcnow()}

        if name is not None:
            update_data["name"] = name

        if customer_id is not None:
            update_data["customer_id"] = customer_id

        if admin_email is not None:
            update_data["admin_email"] = admin_email

        if service_account_json is not None:
            update_data["service_account_json"] = json.dumps(service_account_json)

        if description is not None:
            update_data["description"] = description

        if enabled is not None:
            update_data["enabled"] = enabled

        self.db(self.db.google_workspace_providers.id == provider_id).update(
            **update_data
        )
        self.db.commit()

        provider = self.db.google_workspace_providers[provider_id]
        return self._sanitize_provider(provider.as_dict())

    def delete_provider(self, provider_id: int) -> Dict[str, str]:
        """
        Delete provider.

        Args:
            provider_id: Provider ID

        Returns:
            Success message

        Raises:
            Exception: If provider not found
        """
        provider = self.db.google_workspace_providers[provider_id]

        if not provider:
            raise Exception(f"Google Workspace provider {provider_id} not found")

        self.db(self.db.google_workspace_providers.id == provider_id).delete()
        self.db.commit()

        return {"message": "Google Workspace provider deleted successfully"}

    def test_provider(self, provider_id: int) -> Dict[str, Any]:
        """
        Test provider connectivity.

        Args:
            provider_id: Provider ID

        Returns:
            Test result

        Raises:
            Exception: If provider not found or test fails
        """
        provider = self.db.google_workspace_providers[provider_id]

        if not provider:
            raise Exception(f"Google Workspace provider {provider_id} not found")

        try:
            client = self._get_directory_client(provider)

            # Test by listing domains
            results = client.domains().list(customer=provider.customer_id).execute()

            return {
                "success": True,
                "customer_id": provider.customer_id,
                "domains": results.get("domains", []),
                "message": "Successfully connected to Google Workspace",
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": "Failed to connect to Google Workspace",
            }

    # ===========================
    # User Management
    # ===========================

    def list_users(
        self, provider_id: int, domain: Optional[str] = None, limit: int = 100
    ) -> Dict[str, Any]:
        """
        List Google Workspace users.

        Args:
            provider_id: Provider ID
            domain: Filter by domain
            limit: Maximum results

        Returns:
            Users list with metadata
        """
        provider = self.db.google_workspace_providers[provider_id]

        if not provider:
            raise Exception(f"Google Workspace provider {provider_id} not found")

        try:
            client = self._get_directory_client(provider)

            params = {"customer": provider.customer_id, "maxResults": min(limit, 500)}

            if domain:
                params["domain"] = domain

            results = client.users().list(**params).execute()

            users = results.get("users", [])

            return {
                "users": [self._format_user(u) for u in users],
                "count": len(users),
                "domain": domain,
            }

        except HttpError as e:
            raise Exception(f"Failed to list users: {str(e)}")

    def get_user(self, provider_id: int, user_key: str) -> Dict[str, Any]:
        """
        Get user details.

        Args:
            provider_id: Provider ID
            user_key: User email or ID

        Returns:
            User details

        Raises:
            Exception: If provider or user not found
        """
        provider = self.db.google_workspace_providers[provider_id]

        if not provider:
            raise Exception(f"Google Workspace provider {provider_id} not found")

        try:
            client = self._get_directory_client(provider)
            user = client.users().get(userKey=user_key).execute()

            return self._format_user(user)

        except HttpError as e:
            if e.resp.status == 404:
                raise Exception(f"User {user_key} not found")
            raise Exception(f"Failed to get user: {str(e)}")

    def create_user(
        self,
        provider_id: int,
        primary_email: str,
        given_name: str,
        family_name: str,
        password: str,
        org_unit_path: str = "/",
    ) -> Dict[str, Any]:
        """
        Create Google Workspace user.

        Args:
            provider_id: Provider ID
            primary_email: User's primary email
            given_name: User's first name
            family_name: User's last name
            password: User's password
            org_unit_path: Organizational unit path

        Returns:
            Created user details
        """
        provider = self.db.google_workspace_providers[provider_id]

        if not provider:
            raise Exception(f"Google Workspace provider {provider_id} not found")

        try:
            client = self._get_directory_client(provider)

            user_body = {
                "primaryEmail": primary_email,
                "name": {"givenName": given_name, "familyName": family_name},
                "password": password,
                "orgUnitPath": org_unit_path,
            }

            user = client.users().insert(body=user_body).execute()

            return self._format_user(user)

        except HttpError as e:
            raise Exception(f"Failed to create user: {str(e)}")

    def update_user(
        self,
        provider_id: int,
        user_key: str,
        given_name: Optional[str] = None,
        family_name: Optional[str] = None,
        suspended: Optional[bool] = None,
        org_unit_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Update user details.

        Args:
            provider_id: Provider ID
            user_key: User email or ID
            given_name: New first name
            family_name: New last name
            suspended: Suspend/unsuspend user
            org_unit_path: New organizational unit

        Returns:
            Updated user details
        """
        provider = self.db.google_workspace_providers[provider_id]

        if not provider:
            raise Exception(f"Google Workspace provider {provider_id} not found")

        try:
            client = self._get_directory_client(provider)

            user_body = {}

            if given_name is not None or family_name is not None:
                user_body["name"] = {}
                if given_name is not None:
                    user_body["name"]["givenName"] = given_name
                if family_name is not None:
                    user_body["name"]["familyName"] = family_name

            if suspended is not None:
                user_body["suspended"] = suspended

            if org_unit_path is not None:
                user_body["orgUnitPath"] = org_unit_path

            user = client.users().update(userKey=user_key, body=user_body).execute()

            return self._format_user(user)

        except HttpError as e:
            raise Exception(f"Failed to update user: {str(e)}")

    def delete_user(self, provider_id: int, user_key: str) -> Dict[str, str]:
        """
        Delete Google Workspace user.

        Args:
            provider_id: Provider ID
            user_key: User email or ID

        Returns:
            Success message
        """
        provider = self.db.google_workspace_providers[provider_id]

        if not provider:
            raise Exception(f"Google Workspace provider {provider_id} not found")

        try:
            client = self._get_directory_client(provider)
            client.users().delete(userKey=user_key).execute()

            return {"message": f"User {user_key} deleted successfully"}

        except HttpError as e:
            raise Exception(f"Failed to delete user: {str(e)}")

    # ===========================
    # Group Management
    # ===========================

    def list_groups(
        self, provider_id: int, domain: Optional[str] = None, limit: int = 100
    ) -> Dict[str, Any]:
        """
        List Google Workspace groups.

        Args:
            provider_id: Provider ID
            domain: Filter by domain
            limit: Maximum results

        Returns:
            Groups list with metadata
        """
        provider = self.db.google_workspace_providers[provider_id]

        if not provider:
            raise Exception(f"Google Workspace provider {provider_id} not found")

        try:
            client = self._get_directory_client(provider)

            params = {"customer": provider.customer_id, "maxResults": min(limit, 200)}

            if domain:
                params["domain"] = domain

            results = client.groups().list(**params).execute()

            groups = results.get("groups", [])

            return {
                "groups": [self._format_group(g) for g in groups],
                "count": len(groups),
                "domain": domain,
            }

        except HttpError as e:
            raise Exception(f"Failed to list groups: {str(e)}")

    def get_group(self, provider_id: int, group_key: str) -> Dict[str, Any]:
        """
        Get group details.

        Args:
            provider_id: Provider ID
            group_key: Group email or ID

        Returns:
            Group details
        """
        provider = self.db.google_workspace_providers[provider_id]

        if not provider:
            raise Exception(f"Google Workspace provider {provider_id} not found")

        try:
            client = self._get_directory_client(provider)
            group = client.groups().get(groupKey=group_key).execute()

            return self._format_group(group)

        except HttpError as e:
            if e.resp.status == 404:
                raise Exception(f"Group {group_key} not found")
            raise Exception(f"Failed to get group: {str(e)}")

    def create_group(
        self, provider_id: int, email: str, name: str, description: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create Google Workspace group.

        Args:
            provider_id: Provider ID
            email: Group email address
            name: Group name
            description: Group description

        Returns:
            Created group details
        """
        provider = self.db.google_workspace_providers[provider_id]

        if not provider:
            raise Exception(f"Google Workspace provider {provider_id} not found")

        try:
            client = self._get_directory_client(provider)

            group_body = {"email": email, "name": name}

            if description:
                group_body["description"] = description

            group = client.groups().insert(body=group_body).execute()

            return self._format_group(group)

        except HttpError as e:
            raise Exception(f"Failed to create group: {str(e)}")

    def delete_group(self, provider_id: int, group_key: str) -> Dict[str, str]:
        """
        Delete Google Workspace group.

        Args:
            provider_id: Provider ID
            group_key: Group email or ID

        Returns:
            Success message
        """
        provider = self.db.google_workspace_providers[provider_id]

        if not provider:
            raise Exception(f"Google Workspace provider {provider_id} not found")

        try:
            client = self._get_directory_client(provider)
            client.groups().delete(groupKey=group_key).execute()

            return {"message": f"Group {group_key} deleted successfully"}

        except HttpError as e:
            raise Exception(f"Failed to delete group: {str(e)}")

    def list_group_members(
        self, provider_id: int, group_key: str, limit: int = 100
    ) -> Dict[str, Any]:
        """
        List group members.

        Args:
            provider_id: Provider ID
            group_key: Group email or ID
            limit: Maximum results

        Returns:
            Members list
        """
        provider = self.db.google_workspace_providers[provider_id]

        if not provider:
            raise Exception(f"Google Workspace provider {provider_id} not found")

        try:
            client = self._get_directory_client(provider)

            results = (
                client.members()
                .list(groupKey=group_key, maxResults=min(limit, 200))
                .execute()
            )

            members = results.get("members", [])

            return {"members": members, "count": len(members), "group_key": group_key}

        except HttpError as e:
            raise Exception(f"Failed to list group members: {str(e)}")

    def add_group_member(
        self, provider_id: int, group_key: str, member_email: str, role: str = "MEMBER"
    ) -> Dict[str, Any]:
        """
        Add member to group.

        Args:
            provider_id: Provider ID
            group_key: Group email or ID
            member_email: Member email address
            role: Member role (MEMBER, MANAGER, OWNER)

        Returns:
            Member details
        """
        provider = self.db.google_workspace_providers[provider_id]

        if not provider:
            raise Exception(f"Google Workspace provider {provider_id} not found")

        try:
            client = self._get_directory_client(provider)

            member_body = {"email": member_email, "role": role}

            member = (
                client.members().insert(groupKey=group_key, body=member_body).execute()
            )

            return member

        except HttpError as e:
            raise Exception(f"Failed to add group member: {str(e)}")

    def remove_group_member(
        self, provider_id: int, group_key: str, member_email: str
    ) -> Dict[str, str]:
        """
        Remove member from group.

        Args:
            provider_id: Provider ID
            group_key: Group email or ID
            member_email: Member email address

        Returns:
            Success message
        """
        provider = self.db.google_workspace_providers[provider_id]

        if not provider:
            raise Exception(f"Google Workspace provider {provider_id} not found")

        try:
            client = self._get_directory_client(provider)
            client.members().delete(
                groupKey=group_key, memberKey=member_email
            ).execute()

            return {"message": f"Member {member_email} removed from group {group_key}"}

        except HttpError as e:
            raise Exception(f"Failed to remove group member: {str(e)}")

    # ===========================
    # Helper Methods
    # ===========================

    def _get_directory_client(self, provider: Any):
        """
        Get Google Directory API client.

        Args:
            provider: Provider record

        Returns:
            Directory API client
        """
        credentials_info = json.loads(provider.service_account_json)

        credentials = service_account.Credentials.from_service_account_info(
            credentials_info,
            scopes=[
                "https://www.googleapis.com/auth/admin.directory.user",
                "https://www.googleapis.com/auth/admin.directory.group",
            ],
        )

        # Delegate to admin user
        delegated_credentials = credentials.with_subject(provider.admin_email)

        return build("admin", "directory_v1", credentials=delegated_credentials)

    def _sanitize_provider(self, provider_dict: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sanitize provider dictionary (mask credentials).

        Args:
            provider_dict: Provider dictionary

        Returns:
            Sanitized provider dictionary
        """
        if "service_account_json" in provider_dict:
            provider_dict["service_account_json"] = "***masked***"

        return provider_dict

    def _format_user(self, user: Dict[str, Any]) -> Dict[str, Any]:
        """Format user data for API response."""
        return {
            "id": user.get("id"),
            "primary_email": user.get("primaryEmail"),
            "name": user.get("name", {}),
            "suspended": user.get("suspended", False),
            "org_unit_path": user.get("orgUnitPath"),
            "is_admin": user.get("isAdmin", False),
            "creation_time": user.get("creationTime"),
            "last_login_time": user.get("lastLoginTime"),
        }

    def _format_group(self, group: Dict[str, Any]) -> Dict[str, Any]:
        """Format group data for API response."""
        return {
            "id": group.get("id"),
            "email": group.get("email"),
            "name": group.get("name"),
            "description": group.get("description"),
            "direct_members_count": group.get("directMembersCount", 0),
        }
