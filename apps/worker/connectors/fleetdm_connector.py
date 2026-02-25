"""FleetDM endpoint manager connector for syncing to Elder."""

# flake8: noqa: E501


from typing import Dict, Optional

import httpx

from apps.worker.config.settings import settings
from apps.worker.connectors.base import BaseConnector, SyncResult
from apps.worker.utils.elder_client import ElderAPIClient, Entity, Organization


class FleetDMConnector(BaseConnector):
    """Connector for FleetDM endpoint management platform.

    Syncs hosts, software inventory, and vulnerabilities from FleetDM.
    This is a read-only connector for discovery purposes.
    """

    def __init__(self):
        """Initialize FleetDM connector."""
        super().__init__("fleetdm")
        self.elder_client: Optional[ElderAPIClient] = None
        self.http_client: Optional[httpx.AsyncClient] = None
        self.organization_cache: Dict[str, int] = {}
        # Cache for entity IDs to create relationships
        self.host_entity_cache: Dict[int, int] = (
            {}
        )  # fleetdm_host_id -> elder_entity_id
        self.software_entity_cache: Dict[int, int] = (
            {}
        )  # fleetdm_software_id -> elder_entity_id
        self.vuln_entity_cache: Dict[str, int] = {}  # cve -> elder_entity_id

    async def connect(self) -> None:
        """Establish connection to FleetDM API and Elder API."""
        self.logger.info("Connecting to FleetDM API", url=settings.fleetdm_url)

        try:
            # Initialize HTTP client for FleetDM API
            self.http_client = httpx.AsyncClient(
                base_url=settings.fleetdm_url,
                headers={
                    "Authorization": f"Bearer {settings.fleetdm_api_token}",
                    "Content-Type": "application/json",
                },
                timeout=30.0,
            )

            # Test connection
            response = await self.http_client.get("/api/v1/fleet/config")
            if response.status_code != 200:
                raise Exception(
                    f"FleetDM API connection failed: {response.status_code}"
                )

            self.logger.info("FleetDM API connection established")

        except Exception as e:
            self.logger.error("Failed to connect to FleetDM API", error=str(e))
            raise

        # Initialize Elder API client
        self.elder_client = ElderAPIClient()
        await self.elder_client.connect()

        self.logger.info("FleetDM connector connected successfully")

    async def disconnect(self) -> None:
        """Disconnect from FleetDM API and Elder API."""
        if self.http_client:
            await self.http_client.aclose()
            self.http_client = None

        if self.elder_client:
            await self.elder_client.close()

        self.organization_cache.clear()
        self.logger.info("FleetDM connector disconnected")

    async def _get_or_create_organization(
        self,
        name: str,
        description: str,
        parent_id: Optional[int] = None,
    ) -> int:
        """Get or create an organization in Elder."""
        cache_key = f"{parent_id or 'root'}:{name}"
        if cache_key in self.organization_cache:
            return self.organization_cache[cache_key]

        response = await self.elder_client.list_organizations(per_page=1000)
        for org in response.get("items", []):
            if org["name"] == name and org.get("parent_id") == parent_id:
                self.organization_cache[cache_key] = org["id"]
                return org["id"]

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

    async def _sync_teams(self, fleetdm_org_id: int) -> Dict[int, int]:
        """Sync FleetDM teams as sub-organizations.

        Returns:
            Map of FleetDM team ID to Elder organization ID
        """
        team_map = {}

        try:
            response = await self.http_client.get("/api/v1/fleet/teams")
            if response.status_code != 200:
                self.logger.warning(
                    "Failed to fetch FleetDM teams", status_code=response.status_code
                )
                return team_map

            teams = response.json().get("teams", [])

            for team in teams:
                team_org_id = await self._get_or_create_organization(
                    name=f"Team: {team.get('name')}",
                    description=team.get(
                        "description", f"FleetDM Team: {team.get('name')}"
                    ),
                    parent_id=fleetdm_org_id,
                )
                team_map[team.get("id")] = team_org_id

        except Exception as e:
            self.logger.error("Failed to sync FleetDM teams", error=str(e))

        return team_map

    async def _sync_hosts(
        self, fleetdm_org_id: int, team_map: Dict[int, int]
    ) -> tuple[int, int]:
        """Sync FleetDM hosts to Elder.

        Args:
            fleetdm_org_id: Elder organization ID for FleetDM
            team_map: Map of FleetDM team ID to Elder organization ID

        Returns:
            Tuple of (created_count, updated_count)
        """
        created = 0
        updated = 0

        try:
            # Get all hosts from FleetDM
            page = 0
            per_page = 100

            while True:
                response = await self.http_client.get(
                    "/api/v1/fleet/hosts",
                    params={"page": page, "per_page": per_page},
                )

                if response.status_code != 200:
                    self.logger.error(
                        "Failed to fetch FleetDM hosts",
                        status_code=response.status_code,
                    )
                    break

                data = response.json()
                hosts = data.get("hosts", [])

                if not hosts:
                    break

                for host in hosts:
                    # Determine organization based on team
                    team_id = host.get("team_id")
                    org_id = team_map.get(team_id, fleetdm_org_id)

                    entity = Entity(
                        name=host.get("hostname")
                        or host.get("computer_name")
                        or f"Host-{host.get('id')}",
                        entity_type="compute",
                        organization_id=org_id,
                        description=f"FleetDM managed endpoint: {host.get('hostname')}",
                        attributes={
                            "fleetdm_host_id": host.get("id"),
                            "uuid": host.get("uuid"),
                            "hostname": host.get("hostname"),
                            "computer_name": host.get("computer_name"),
                            "platform": host.get("platform"),
                            "os_version": host.get("os_version"),
                            "osquery_version": host.get("osquery_version"),
                            "hardware_model": host.get("hardware_model"),
                            "hardware_serial": host.get("hardware_serial"),
                            "hardware_vendor": host.get("hardware_vendor"),
                            "cpu_type": host.get("cpu_type"),
                            "cpu_brand": host.get("cpu_brand"),
                            "cpu_physical_cores": host.get("cpu_physical_cores"),
                            "cpu_logical_cores": host.get("cpu_logical_cores"),
                            "memory": host.get("memory"),
                            "memory_gb": (
                                round(host.get("memory", 0) / (1024**3), 2)
                                if host.get("memory")
                                else None
                            ),
                            "primary_ip": host.get("primary_ip"),
                            "primary_mac": host.get("primary_mac"),
                            "status": host.get("status"),
                            "seen_time": host.get("seen_time"),
                            "uptime": host.get("uptime"),
                            "issues": host.get("issues", {}),
                            "provider": "fleetdm",
                        },
                        tags=["fleetdm", "endpoint", host.get("platform", "unknown")],
                        is_active=host.get("status") == "online",
                    )

                    # Check if entity exists
                    existing = await self.elder_client.list_entities(
                        organization_id=org_id,
                        entity_type="compute",
                    )

                    found = None
                    for item in existing.get("items", []):
                        if item.get("attributes", {}).get(
                            "fleetdm_host_id"
                        ) == host.get("id"):
                            found = item
                            break

                    if found:
                        await self.elder_client.update_entity(found["id"], entity)
                        self.host_entity_cache[host.get("id")] = found["id"]
                        updated += 1
                    else:
                        result = await self.elder_client.create_entity(entity)
                        self.host_entity_cache[host.get("id")] = result["id"]
                        created += 1

                page += 1

        except Exception as e:
            self.logger.error("Failed to sync FleetDM hosts", error=str(e))

        return created, updated

    async def _sync_vulnerabilities(self, fleetdm_org_id: int) -> tuple[int, int]:
        """Sync FleetDM vulnerabilities to Elder as security issues.

        Args:
            fleetdm_org_id: Elder organization ID for FleetDM

        Returns:
            Tuple of (created_count, updated_count)
        """
        created = 0
        updated = 0

        try:
            # Get vulnerabilities from FleetDM
            response = await self.http_client.get("/api/v1/fleet/vulnerabilities")

            if response.status_code != 200:
                self.logger.warning(
                    "Failed to fetch FleetDM vulnerabilities",
                    status_code=response.status_code,
                )
                return created, updated

            data = response.json()
            vulnerabilities = data.get("vulnerabilities", [])

            for vuln in vulnerabilities:
                # Determine severity based on CVSS
                cvss = vuln.get("cvss_score", 0)
                if cvss >= 9.0:
                    severity = "critical"
                elif cvss >= 7.0:
                    severity = "high"
                elif cvss >= 4.0:
                    severity = "medium"
                else:
                    severity = "low"

                entity = Entity(
                    name=f"CVE: {vuln.get('cve')}",
                    entity_type="security",
                    organization_id=fleetdm_org_id,
                    description=vuln.get(
                        "details_link", f"Vulnerability: {vuln.get('cve')}"
                    ),
                    attributes={
                        "fleetdm_vuln_id": vuln.get("cve"),
                        "cve": vuln.get("cve"),
                        "cvss_score": vuln.get("cvss_score"),
                        "severity": severity,
                        "hosts_count": vuln.get("hosts_count", 0),
                        "software_name": vuln.get("software", {}).get("name"),
                        "software_version": vuln.get("software", {}).get("version"),
                        "details_link": vuln.get("details_link"),
                        "published": vuln.get("published"),
                        "provider": "fleetdm",
                        "issue_type": "vulnerability",
                    },
                    tags=[
                        "fleetdm",
                        "vulnerability",
                        "security",
                        severity,
                        vuln.get("cve", ""),
                    ],
                    is_active=vuln.get("hosts_count", 0) > 0,
                )

                # Check if entity exists
                existing = await self.elder_client.list_entities(
                    organization_id=fleetdm_org_id,
                    entity_type="security",
                )

                found = None
                for item in existing.get("items", []):
                    if item.get("attributes", {}).get("cve") == vuln.get("cve"):
                        found = item
                        break

                if found:
                    await self.elder_client.update_entity(found["id"], entity)
                    self.vuln_entity_cache[vuln.get("cve")] = found["id"]
                    updated += 1
                else:
                    result = await self.elder_client.create_entity(entity)
                    self.vuln_entity_cache[vuln.get("cve")] = result["id"]
                    created += 1

        except Exception as e:
            self.logger.error("Failed to sync FleetDM vulnerabilities", error=str(e))

        return created, updated

    async def _sync_software(self, fleetdm_org_id: int) -> tuple[int, int]:
        """Sync FleetDM software inventory to Elder.

        Args:
            fleetdm_org_id: Elder organization ID for FleetDM

        Returns:
            Tuple of (created_count, updated_count)
        """
        created = 0
        updated = 0

        try:
            # Get software inventory from FleetDM
            page = 0
            per_page = 100

            while True:
                response = await self.http_client.get(
                    "/api/v1/fleet/software",
                    params={"page": page, "per_page": per_page},
                )

                if response.status_code != 200:
                    self.logger.warning(
                        "Failed to fetch FleetDM software",
                        status_code=response.status_code,
                    )
                    break

                data = response.json()
                software_list = data.get("software", [])

                if not software_list:
                    break

                for software in software_list:
                    entity = Entity(
                        name=f"Software: {software.get('name')}",
                        entity_type="compute",
                        organization_id=fleetdm_org_id,
                        description=f"{software.get('name')} v{software.get('version', 'unknown')}",
                        attributes={
                            "fleetdm_software_id": software.get("id"),
                            "software_name": software.get("name"),
                            "version": software.get("version"),
                            "source": software.get("source"),
                            "bundle_identifier": software.get("bundle_identifier"),
                            "hosts_count": software.get("hosts_count", 0),
                            "vulnerabilities_count": len(
                                software.get("vulnerabilities", [])
                            ),
                            "last_opened_at": software.get("last_opened_at"),
                            "generated_cpe": software.get("generated_cpe"),
                            "provider": "fleetdm",
                            "resource_type": "software",
                        },
                        tags=[
                            "fleetdm",
                            "software",
                            "application",
                            software.get("source", "unknown"),
                        ],
                        is_active=software.get("hosts_count", 0) > 0,
                    )

                    # Check if entity exists
                    existing = await self.elder_client.list_entities(
                        organization_id=fleetdm_org_id,
                        entity_type="compute",
                    )

                    found = None
                    for item in existing.get("items", []):
                        if item.get("attributes", {}).get(
                            "fleetdm_software_id"
                        ) == software.get("id"):
                            found = item
                            break

                    if found:
                        await self.elder_client.update_entity(found["id"], entity)
                        self.software_entity_cache[software.get("id")] = found["id"]
                        updated += 1
                    else:
                        result = await self.elder_client.create_entity(entity)
                        self.software_entity_cache[software.get("id")] = result["id"]
                        created += 1

                page += 1

        except Exception as e:
            self.logger.error("Failed to sync FleetDM software", error=str(e))

        return created, updated

    async def _sync_relationships(self) -> int:
        """Create relationships between FleetDM entities.

        Links:
        - Vulnerabilities to affected hosts
        - Software to hosts that have it installed

        Returns:
            Number of relationships created
        """
        relationships_created = 0

        try:
            # Link vulnerabilities to hosts
            for cve, vuln_entity_id in self.vuln_entity_cache.items():
                try:
                    # Get hosts affected by this vulnerability
                    response = await self.http_client.get(
                        f"/api/v1/fleet/vulnerabilities/{cve}/hosts"
                    )
                    if response.status_code == 200:
                        hosts = response.json().get("hosts", [])
                        for host in hosts:
                            host_entity_id = self.host_entity_cache.get(host.get("id"))
                            if host_entity_id:
                                await self.elder_client.get_or_create_dependency(
                                    source_entity_id=vuln_entity_id,
                                    target_entity_id=host_entity_id,
                                    dependency_type="affects",
                                    description=f"Vulnerability {cve} affects host",
                                )
                                relationships_created += 1
                except Exception as e:
                    self.logger.warning(
                        f"Failed to link vulnerability {cve}", error=str(e)
                    )

            # Link software to hosts
            for software_id, software_entity_id in self.software_entity_cache.items():
                try:
                    # Get hosts with this software
                    response = await self.http_client.get(
                        f"/api/v1/fleet/software/{software_id}/hosts"
                    )
                    if response.status_code == 200:
                        hosts = response.json().get("hosts", [])
                        for host in hosts[:50]:  # Limit to 50 hosts per software
                            host_entity_id = self.host_entity_cache.get(host.get("id"))
                            if host_entity_id:
                                await self.elder_client.get_or_create_dependency(
                                    source_entity_id=software_entity_id,
                                    target_entity_id=host_entity_id,
                                    dependency_type="installed_on",
                                    description="Software installed on host",
                                )
                                relationships_created += 1
                except Exception as e:
                    self.logger.warning(
                        f"Failed to link software {software_id}", error=str(e)
                    )

        except Exception as e:
            self.logger.error("Failed to sync FleetDM relationships", error=str(e))

        return relationships_created

    async def _sync_policies(self, fleetdm_org_id: int) -> tuple[int, int]:
        """Sync FleetDM policies to Elder.

        Args:
            fleetdm_org_id: Elder organization ID for FleetDM

        Returns:
            Tuple of (created_count, updated_count)
        """
        created = 0
        updated = 0

        try:
            response = await self.http_client.get("/api/v1/fleet/global/policies")

            if response.status_code != 200:
                self.logger.warning(
                    "Failed to fetch FleetDM policies",
                    status_code=response.status_code,
                )
                return created, updated

            data = response.json()
            policies = data.get("policies", [])

            for policy in policies:
                passing = policy.get("passing_host_count", 0)
                failing = policy.get("failing_host_count", 0)
                total = passing + failing

                entity = Entity(
                    name=f"Policy: {policy.get('name')}",
                    entity_type="security",
                    organization_id=fleetdm_org_id,
                    description=policy.get(
                        "description", f"FleetDM Policy: {policy.get('name')}"
                    ),
                    attributes={
                        "fleetdm_policy_id": policy.get("id"),
                        "policy_name": policy.get("name"),
                        "query": policy.get("query"),
                        "resolution": policy.get("resolution"),
                        "platform": policy.get("platform"),
                        "passing_host_count": passing,
                        "failing_host_count": failing,
                        "compliance_rate": (
                            round(passing / total * 100, 2) if total > 0 else 100
                        ),
                        "critical": policy.get("critical", False),
                        "provider": "fleetdm",
                        "issue_type": "policy",
                    },
                    tags=["fleetdm", "policy", "security", "compliance"],
                    is_active=True,
                )

                # Check if entity exists
                existing = await self.elder_client.list_entities(
                    organization_id=fleetdm_org_id,
                    entity_type="security",
                )

                found = None
                for item in existing.get("items", []):
                    if item.get("attributes", {}).get(
                        "fleetdm_policy_id"
                    ) == policy.get("id"):
                        found = item
                        break

                if found:
                    await self.elder_client.update_entity(found["id"], entity)
                    updated += 1
                else:
                    await self.elder_client.create_entity(entity)
                    created += 1

        except Exception as e:
            self.logger.error("Failed to sync FleetDM policies", error=str(e))

        return created, updated

    async def sync(self) -> SyncResult:
        """Synchronize FleetDM resources to Elder.

        Returns:
            SyncResult with statistics
        """
        result = SyncResult(connector_name=self.name)
        self.logger.info("Starting FleetDM sync", url=settings.fleetdm_url)

        try:
            # Create FleetDM root organization
            fleetdm_org_id = await self._get_or_create_organization(
                "FleetDM",
                "FleetDM endpoint management platform",
            )
            result.organizations_created += 1

            # Sync teams as sub-organizations
            team_map = await self._sync_teams(fleetdm_org_id)
            result.organizations_created += len(team_map)

            # Sync hosts
            hosts_created, hosts_updated = await self._sync_hosts(
                fleetdm_org_id, team_map
            )
            result.entities_created += hosts_created
            result.entities_updated += hosts_updated

            # Sync vulnerabilities
            vulns_created, vulns_updated = await self._sync_vulnerabilities(
                fleetdm_org_id
            )
            result.entities_created += vulns_created
            result.entities_updated += vulns_updated

            # Sync software inventory
            software_created, software_updated = await self._sync_software(
                fleetdm_org_id
            )
            result.entities_created += software_created
            result.entities_updated += software_updated

            # Sync policies
            policies_created, policies_updated = await self._sync_policies(
                fleetdm_org_id
            )
            result.entities_created += policies_created
            result.entities_updated += policies_updated

            # Create relationships between entities
            relationships_created = await self._sync_relationships()

            self.logger.info(
                "FleetDM sync completed",
                total_ops=result.total_operations,
                orgs_created=result.organizations_created,
                entities_created=result.entities_created,
                entities_updated=result.entities_updated,
                relationships_created=relationships_created,
            )

        except Exception as e:
            error_msg = f"FleetDM sync failed: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            result.errors.append(error_msg)

        return result

    async def health_check(self) -> bool:
        """Check FleetDM API connectivity.

        Returns:
            True if healthy, False otherwise
        """
        try:
            if not self.http_client:
                return False

            response = await self.http_client.get("/api/v1/fleet/config")
            return response.status_code == 200
        except Exception as e:
            self.logger.warning("FleetDM health check failed", error=str(e))
            return False
