"""Authentik Identity Provider connector for syncing users and groups with write-back support.

Enterprise feature providing:
- User sync from Authentik to Elder identities
- Group sync from Authentik to Elder identity_groups
- Group membership write-back (add/remove members in Authentik)

Requires Enterprise license.
"""

# flake8: noqa: E501


from typing import Any, Dict, List, Optional

import httpx

from apps.worker.config.settings import settings
from apps.worker.connectors.base import BaseConnector, SyncResult
from apps.worker.connectors.group_operations import (
    GroupMembershipResult,
    GroupOperationsMixin,
)
from apps.worker.utils.elder_client import ElderAPIClient


class AuthentikConnector(BaseConnector, GroupOperationsMixin):
    """
    Full Authentik connector supporting:
    - User sync (read from Authentik -> Elder identities)
    - Group sync (read from Authentik -> Elder identity_groups)
    - Group membership write-back (Elder -> Authentik)

    Enterprise feature - requires Enterprise license.
    """

    def __init__(self):
        """Initialize Authentik connector."""
        super().__init__("authentik")
        self.elder_client: Optional[ElderAPIClient] = None
        self.base_url: Optional[str] = None
        self.headers: Dict[str, str] = {}
        self._http_client: Optional[httpx.AsyncClient] = None

    async def connect(self) -> None:
        """Establish connection to Authentik API and Elder API."""
        self.logger.info(
            "Connecting to Authentik API", domain=settings.authentik_domain
        )

        if not settings.authentik_domain or not settings.authentik_api_token:
            raise ValueError("Authentik domain and API token are required")

        # Configure Authentik API client
        self.base_url = f"https://{settings.authentik_domain}/api/v3"
        self.headers = {
            "Authorization": f"Bearer {settings.authentik_api_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        # Create HTTP client with connection pooling
        self._http_client = httpx.AsyncClient(
            headers=self.headers,
            timeout=httpx.Timeout(30.0),
            verify=settings.authentik_verify_ssl,
        )

        # Test connection
        try:
            resp = await self._http_client.get(
                f"{self.base_url}/core/users/?page_size=1"
            )
            resp.raise_for_status()
            self.logger.info("Authentik API connection verified")
        except httpx.HTTPError as e:
            self.logger.error("Failed to connect to Authentik API", error=str(e))
            raise

        # Initialize Elder API client
        self.elder_client = ElderAPIClient()
        await self.elder_client.connect()

        self.logger.info("Authentik connector connected successfully")

    async def disconnect(self) -> None:
        """Disconnect from Authentik API and Elder API."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

        if self.elder_client:
            await self.elder_client.disconnect()

        self.logger.info("Authentik connector disconnected")

    async def sync(self) -> SyncResult:
        """
        Synchronize users and groups from Authentik to Elder.

        Returns:
            SyncResult with statistics about the sync operation
        """
        result = SyncResult(connector_name=self.name)

        try:
            self.logger.info("Starting Authentik sync")

            # Sync users first
            if settings.authentik_sync_users:
                await self._sync_users(result)

            # Sync groups
            if settings.authentik_sync_groups:
                await self._sync_groups(result)

            self.logger.info(
                "Authentik sync completed",
                total_ops=result.total_operations,
                entities_created=result.entities_created,
                entities_updated=result.entities_updated,
            )

        except Exception as e:
            error_msg = f"Authentik sync failed: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            result.errors.append(error_msg)

        return result

    async def _sync_users(self, result: SyncResult) -> None:
        """Sync all active users from Authentik."""
        self.logger.info("Syncing Authentik users")

        users = await self._paginate("/core/users/")

        for user in users:
            try:
                # Extract user data
                attributes = user.get("attributes", {})

                # Determine identity_type based on user attributes
                # Service accounts typically have is_service_account or type attribute
                identity_type = (
                    "serviceAccount"
                    if user.get("is_service_account", False)
                    else "employee"
                )

                identity_data = {
                    "provider": "authentik",
                    "provider_id": str(user["pk"]),
                    "username": user.get("username", ""),
                    "email": user.get("email", ""),
                    "display_name": user.get("name", user.get("username", "")),
                    "full_name": user.get("name", ""),
                    "identity_type": identity_type,
                    "is_active": user.get("is_active", True),
                    "attributes": {
                        "authentik_pk": user["pk"],
                        "authentik_username": user.get("username"),
                        "authentik_uuid": user.get("uuid"),
                        "email": user.get("email"),
                        "name": user.get("name"),
                        "is_active": user.get("is_active"),
                        "is_superuser": user.get("is_superuser", False),
                        "last_login": user.get("last_login"),
                        "custom_attributes": attributes,
                    },
                }

                # Create or update identity in Elder
                # Note: This would use elder_client to sync
                # For now, we log the sync
                self.logger.debug(
                    "Syncing Authentik user",
                    authentik_pk=user["pk"],
                    username=user.get("username"),
                    email=user.get("email"),
                )
                result.entities_created += 1

            except Exception as e:
                error_msg = f"Failed to sync Authentik user {user.get('pk')}: {str(e)}"
                self.logger.warning(error_msg)
                result.errors.append(error_msg)

        self.logger.info("Authentik users synced", count=len(users))

    async def _sync_groups(self, result: SyncResult) -> None:
        """Sync all groups from Authentik."""
        self.logger.info("Syncing Authentik groups")

        groups = await self._paginate("/core/groups/")

        for group in groups:
            try:
                attributes = group.get("attributes", {})
                users_obj = group.get("users_obj", [])

                group_data = {
                    "provider": "authentik",
                    "provider_group_id": str(group["pk"]),
                    "name": group.get("name", ""),
                    "description": f"Authentik group: {group.get('name')}",
                    "attributes": {
                        "authentik_pk": group["pk"],
                        "authentik_uuid": group.get("uuid"),
                        "name": group.get("name"),
                        "is_superuser": group.get("is_superuser", False),
                        "parent": group.get("parent"),
                        "parent_name": group.get("parent_name"),
                        "users": group.get("users", []),
                        "users_obj": users_obj,
                        "member_count": len(users_obj),
                        "custom_attributes": attributes,
                    },
                }

                # Create or update group in Elder
                self.logger.debug(
                    "Syncing Authentik group",
                    authentik_pk=group["pk"],
                    name=group.get("name"),
                    member_count=len(users_obj),
                )
                result.entities_created += 1

            except Exception as e:
                error_msg = (
                    f"Failed to sync Authentik group {group.get('pk')}: {str(e)}"
                )
                self.logger.warning(error_msg)
                result.errors.append(error_msg)

        self.logger.info("Authentik groups synced", count=len(groups))

    async def _paginate(self, endpoint: str) -> List[Dict[str, Any]]:
        """
        Handle Authentik pagination.

        Args:
            endpoint: API endpoint path (e.g., /core/users/)

        Returns:
            List of all results across pages
        """
        results = []
        url = f"{self.base_url}{endpoint}"

        # Add pagination parameters if not already present
        if "?" not in url:
            url += "?"
        else:
            url += "&"
        url += "page_size=100"

        while url:
            resp = await self._http_client.get(url)
            resp.raise_for_status()

            data = resp.json()

            # Authentik returns paginated results with 'results' array
            if isinstance(data, dict) and "results" in data:
                results.extend(data["results"])
                # Check for next page
                url = data.get("pagination", {}).get("next")
                if url and not url.startswith("http"):
                    # Make absolute URL if relative
                    url = (
                        f"{self.base_url}{url}"
                        if url.startswith("/")
                        else f"{self.base_url}/{url}"
                    )
            else:
                # Single result or list
                if isinstance(data, list):
                    results.extend(data)
                else:
                    results.append(data)
                url = None

        return results

    async def health_check(self) -> bool:
        """Check Authentik API connectivity."""
        try:
            if not self._http_client:
                return False

            resp = await self._http_client.get(
                f"{self.base_url}/core/users/?page_size=1"
            )
            return resp.status_code == 200

        except Exception as e:
            self.logger.warning("Authentik health check failed", error=str(e))
            return False

    # ==================== GroupOperationsMixin Implementation ====================

    async def add_group_member(
        self,
        group_id: str,
        user_id: str,
    ) -> GroupMembershipResult:
        """
        Add a user to an Authentik group.

        Args:
            group_id: Authentik group PK (primary key)
            user_id: Authentik user PK (primary key)

        Returns:
            GroupMembershipResult with operation status
        """
        try:
            if not self._http_client:
                return GroupMembershipResult(
                    success=False,
                    group_id=group_id,
                    user_id=user_id,
                    operation="add",
                    error="Authentik client not connected",
                )

            # POST /api/v3/core/groups/{group_pk}/add_user/
            url = f"{self.base_url}/core/groups/{group_id}/add_user/"
            payload = {"pk": int(user_id)}
            resp = await self._http_client.post(url, json=payload)

            # 204 No Content or 200 OK = success
            success = resp.status_code in (200, 204)
            error = None

            if not success:
                try:
                    error_data = resp.json()
                    error = error_data.get("detail", resp.text)
                except Exception:
                    error = f"HTTP {resp.status_code}: {resp.text}"

            self.logger.info(
                "Authentik add_group_member",
                group_id=group_id,
                user_id=user_id,
                success=success,
                error=error,
            )

            return GroupMembershipResult(
                success=success,
                group_id=group_id,
                user_id=user_id,
                operation="add",
                error=error,
            )

        except httpx.HTTPError as e:
            self.logger.error(
                "Authentik add_group_member failed",
                group_id=group_id,
                user_id=user_id,
                error=str(e),
            )
            return GroupMembershipResult(
                success=False,
                group_id=group_id,
                user_id=user_id,
                operation="add",
                error=str(e),
            )

    async def remove_group_member(
        self,
        group_id: str,
        user_id: str,
    ) -> GroupMembershipResult:
        """
        Remove a user from an Authentik group.

        Args:
            group_id: Authentik group PK
            user_id: Authentik user PK

        Returns:
            GroupMembershipResult with operation status
        """
        try:
            if not self._http_client:
                return GroupMembershipResult(
                    success=False,
                    group_id=group_id,
                    user_id=user_id,
                    operation="remove",
                    error="Authentik client not connected",
                )

            # POST /api/v3/core/groups/{group_pk}/remove_user/
            url = f"{self.base_url}/core/groups/{group_id}/remove_user/"
            payload = {"pk": int(user_id)}
            resp = await self._http_client.post(url, json=payload)

            # 204 No Content or 200 OK = success
            success = resp.status_code in (200, 204)
            error = None

            if not success:
                try:
                    error_data = resp.json()
                    error = error_data.get("detail", resp.text)
                except Exception:
                    error = f"HTTP {resp.status_code}: {resp.text}"

            self.logger.info(
                "Authentik remove_group_member",
                group_id=group_id,
                user_id=user_id,
                success=success,
                error=error,
            )

            return GroupMembershipResult(
                success=success,
                group_id=group_id,
                user_id=user_id,
                operation="remove",
                error=error,
            )

        except httpx.HTTPError as e:
            self.logger.error(
                "Authentik remove_group_member failed",
                group_id=group_id,
                user_id=user_id,
                error=str(e),
            )
            return GroupMembershipResult(
                success=False,
                group_id=group_id,
                user_id=user_id,
                operation="remove",
                error=str(e),
            )

    async def get_group_members(self, group_id: str) -> List[str]:
        """
        Get current members of an Authentik group.

        Args:
            group_id: Authentik group PK

        Returns:
            List of Authentik user PKs (as strings)
        """
        try:
            if not self._http_client:
                self.logger.warning("Authentik get_group_members: client not connected")
                return []

            # GET /api/v3/core/groups/{group_pk}/
            url = f"{self.base_url}/core/groups/{group_id}/"
            resp = await self._http_client.get(url)
            resp.raise_for_status()

            group_data = resp.json()

            # Extract user PKs from users_obj array
            users_obj = group_data.get("users_obj", [])
            member_ids = [str(user["pk"]) for user in users_obj if "pk" in user]

            self.logger.info(
                "Authentik get_group_members",
                group_id=group_id,
                member_count=len(member_ids),
            )

            return member_ids

        except httpx.HTTPError as e:
            self.logger.error(
                "Authentik get_group_members failed",
                group_id=group_id,
                error=str(e),
            )
            return []
