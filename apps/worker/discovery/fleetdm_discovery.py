"""FleetDM endpoint discovery client for Elder UI-configurable discovery jobs."""

# flake8: noqa: E501

import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from apps.worker.discovery.base import BaseDiscoveryProvider
from shared.fleetdm_helpers import (
    _cvss_to_severity,
    _extract_safe_mdm,
    _parse_retry_after,
    _safe_json,
)

logger = logging.getLogger(__name__)


class FleetDMDiscoveryClient(BaseDiscoveryProvider):
    """FleetDM resource discovery via the Fleet v4.95.0 / Fleet 5 REST API.

    Discovers managed hosts, software versions, OS versions, labels,
    vulnerabilities, policies, and MDM configuration profiles without
    persisting PII or secret material.
    """

    def __init__(self, config: dict[str, Any]):
        """Initialize FleetDM discovery client."""
        super().__init__(config)
        self.provider_type = "fleetdm"
        raw_url = (
            config.get("url")
            or config.get("fleetdm_url")
            or os.getenv("FLEETDM_URL", "")
        )
        self.url = str(raw_url).strip().rstrip("/")
        raw_token = (
            config.get("api_token")
            or config.get("token")
            or config.get("fleetdm_api_token")
            or os.getenv("FLEETDM_API_TOKEN", "")
        )
        self.api_token = str(raw_token).strip()
        self.verify_ssl = bool(config.get("verify_ssl", True))
        self.sync_mdm = bool(config.get("sync_mdm", True))
        self.sync_labels = bool(config.get("sync_labels", True))
        try:
            raw_per_page = int(config.get("per_page", 100) or 100)
        except (TypeError, ValueError):
            raw_per_page = 100
        self.per_page = min(max(raw_per_page, 1), 500)
        self._headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def validate_config(self) -> bool:
        """Validate that non-empty FleetDM url and api_token are provided."""
        if not self.url:
            raise ValueError("FleetDM url is required")
        if not self.api_token:
            raise ValueError("FleetDM api_token is required")
        return True

    def test_connection(self) -> bool:
        """Test FleetDM API connectivity via GET /api/v1/fleet/config."""
        if not self.url or not self.api_token:
            return False
        try:
            with httpx.Client(
                base_url=self.url,
                headers=self._headers,
                timeout=15.0,
                verify=self.verify_ssl,
            ) as client:
                resp = client.get("/api/v1/fleet/config")
                return resp.status_code == 200
        except Exception as exc:
            logger.warning("FleetDM discovery connection test failed: %s", exc)
            return False

    def get_supported_services(self) -> list[str]:
        """Return FleetDM resource categories supported for discovery."""
        return [
            "hosts",
            "software",
            "os_versions",
            "vulnerabilities",
            "policies",
            "labels",
            "mdm_profiles",
        ]

    def _get(
        self,
        client: httpx.Client,
        path: str,
        params: dict[str, Any] | None = None,
        fallback_paths: list[str] | None = None,
    ) -> tuple[int, Any]:
        """Execute synchronous GET with retry on 429/5xx and 404/410 fallbacks."""
        paths = [path] + list(fallback_paths or [])
        max_attempts = 3

        for idx, candidate_path in enumerate(paths):
            has_next_fallback = idx < len(paths) - 1
            for attempt in range(max_attempts):
                try:
                    resp = client.get(candidate_path, params=params)
                except httpx.RequestError as exc:
                    if attempt < max_attempts - 1:
                        time.sleep(min(0.5 * (2**attempt), 4.0))
                        continue
                    logger.warning(
                        "FleetDM request failed for %s: %s", candidate_path, exc
                    )
                    return 0, None

                status = resp.status_code
                if status == 429:
                    if attempt < max_attempts - 1:
                        time.sleep(_parse_retry_after(resp, attempt))
                        continue
                    return 429, _safe_json(resp)

                if 500 <= status < 600:
                    if attempt < max_attempts - 1:
                        time.sleep(min(0.5 * (2**attempt), 4.0))
                        continue
                    return status, _safe_json(resp)

                if status in (404, 410) and has_next_fallback:
                    break

                if status == 204:
                    return 204, None

                return status, _safe_json(resp)

        return 404, None

    def _paginate(
        self,
        client: httpx.Client,
        path: str,
        result_key: str,
        params: dict[str, Any] | None = None,
        fallback_paths: list[str] | None = None,
        fallback_result_key: str | None = None,
        max_pages: int = 500,
    ) -> list[dict[str, Any]]:
        """Paginate a FleetDM list endpoint synchronously."""
        all_items: list[dict[str, Any]] = []

        for page in range(max_pages):
            req_params = {**(params or {}), "page": page, "per_page": self.per_page}
            status, data = self._get(
                client,
                path,
                params=req_params,
                fallback_paths=fallback_paths,
            )
            if status != 200 or not isinstance(data, dict):
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
            elif len(items) < self.per_page:
                break

        return all_items

    def _new_client(self) -> httpx.Client:
        """Create a configured httpx.Client for FleetDM API requests."""
        return httpx.Client(
            base_url=self.url,
            headers=self._headers,
            timeout=30.0,
            verify=self.verify_ssl,
        )

    def _discover_compute_with_client(
        self, client: httpx.Client
    ) -> list[dict[str, Any]]:
        """Discover FleetDM hosts, software versions, OS versions, and labels."""
        resources: list[dict[str, Any]] = []

        # 1. Hosts (strictly PII-free)
        hosts = self._paginate(
            client,
            "/api/v1/fleet/hosts",
            "hosts",
            params={"include_device_status": "true"},
        )
        for host in hosts:
            host_id = host.get("id")
            if host_id is None:
                continue
            ext_id = f"fleetdm:host:{host_id}"
            platform = str(host.get("platform") or "unknown")
            hostname = (
                host.get("hostname")
                or host.get("computer_name")
                or host.get("display_name")
                or f"Host-{host_id}"
            )
            mdm_safe = _extract_safe_mdm(host.get("mdm"))
            relationships: list[dict[str, Any]] = []
            if host.get("os_version_id") is not None:
                relationships.append(
                    {
                        "target_external_id": f"fleetdm:os:{host['os_version_id']}",
                        "target_kind": "entity",
                        "edge_type": "runs_on",
                    }
                )
            resources.append(
                self.format_resource(
                    resource_id=str(host_id),
                    resource_type="fleetdm_host",
                    name=str(hostname)[:255],
                    external_id=ext_id,
                    metadata={
                        "fleetdm_host_id": host_id,
                        "uuid": host.get("uuid"),
                        "hostname": host.get("hostname"),
                        "computer_name": host.get("computer_name"),
                        "platform": platform,
                        "os_version": host.get("os_version"),
                        "osquery_version": host.get("osquery_version"),
                        "hardware_model": host.get("hardware_model"),
                        "hardware_serial": host.get("hardware_serial"),
                        "hardware_vendor": host.get("hardware_vendor"),
                        "memory": host.get("memory"),
                        "disk_encryption_enabled": host.get("disk_encryption_enabled"),
                        "primary_ip": host.get("primary_ip"),
                        "primary_mac": host.get("primary_mac"),
                        "public_ip": host.get("public_ip"),
                        "status": host.get("status"),
                        "seen_time": host.get("seen_time"),
                        "fleet_id": host.get("fleet_id")
                        if host.get("fleet_id") is not None
                        else host.get("team_id"),
                        "mdm": mdm_safe,
                    },
                    tags={"provider": "fleetdm", "platform": platform},
                    relationships=relationships,
                )
            )

        # 2. Software versions (with Fleet 4.x fallback)
        software_items = self._paginate(
            client,
            "/api/v1/fleet/software/versions",
            "software",
            params={"without_vulnerability_details": "true"},
            fallback_paths=["/api/v1/fleet/software"],
            fallback_result_key="software",
        )
        for sw in software_items:
            sw_id = sw.get("id")
            if sw_id is None:
                continue
            sw_name = sw.get("name") or f"Software-{sw_id}"
            sw_ver = sw.get("version") or ""
            display = f"{sw_name} {sw_ver}".strip()[:255]
            resources.append(
                self.format_resource(
                    resource_id=f"software-{sw_id}",
                    resource_type="fleetdm_software",
                    name=display,
                    external_id=f"fleetdm:software:{sw_id}",
                    metadata={
                        "fleetdm_software_id": sw_id,
                        "software_name": sw_name,
                        "version": sw.get("version"),
                        "source": sw.get("source"),
                        "bundle_identifier": sw.get("bundle_identifier"),
                        "hosts_count": sw.get("hosts_count", 0),
                        "generated_cpe": sw.get("generated_cpe"),
                    },
                    tags={
                        "provider": "fleetdm",
                        "source": str(sw.get("source") or "unknown"),
                    },
                )
            )

        # 3. OS versions
        os_versions = self._paginate(
            client,
            "/api/v1/fleet/os_versions",
            "os_versions",
        )
        for os_item in os_versions:
            os_id = os_item.get("os_version_id")
            if os_id is None:
                os_id = os_item.get("id")
            if os_id is None:
                continue
            name_only = os_item.get("name_only") or "OS"
            version = os_item.get("version") or ""
            display = (os_item.get("name") or f"{name_only} {version}".strip())[:255]
            platform = str(os_item.get("platform") or "unknown")
            resources.append(
                self.format_resource(
                    resource_id=f"os-{os_id}",
                    resource_type="fleetdm_os_version",
                    name=display or f"OS-{os_id}",
                    external_id=f"fleetdm:os:{os_id}",
                    metadata={
                        "os_version_id": os_id,
                        "name_only": os_item.get("name_only"),
                        "version": os_item.get("version"),
                        "platform": platform,
                        "hosts_count": os_item.get("hosts_count", 0),
                        "generated_cpes": os_item.get("generated_cpes") or [],
                    },
                    tags={"provider": "fleetdm", "platform": platform},
                )
            )

        # 4. Labels
        if self.sync_labels:
            labels = self._paginate(client, "/api/v1/fleet/labels", "labels")
            for label in labels:
                lid = label.get("id")
                if lid is None:
                    continue
                label_type = str(label.get("label_type") or "custom")
                label_rels: list[dict[str, Any]] = []
                if label_type != "builtin":
                    label_hosts = self._paginate(
                        client, f"/api/v1/fleet/labels/{lid}/hosts", "hosts"
                    )
                    for lhost in label_hosts:
                        if lhost.get("id") is not None:
                            label_rels.append(
                                {
                                    "target_external_id": f"fleetdm:host:{lhost['id']}",
                                    "target_kind": "entity",
                                    "edge_type": "manages",
                                }
                            )
                resources.append(
                    self.format_resource(
                        resource_id=f"label-{lid}",
                        resource_type="fleetdm_label",
                        name=str(label.get("name") or f"Label-{lid}")[:255],
                        external_id=f"fleetdm:label:{lid}",
                        metadata={
                            "fleetdm_label_id": lid,
                            "label_type": label_type,
                            "label_membership_type": label.get("label_membership_type"),
                            "platform": label.get("platform"),
                            "host_count": label.get("host_count"),
                        },
                        tags={"provider": "fleetdm", "label_type": label_type},
                        relationships=label_rels,
                    )
                )

        return resources

    def _discover_security_with_client(
        self, client: httpx.Client
    ) -> list[dict[str, Any]]:
        """Discover FleetDM vulnerabilities, policies, and MDM profiles."""
        security: list[dict[str, Any]] = []

        # 1. Vulnerabilities
        vulns = self._paginate(
            client, "/api/v1/fleet/vulnerabilities", "vulnerabilities"
        )
        for vuln in vulns:
            cve = vuln.get("cve")
            if not cve:
                continue
            cvss = vuln.get("cvss_score")
            severity = _cvss_to_severity(cvss)
            security.append(
                self.format_resource(
                    resource_id=str(cve),
                    resource_type="fleetdm_vulnerability",
                    name=f"CVE: {cve}"[:255],
                    external_id=f"fleetdm:vuln:{cve}",
                    metadata={
                        "cve": cve,
                        "cvss_score": cvss,
                        "severity": severity,
                        "epss_probability": vuln.get("epss_probability"),
                        "cisa_known_exploit": vuln.get("cisa_known_exploit"),
                        "cve_published": vuln.get("cve_published")
                        or vuln.get("published"),
                        "hosts_count": vuln.get("hosts_count", 0),
                        "details_link": vuln.get("details_link"),
                    },
                    tags={"provider": "fleetdm", "severity": severity},
                )
            )

        # 2. Policies (Fleet 5 unified with Fleet 4.x fallback)
        policies = self._paginate(
            client,
            "/api/v1/fleet/policies",
            "policies",
            fallback_paths=["/api/v1/fleet/global/policies"],
            fallback_result_key="policies",
        )
        for policy in policies:
            pid = policy.get("id")
            if pid is None:
                continue
            platform = str(policy.get("platform") or "all")
            security.append(
                self.format_resource(
                    resource_id=f"policy-{pid}",
                    resource_type="fleetdm_policy",
                    name=str(policy.get("name") or f"Policy-{pid}")[:255],
                    external_id=f"fleetdm:policy:{pid}",
                    metadata={
                        "fleetdm_policy_id": pid,
                        "platform": policy.get("platform"),
                        "critical": bool(policy.get("critical", False)),
                        "passing_host_count": policy.get("passing_host_count", 0),
                        "failing_host_count": policy.get("failing_host_count", 0),
                        "query": policy.get("query"),
                        "resolution": policy.get("resolution"),
                    },
                    tags={"provider": "fleetdm", "platform": platform},
                )
            )

        # 3. MDM Configuration Profiles (graceful on Free/402/403/404)
        if self.sync_mdm:
            profiles = self._paginate(
                client,
                "/api/v1/fleet/configuration_profiles",
                "profiles",
            )
            for prof in profiles:
                puuid = prof.get("profile_uuid") or prof.get("uuid") or prof.get("id")
                if not puuid:
                    continue
                puuid_str = str(puuid)
                platform = str(prof.get("platform") or "unknown")
                security.append(
                    self.format_resource(
                        resource_id=f"profile-{puuid_str}",
                        resource_type="fleetdm_mdm_profile",
                        name=str(prof.get("name") or puuid_str)[:255],
                        external_id=f"fleetdm:profile:{puuid_str}"[:255],
                        metadata={
                            "profile_uuid": puuid_str,
                            "platform": prof.get("platform"),
                            "identifier": prof.get("identifier"),
                            "checksum": prof.get("checksum"),
                            "self_service": prof.get("self_service"),
                            "hidden": prof.get("hidden"),
                        },
                        tags={"provider": "fleetdm", "platform": platform},
                    )
                )

        return security

    def discover_compute(self) -> list[dict[str, Any]]:
        """Discover compute resources (hosts, software, OS versions, labels)."""
        self.validate_config()
        with self._new_client() as client:
            return self._discover_compute_with_client(client)

    def discover_storage(self) -> list[dict[str, Any]]:
        """FleetDM does not expose standalone storage resources."""
        return []

    def discover_network(self) -> list[dict[str, Any]]:
        """FleetDM does not expose standalone network resources."""
        return []

    def discover_databases(self) -> list[dict[str, Any]]:
        """FleetDM does not expose standalone database resources."""
        return []

    def discover_serverless(self) -> list[dict[str, Any]]:
        """FleetDM does not expose serverless resources."""
        return []

    def discover_all(self) -> dict[str, Any]:
        """Discover all FleetDM compute and security resources."""
        self.validate_config()
        with self._new_client() as client:
            compute = self._discover_compute_with_client(client)
            security = self._discover_security_with_client(client)

        return {
            "compute": compute,
            "storage": [],
            "network": [],
            "database": [],
            "serverless": [],
            "security": security,
            "resources_count": len(compute) + len(security),
            "discovery_time": datetime.now(UTC),
        }
