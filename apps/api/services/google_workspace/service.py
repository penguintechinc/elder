"""Google Workspace Integration Service for Elder v1.2.0 (Phase 7)."""

# flake8: noqa: E501

import json
from datetime import UTC, datetime, timezone
from typing import Any, Dict, List, Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from penguin_dal import DAL

_REDACTED_SENTINELS = frozenset({"", "***REDACTED***", "***masked***"})
_REQUIRED_SERVICE_ACCOUNT_KEYS = ("type", "project_id", "private_key", "client_email")


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


def _is_redacted_or_empty(value: Any) -> bool:
    """Return True when credential input is empty, None, or a redaction sentinel."""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() in _REDACTED_SENTINELS
    if isinstance(value, dict) and not value:
        return True
    return False


def _parse_service_account_json(raw: Any) -> dict[str, Any]:
    """Validate and normalize a Google service account credential payload."""
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise ValueError("Invalid service account JSON") from exc
    elif isinstance(raw, dict):
        parsed = dict(raw)
    else:
        raise ValueError("service_account_json must be a JSON object or JSON string")

    if not isinstance(parsed, dict):
        raise ValueError("service_account_json must be a JSON object")

    missing = [k for k in _REQUIRED_SERVICE_ACCOUNT_KEYS if not parsed.get(k)]
    if missing:
        raise ValueError(
            f"Invalid service account JSON. Missing keys: {', '.join(missing)}"
        )

    return dict(parsed)


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
        self, organization_id: int | None = None
    ) -> list[dict[str, Any]]:
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

    def get_provider(self, provider_id: int) -> dict[str, Any]:
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
        service_account_json: dict[str, Any] | str,
        description: str | None = None,
        domain: str | None = None,
    ) -> dict[str, Any]:
        """
        Create Google Workspace provider.

        Args:
            name: Provider name
            organization_id: Organization ID
            customer_id: Google Workspace customer ID
            admin_email: Admin user email for delegation
            service_account_json: Service account credentials JSON (dict or JSON string)
            description: Optional description
            domain: Optional workspace domain (defaults from admin_email or customer_id)

        Returns:
            Created provider dictionary
        """
        if not name or not str(name).strip():
            raise ValueError("name is required")
        if organization_id is None:
            raise ValueError("organization_id is required")
        if not customer_id or not str(customer_id).strip():
            raise ValueError("customer_id is required")
        if not admin_email or not str(admin_email).strip():
            raise ValueError("admin_email is required")

        sa_dict = _parse_service_account_json(service_account_json)

        resolved_domain = (domain or "").strip()
        if not resolved_domain:
            if "@" in admin_email:
                resolved_domain = admin_email.split("@", 1)[1].strip()
            else:
                resolved_domain = str(customer_id).strip()

        creds_payload: dict[str, Any] = {
            "service_account_json": sa_dict,
            "customer_id": str(customer_id).strip(),
        }
        if description is not None:
            creds_payload["_description"] = description

        from apps.api.utils.village_id import (
            mint_village_id,
            resolve_tenant_id_for_org,
        )

        tenant_id = resolve_tenant_id_for_org(self.db, organization_id)
        now = datetime.now(UTC)
        provider_id = self.db.google_workspace_providers.insert(
            name=str(name).strip(),
            domain=resolved_domain,
            admin_email=str(admin_email).strip(),
            credentials_json=creds_payload,
            organization_id=organization_id,
            enabled=True,
            village_id=mint_village_id(tenant_id),
            created_at=now,
            updated_at=now,
        )

        self.db.commit()

        provider = self.db.google_workspace_providers[provider_id]
        return self._sanitize_provider(provider.as_dict())

    def update_provider(
        self,
        provider_id: int,
        name: str | None = None,
        customer_id: str | None = None,
        admin_email: str | None = None,
        service_account_json: dict[str, Any] | str | None = None,
        description: str | None = None,
        enabled: bool | None = None,
        domain: str | None = None,
    ) -> dict[str, Any]:
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
            domain: New workspace domain

        Returns:
            Updated provider dictionary

        Raises:
            Exception: If provider not found
        """
        provider = self.db.google_workspace_providers[provider_id]

        if not provider:
            raise Exception(f"Google Workspace provider {provider_id} not found")

        existing_creds = _parse_json_dict(getattr(provider, "credentials_json", None))
        if (
            "service_account_json" not in existing_creds
            and "private_key" in existing_creds
        ):
            merged_creds: dict[str, Any] = {"service_account_json": existing_creds}
        else:
            merged_creds = dict(existing_creds)

        creds_changed = False
        if customer_id is not None:
            merged_creds["customer_id"] = str(customer_id).strip()
            creds_changed = True
        if description is not None:
            merged_creds["_description"] = description
            creds_changed = True
        if service_account_json is not None and not _is_redacted_or_empty(
            service_account_json
        ):
            merged_creds["service_account_json"] = _parse_service_account_json(
                service_account_json
            )
            creds_changed = True

        update_data: dict[str, Any] = {"updated_at": datetime.now(UTC)}

        if name is not None:
            update_data["name"] = str(name).strip()

        if domain is not None:
            update_data["domain"] = str(domain).strip()

        if admin_email is not None:
            update_data["admin_email"] = str(admin_email).strip()

        if enabled is not None:
            update_data["enabled"] = enabled

        if creds_changed:
            update_data["credentials_json"] = merged_creds

        self.db(self.db.google_workspace_providers.id == provider_id).update(
            **update_data
        )
        self.db.commit()

        provider = self.db.google_workspace_providers[provider_id]
        return self._sanitize_provider(provider.as_dict())

    def delete_provider(self, provider_id: int) -> dict[str, str]:
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

    def test_provider(self, provider_id: int) -> dict[str, Any]:
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
            customer_id = self._get_customer_id(provider)

            # Test by listing domains
            results = client.domains().list(customer=customer_id).execute()

            return {
                "success": True,
                "customer_id": customer_id,
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
        self, provider_id: int, domain: str | None = None, limit: int = 100
    ) -> dict[str, Any]:
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

            params = {
                "customer": self._get_customer_id(provider),
                "maxResults": min(limit, 500),
            }

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

    def get_user(self, provider_id: int, user_key: str) -> dict[str, Any]:
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
    ) -> dict[str, Any]:
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
        given_name: str | None = None,
        family_name: str | None = None,
        suspended: bool | None = None,
        org_unit_path: str | None = None,
    ) -> dict[str, Any]:
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

    def delete_user(self, provider_id: int, user_key: str) -> dict[str, str]:
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
        self, provider_id: int, domain: str | None = None, limit: int = 100
    ) -> dict[str, Any]:
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

            params = {
                "customer": self._get_customer_id(provider),
                "maxResults": min(limit, 200),
            }

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

    def get_group(self, provider_id: int, group_key: str) -> dict[str, Any]:
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
        self, provider_id: int, email: str, name: str, description: str | None = None
    ) -> dict[str, Any]:
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

    def delete_group(self, provider_id: int, group_key: str) -> dict[str, str]:
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
    ) -> dict[str, Any]:
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
    ) -> dict[str, Any]:
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
    ) -> dict[str, str]:
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

    def _get_customer_id(self, provider: Any) -> str:
        """Resolve Google Workspace customer_id from credentials_json or fallback."""
        creds = _parse_json_dict(getattr(provider, "credentials_json", None))
        customer_id = creds.get("customer_id") or getattr(provider, "customer_id", None)
        return str(customer_id or "my_customer")

    def _get_workspace_client(self, provider: Any):
        """Alias for _get_directory_client."""
        return self._get_directory_client(provider)

    def _get_directory_client(self, provider: Any):
        """
        Get Google Directory API client.

        Args:
            provider: Provider record

        Returns:
            Directory API client
        """
        raw_creds = getattr(provider, "credentials_json", None)
        if raw_creds is None:
            raw_creds = getattr(provider, "service_account_json", None)

        creds_blob = _parse_json_dict(raw_creds)
        if "service_account_json" in creds_blob:
            inner = creds_blob["service_account_json"]
            credentials_info = (
                _parse_json_dict(inner) if isinstance(inner, (str, dict)) else {}
            )
        else:
            credentials_info = creds_blob

        if not credentials_info:
            raise ValueError("Google Workspace provider has no valid credentials")

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

    def _sanitize_provider(self, provider_dict: dict[str, Any]) -> dict[str, Any]:
        """
        Sanitize provider dictionary (mask credentials and surface safe metadata).

        Args:
            provider_dict: Provider dictionary

        Returns:
            Sanitized provider dictionary
        """
        sanitized = dict(provider_dict)
        creds_blob = _parse_json_dict(sanitized.get("credentials_json"))

        customer_id = sanitized.get("customer_id") or creds_blob.get("customer_id")
        description = (
            sanitized.get("description")
            if "description" in sanitized
            else creds_blob.get("_description")
        )

        sa_info = (
            creds_blob.get("service_account_json")
            if "service_account_json" in creds_blob
            else (creds_blob if "private_key" in creds_blob else None)
        )
        has_credentials = bool(sa_info) or bool(sanitized.get("service_account_json"))

        if (
            "credentials_json" in sanitized
            and sanitized["credentials_json"] is not None
        ):
            sanitized["credentials_json"] = "***REDACTED***"
        if (
            "service_account_json" in sanitized
            and sanitized["service_account_json"] is not None
        ):
            sanitized["service_account_json"] = "***REDACTED***"

        sanitized["customer_id"] = customer_id
        sanitized["description"] = description
        sanitized["has_credentials"] = has_credentials

        return sanitized

    def _format_user(self, user: dict[str, Any]) -> dict[str, Any]:
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

    def _format_group(self, group: dict[str, Any]) -> dict[str, Any]:
        """Format group data for API response."""
        return {
            "id": group.get("id"),
            "email": group.get("email"),
            "name": group.get("name"),
            "description": group.get("description"),
            "direct_members_count": group.get("directMembersCount", 0),
        }
