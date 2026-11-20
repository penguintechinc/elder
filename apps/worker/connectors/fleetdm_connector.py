"""FleetDM endpoint manager connector for syncing to Elder."""

# flake8: noqa: E501

import asyncio
from collections import Counter
from typing import Any

import httpx

from apps.worker.config.settings import settings
from apps.worker.connectors.base import BaseConnector, SyncResult
from apps.worker.utils.elder_client import ElderAPIClient, Entity, Organization
from shared.fleetdm_helpers import (
    _SENSITIVE_KEY_EXACT,
    _SENSITIVE_KEY_SUBSTRINGS,
    _cvss_to_severity,
    _extract_safe_mdm,
    _is_sensitive_key,
    _parse_retry_after,
    _safe_json,
    _sanitize_dict,
)


def _resolve_fleet_org_id(
    item: dict[str, Any], fleetdm_org_id: int, fleet_map: dict[int, int]
) -> tuple[int | None, int]:
    """Return (fleet_id_or_none, target_elder_org_id) for a Fleet/Team-scoped item."""
    fid = item.get("fleet_id")
    if fid is None:
        fid = item.get("team_id")
    if fid is None:
        return None, fleetdm_org_id
    return fid, fleet_map.get(int(fid), fleetdm_org_id)


def _extract_entity_attrs(item: dict[str, Any]) -> dict[str, Any]:
    """Safely extract attributes/metadata from an Elder entity payload."""
    attrs = item.get("metadata")
    if isinstance(attrs, dict) and attrs:
        return attrs
    legacy_attrs = item.get("attributes")
    return legacy_attrs if isinstance(legacy_attrs, dict) else {}


class FleetDMConnector(BaseConnector):
    """Connector for FleetDM endpoint management platform (Fleet v4.95.0 / Fleet 5).

    Syncs fleets/teams, posture summaries, hosts, OS versions, software
    inventory, vulnerabilities, policies, labels, MDM configuration profiles,
    scripts, reports, custom host vitals, and entity relationships without
    persisting PII or secret material.
    """

    def __init__(self) -> None:
        """Initialize FleetDM connector."""
        super().__init__("fleetdm")
        self.elder_client: ElderAPIClient | None = None
        self.http_client: httpx.AsyncClient | None = None
        self.is_premium: bool = False
        self.mdm_enabled: bool = False
        self.fleet_version: str | None = None
        self.organization_cache: dict[str, int] = {}
        self.host_entity_cache: dict[int, int] = {}
        self.software_entity_cache: dict[int, int] = {}
        self.os_entity_cache: dict[int, int] = {}
        self.vuln_entity_cache: dict[str, int] = {}
        self.policy_cache: dict[int, tuple[int, int]] = {}
        self.label_entity_cache: dict[int, int] = {}
        self.vuln_details_cache: dict[str, dict[str, Any]] = {}

    def _clear_sync_caches(self) -> None:
        """Clear per-sync entity and detail caches."""
        self.host_entity_cache.clear()
        self.software_entity_cache.clear()
        self.os_entity_cache.clear()
        self.vuln_entity_cache.clear()
        self.policy_cache.clear()
        self.label_entity_cache.clear()
        self.vuln_details_cache.clear()

    async def connect(self) -> None:
        """Establish connection to FleetDM API and Elder API."""
        url = (settings.fleetdm_url or "").strip().rstrip("/")
        token = (settings.fleetdm_api_token or "").strip()
        if not url or not token:
            raise ValueError("FleetDM URL and API token are required")

        self.logger.info("Connecting to FleetDM API", url=url)

        try:
            self.http_client = httpx.AsyncClient(
                base_url=url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=30.0,
                verify=settings.fleetdm_verify_ssl,
            )

            status_code, cfg = await self._fleet_get("/api/v1/fleet/config")
            if status_code != 200 or not isinstance(cfg, dict):
                raise RuntimeError(f"FleetDM API connection failed: HTTP {status_code}")

            license_cfg = cfg.get("license") or {}
            mdm_cfg = cfg.get("mdm") or {}
            self.is_premium = license_cfg.get("tier") == "premium"
            self.mdm_enabled = bool(mdm_cfg.get("enabled_and_configured"))

            try:
                ver_status, ver_data = await self._fleet_get("/api/v1/fleet/version")
                if ver_status == 200 and isinstance(ver_data, dict):
                    self.fleet_version = ver_data.get("version")
            except Exception as e:
                self.logger.debug("Unable to fetch FleetDM version", error=str(e))

            self.logger.info(
                "FleetDM API connection established",
                fleet_version=self.fleet_version,
                is_premium=self.is_premium,
                mdm_enabled=self.mdm_enabled,
            )
        except Exception as e:
            if self.http_client is not None:
                await self.http_client.aclose()
                self.http_client = None
            self.logger.error("Failed to connect to FleetDM API", error=str(e))
            raise

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
            self.elder_client = None

        self.organization_cache.clear()
        self._clear_sync_caches()
        self.logger.info("FleetDM connector disconnected")

    async def _fleet_get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        fallback_paths: list[str] | None = None,
    ) -> tuple[int, Any]:
        """Execute GET against FleetDM API with retries, 429 handling, and fallbacks.

        Handles HTTP 429 (Retry-After + bounded backoff), transient 5xx and
        transport errors (up to 3 attempts), and Fleet 4.x/5 route fallbacks
        on 404/410. Returns (status_code, json_or_none) without raising on
        204/402/403/404/410.
        """
        if not self.http_client:
            raise RuntimeError("FleetDM HTTP client is not connected")

        paths = [path] + list(fallback_paths or [])
        max_attempts = 3

        for idx, candidate_path in enumerate(paths):
            has_next_fallback = idx < len(paths) - 1
            for attempt in range(max_attempts):
                try:
                    response = await self.http_client.get(candidate_path, params=params)
                except httpx.RequestError as exc:
                    if attempt < max_attempts - 1:
                        await asyncio.sleep(min(0.5 * (2**attempt), 4.0))
                        continue
                    self.logger.error(
                        "FleetDM transport error",
                        path=candidate_path,
                        error=str(exc),
                    )
                    raise

                status = response.status_code

                if status == 429:
                    if attempt < max_attempts - 1:
                        delay = _parse_retry_after(response, attempt)
                        await asyncio.sleep(delay)
                        continue
                    return 429, _safe_json(response)

                if 500 <= status < 600:
                    if attempt < max_attempts - 1:
                        await asyncio.sleep(min(0.5 * (2**attempt), 4.0))
                        continue
                    return status, _safe_json(response)

                if status in (404, 410) and has_next_fallback:
                    break

                if status == 204:
                    return 204, None

                return status, _safe_json(response)

        return 404, None

    async def _fleet_paginate_with_status(
        self,
        path: str,
        result_key: str,
        params: dict[str, Any] | None = None,
        fallback_paths: list[str] | None = None,
        fallback_result_key: str | None = None,
        max_pages: int = 500,
    ) -> tuple[int, list[dict[str, Any]]]:
        """Paginate a FleetDM list endpoint and return (last_status, items)."""
        per_page = settings.fleetdm_per_page
        all_items: list[dict[str, Any]] = []
        last_status = 200

        for page in range(max_pages):
            req_params = {**(params or {}), "page": page, "per_page": per_page}
            status, data = await self._fleet_get(
                path,
                params=req_params,
                fallback_paths=fallback_paths,
            )
            last_status = status

            if status != 200 or not isinstance(data, dict):
                if status in (402, 403):
                    self.logger.debug(
                        "FleetDM endpoint skipped due to license/permissions",
                        path=path,
                        status_code=status,
                    )
                elif status in (404, 410):
                    self.logger.debug(
                        "FleetDM endpoint unavailable",
                        path=path,
                        status_code=status,
                    )
                else:
                    self.logger.warning(
                        "Failed to fetch FleetDM page",
                        path=path,
                        page=page,
                        status_code=status,
                    )
                break

            items = data.get(result_key)
            if not isinstance(items, list) and fallback_result_key:
                items = data.get(fallback_result_key)

            if not isinstance(items, list) or not items:
                break

            all_items.extend(items)

            meta = data.get("meta")
            if (
                isinstance(meta, dict)
                and "has_next_results" in meta
                and meta["has_next_results"] is not None
            ):
                if not meta["has_next_results"]:
                    break
            elif len(items) < per_page:
                break

        return last_status, all_items

    async def _fleet_paginate(
        self,
        path: str,
        result_key: str,
        params: dict[str, Any] | None = None,
        fallback_paths: list[str] | None = None,
        fallback_result_key: str | None = None,
        max_pages: int = 500,
    ) -> list[dict[str, Any]]:
        """Paginate a FleetDM list endpoint cleanly across pages."""
        _, items = await self._fleet_paginate_with_status(
            path=path,
            result_key=result_key,
            params=params,
            fallback_paths=fallback_paths,
            fallback_result_key=fallback_result_key,
            max_pages=max_pages,
        )
        return items

    def _require_elder_client(self) -> ElderAPIClient:
        """Return connected ElderAPIClient or raise if not connected."""
        if self.elder_client is None:
            raise RuntimeError("Elder API client is not connected")
        return self.elder_client

    async def _get_or_create_organization(
        self,
        name: str,
        description: str,
        parent_id: int | None = None,
    ) -> int:
        """Get or create an organization in Elder."""
        cache_key = f"{parent_id or 'root'}:{name}"
        if cache_key in self.organization_cache:
            return self.organization_cache[cache_key]

        elder = self._require_elder_client()
        response = await elder.list_organizations(per_page=1000)
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
            created = await elder.create_organization(org_data)
            org_id = created["id"]
            self.organization_cache[cache_key] = org_id
            return org_id
        if settings.default_organization_id:
            return settings.default_organization_id
        raise ValueError(f"Organization '{name}' not found and auto-creation disabled")

    async def _upsert_entity_with_status(self, entity: Entity) -> tuple[int, bool]:
        """Create or update an Elder entity using external_id lookup."""
        elder = self._require_elder_client()
        existing = None
        if entity.external_id:
            existing = await elder.get_entity_by_external_id(
                entity.external_id,
                organization_id=entity.organization_id,
            )

        if existing:
            await elder.update_entity(existing["id"], entity)
            return existing["id"], False

        created = await elder.create_entity(entity)
        return created["id"], True

    async def _upsert_entity(self, entity: Entity) -> int:
        """Create or update an Elder entity and return its Elder entity ID."""
        entity_id, _ = await self._upsert_entity_with_status(entity)
        return entity_id

    async def _sync_fleets(self, fleetdm_org_id: int) -> dict[int, int]:
        """Sync FleetDM fleets (Fleet 5) / teams (Fleet 4.x) as sub-organizations."""
        fleet_map: dict[int, int] = {}

        try:
            status, fleets = await self._fleet_paginate_with_status(
                "/api/v1/fleet/fleets",
                "fleets",
                fallback_paths=["/api/v1/fleet/teams"],
                fallback_result_key="teams",
            )
            if status in (402, 403):
                self.logger.info("FleetDM fleets/teams require premium; skipping")
                return fleet_map

            for fleet in fleets:
                fid = fleet.get("id")
                if fid is None:
                    continue
                fname = fleet.get("name") or f"Fleet {fid}"
                fleet_org_id = await self._get_or_create_organization(
                    name=f"Fleet: {fname}",
                    description=fleet.get("description") or f"FleetDM Fleet: {fname}",
                    parent_id=fleetdm_org_id,
                )
                fleet_map[int(fid)] = fleet_org_id
        except Exception as e:
            self.logger.error("Failed to sync FleetDM fleets", error=str(e))

        return fleet_map

    async def _sync_posture(self, fleetdm_org_id: int) -> tuple[int, int]:
        """Sync fleet-wide endpoint/MDM/encryption posture summary."""
        created = 0
        updated = 0

        try:
            endpoints = {
                "host_summary": "/api/v1/fleet/host_summary",
                "mdm_summary": "/api/v1/fleet/hosts/summary/mdm",
                "disk_encryption": "/api/v1/fleet/disk_encryption",
                "configuration_profiles_summary": (
                    "/api/v1/fleet/configuration_profiles/summary"
                ),
            }
            posture_data: dict[str, Any] = {}
            for key, endpoint in endpoints.items():
                status, data = await self._fleet_get(endpoint)
                if status == 200 and isinstance(data, dict):
                    posture_data[key] = _sanitize_dict(data)

            if not posture_data:
                return 0, 0

            entity = Entity(
                name="FleetDM Endpoint Posture",
                entity_type="security",
                sub_type="endpoint_posture",
                organization_id=fleetdm_org_id,
                external_id="fleetdm:posture",
                description="FleetDM aggregated endpoint, MDM, and disk encryption posture",
                attributes={
                    **posture_data,
                    "fleet_version": self.fleet_version,
                    "is_premium": self.is_premium,
                    "mdm_enabled": self.mdm_enabled,
                    "provider": "fleetdm",
                },
                tags=["fleetdm", "posture", "security", "mdm"],
                is_active=True,
            )
            _, is_new = await self._upsert_entity_with_status(entity)
            if is_new:
                created += 1
            else:
                updated += 1
        except Exception as e:
            self.logger.error("Failed to sync FleetDM posture", error=str(e))

        return created, updated

    async def _fetch_host_details(self, host_id: int) -> dict[str, Any]:
        """Fetch PII-free counts/summaries of host certs and config profiles."""
        details: dict[str, Any] = {}

        cert_status, cert_data = await self._fleet_get(
            f"/api/v1/fleet/hosts/{host_id}/certificates"
        )
        if cert_status == 200 and isinstance(cert_data, dict):
            certs = cert_data.get("certificates") or []
            details["certificates_count"] = len(certs)
            details["certificate_sources"] = sorted(
                {
                    str(c.get("source"))
                    for c in certs
                    if isinstance(c, dict) and c.get("source")
                }
            )

        prof_status, prof_data = await self._fleet_get(
            f"/api/v1/fleet/hosts/{host_id}/configuration_profiles"
        )
        if prof_status == 200 and isinstance(prof_data, dict):
            profiles = prof_data.get("profiles") or []
            details["profiles_count"] = len(profiles)
            details["profiles_status_summary"] = dict(
                Counter(
                    str(p.get("status") or "unknown")
                    for p in profiles
                    if isinstance(p, dict)
                )
            )

        return details

    async def _sync_hosts(
        self, fleetdm_org_id: int, fleet_map: dict[int, int]
    ) -> tuple[int, int]:
        """Sync FleetDM hosts to Elder without persisting PII or secrets."""
        created = 0
        updated = 0

        try:
            hosts = await self._fleet_paginate(
                "/api/v1/fleet/hosts",
                "hosts",
                params={"include_device_status": "true"},
            )

            for host in hosts:
                host_id = host.get("id")
                if host_id is None:
                    continue

                fleet_id, org_id = _resolve_fleet_org_id(
                    host, fleetdm_org_id, fleet_map
                )
                fleet_name = host.get("fleet_name") or host.get("team_name")

                raw_battery = host.get("battery")
                battery = None
                if isinstance(raw_battery, dict):
                    battery = {
                        k: raw_battery.get(k)
                        for k in ("health", "cycle_count")
                        if k in raw_battery
                    }

                mdm_safe = _extract_safe_mdm(host.get("mdm"))

                memory = host.get("memory")
                attrs: dict[str, Any] = {
                    "fleetdm_host_id": host_id,
                    "uuid": host.get("uuid"),
                    "hostname": host.get("hostname"),
                    "computer_name": host.get("computer_name"),
                    "display_name": host.get("display_name"),
                    "platform": host.get("platform"),
                    "platform_like": host.get("platform_like"),
                    "os_version": host.get("os_version"),
                    "build": host.get("build"),
                    "osquery_version": host.get("osquery_version"),
                    "hardware_model": host.get("hardware_model"),
                    "hardware_marketing_name": host.get("hardware_marketing_name"),
                    "hardware_serial": host.get("hardware_serial"),
                    "hardware_vendor": host.get("hardware_vendor"),
                    "cpu_type": host.get("cpu_type"),
                    "cpu_brand": host.get("cpu_brand"),
                    "cpu_physical_cores": host.get("cpu_physical_cores"),
                    "cpu_logical_cores": host.get("cpu_logical_cores"),
                    "memory": memory,
                    "memory_gb": (round(memory / (1024**3), 2) if memory else None),
                    "gigs_disk_space_available": host.get("gigs_disk_space_available"),
                    "percent_disk_space_available": host.get(
                        "percent_disk_space_available"
                    ),
                    "gigs_total_disk_space": host.get("gigs_total_disk_space"),
                    "disk_encryption_enabled": host.get("disk_encryption_enabled"),
                    "primary_ip": host.get("primary_ip"),
                    "primary_mac": host.get("primary_mac"),
                    "public_ip": host.get("public_ip"),
                    "status": host.get("status"),
                    "seen_time": host.get("seen_time"),
                    "last_enrolled_at": host.get("last_enrolled_at"),
                    "last_restarted_at": host.get("last_restarted_at"),
                    "detail_updated_at": host.get("detail_updated_at"),
                    "software_updated_at": host.get("software_updated_at"),
                    "policy_updated_at": host.get("policy_updated_at"),
                    "uptime": host.get("uptime"),
                    "group_tag": host.get("group_tag"),
                    "fleet_id": fleet_id,
                    "fleet_name": fleet_name,
                    "issues": host.get("issues") or {},
                    "battery": battery,
                    "mdm": mdm_safe,
                    "provider": "fleetdm",
                }

                if settings.fleetdm_sync_host_details:
                    attrs.update(await self._fetch_host_details(int(host_id)))

                host_name = (
                    host.get("hostname")
                    or host.get("computer_name")
                    or host.get("display_name")
                    or f"Host-{host_id}"
                )[:255]

                entity = Entity(
                    name=host_name,
                    entity_type="compute",
                    sub_type="endpoint",
                    organization_id=org_id,
                    external_id=f"fleetdm:host:{host_id}",
                    description=f"FleetDM managed endpoint: {host_name}",
                    attributes=attrs,
                    status_metadata={
                        "status": host.get("status"),
                        "seen_time": host.get("seen_time"),
                    },
                    tags=[
                        "fleetdm",
                        "endpoint",
                        str(host.get("platform") or "unknown"),
                    ],
                    is_active=host.get("status") == "online",
                )

                entity_id, is_new = await self._upsert_entity_with_status(entity)
                self.host_entity_cache[int(host_id)] = entity_id
                if is_new:
                    created += 1
                else:
                    updated += 1
        except Exception as e:
            self.logger.error("Failed to sync FleetDM hosts", error=str(e))

        return created, updated

    async def _sync_os_versions(self, fleetdm_org_id: int) -> tuple[int, int]:
        """Sync FleetDM operating system versions to Elder."""
        created = 0
        updated = 0

        try:
            os_versions = await self._fleet_paginate(
                "/api/v1/fleet/os_versions",
                "os_versions",
            )

            for os_item in os_versions:
                os_id = os_item.get("os_version_id")
                if os_id is None:
                    os_id = os_item.get("id")
                if os_id is None:
                    continue

                vulns = os_item.get("vulnerabilities") or []
                cves = [
                    v.get("cve") if isinstance(v, dict) else str(v)
                    for v in vulns
                    if (isinstance(v, dict) and v.get("cve")) or isinstance(v, str)
                ]
                vuln_count = os_item.get("vulnerabilities_count")
                if vuln_count is None:
                    vuln_count = len(vulns)

                name_only = os_item.get("name_only") or "OS"
                version = os_item.get("version") or ""
                display_name = (
                    os_item.get("name") or f"{name_only} {version}".strip()
                )[:255]
                hosts_count = os_item.get("hosts_count", 0) or 0

                entity = Entity(
                    name=display_name or f"OS-{os_id}",
                    entity_type="compute",
                    sub_type="os_version",
                    organization_id=fleetdm_org_id,
                    external_id=f"fleetdm:os:{os_id}",
                    description=f"FleetDM OS version: {display_name}",
                    attributes={
                        "os_version_id": os_id,
                        "name_only": os_item.get("name_only"),
                        "version": os_item.get("version"),
                        "platform": os_item.get("platform"),
                        "hosts_count": hosts_count,
                        "generated_cpes": os_item.get("generated_cpes") or [],
                        "vulnerabilities_count": vuln_count,
                        "cves": cves,
                        "provider": "fleetdm",
                    },
                    tags=[
                        "fleetdm",
                        "os_version",
                        str(os_item.get("platform") or "unknown"),
                    ],
                    is_active=hosts_count > 0,
                )

                entity_id, is_new = await self._upsert_entity_with_status(entity)
                self.os_entity_cache[int(os_id)] = entity_id
                if is_new:
                    created += 1
                else:
                    updated += 1
        except Exception as e:
            self.logger.error("Failed to sync FleetDM OS versions", error=str(e))

        return created, updated

    async def _fetch_software_titles_map(
        self,
    ) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
        """Best-effort fetch of /software/titles to enrich version metadata."""
        version_map: dict[int, dict[str, Any]] = {}
        title_map: dict[int, dict[str, Any]] = {}

        try:
            titles = await self._fleet_paginate(
                "/api/v1/fleet/software/titles",
                "software_titles",
                fallback_result_key="titles",
            )
            for title in titles:
                tid = title.get("id")
                sw_pkg = title.get("software_package")
                app_store = title.get("app_store_app")
                hash_sha256 = (
                    sw_pkg.get("hash_sha256") if isinstance(sw_pkg, dict) else None
                ) or title.get("hash_sha256")
                meta = {
                    "software_title_id": tid,
                    "ai_tool": title.get("ai_tool"),
                    "extension_for": title.get("extension_for"),
                    "bundle_identifier": title.get("bundle_identifier"),
                    "hash_sha256": hash_sha256,
                    "has_installer_package": bool(sw_pkg),
                    "has_app_store_app": bool(app_store),
                }
                if tid is not None:
                    title_map[int(tid)] = meta
                for ver in title.get("versions") or []:
                    if isinstance(ver, dict) and ver.get("id") is not None:
                        version_map[int(ver["id"])] = meta
        except Exception as e:
            self.logger.debug("Best-effort software/titles fetch skipped", error=str(e))

        return version_map, title_map

    async def _sync_software(self, fleetdm_org_id: int) -> tuple[int, int]:
        """Sync FleetDM software inventory to Elder."""
        created = 0
        updated = 0

        try:
            software_list = await self._fleet_paginate(
                "/api/v1/fleet/software/versions",
                "software",
                params={"without_vulnerability_details": "true"},
                fallback_paths=["/api/v1/fleet/software"],
                fallback_result_key="software",
            )
            version_title_map, title_id_map = await self._fetch_software_titles_map()

            for software in software_list:
                sw_id = software.get("id")
                if sw_id is None:
                    continue

                tmeta = (
                    version_title_map.get(int(sw_id))
                    or (
                        title_id_map.get(int(software["title_id"]))
                        if software.get("title_id") is not None
                        else None
                    )
                    or {}
                )

                vulns_count = software.get("vulnerabilities_count")
                if vulns_count is None:
                    vulns_count = len(software.get("vulnerabilities") or [])

                hosts_count = software.get("hosts_count", 0) or 0
                sw_name = software.get("name") or f"Software-{sw_id}"

                entity = Entity(
                    name=f"Software: {sw_name}"[:255],
                    entity_type="compute",
                    sub_type="software",
                    organization_id=fleetdm_org_id,
                    external_id=f"fleetdm:software:{sw_id}",
                    description=f"{sw_name} v{software.get('version') or 'unknown'}",
                    attributes={
                        "fleetdm_software_id": sw_id,
                        "software_name": sw_name,
                        "version": software.get("version"),
                        "source": software.get("source"),
                        "bundle_identifier": (
                            software.get("bundle_identifier")
                            or tmeta.get("bundle_identifier")
                        ),
                        "hosts_count": hosts_count,
                        "vulnerabilities_count": vulns_count,
                        "last_opened_at": software.get("last_opened_at"),
                        "generated_cpe": software.get("generated_cpe"),
                        "software_title_id": (
                            software.get("title_id") or tmeta.get("software_title_id")
                        ),
                        "ai_tool": (
                            software.get("ai_tool")
                            if software.get("ai_tool") is not None
                            else tmeta.get("ai_tool")
                        ),
                        "extension_for": (
                            software.get("extension_for") or tmeta.get("extension_for")
                        ),
                        "hash_sha256": (
                            software.get("hash_sha256") or tmeta.get("hash_sha256")
                        ),
                        "has_installer_package": tmeta.get(
                            "has_installer_package", False
                        ),
                        "has_app_store_app": tmeta.get("has_app_store_app", False),
                        "provider": "fleetdm",
                        "resource_type": "software",
                    },
                    tags=[
                        "fleetdm",
                        "software",
                        "application",
                        str(software.get("source") or "unknown"),
                    ],
                    is_active=hosts_count > 0,
                )

                entity_id, is_new = await self._upsert_entity_with_status(entity)
                self.software_entity_cache[int(sw_id)] = entity_id
                if is_new:
                    created += 1
                else:
                    updated += 1
        except Exception as e:
            self.logger.error("Failed to sync FleetDM software", error=str(e))

        return created, updated

    async def _fetch_vulnerability_details(
        self, cve: str, list_item: dict[str, Any]
    ) -> dict[str, Any]:
        """Fetch /vulnerabilities/{cve} details cleanly, handling 204/404."""
        detail: dict[str, Any] = {}
        try:
            status, payload = await self._fleet_get(
                f"/api/v1/fleet/vulnerabilities/{cve}"
            )
            if status == 200 and isinstance(payload, dict):
                raw_detail = payload.get("vulnerability", payload)
                if isinstance(raw_detail, dict):
                    detail = raw_detail
        except Exception as e:
            self.logger.debug(
                "Failed to fetch vulnerability details", cve=cve, error=str(e)
            )

        raw_sw = detail.get("software") or list_item.get("software") or []
        if isinstance(raw_sw, dict):
            sw_list = [raw_sw]
        elif isinstance(raw_sw, list):
            sw_list = [s for s in raw_sw if isinstance(s, dict)]
        else:
            sw_list = []

        raw_os = detail.get("os_versions") or list_item.get("os_versions") or []
        os_list = (
            [o for o in raw_os if isinstance(o, dict)]
            if isinstance(raw_os, list)
            else []
        )

        affected_software_ids = [
            int(s["id"]) for s in sw_list if s.get("id") is not None
        ]
        affected_os_version_ids = []
        for os_entry in os_list:
            oid = os_entry.get("os_version_id")
            if oid is None:
                oid = os_entry.get("id")
            if oid is not None:
                affected_os_version_ids.append(int(oid))

        resolved_in_versions = sorted(
            {
                str(entry["resolved_in_version"])
                for entry in sw_list + os_list
                if entry.get("resolved_in_version")
            }
        )
        software_names = sorted({str(s["name"]) for s in sw_list if s.get("name")})

        return {
            "detail": detail,
            "affected_software_ids": affected_software_ids,
            "affected_os_version_ids": affected_os_version_ids,
            "resolved_in_versions": resolved_in_versions,
            "software_names": software_names,
        }

    async def _sync_vulnerabilities(self, fleetdm_org_id: int) -> tuple[int, int]:
        """Sync FleetDM vulnerabilities to Elder as security entities."""
        created = 0
        updated = 0

        try:
            vulnerabilities = await self._fleet_paginate(
                "/api/v1/fleet/vulnerabilities",
                "vulnerabilities",
            )

            for idx, vuln in enumerate(vulnerabilities):
                cve = vuln.get("cve")
                if not cve:
                    continue

                if idx < 500:
                    enriched = await self._fetch_vulnerability_details(cve, vuln)
                else:
                    enriched = {
                        "detail": {},
                        "affected_software_ids": [],
                        "affected_os_version_ids": [],
                        "resolved_in_versions": [],
                        "software_names": [],
                    }
                self.vuln_details_cache[cve] = enriched
                detail = enriched["detail"]

                cvss = vuln.get("cvss_score")
                if cvss is None:
                    cvss = detail.get("cvss_score")
                severity = _cvss_to_severity(cvss)

                hosts_count = vuln.get("hosts_count")
                if hosts_count is None:
                    hosts_count = detail.get("hosts_count", 0)
                hosts_count = hosts_count or 0

                cve_description = vuln.get("cve_description") or detail.get(
                    "cve_description"
                )
                details_link = vuln.get("details_link") or detail.get("details_link")

                entity = Entity(
                    name=f"CVE: {cve}"[:255],
                    entity_type="security",
                    sub_type="vulnerability",
                    organization_id=fleetdm_org_id,
                    external_id=f"fleetdm:vuln:{cve}",
                    description=(
                        cve_description or details_link or f"Vulnerability: {cve}"
                    ),
                    attributes={
                        "fleetdm_vuln_id": cve,
                        "cve": cve,
                        "cvss_score": cvss,
                        "severity": severity,
                        "epss_probability": (
                            vuln.get("epss_probability")
                            if vuln.get("epss_probability") is not None
                            else detail.get("epss_probability")
                        ),
                        "cisa_known_exploit": (
                            vuln.get("cisa_known_exploit")
                            if vuln.get("cisa_known_exploit") is not None
                            else detail.get("cisa_known_exploit")
                        ),
                        "cve_published": (
                            vuln.get("cve_published")
                            or detail.get("cve_published")
                            or vuln.get("published")
                        ),
                        "cve_description": cve_description,
                        "created_at": vuln.get("created_at")
                        or detail.get("created_at"),
                        "hosts_count": hosts_count,
                        "details_link": details_link,
                        "affected_software_ids": enriched["affected_software_ids"],
                        "affected_os_version_ids": enriched["affected_os_version_ids"],
                        "resolved_in_versions": enriched["resolved_in_versions"],
                        "software_names": enriched["software_names"],
                        "provider": "fleetdm",
                        "issue_type": "vulnerability",
                    },
                    tags=["fleetdm", "vulnerability", "security", severity, cve],
                    is_active=hosts_count > 0,
                )

                entity_id, is_new = await self._upsert_entity_with_status(entity)
                self.vuln_entity_cache[cve] = entity_id
                if is_new:
                    created += 1
                else:
                    updated += 1
        except Exception as e:
            self.logger.error("Failed to sync FleetDM vulnerabilities", error=str(e))

        return created, updated

    async def _sync_policies(
        self, fleetdm_org_id: int, fleet_map: dict[int, int]
    ) -> tuple[int, int]:
        """Sync FleetDM global and per-fleet policies (Fleet 5 + Fleet 4.x fallback)."""
        created = 0
        updated = 0

        try:
            all_policies: list[dict[str, Any]] = []
            status, global_policies = await self._fleet_paginate_with_status(
                "/api/v1/fleet/policies",
                "policies",
            )

            if status in (404, 410):
                # Fleet 4.x route layout
                _, global_policies = await self._fleet_paginate_with_status(
                    "/api/v1/fleet/global/policies",
                    "policies",
                )
                all_policies.extend(global_policies)
                for fid in fleet_map:
                    _, fleet_policies = await self._fleet_paginate_with_status(
                        f"/api/v1/fleet/fleets/{fid}/policies",
                        "policies",
                        fallback_paths=[f"/api/v1/fleet/teams/{fid}/policies"],
                        fallback_result_key="policies",
                    )
                    all_policies.extend(fleet_policies)
            else:
                all_policies.extend(global_policies)
                for fid in fleet_map:
                    _, fleet_policies = await self._fleet_paginate_with_status(
                        "/api/v1/fleet/policies",
                        "policies",
                        params={"fleet_id": fid},
                    )
                    all_policies.extend(fleet_policies)

            seen_policy_ids: set[int] = set()
            for policy in all_policies:
                pid = policy.get("id")
                if pid is None:
                    continue
                pid_int = int(pid)
                if pid_int in seen_policy_ids:
                    continue
                seen_policy_ids.add(pid_int)

                passing = policy.get("passing_host_count", 0) or 0
                failing = policy.get("failing_host_count", 0) or 0
                total = passing + failing

                policy_fleet_id, target_org_id = _resolve_fleet_org_id(
                    policy, fleetdm_org_id, fleet_map
                )

                policy_name = policy.get("name") or f"Policy-{pid_int}"
                entity = Entity(
                    name=f"Policy: {policy_name}"[:255],
                    entity_type="security",
                    sub_type="policy",
                    organization_id=target_org_id,
                    external_id=f"fleetdm:policy:{pid_int}",
                    description=(
                        policy.get("description") or f"FleetDM Policy: {policy_name}"
                    ),
                    attributes={
                        "fleetdm_policy_id": pid_int,
                        "policy_name": policy_name,
                        "query": policy.get("query"),
                        "resolution": policy.get("resolution"),
                        "platform": policy.get("platform"),
                        "critical": bool(policy.get("critical", False)),
                        "type": policy.get("type"),
                        "team_id": policy.get("team_id"),
                        "fleet_id": policy_fleet_id,
                        "passing_host_count": passing,
                        "failing_host_count": failing,
                        "compliance_rate": (
                            round(passing / total * 100, 2) if total > 0 else 100
                        ),
                        "calendar_events_enabled": policy.get(
                            "calendar_events_enabled"
                        ),
                        "conditional_access_enabled": policy.get(
                            "conditional_access_enabled"
                        ),
                        "install_software": _sanitize_dict(
                            policy.get("install_software")
                        ),
                        "run_script": _sanitize_dict(policy.get("run_script")),
                        "labels_include_any": policy.get("labels_include_any"),
                        "labels_exclude_any": policy.get("labels_exclude_any"),
                        "provider": "fleetdm",
                        "issue_type": "policy",
                    },
                    tags=["fleetdm", "policy", "security", "compliance"],
                    is_active=True,
                )

                entity_id, is_new = await self._upsert_entity_with_status(entity)
                self.policy_cache[pid_int] = (entity_id, int(failing))
                if is_new:
                    created += 1
                else:
                    updated += 1
        except Exception as e:
            self.logger.error("Failed to sync FleetDM policies", error=str(e))

        return created, updated

    async def _sync_labels(
        self, fleetdm_org_id: int, fleet_map: dict[int, int]
    ) -> tuple[int, int]:
        """Sync FleetDM labels and cache non-builtin labels for membership links."""
        created = 0
        updated = 0

        try:
            labels = await self._fleet_paginate("/api/v1/fleet/labels", "labels")

            for label in labels:
                lid = label.get("id")
                if lid is None:
                    continue
                lid_int = int(lid)
                label_type = label.get("label_type")
                label_fleet_id, target_org_id = _resolve_fleet_org_id(
                    label, fleetdm_org_id, fleet_map
                )
                label_name = label.get("name") or f"Label-{lid_int}"

                entity = Entity(
                    name=f"Label: {label_name}"[:255],
                    entity_type="compute",
                    sub_type="fleet_label",
                    organization_id=target_org_id,
                    external_id=f"fleetdm:label:{lid_int}",
                    description=(
                        label.get("description") or f"FleetDM label: {label_name}"
                    ),
                    attributes={
                        "fleetdm_label_id": lid_int,
                        "name": label_name,
                        "label_type": label_type,
                        "label_membership_type": label.get("label_membership_type"),
                        "query": label.get("query"),
                        "platform": label.get("platform"),
                        "host_count": label.get("host_count"),
                        "fleet_id": label_fleet_id,
                        "criteria": label.get("criteria"),
                        "provider": "fleetdm",
                    },
                    tags=["fleetdm", "label", str(label_type or "custom")],
                    is_active=True,
                )

                entity_id, is_new = await self._upsert_entity_with_status(entity)
                if label_type != "builtin":
                    self.label_entity_cache[lid_int] = entity_id
                if is_new:
                    created += 1
                else:
                    updated += 1
        except Exception as e:
            self.logger.error("Failed to sync FleetDM labels", error=str(e))

        return created, updated

    async def _sync_mdm_profiles(
        self, fleetdm_org_id: int, fleet_map: dict[int, int]
    ) -> tuple[int, int]:
        """Sync FleetDM MDM configuration profile metadata (no raw mobileconfig)."""
        created = 0
        updated = 0

        try:
            profiles: list[dict[str, Any]] = []
            status, global_profiles = await self._fleet_paginate_with_status(
                "/api/v1/fleet/configuration_profiles",
                "profiles",
            )
            if status in (402, 403, 404, 410):
                return 0, 0
            profiles.extend(global_profiles)

            for fid in fleet_map:
                _, fleet_profiles = await self._fleet_paginate_with_status(
                    "/api/v1/fleet/configuration_profiles",
                    "profiles",
                    params={"fleet_id": fid},
                )
                profiles.extend(fleet_profiles)

            seen_uuids: set[str] = set()
            for profile in profiles:
                profile_uuid = (
                    profile.get("profile_uuid")
                    or profile.get("uuid")
                    or profile.get("id")
                )
                if not profile_uuid:
                    continue
                profile_uuid_str = str(profile_uuid)
                if profile_uuid_str in seen_uuids:
                    continue
                seen_uuids.add(profile_uuid_str)

                pf_id, target_org_id = _resolve_fleet_org_id(
                    profile, fleetdm_org_id, fleet_map
                )
                prof_name = profile.get("name") or profile_uuid_str

                entity = Entity(
                    name=f"MDM Profile: {prof_name}"[:255],
                    entity_type="security",
                    sub_type="mdm_profile",
                    organization_id=target_org_id,
                    external_id=f"fleetdm:profile:{profile_uuid_str}"[:255],
                    description=f"FleetDM MDM profile: {prof_name}",
                    attributes={
                        "profile_uuid": profile_uuid_str,
                        "name": prof_name,
                        "platform": profile.get("platform"),
                        "identifier": profile.get("identifier"),
                        "self_service": profile.get("self_service"),
                        "hidden": profile.get("hidden"),
                        "checksum": profile.get("checksum"),
                        "team_id": profile.get("team_id"),
                        "fleet_id": pf_id,
                        "labels_include_all": profile.get("labels_include_all"),
                        "labels_include_any": profile.get("labels_include_any"),
                        "labels_exclude_any": profile.get("labels_exclude_any"),
                        "provider": "fleetdm",
                        "issue_type": "mdm_profile",
                    },
                    tags=[
                        "fleetdm",
                        "mdm",
                        "profile",
                        str(profile.get("platform") or "unknown"),
                    ],
                    is_active=True,
                )

                _, is_new = await self._upsert_entity_with_status(entity)
                if is_new:
                    created += 1
                else:
                    updated += 1
        except Exception as e:
            self.logger.error("Failed to sync FleetDM MDM profiles", error=str(e))

        return created, updated

    async def _sync_scripts_and_reports(
        self, fleetdm_org_id: int, fleet_map: dict[int, int]
    ) -> tuple[int, int]:
        """Sync FleetDM saved reports/queries, scripts, and custom host vitals."""
        created = 0
        updated = 0

        try:
            # 1. Reports / Saved Queries
            reports: list[dict[str, Any]] = []
            _, global_reports = await self._fleet_paginate_with_status(
                "/api/v1/fleet/reports",
                "reports",
                fallback_paths=["/api/v1/fleet/queries"],
                fallback_result_key="queries",
            )
            reports.extend(global_reports)
            for fid in fleet_map:
                _, fleet_reports = await self._fleet_paginate_with_status(
                    "/api/v1/fleet/reports",
                    "reports",
                    params={"fleet_id": fid},
                    fallback_paths=["/api/v1/fleet/queries"],
                    fallback_result_key="queries",
                )
                reports.extend(fleet_reports)

            seen_reports: set[int] = set()
            for report in reports:
                rid = report.get("id")
                if rid is None or int(rid) in seen_reports:
                    continue
                rid_int = int(rid)
                seen_reports.add(rid_int)
                rfid, target_org_id = _resolve_fleet_org_id(
                    report, fleetdm_org_id, fleet_map
                )
                rname = report.get("name") or f"Report-{rid_int}"
                entity = Entity(
                    name=f"Report: {rname}"[:255],
                    entity_type="compute",
                    sub_type="fleet_report",
                    organization_id=target_org_id,
                    external_id=f"fleetdm:report:{rid_int}",
                    description=(
                        report.get("description") or f"FleetDM report: {rname}"
                    ),
                    attributes={
                        "fleetdm_report_id": rid_int,
                        "name": rname,
                        "query": report.get("query"),
                        "interval": report.get("interval"),
                        "platform": report.get("platform"),
                        "min_osquery_version": report.get("min_osquery_version"),
                        "observer_can_run": report.get("observer_can_run"),
                        "automations_enabled": report.get("automations_enabled"),
                        "logging": report.get("logging"),
                        "discard_data": report.get("discard_data"),
                        "fleet_id": rfid,
                        "team_id": report.get("team_id"),
                        "provider": "fleetdm",
                    },
                    tags=["fleetdm", "report", "osquery"],
                    is_active=True,
                )
                _, is_new = await self._upsert_entity_with_status(entity)
                if is_new:
                    created += 1
                else:
                    updated += 1

            # 2. Scripts
            scripts: list[dict[str, Any]] = []
            status, global_scripts = await self._fleet_paginate_with_status(
                "/api/v1/fleet/scripts",
                "scripts",
            )
            if status == 200:
                scripts.extend(global_scripts)
                for fid in fleet_map:
                    _, fleet_scripts = await self._fleet_paginate_with_status(
                        "/api/v1/fleet/scripts",
                        "scripts",
                        params={"fleet_id": fid},
                    )
                    scripts.extend(fleet_scripts)

            seen_scripts: set[int] = set()
            for script in scripts:
                sid = script.get("id")
                if sid is None or int(sid) in seen_scripts:
                    continue
                sid_int = int(sid)
                seen_scripts.add(sid_int)
                sfid, target_org_id = _resolve_fleet_org_id(
                    script, fleetdm_org_id, fleet_map
                )
                sname = script.get("name") or f"Script-{sid_int}"
                entity = Entity(
                    name=f"Script: {sname}"[:255],
                    entity_type="compute",
                    sub_type="fleet_script",
                    organization_id=target_org_id,
                    external_id=f"fleetdm:script:{sid_int}",
                    description=f"FleetDM script: {sname}",
                    attributes={
                        "fleetdm_script_id": sid_int,
                        "name": sname,
                        "fleet_id": sfid,
                        "team_id": script.get("team_id"),
                        "created_at": script.get("created_at"),
                        "updated_at": script.get("updated_at"),
                        "provider": "fleetdm",
                    },
                    tags=["fleetdm", "script"],
                    is_active=True,
                )
                _, is_new = await self._upsert_entity_with_status(entity)
                if is_new:
                    created += 1
                else:
                    updated += 1

            # 3. Custom Host Vitals
            vitals = await self._fleet_paginate(
                "/api/v1/fleet/custom_host_vitals",
                "custom_host_vitals",
                fallback_result_key="vitals",
            )
            for vital in vitals:
                vid = vital.get("id")
                if vid is None:
                    continue
                vid_int = int(vid)
                vname = vital.get("name") or f"Vital-{vid_int}"
                entity = Entity(
                    name=f"Custom Vital: {vname}"[:255],
                    entity_type="compute",
                    sub_type="fleet_custom_vital",
                    organization_id=fleetdm_org_id,
                    external_id=f"fleetdm:vital:{vid_int}",
                    description=f"FleetDM custom host vital: {vname}",
                    attributes={
                        "fleetdm_vital_id": vid_int,
                        "name": vname,
                        "query": vital.get("query"),
                        "platform": vital.get("platform"),
                        "provider": "fleetdm",
                    },
                    tags=["fleetdm", "custom_vital"],
                    is_active=True,
                )
                _, is_new = await self._upsert_entity_with_status(entity)
                if is_new:
                    created += 1
                else:
                    updated += 1
        except Exception as e:
            self.logger.error(
                "Failed to sync FleetDM scripts/reports/vitals", error=str(e)
            )

        return created, updated

    async def _link_paginated_hosts(
        self,
        path: str,
        params: dict[str, Any] | None,
        source_or_target_id: int,
        dep_type: str,
        description: str,
        host_is_source: bool = False,
    ) -> int:
        """Link paginated /hosts results to or from a cached FleetDM entity."""
        elder = self._require_elder_client()
        count = 0
        hosts = await self._fleet_paginate(path, "hosts", params=params)
        for host in hosts:
            hid = host.get("id")
            if hid is None:
                continue
            host_entity_id = self.host_entity_cache.get(int(hid))
            if not host_entity_id:
                continue
            src = host_entity_id if host_is_source else source_or_target_id
            tgt = source_or_target_id if host_is_source else host_entity_id
            await elder.get_or_create_dependency(
                source_entity_id=src,
                target_entity_id=tgt,
                dependency_type=dep_type,
                description=description,
            )
            count += 1
        return count

    async def _sync_relationships(self) -> int:
        """Create relationships between FleetDM entities using FleetDM endpoints."""
        elder = self._require_elder_client()
        relationships_created = 0

        try:
            # 1. Vulnerabilities -> Hosts (affects)
            for cve, vuln_entity_id in self.vuln_entity_cache.items():
                try:
                    relationships_created += await self._link_paginated_hosts(
                        "/api/v1/fleet/hosts",
                        {"vulnerability": cve},
                        vuln_entity_id,
                        "affects",
                        f"Vulnerability {cve} affects host",
                        host_is_source=False,
                    )
                except Exception as e:
                    self.logger.warning(
                        f"Failed to link vulnerability {cve} to hosts",
                        error=str(e),
                    )

            # 2. Vulnerabilities -> Software versions & OS versions (affects)
            for cve, details in self.vuln_details_cache.items():
                cve_entity_id = self.vuln_entity_cache.get(cve)
                if not cve_entity_id:
                    continue
                for sw_id in details.get("affected_software_ids", []):
                    sw_entity_id = self.software_entity_cache.get(sw_id)
                    if sw_entity_id:
                        await elder.get_or_create_dependency(
                            source_entity_id=cve_entity_id,
                            target_entity_id=sw_entity_id,
                            dependency_type="affects",
                            description=f"Vulnerability {cve} affects software",
                        )
                        relationships_created += 1
                for os_id in details.get("affected_os_version_ids", []):
                    os_entity_id = self.os_entity_cache.get(os_id)
                    if os_entity_id:
                        await elder.get_or_create_dependency(
                            source_entity_id=cve_entity_id,
                            target_entity_id=os_entity_id,
                            dependency_type="affects",
                            description=f"Vulnerability {cve} affects OS version",
                        )
                        relationships_created += 1

            # 3. Software versions -> Hosts (installed_on)
            for sw_id, sw_entity_id in self.software_entity_cache.items():
                try:
                    relationships_created += await self._link_paginated_hosts(
                        "/api/v1/fleet/hosts",
                        {"software_version_id": sw_id},
                        sw_entity_id,
                        "installed_on",
                        "Software installed on host",
                        host_is_source=False,
                    )
                except Exception as e:
                    self.logger.warning(
                        f"Failed to link software {sw_id} to hosts",
                        error=str(e),
                    )

            # 4. Failing Policies -> Hosts (fails_policy)
            for pid, (policy_entity_id, failing_count) in self.policy_cache.items():
                if failing_count <= 0:
                    continue
                try:
                    relationships_created += await self._link_paginated_hosts(
                        "/api/v1/fleet/hosts",
                        {"policy_id": pid, "policy_response": "failing"},
                        policy_entity_id,
                        "fails_policy",
                        f"Host failing policy {pid}",
                        host_is_source=True,
                    )
                except Exception as e:
                    self.logger.warning(
                        f"Failed to link failing policy {pid} to hosts",
                        error=str(e),
                    )

            # 5. Custom/manual Labels -> Hosts (member_of)
            for lid, label_entity_id in self.label_entity_cache.items():
                try:
                    relationships_created += await self._link_paginated_hosts(
                        f"/api/v1/fleet/labels/{lid}/hosts",
                        None,
                        label_entity_id,
                        "member_of",
                        f"Host is member of label {lid}",
                        host_is_source=True,
                    )
                except Exception as e:
                    self.logger.warning(
                        f"Failed to link label {lid} to hosts",
                        error=str(e),
                    )
        except Exception as e:
            self.logger.error("Failed to sync FleetDM relationships", error=str(e))

        return relationships_created

    async def sync(self) -> SyncResult:
        """Synchronize all FleetDM resources and relationships to Elder."""
        result = SyncResult(connector_name=self.name)
        self._clear_sync_caches()
        self.logger.info("Starting FleetDM sync", url=settings.fleetdm_url)

        try:
            fleetdm_org_id = await self._get_or_create_organization(
                "FleetDM",
                "FleetDM endpoint management platform",
            )
            result.organizations_created += 1

            fleet_map = await self._sync_fleets(fleetdm_org_id)
            result.organizations_created += len(fleet_map)

            stages = [
                self._sync_posture(fleetdm_org_id),
                self._sync_hosts(fleetdm_org_id, fleet_map),
            ]
            if settings.fleetdm_sync_os_versions:
                stages.append(self._sync_os_versions(fleetdm_org_id))
            stages.extend(
                [
                    self._sync_software(fleetdm_org_id),
                    self._sync_vulnerabilities(fleetdm_org_id),
                    self._sync_policies(fleetdm_org_id, fleet_map),
                ]
            )
            if settings.fleetdm_sync_labels:
                stages.append(self._sync_labels(fleetdm_org_id, fleet_map))
            if settings.fleetdm_sync_mdm:
                stages.append(self._sync_mdm_profiles(fleetdm_org_id, fleet_map))
            if settings.fleetdm_sync_scripts_reports:
                stages.append(self._sync_scripts_and_reports(fleetdm_org_id, fleet_map))

            for stage_coro in stages:
                created_count, updated_count = await stage_coro
                result.entities_created += created_count
                result.entities_updated += updated_count

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
        """Check FleetDM API connectivity."""
        try:
            if not self.http_client:
                return False
            status, _ = await self._fleet_get("/api/v1/fleet/config")
            return status == 200
        except Exception as e:
            self.logger.warning("FleetDM health check failed", error=str(e))
            return False
