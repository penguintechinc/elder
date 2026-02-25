"""Google Workspace connector for syncing users, groups, and org units to Elder."""

# flake8: noqa: E501


from typing import Dict, Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from apps.worker.config.settings import settings
from apps.worker.connectors.base import BaseConnector, SyncResult
from apps.worker.utils.elder_client import ElderAPIClient, Entity, Organization


class GoogleWorkspaceConnector(BaseConnector):
    """Connector for Google Workspace resources."""

    SCOPES = [
        "https://www.googleapis.com/auth/admin.directory.user.readonly",
        "https://www.googleapis.com/auth/admin.directory.group.readonly",
        "https://www.googleapis.com/auth/admin.directory.orgunit.readonly",
    ]

    def __init__(self):
        """Initialize Google Workspace connector."""
        super().__init__("google_workspace")
        self.elder_client: Optional[ElderAPIClient] = None
        self.admin_service = None
        self.organization_cache: Dict[str, int] = {}
        self.orgunit_cache: Dict[str, int] = {}  # Map org unit path to Elder org ID

    async def connect(self) -> None:
        """Establish connection to Google Workspace and Elder API."""
        self.logger.info("Connecting to Google Workspace")

        if not settings.google_workspace_credentials_path:
            raise ValueError("Google Workspace credentials path not configured")

        if not settings.google_workspace_admin_email:
            raise ValueError("Google Workspace admin email not configured")

        try:
            # Load service account credentials
            credentials = service_account.Credentials.from_service_account_file(
                settings.google_workspace_credentials_path,
                scopes=self.SCOPES,
            )

            # Delegate credentials to admin user
            delegated_credentials = credentials.with_subject(
                settings.google_workspace_admin_email
            )

            # Build Admin SDK service
            self.admin_service = build(
                "admin",
                "directory_v1",
                credentials=delegated_credentials,
            )

            self.logger.info(
                "Google Workspace connected",
                admin_email=settings.google_workspace_admin_email,
            )

        except Exception as e:
            self.logger.error("Failed to connect to Google Workspace", error=str(e))
            raise

        # Initialize Elder API client
        self.elder_client = ElderAPIClient()
        await self.elder_client.connect()

        self.logger.info("Google Workspace connector connected successfully")

    async def disconnect(self) -> None:
        """Disconnect from Google Workspace and Elder API."""
        if self.elder_client:
            await self.elder_client.close()
        self.admin_service = None
        self.organization_cache.clear()
        self.orgunit_cache.clear()
        self.logger.info("Google Workspace connector disconnected")

    async def _get_or_create_organization(
        self,
        name: str,
        description: str,
        parent_id: Optional[int] = None,
    ) -> int:
        """
        Get or create an organization in Elder.

        Args:
            name: Organization name
            description: Organization description
            parent_id: Parent organization ID

        Returns:
            Organization ID
        """
        cache_key = f"{parent_id or 'root'}:{name}"
        if cache_key in self.organization_cache:
            return self.organization_cache[cache_key]

        # Search for existing organization
        response = await self.elder_client.list_organizations(per_page=1000)
        for org in response.get("items", []):
            if org["name"] == name and org.get("parent_id") == parent_id:
                self.organization_cache[cache_key] = org["id"]
                return org["id"]

        # Create new organization
        if settings.create_missing_organizations:
            org_data = Organization(
                name=name,
                description=description,
                parent_id=parent_id,
            )
            created = await self.elder_client.create_organization(org_data)
            org_id = created["id"]
            self.organization_cache[cache_key] = org_id
            return org_id
        elif settings.default_organization_id:
            return settings.default_organization_id
        else:
            raise ValueError(
                f"Organization '{name}' not found and auto-creation disabled"
            )

    async def _sync_organizational_units(
        self, workspace_org_id: int
    ) -> tuple[int, int]:
        """
        Sync Google Workspace organizational units.

        Args:
            workspace_org_id: Elder organization ID for Google Workspace root

        Returns:
            Tuple of (created_count, updated_count)
        """
        created = 0
        updated = 0

        try:
            # List all organizational units
            orgunits_result = (
                self.admin_service.orgunits()
                .list(
                    customerId=settings.google_workspace_customer_id,
                )
                .execute()
            )

            orgunits = orgunits_result.get("organizationUnits", [])

            # Sort by path depth to create parent orgs first
            orgunits.sort(key=lambda ou: ou["orgUnitPath"].count("/"))

            for orgunit in orgunits:
                ou_path = orgunit["orgUnitPath"]
                ou_name = orgunit["name"]
                ou_description = orgunit.get("description", "")

                # Determine parent organization
                parent_path = "/".join(ou_path.split("/")[:-1]) or "/"
                if parent_path == "/":
                    parent_org_id = workspace_org_id
                else:
                    parent_org_id = self.orgunit_cache.get(
                        parent_path, workspace_org_id
                    )

                # Create or get organization in Elder
                org_id = await self._get_or_create_organization(
                    name=ou_name,
                    description=ou_description
                    or f"Google Workspace organizational unit: {ou_path}",
                    parent_id=parent_org_id,
                )

                # Cache the mapping
                self.orgunit_cache[ou_path] = org_id

                if org_id not in [
                    org["id"]
                    for org in (await self.elder_client.list_organizations()).get(
                        "items", []
                    )
                ]:
                    created += 1
                else:
                    updated += 1

        except HttpError as e:
            self.logger.error("Failed to sync organizational units", error=str(e))

        return created, updated

    async def _sync_users(self, workspace_org_id: int) -> tuple[int, int]:
        """
        Sync Google Workspace users.

        Args:
            workspace_org_id: Elder organization ID for Google Workspace root

        Returns:
            Tuple of (created_count, updated_count)
        """
        created = 0
        updated = 0

        try:
            # List all users
            page_token = None
            while True:
                users_result = (
                    self.admin_service.users()
                    .list(
                        customer=settings.google_workspace_customer_id,
                        maxResults=500,
                        pageToken=page_token,
                    )
                    .execute()
                )

                users = users_result.get("users", [])

                for user in users:
                    email = user["primaryEmail"]
                    full_name = user.get("name", {}).get("fullName", email)
                    org_unit_path = user.get("orgUnitPath", "/")

                    # Determine organization
                    org_id = self.orgunit_cache.get(org_unit_path, workspace_org_id)

                    entity = Entity(
                        name=full_name,
                        entity_type="user",
                        organization_id=org_id,
                        description=f"Google Workspace user: {email}",
                        attributes={
                            "email": email,
                            "user_id": user["id"],
                            "first_name": user.get("name", {}).get("givenName"),
                            "last_name": user.get("name", {}).get("familyName"),
                            "is_admin": user.get("isAdmin", False),
                            "is_delegated_admin": user.get("isDelegatedAdmin", False),
                            "suspended": user.get("suspended", False),
                            "org_unit_path": org_unit_path,
                            "provider": "google_workspace",
                            "creation_time": user.get("creationTime"),
                            "last_login_time": user.get("lastLoginTime"),
                        },
                        tags=["google_workspace", "user", "identity"],
                        is_active=not user.get("suspended", False),
                    )

                    # Check if entity already exists
                    existing = await self.elder_client.list_entities(
                        organization_id=org_id,
                        entity_type="user",
                    )

                    found = None
                    for item in existing.get("items", []):
                        if item.get("attributes", {}).get("email") == email:
                            found = item
                            break

                    if found:
                        await self.elder_client.update_entity(found["id"], entity)
                        updated += 1
                    else:
                        await self.elder_client.create_entity(entity)
                        created += 1

                page_token = users_result.get("nextPageToken")
                if not page_token:
                    break

        except HttpError as e:
            self.logger.error("Failed to sync users", error=str(e))

        return created, updated

    async def _sync_groups(self, workspace_org_id: int) -> tuple[int, int]:
        """
        Sync Google Workspace groups.

        Args:
            workspace_org_id: Elder organization ID for Google Workspace root

        Returns:
            Tuple of (created_count, updated_count)
        """
        created = 0
        updated = 0

        try:
            # List all groups
            page_token = None
            while True:
                groups_result = (
                    self.admin_service.groups()
                    .list(
                        customer=settings.google_workspace_customer_id,
                        maxResults=200,
                        pageToken=page_token,
                    )
                    .execute()
                )

                groups = groups_result.get("groups", [])

                for group in groups:
                    email = group["email"]
                    name = group.get("name", email)
                    description = group.get("description", "")

                    entity = Entity(
                        name=f"Group: {name}",
                        entity_type="user",  # Groups are also user-related entities
                        organization_id=workspace_org_id,
                        description=description or f"Google Workspace group: {email}",
                        attributes={
                            "email": email,
                            "group_id": group["id"],
                            "group_name": name,
                            "direct_members_count": group.get("directMembersCount", 0),
                            "provider": "google_workspace",
                            "type": "group",
                        },
                        tags=["google_workspace", "group", "identity"],
                    )

                    # Check if entity already exists
                    existing = await self.elder_client.list_entities(
                        organization_id=workspace_org_id,
                        entity_type="user",
                    )

                    found = None
                    for item in existing.get("items", []):
                        attrs = item.get("attributes", {})
                        if attrs.get("email") == email and attrs.get("type") == "group":
                            found = item
                            break

                    if found:
                        await self.elder_client.update_entity(found["id"], entity)
                        updated += 1
                    else:
                        await self.elder_client.create_entity(entity)
                        created += 1

                page_token = groups_result.get("nextPageToken")
                if not page_token:
                    break

        except HttpError as e:
            self.logger.error("Failed to sync groups", error=str(e))

        return created, updated

    async def sync(self) -> SyncResult:
        """
        Synchronize Google Workspace resources to Elder.

        Returns:
            SyncResult with statistics
        """
        result = SyncResult(connector_name=self.name)
        self.logger.info("Starting Google Workspace sync")

        try:
            # Create Google Workspace root organization
            workspace_org_id = await self._get_or_create_organization(
                "Google Workspace",
                f"Google Workspace domain: {settings.google_workspace_customer_id}",
            )
            result.organizations_created += 1

            # Sync organizational units (this creates organizations in Elder)
            ou_created, ou_updated = await self._sync_organizational_units(
                workspace_org_id
            )
            result.organizations_created += ou_created
            result.organizations_updated += ou_updated

            # Sync users
            users_created, users_updated = await self._sync_users(workspace_org_id)
            result.entities_created += users_created
            result.entities_updated += users_updated

            # Sync groups
            groups_created, groups_updated = await self._sync_groups(workspace_org_id)
            result.entities_created += groups_created
            result.entities_updated += groups_updated

            self.logger.info(
                "Google Workspace sync completed",
                total_ops=result.total_operations,
                orgs_created=result.organizations_created,
                entities_created=result.entities_created,
                entities_updated=result.entities_updated,
            )

        except Exception as e:
            error_msg = f"Google Workspace sync failed: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            result.errors.append(error_msg)

        return result

    async def health_check(self) -> bool:
        """Check Google Workspace connectivity."""
        try:
            # Try to list users with max 1 result as health check
            self.admin_service.users().list(
                customer=settings.google_workspace_customer_id,
                maxResults=1,
            ).execute()
            return True
        except Exception as e:
            self.logger.warning("Google Workspace health check failed", error=str(e))
            return False
