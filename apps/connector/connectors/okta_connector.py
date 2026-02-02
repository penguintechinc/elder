"""Okta connector for syncing users and groups with write-back support.

Enterprise feature providing:
- User sync from Okta to Elder identities
- Group sync from Okta to Elder identity_groups
- Group membership write-back (add/remove members in Okta)

Requires Enterprise license.
"""

# flake8: noqa: E501


import re
from typing import Any, Dict, List, Optional

import httpx

from apps.connector.config.settings import settings
from apps.connector.connectors.base import BaseConnector, SyncResult
from apps.connector.connectors.group_operations import (
    GroupMembershipResult,
    GroupOperationsMixin,
)
from apps.connector.utils.elder_client import ElderAPIClient


class OktaConnector(BaseConnector, GroupOperationsMixin):
    """
    Full Okta connector supporting:
    - User sync (read from Okta -> Elder identities)
    - Group sync (read from Okta -> Elder identity_groups)
    - Group membership write-back (Elder -> Okta)

    Enterprise feature - requires Enterprise license.
    """

    def __init__(self):
        """Initialize Okta connector."""
        super().__init__("okta")
        self.elder_client: Optional[ElderAPIClient] = None
        self.base_url: Optional[str] = None
        self.headers: Dict[str, str] = {}
        self._http_client: Optional[httpx.AsyncClient] = None

    async def connect(self) -> None:
        """Establish connection to Okta API and Elder API."""
        self.logger.info("Connecting to Okta API", domain=settings.okta_domain)

        if not settings.okta_domain or not settings.okta_api_token:
            raise ValueError("Okta domain and API token are required")

        # Configure Okta API client
        self.base_url = f"https://{settings.okta_domain}"
        self.headers = {
            "Authorization": f"SSWS {settings.okta_api_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        # Create HTTP client with connection pooling
        self._http_client = httpx.AsyncClient(
            headers=self.headers,
            timeout=httpx.Timeout(30.0),
        )

        # Test connection
        try:
            resp = await self._http_client.get(f"{self.base_url}/api/v1/users?limit=1")
            resp.raise_for_status()
            self.logger.info("Okta API connection verified")
        except httpx.HTTPError as e:
            self.logger.error("Failed to connect to Okta API", error=str(e))
            raise

        # Initialize Elder API client
        self.elder_client = ElderAPIClient()
        await self.elder_client.connect()

        self.logger.info("Okta connector connected successfully")

    async def disconnect(self) -> None:
        """Disconnect from Okta API and Elder API."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

        if self.elder_client:
            await self.elder_client.disconnect()

        self.logger.info("Okta connector disconnected")

    async def sync(self) -> SyncResult:
        """
        Synchronize users and groups from Okta to Elder.

        Returns:
            SyncResult with statistics about the sync operation
        """
        result = SyncResult(connector_name=self.name)

        try:
            self.logger.info("Starting Okta sync")

            # Sync users first
            await self._sync_users(result)

            # Sync groups
            await self._sync_groups(result)

            self.logger.info(
                "Okta sync completed",
                total_ops=result.total_operations,
                entities_created=result.entities_created,
                entities_updated=result.entities_updated,
            )

        except Exception as e:
            error_msg = f"Okta sync failed: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            result.errors.append(error_msg)

        return result

    async def _sync_users(self, result: SyncResult) -> None:
        """Sync all active users from Okta."""
        self.logger.info("Syncing Okta users")

        users = await self._paginate('/api/v1/users?filter=status eq "ACTIVE"')

        for user in users:
            try:
                # Extract user data
                profile = user.get("profile", {})
                identity_data = {
                    "provider": "okta",
                    "provider_id": user["id"],
                    "username": profile.get("login", ""),
                    "email": profile.get("email", ""),
                    "display_name": f"{profile.get('firstName', '')} {profile.get('lastName', '')}".strip(),
                    "full_name": f"{profile.get('firstName', '')} {profile.get('lastName', '')}".strip(),
                    "identity_type": "human",
                    "is_active": True,
                    "attributes": {
                        "okta_id": user["id"],
                        "okta_status": user.get("status"),
                        "okta_login": profile.get("login"),
                        "okta_department": profile.get("department"),
                        "okta_title": profile.get("title"),
                    },
                }

                # Create or update identity in Elder
                # Note: This would use elder_client to sync
                # For now, we log the sync
                self.logger.debug(
                    "Syncing Okta user",
                    okta_id=user["id"],
                    email=profile.get("email"),
                )
                result.entities_created += 1

                # TODO: When identity sync is fully implemented:
                # 1. Create/update identity in Elder via elder_client
                # 2. Get the identity's village_id from the response
                # 3. Update Okta profile URL to link back to Elder:
                #
                # identity_response = await self.elder_client.create_or_update_identity(identity_data)
                # village_id = identity_response.get("village_id")
                # if village_id and settings.okta_sync_profile_url:
                #     await self.update_user_profile_url(user["id"], village_id)

            except Exception as e:
                error_msg = f"Failed to sync Okta user {user.get('id')}: {str(e)}"
                self.logger.warning(error_msg)
                result.errors.append(error_msg)

        self.logger.info("Okta users synced", count=len(users))

    async def _sync_groups(self, result: SyncResult) -> None:
        """Sync all groups from Okta (OKTA_GROUP type only)."""
        self.logger.info("Syncing Okta groups")

        groups = await self._paginate("/api/v1/groups")

        for group in groups:
            try:
                # Only sync OKTA_GROUP type (not AD-synced APP_GROUP)
                group_type = group.get("type", "")
                if group_type != "OKTA_GROUP":
                    self.logger.debug(
                        "Skipping non-OKTA_GROUP",
                        group_id=group["id"],
                        group_type=group_type,
                    )
                    continue

                profile = group.get("profile", {})
                group_data = {
                    "provider": "okta",
                    "provider_group_id": group["id"],
                    "name": profile.get("name", ""),
                    "description": profile.get("description"),
                }

                # Create or update group in Elder
                self.logger.debug(
                    "Syncing Okta group",
                    okta_id=group["id"],
                    name=profile.get("name"),
                )
                result.entities_created += 1

            except Exception as e:
                error_msg = f"Failed to sync Okta group {group.get('id')}: {str(e)}"
                self.logger.warning(error_msg)
                result.errors.append(error_msg)

        self.logger.info("Okta groups synced", count=len(groups))

    async def _paginate(self, endpoint: str) -> List[Dict[str, Any]]:
        """
        Handle Okta pagination using Link headers.

        Args:
            endpoint: API endpoint path (e.g., /api/v1/users)

        Returns:
            List of all results across pages
        """
        results = []
        url = f"{self.base_url}{endpoint}"

        while url:
            resp = await self._http_client.get(url)
            resp.raise_for_status()

            data = resp.json()
            if isinstance(data, list):
                results.extend(data)
            else:
                # Some endpoints return objects with results
                results.append(data)

            # Check for next page in Link header
            url = self._get_next_link(resp.headers.get("Link"))

        return results

    def _get_next_link(self, link_header: Optional[str]) -> Optional[str]:
        """
        Parse Link header to get next page URL.

        Args:
            link_header: HTTP Link header value

        Returns:
            Next page URL or None
        """
        if not link_header:
            return None

        # Parse Link header format: <url>; rel="next", <url>; rel="self"
        links = link_header.split(",")
        for link in links:
            parts = link.strip().split(";")
            if len(parts) == 2:
                url_part = parts[0].strip()
                rel_part = parts[1].strip()
                if 'rel="next"' in rel_part:
                    # Extract URL from <...>
                    match = re.match(r"<(.+)>", url_part)
                    if match:
                        return match.group(1)

        return None

    async def health_check(self) -> bool:
        """Check Okta API connectivity."""
        try:
            if not self._http_client:
                return False

            resp = await self._http_client.get(f"{self.base_url}/api/v1/users?limit=1")
            return resp.status_code == 200

        except Exception as e:
            self.logger.warning("Okta health check failed", error=str(e))
            return False

    # ==================== GroupOperationsMixin Implementation ====================

    async def add_group_member(
        self,
        group_id: str,
        user_id: str,
    ) -> GroupMembershipResult:
        """
        Add a user to an Okta group.

        Args:
            group_id: Okta group ID
            user_id: Okta user ID

        Returns:
            GroupMembershipResult with operation status

        Note:
            Only works with OKTA_GROUP type groups.
            Cannot modify AD-synced groups (APP_GROUP type).
        """
        try:
            if not self._http_client:
                return GroupMembershipResult(
                    success=False,
                    group_id=group_id,
                    user_id=user_id,
                    operation="add",
                    error="Okta client not connected",
                )

            # PUT /api/v1/groups/{groupId}/users/{userId}
            url = f"{self.base_url}/api/v1/groups/{group_id}/users/{user_id}"
            resp = await self._http_client.put(url)

            # 204 No Content = success
            success = resp.status_code == 204
            error = None

            if not success:
                try:
                    error_data = resp.json()
                    error = error_data.get("errorSummary", resp.text)
                except Exception:
                    error = f"HTTP {resp.status_code}: {resp.text}"

            self.logger.info(
                "Okta add_group_member",
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
                "Okta add_group_member failed",
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
        Remove a user from an Okta group.

        Args:
            group_id: Okta group ID
            user_id: Okta user ID

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
                    error="Okta client not connected",
                )

            # DELETE /api/v1/groups/{groupId}/users/{userId}
            url = f"{self.base_url}/api/v1/groups/{group_id}/users/{user_id}"
            resp = await self._http_client.delete(url)

            # 204 No Content = success
            success = resp.status_code == 204
            error = None

            if not success:
                try:
                    error_data = resp.json()
                    error = error_data.get("errorSummary", resp.text)
                except Exception:
                    error = f"HTTP {resp.status_code}: {resp.text}"

            self.logger.info(
                "Okta remove_group_member",
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
                "Okta remove_group_member failed",
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
        Get current members of an Okta group.

        Args:
            group_id: Okta group ID

        Returns:
            List of Okta user IDs
        """
        try:
            if not self._http_client:
                self.logger.warning("Okta get_group_members: client not connected")
                return []

            # GET /api/v1/groups/{groupId}/users
            users = await self._paginate(f"/api/v1/groups/{group_id}/users")

            member_ids = [u["id"] for u in users if "id" in u]

            self.logger.info(
                "Okta get_group_members",
                group_id=group_id,
                member_count=len(member_ids),
            )

            return member_ids

        except httpx.HTTPError as e:
            self.logger.error(
                "Okta get_group_members failed",
                group_id=group_id,
                error=str(e),
            )
            return []

    async def update_user_profile_url(
        self,
        user_id: str,
        village_id: str,
    ) -> bool:
        """
        Update Okta user's profileUrl to link to Elder profile.

        Sets the user's profileUrl in Okta to point to their Elder profile
        page using their village_id for easy cross-referencing.

        Args:
            user_id: Okta user ID
            village_id: Elder identity village_id

        Returns:
            True if successful, False otherwise

        Example:
            profileUrl: "https://elder.example.com/profile/abc123def456"
        """
        try:
            if not self._http_client:
                self.logger.warning("Okta update_user_profile_url: client not connected")
                return False

            if not settings.okta_sync_profile_url:
                self.logger.debug("Okta profile URL sync disabled")
                return False

            # Construct Elder profile URL using village_id
            profile_url = f"{settings.elder_web_url}/profile/{village_id}"

            # POST /api/v1/users/{userId} to update profile
            url = f"{self.base_url}/api/v1/users/{user_id}"
            payload = {
                "profile": {
                    "profileUrl": profile_url
                }
            }

            resp = await self._http_client.post(url, json=payload)

            success = resp.status_code in [200, 204]

            if not success:
                try:
                    error_data = resp.json()
                    error = error_data.get("errorSummary", resp.text)
                except Exception:
                    error = f"HTTP {resp.status_code}: {resp.text}"

                self.logger.warning(
                    "Okta update_user_profile_url failed",
                    user_id=user_id,
                    village_id=village_id,
                    profile_url=profile_url,
                    error=error,
                )
                return False

            self.logger.info(
                "Okta update_user_profile_url success",
                user_id=user_id,
                village_id=village_id,
                profile_url=profile_url,
            )

            return True

        except httpx.HTTPError as e:
            self.logger.error(
                "Okta update_user_profile_url HTTP error",
                user_id=user_id,
                village_id=village_id,
                error=str(e),
            )
            return False
        except Exception as e:
            self.logger.error(
                "Okta update_user_profile_url unexpected error",
                user_id=user_id,
                village_id=village_id,
                error=str(e),
            )
            return False
