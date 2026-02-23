"""Elder API client for creating and updating organizations and entities."""

# flake8: noqa: E501


from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import aiohttp
import backoff

from apps.worker.config.settings import settings
from apps.worker.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class Organization:
    """Organization data model."""

    name: str
    description: Optional[str] = None
    parent_id: Optional[int] = None
    ldap_dn: Optional[str] = None
    saml_group: Optional[str] = None
    owner_identity_id: Optional[int] = None
    owner_group_id: Optional[int] = None


@dataclass
class Entity:
    """Entity data model."""

    name: str
    entity_type: str
    organization_id: int
    description: Optional[str] = None
    sub_type: Optional[str] = None  # v1.2.1: Entity sub-type
    parent_id: Optional[int] = None
    owner_identity_id: Optional[int] = None
    attributes: Optional[Dict[str, Any]] = None
    status_metadata: Optional[Dict[str, Any]] = None  # v1.2.1: Status tracking
    tags: Optional[List[str]] = None
    is_active: bool = True


@dataclass
class Dependency:
    """Dependency/relationship data model."""

    source_entity_id: int
    target_entity_id: int
    dependency_type: str  # uses, requires, contains, connects_to, etc.
    description: Optional[str] = None
    attributes: Optional[Dict[str, Any]] = None
    is_active: bool = True


class ElderAPIClient:
    """Client for interacting with Elder REST API."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        """
        Initialize Elder API client.

        Args:
            base_url: Elder API base URL (defaults to settings)
            api_key: API authentication key (defaults to settings)
        """
        self.base_url = (base_url or settings.elder_api_url).rstrip("/")
        self.api_key = api_key or settings.elder_api_key
        self.session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()

    async def connect(self) -> None:
        """Create aiohttp session."""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        self.session = aiohttp.ClientSession(
            base_url=self.base_url,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        )
        logger.info("Elder API client connected", base_url=self.base_url)

    async def close(self) -> None:
        """Close aiohttp session."""
        if self.session:
            await self.session.close()
            logger.info("Elder API client closed")

    @backoff.on_exception(
        backoff.expo,
        aiohttp.ClientError,
        max_tries=settings.sync_max_retries,
    )
    async def _request(
        self,
        method: str,
        endpoint: str,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Make HTTP request to Elder API with retry logic.

        Args:
            method: HTTP method
            endpoint: API endpoint (without base URL)
            **kwargs: Additional arguments to pass to aiohttp request

        Returns:
            Response JSON data

        Raises:
            aiohttp.ClientError: On request failure after retries
        """
        if not self.session:
            await self.connect()

        url = f"/api/v1{endpoint}"
        logger.debug(
            "Elder API request",
            method=method,
            url=url,
            **{k: v for k, v in kwargs.items() if k != "json"},
        )

        async with self.session.request(method, url, **kwargs) as response:
            response.raise_for_status()
            if response.status == 204:
                return {}
            return await response.json()

    # Organization operations
    async def list_organizations(
        self,
        page: int = 1,
        per_page: int = 100,
        parent_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        List organizations with pagination.

        Args:
            page: Page number
            per_page: Items per page
            parent_id: Filter by parent organization ID

        Returns:
            Paginated list of organizations
        """
        params = {"page": page, "per_page": per_page}
        if parent_id is not None:
            params["parent_id"] = parent_id

        return await self._request("GET", "/organizations", params=params)

    async def get_organization(self, org_id: int) -> Dict[str, Any]:
        """
        Get organization by ID.

        Args:
            org_id: Organization ID

        Returns:
            Organization details
        """
        return await self._request("GET", f"/organizations/{org_id}")

    async def create_organization(self, org: Organization) -> Dict[str, Any]:
        """
        Create a new organization.

        Args:
            org: Organization data

        Returns:
            Created organization
        """
        data = {
            "name": org.name,
            "description": org.description,
            "parent_id": org.parent_id,
            "ldap_dn": org.ldap_dn,
            "saml_group": org.saml_group,
            "owner_identity_id": org.owner_identity_id,
            "owner_group_id": org.owner_group_id,
        }
        # Remove None values
        data = {k: v for k, v in data.items() if v is not None}

        logger.info("Creating organization", name=org.name)
        return await self._request("POST", "/organizations", json=data)

    async def update_organization(
        self,
        org_id: int,
        org: Organization,
    ) -> Dict[str, Any]:
        """
        Update an existing organization.

        Args:
            org_id: Organization ID
            org: Updated organization data

        Returns:
            Updated organization
        """
        data = {
            "name": org.name,
            "description": org.description,
            "parent_id": org.parent_id,
            "ldap_dn": org.ldap_dn,
            "saml_group": org.saml_group,
            "owner_identity_id": org.owner_identity_id,
            "owner_group_id": org.owner_group_id,
        }
        # Remove None values
        data = {k: v for k, v in data.items() if v is not None}

        logger.info("Updating organization", org_id=org_id, name=org.name)
        return await self._request("PATCH", f"/organizations/{org_id}", json=data)

    # Entity operations
    async def list_entities(
        self,
        page: int = 1,
        per_page: int = 100,
        organization_id: Optional[int] = None,
        entity_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        List entities with pagination.

        Args:
            page: Page number
            per_page: Items per page
            organization_id: Filter by organization ID
            entity_type: Filter by entity type

        Returns:
            Paginated list of entities
        """
        params = {"page": page, "per_page": per_page}
        if organization_id is not None:
            params["organization_id"] = organization_id
        if entity_type:
            params["entity_type"] = entity_type

        return await self._request("GET", "/entities", params=params)

    async def get_entity(self, entity_id: int) -> Dict[str, Any]:
        """
        Get entity by ID.

        Args:
            entity_id: Entity ID

        Returns:
            Entity details
        """
        return await self._request("GET", f"/entities/{entity_id}")

    async def create_entity(self, entity: Entity) -> Dict[str, Any]:
        """
        Create a new entity.

        Args:
            entity: Entity data

        Returns:
            Created entity
        """
        data = {
            "name": entity.name,
            "entity_type": entity.entity_type,
            "organization_id": entity.organization_id,
            "description": entity.description,
            "parent_id": entity.parent_id,
            "owner_identity_id": entity.owner_identity_id,
            "attributes": entity.attributes or {},
            "tags": entity.tags or [],
            "is_active": entity.is_active,
        }
        # Remove None values (except attributes and tags which default to empty)
        data = {k: v for k, v in data.items() if v is not None}

        logger.info(
            "Creating entity",
            name=entity.name,
            type=entity.entity_type,
            org_id=entity.organization_id,
        )
        return await self._request("POST", "/entities", json=data)

    async def update_entity(
        self,
        entity_id: int,
        entity: Entity,
    ) -> Dict[str, Any]:
        """
        Update an existing entity.

        Args:
            entity_id: Entity ID
            entity: Updated entity data

        Returns:
            Updated entity
        """
        data = {
            "name": entity.name,
            "entity_type": entity.entity_type,
            "organization_id": entity.organization_id,
            "description": entity.description,
            "parent_id": entity.parent_id,
            "owner_identity_id": entity.owner_identity_id,
            "attributes": entity.attributes or {},
            "tags": entity.tags or [],
            "is_active": entity.is_active,
        }
        # Remove None values
        data = {k: v for k, v in data.items() if v is not None}

        logger.info(
            "Updating entity",
            entity_id=entity_id,
            name=entity.name,
            type=entity.entity_type,
        )
        return await self._request("PATCH", f"/entities/{entity_id}", json=data)

    # Dependency operations
    async def list_dependencies(
        self,
        page: int = 1,
        per_page: int = 100,
        source_entity_id: Optional[int] = None,
        target_entity_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        List dependencies with pagination.

        Args:
            page: Page number
            per_page: Items per page
            source_entity_id: Filter by source entity ID
            target_entity_id: Filter by target entity ID

        Returns:
            Paginated list of dependencies
        """
        params = {"page": page, "per_page": per_page}
        if source_entity_id is not None:
            params["source_entity_id"] = source_entity_id
        if target_entity_id is not None:
            params["target_entity_id"] = target_entity_id

        return await self._request("GET", "/dependencies", params=params)

    async def create_dependency(self, dep: Dependency) -> Dict[str, Any]:
        """
        Create a new dependency/relationship.

        Args:
            dep: Dependency data

        Returns:
            Created dependency
        """
        data = {
            "source_entity_id": dep.source_entity_id,
            "target_entity_id": dep.target_entity_id,
            "dependency_type": dep.dependency_type,
            "description": dep.description,
            "attributes": dep.attributes or {},
            "is_active": dep.is_active,
        }
        # Remove None values
        data = {k: v for k, v in data.items() if v is not None}

        logger.info(
            "Creating dependency",
            source=dep.source_entity_id,
            target=dep.target_entity_id,
            type=dep.dependency_type,
        )
        return await self._request("POST", "/dependencies", json=data)

    async def get_or_create_dependency(
        self,
        source_entity_id: int,
        target_entity_id: int,
        dependency_type: str,
        description: Optional[str] = None,
        attributes: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Get existing dependency or create new one.

        Args:
            source_entity_id: Source entity ID
            target_entity_id: Target entity ID
            dependency_type: Type of dependency
            description: Optional description
            attributes: Optional attributes

        Returns:
            Existing or created dependency
        """
        # Check if dependency already exists
        existing = await self.list_dependencies(
            source_entity_id=source_entity_id,
            target_entity_id=target_entity_id,
        )

        for dep in existing.get("items", []):
            if dep.get("dependency_type") == dependency_type:
                return dep

        # Create new dependency
        return await self.create_dependency(
            Dependency(
                source_entity_id=source_entity_id,
                target_entity_id=target_entity_id,
                dependency_type=dependency_type,
                description=description,
                attributes=attributes,
            )
        )

    async def health_check(self) -> bool:
        """
        Check Elder API health.

        Returns:
            True if API is healthy, False otherwise
        """
        try:
            await self._request("GET", "/healthz")
            return True
        except Exception as e:
            logger.warning("Elder API health check failed", error=str(e))
            return False
