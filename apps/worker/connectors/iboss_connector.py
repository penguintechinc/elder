"""iBoss cloud security platform connector for syncing to Elder."""

# flake8: noqa: E501


from typing import Dict, List, Optional

import httpx

from apps.worker.config.settings import settings
from apps.worker.connectors.base import BaseConnector, SyncResult
from apps.worker.utils.elder_client import ElderAPIClient, Entity, Organization


class IBossConnector(BaseConnector):
    """Connector for iBoss cloud security platform.

    Syncs networking policies, users, groups, and security metadata from iBoss.
    This is a read-only connector for discovery purposes.
    """

    def __init__(self):
        """Initialize iBoss connector."""
        super().__init__("iboss")
        self.elder_client: Optional[ElderAPIClient] = None
        self.http_client: Optional[httpx.AsyncClient] = None
        self.organization_cache: Dict[str, int] = {}
        # Cache for entity IDs to create relationships
        self.user_entity_cache: Dict[str, int] = {}  # iboss_user_id -> elder_entity_id
        self.group_entity_cache: Dict[str, int] = (
            {}
        )  # iboss_group_id -> elder_entity_id
        self.app_entity_cache: Dict[str, int] = {}  # iboss_app_id -> elder_entity_id
        # Cache user-group memberships for relationships
        self.user_groups: Dict[str, List[str]] = {}  # user_id -> [group_ids]

    async def connect(self) -> None:
        """Establish connection to iBoss API and Elder API."""
        self.logger.info("Connecting to iBoss API", url=settings.iboss_api_url)

        try:
            # Initialize HTTP client for iBoss API
            self.http_client = httpx.AsyncClient(
                base_url=settings.iboss_api_url,
                headers={
                    "Authorization": f"Bearer {settings.iboss_api_key}",
                    "Content-Type": "application/json",
                    "X-Tenant-ID": settings.iboss_tenant_id or "",
                },
                timeout=30.0,
            )

            # Test connection
            response = await self.http_client.get("/api/v1/health")
            if response.status_code != 200:
                raise Exception(
                    f"iBoss API health check failed: {response.status_code}"
                )

            self.logger.info("iBoss API connection established")

        except Exception as e:
            self.logger.error("Failed to connect to iBoss API", error=str(e))
            raise

        # Initialize Elder API client
        self.elder_client = ElderAPIClient()
        await self.elder_client.connect()

        self.logger.info("iBoss connector connected successfully")

    async def disconnect(self) -> None:
        """Disconnect from iBoss API and Elder API."""
        if self.http_client:
            await self.http_client.aclose()
            self.http_client = None

        if self.elder_client:
            await self.elder_client.close()

        self.organization_cache.clear()
        self.logger.info("iBoss connector disconnected")

    async def _get_or_create_organization(
        self,
        name: str,
        description: str,
        parent_id: Optional[int] = None,
    ) -> int:
        """Get or create an organization in Elder.

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

    async def _sync_users(self, iboss_org_id: int) -> tuple[int, int]:
        """Sync iBoss users to Elder.

        Args:
            iboss_org_id: Elder organization ID for iBoss

        Returns:
            Tuple of (created_count, updated_count)
        """
        created = 0
        updated = 0

        try:
            # Get users from iBoss API
            response = await self.http_client.get("/api/v1/users")
            if response.status_code != 200:
                self.logger.error(
                    "Failed to fetch iBoss users", status_code=response.status_code
                )
                return created, updated

            users = response.json().get("users", [])

            for user in users:
                entity = Entity(
                    name=user.get("username") or user.get("email"),
                    entity_type="identity",
                    organization_id=iboss_org_id,
                    description=f"iBoss user: {user.get('email', '')}",
                    attributes={
                        "iboss_user_id": user.get("id"),
                        "email": user.get("email"),
                        "username": user.get("username"),
                        "first_name": user.get("firstName"),
                        "last_name": user.get("lastName"),
                        "department": user.get("department"),
                        "groups": user.get("groups", []),
                        "status": user.get("status"),
                        "provider": "iboss",
                        "identity_type": "employee",
                    },
                    tags=["iboss", "user", "identity"],
                    is_active=user.get("status") == "active",
                )

                # Check if entity already exists
                existing = await self.elder_client.list_entities(
                    organization_id=iboss_org_id,
                    entity_type="identity",
                )

                found = None
                for item in existing.get("items", []):
                    if item.get("attributes", {}).get("iboss_user_id") == user.get(
                        "id"
                    ):
                        found = item
                        break

                if found:
                    await self.elder_client.update_entity(found["id"], entity)
                    self.user_entity_cache[user.get("id")] = found["id"]
                    updated += 1
                else:
                    result = await self.elder_client.create_entity(entity)
                    self.user_entity_cache[user.get("id")] = result["id"]
                    created += 1

                # Cache user's group memberships for relationship creation
                self.user_groups[user.get("id")] = user.get("groups", [])

        except Exception as e:
            self.logger.error("Failed to sync iBoss users", error=str(e))

        return created, updated

    async def _sync_groups(self, iboss_org_id: int) -> tuple[int, int]:
        """Sync iBoss groups to Elder.

        Args:
            iboss_org_id: Elder organization ID for iBoss

        Returns:
            Tuple of (created_count, updated_count)
        """
        created = 0
        updated = 0

        try:
            # Get groups from iBoss API
            response = await self.http_client.get("/api/v1/groups")
            if response.status_code != 200:
                self.logger.error(
                    "Failed to fetch iBoss groups", status_code=response.status_code
                )
                return created, updated

            groups = response.json().get("groups", [])

            for group in groups:
                entity = Entity(
                    name=f"Group: {group.get('name')}",
                    entity_type="identity",
                    organization_id=iboss_org_id,
                    description=group.get(
                        "description", f"iBoss group: {group.get('name')}"
                    ),
                    attributes={
                        "iboss_group_id": group.get("id"),
                        "group_name": group.get("name"),
                        "member_count": group.get("memberCount", 0),
                        "policies": group.get("policies", []),
                        "provider": "iboss",
                        "identity_type": "group",
                    },
                    tags=["iboss", "group", "identity"],
                )

                # Check if entity already exists
                existing = await self.elder_client.list_entities(
                    organization_id=iboss_org_id,
                    entity_type="identity",
                )

                found = None
                for item in existing.get("items", []):
                    attrs = item.get("attributes", {})
                    if attrs.get("iboss_group_id") == group.get("id"):
                        found = item
                        break

                if found:
                    await self.elder_client.update_entity(found["id"], entity)
                    self.group_entity_cache[group.get("id")] = found["id"]
                    updated += 1
                else:
                    result = await self.elder_client.create_entity(entity)
                    self.group_entity_cache[group.get("id")] = result["id"]
                    created += 1

        except Exception as e:
            self.logger.error("Failed to sync iBoss groups", error=str(e))

        return created, updated

    async def _sync_policies(self, iboss_org_id: int) -> tuple[int, int]:
        """Sync iBoss web filtering policies to Elder.

        Args:
            iboss_org_id: Elder organization ID for iBoss

        Returns:
            Tuple of (created_count, updated_count)
        """
        created = 0
        updated = 0

        try:
            # Get policies from iBoss API
            response = await self.http_client.get("/api/v1/policies")
            if response.status_code != 200:
                self.logger.error(
                    "Failed to fetch iBoss policies", status_code=response.status_code
                )
                return created, updated

            policies = response.json().get("policies", [])

            for policy in policies:
                entity = Entity(
                    name=f"Policy: {policy.get('name')}",
                    entity_type="security",
                    organization_id=iboss_org_id,
                    description=policy.get(
                        "description", f"iBoss policy: {policy.get('name')}"
                    ),
                    attributes={
                        "iboss_policy_id": policy.get("id"),
                        "policy_name": policy.get("name"),
                        "policy_type": policy.get("type"),
                        "enabled": policy.get("enabled", False),
                        "priority": policy.get("priority"),
                        "categories": policy.get("categories", []),
                        "actions": policy.get("actions", {}),
                        "provider": "iboss",
                    },
                    tags=["iboss", "policy", "security", "web-filtering"],
                    is_active=policy.get("enabled", False),
                )

                # Check if entity already exists
                existing = await self.elder_client.list_entities(
                    organization_id=iboss_org_id,
                    entity_type="security",
                )

                found = None
                for item in existing.get("items", []):
                    if item.get("attributes", {}).get("iboss_policy_id") == policy.get(
                        "id"
                    ):
                        found = item
                        break

                if found:
                    await self.elder_client.update_entity(found["id"], entity)
                    updated += 1
                else:
                    await self.elder_client.create_entity(entity)
                    created += 1

        except Exception as e:
            self.logger.error("Failed to sync iBoss policies", error=str(e))

        return created, updated

    async def _sync_applications(self, iboss_org_id: int) -> tuple[int, int]:
        """Sync iBoss application usage/visibility to Elder.

        Args:
            iboss_org_id: Elder organization ID for iBoss

        Returns:
            Tuple of (created_count, updated_count)
        """
        created = 0
        updated = 0

        try:
            # Get applications from iBoss API
            response = await self.http_client.get("/api/v1/applications")
            if response.status_code != 200:
                self.logger.warning(
                    "Failed to fetch iBoss applications",
                    status_code=response.status_code,
                )
                return created, updated

            applications = response.json().get("applications", [])

            for app in applications:
                # Determine risk level based on category
                category = app.get("category", "").lower()
                risk_tags = []
                if any(x in category for x in ["malware", "phishing", "botnet"]):
                    risk_tags.append("high-risk")
                elif any(x in category for x in ["file-sharing", "proxy", "vpn"]):
                    risk_tags.append("medium-risk")

                entity = Entity(
                    name=f"App: {app.get('name')}",
                    entity_type="compute",
                    organization_id=iboss_org_id,
                    description=app.get(
                        "description", f"iBoss tracked application: {app.get('name')}"
                    ),
                    attributes={
                        "iboss_app_id": app.get("id"),
                        "app_name": app.get("name"),
                        "category": app.get("category"),
                        "subcategory": app.get("subcategory"),
                        "risk_score": app.get("riskScore"),
                        "users_count": app.get("usersCount", 0),
                        "sessions_count": app.get("sessionsCount", 0),
                        "bytes_transferred": app.get("bytesTransferred", 0),
                        "last_seen": app.get("lastSeen"),
                        "blocked": app.get("blocked", False),
                        "vendor": app.get("vendor"),
                        "provider": "iboss",
                        "resource_type": "application",
                    },
                    tags=[
                        "iboss",
                        "application",
                        "software",
                        app.get("category", "unknown"),
                    ]
                    + risk_tags,
                    is_active=app.get("usersCount", 0) > 0,
                )

                # Check if entity already exists
                existing = await self.elder_client.list_entities(
                    organization_id=iboss_org_id,
                    entity_type="compute",
                )

                found = None
                for item in existing.get("items", []):
                    if item.get("attributes", {}).get("iboss_app_id") == app.get("id"):
                        found = item
                        break

                if found:
                    await self.elder_client.update_entity(found["id"], entity)
                    self.app_entity_cache[app.get("id")] = found["id"]
                    updated += 1
                else:
                    result = await self.elder_client.create_entity(entity)
                    self.app_entity_cache[app.get("id")] = result["id"]
                    created += 1

        except Exception as e:
            self.logger.error("Failed to sync iBoss applications", error=str(e))

        return created, updated

    async def _sync_relationships(self) -> int:
        """Create relationships between iBoss entities.

        Links:
        - Users to groups they belong to
        - Users to applications they use

        Returns:
            Number of relationships created
        """
        relationships_created = 0

        try:
            # Link users to their groups
            for user_id, user_entity_id in self.user_entity_cache.items():
                group_ids = self.user_groups.get(user_id, [])
                for group_id in group_ids:
                    group_entity_id = self.group_entity_cache.get(group_id)
                    if group_entity_id:
                        await self.elder_client.get_or_create_dependency(
                            source_entity_id=user_entity_id,
                            target_entity_id=group_entity_id,
                            dependency_type="member_of",
                            description="User is member of group",
                        )
                        relationships_created += 1

            # Get application usage per user
            try:
                response = await self.http_client.get(
                    "/api/v1/reports/application-usage"
                )
                if response.status_code == 200:
                    usage_data = response.json().get("usage", [])
                    for usage in usage_data:
                        user_id = usage.get("userId")
                        app_id = usage.get("applicationId")
                        user_entity_id = self.user_entity_cache.get(user_id)
                        app_entity_id = self.app_entity_cache.get(app_id)

                        if user_entity_id and app_entity_id:
                            await self.elder_client.get_or_create_dependency(
                                source_entity_id=user_entity_id,
                                target_entity_id=app_entity_id,
                                dependency_type="uses",
                                description="User uses application",
                                attributes={
                                    "sessions_count": usage.get("sessionsCount"),
                                    "bytes_transferred": usage.get("bytesTransferred"),
                                },
                            )
                            relationships_created += 1
            except Exception as e:
                self.logger.warning("Failed to fetch application usage", error=str(e))

        except Exception as e:
            self.logger.error("Failed to sync iBoss relationships", error=str(e))

        return relationships_created

    async def _sync_cloud_connectors(self, iboss_org_id: int) -> tuple[int, int]:
        """Sync iBoss cloud connectors/gateways to Elder.

        Args:
            iboss_org_id: Elder organization ID for iBoss

        Returns:
            Tuple of (created_count, updated_count)
        """
        created = 0
        updated = 0

        try:
            # Get cloud connectors from iBoss API
            response = await self.http_client.get("/api/v1/connectors")
            if response.status_code != 200:
                self.logger.error(
                    "Failed to fetch iBoss connectors",
                    status_code=response.status_code,
                )
                return created, updated

            connectors = response.json().get("connectors", [])

            for connector in connectors:
                entity = Entity(
                    name=connector.get("name", f"Connector-{connector.get('id')}"),
                    entity_type="network",
                    organization_id=iboss_org_id,
                    description=f"iBoss cloud connector: {connector.get('name')}",
                    attributes={
                        "iboss_connector_id": connector.get("id"),
                        "connector_type": connector.get("type"),
                        "location": connector.get("location"),
                        "ip_address": connector.get("ipAddress"),
                        "status": connector.get("status"),
                        "version": connector.get("version"),
                        "last_seen": connector.get("lastSeen"),
                        "provider": "iboss",
                    },
                    tags=["iboss", "connector", "network", "gateway"],
                    is_active=connector.get("status") == "online",
                )

                # Check if entity already exists
                existing = await self.elder_client.list_entities(
                    organization_id=iboss_org_id,
                    entity_type="network",
                )

                found = None
                for item in existing.get("items", []):
                    attrs = item.get("attributes", {})
                    if attrs.get("iboss_connector_id") == connector.get("id"):
                        found = item
                        break

                if found:
                    await self.elder_client.update_entity(found["id"], entity)
                    updated += 1
                else:
                    await self.elder_client.create_entity(entity)
                    created += 1

        except Exception as e:
            self.logger.error("Failed to sync iBoss connectors", error=str(e))

        return created, updated

    async def sync(self) -> SyncResult:
        """Synchronize iBoss resources to Elder.

        Returns:
            SyncResult with statistics
        """
        result = SyncResult(connector_name=self.name)
        self.logger.info("Starting iBoss sync")

        try:
            # Create iBoss root organization
            iboss_org_id = await self._get_or_create_organization(
                f"iBoss: {settings.iboss_tenant_id or 'Default'}",
                "iBoss cloud security platform",
            )
            result.organizations_created += 1

            # Sync users
            users_created, users_updated = await self._sync_users(iboss_org_id)
            result.entities_created += users_created
            result.entities_updated += users_updated

            # Sync groups
            groups_created, groups_updated = await self._sync_groups(iboss_org_id)
            result.entities_created += groups_created
            result.entities_updated += groups_updated

            # Sync policies
            policies_created, policies_updated = await self._sync_policies(iboss_org_id)
            result.entities_created += policies_created
            result.entities_updated += policies_updated

            # Sync applications
            apps_created, apps_updated = await self._sync_applications(iboss_org_id)
            result.entities_created += apps_created
            result.entities_updated += apps_updated

            # Sync cloud connectors
            connectors_created, connectors_updated = await self._sync_cloud_connectors(
                iboss_org_id
            )
            result.entities_created += connectors_created
            result.entities_updated += connectors_updated

            # Create relationships between entities
            relationships_created = await self._sync_relationships()

            self.logger.info(
                "iBoss sync completed",
                total_ops=result.total_operations,
                entities_created=result.entities_created,
                entities_updated=result.entities_updated,
                relationships_created=relationships_created,
            )

        except Exception as e:
            error_msg = f"iBoss sync failed: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            result.errors.append(error_msg)

        return result

    async def health_check(self) -> bool:
        """Check iBoss API connectivity.

        Returns:
            True if healthy, False otherwise
        """
        try:
            if not self.http_client:
                return False

            response = await self.http_client.get("/api/v1/health")
            return response.status_code == 200
        except Exception as e:
            self.logger.warning("iBoss health check failed", error=str(e))
            return False
