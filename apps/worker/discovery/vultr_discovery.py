"""Vultr cloud discovery client for Elder.

Discovers Vultr resources via the Vultr API v2.
Requires API key authentication (VULTR_API_KEY env var or config['api_key']).
"""

# flake8: noqa: E501

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    import httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

from apps.worker.discovery.base import BaseDiscoveryProvider

logger = logging.getLogger(__name__)

VULTR_API_BASE = "https://api.vultr.com/v2"


class VultrDiscoveryClient(BaseDiscoveryProvider):
    """Vultr cloud resource discovery via Vultr API v2.

    Auth: API key passed as Bearer token in Authorization header.
    Set VULTR_API_KEY env var or config['api_key'].
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.provider_type = "vultr"
        self.api_key = config.get("api_key") or os.getenv("VULTR_API_KEY", "")
        self._headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _get(self, path: str) -> Dict[str, Any]:
        """Make authenticated GET request to Vultr API."""
        if not _HTTPX_AVAILABLE:
            logger.warning("httpx not available — Vultr API calls disabled")
            return {}
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.get(f"{VULTR_API_BASE}{path}", headers=self._headers)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.error("Vultr API error for %s: %s", path, exc)
            return {}

    def test_connection(self) -> bool:
        data = self._get("/account")
        return bool(data.get("account"))

    def validate_config(self) -> bool:
        return bool(self.api_key)

    def discover_compute(self) -> List[Dict[str, Any]]:
        """Discover Vultr instances (VPS/bare metal)."""
        resources: List[Dict[str, Any]] = []
        instances = self._get("/instances").get("instances", [])
        for inst in instances:
            resources.append({
                "id": inst.get("id"),
                "name": inst.get("label") or inst.get("id"),
                "type": "vultr_instance",
                "region": inst.get("region"),
                "status": inst.get("status"),
                "plan": inst.get("plan"),
                "main_ip": inst.get("main_ip"),
                "os": inst.get("os"),
                "provider": "vultr",
            })
        bare_metals = self._get("/bare-metals").get("bare_metals", [])
        for bm in bare_metals:
            resources.append({
                "id": bm.get("id"),
                "name": bm.get("label") or bm.get("id"),
                "type": "vultr_bare_metal",
                "region": bm.get("region"),
                "status": bm.get("status"),
                "plan": bm.get("plan"),
                "main_ip": bm.get("main_ip"),
                "os": bm.get("os"),
                "provider": "vultr",
            })
        return resources

    def discover_storage(self) -> List[Dict[str, Any]]:
        """Discover Vultr block storage volumes and object storage."""
        resources: List[Dict[str, Any]] = []
        blocks = self._get("/blocks").get("blocks", [])
        for blk in blocks:
            resources.append({
                "id": blk.get("id"),
                "name": blk.get("label") or blk.get("id"),
                "type": "vultr_block_storage",
                "region": blk.get("region"),
                "size_gb": blk.get("size_gb"),
                "status": blk.get("status"),
                "provider": "vultr",
            })
        objects = self._get("/object-storage").get("object_storages", [])
        for obj in objects:
            resources.append({
                "id": obj.get("id"),
                "name": obj.get("label") or obj.get("id"),
                "type": "vultr_object_storage",
                "region": obj.get("region"),
                "status": obj.get("status"),
                "provider": "vultr",
            })
        return resources

    def discover_network(self) -> List[Dict[str, Any]]:
        """Discover Vultr VPCs and reserved IPs."""
        resources: List[Dict[str, Any]] = []
        vpcs = self._get("/vpcs").get("vpcs", [])
        for vpc in vpcs:
            resources.append({
                "id": vpc.get("id"),
                "name": vpc.get("description") or vpc.get("id"),
                "type": "vultr_vpc",
                "region": vpc.get("region"),
                "ip_block": vpc.get("ip_block"),
                "provider": "vultr",
            })
        reserved_ips = self._get("/reserved-ips").get("reserved_ips", [])
        for rip in reserved_ips:
            resources.append({
                "id": rip.get("id"),
                "name": rip.get("label") or rip.get("id"),
                "type": "vultr_reserved_ip",
                "region": rip.get("region"),
                "subnet": rip.get("subnet"),
                "provider": "vultr",
            })
        return resources

    def discover_databases(self) -> List[Dict[str, Any]]:
        """Discover Vultr managed databases."""
        resources: List[Dict[str, Any]] = []
        dbs = self._get("/databases").get("databases", [])
        for db in dbs:
            resources.append({
                "id": db.get("id"),
                "name": db.get("label") or db.get("id"),
                "type": "vultr_managed_database",
                "region": db.get("region"),
                "status": db.get("status"),
                "database_engine": db.get("database_engine"),
                "provider": "vultr",
            })
        return resources

    def discover_serverless(self) -> List[Dict[str, Any]]:
        """Vultr does not offer serverless/FaaS — returns empty list."""
        return []

    def discover_all(self) -> Dict[str, Any]:
        start = datetime.now(timezone.utc)
        compute = self.discover_compute()
        storage = self.discover_storage()
        network = self.discover_network()
        databases = self.discover_databases()
        return {
            "compute": compute,
            "storage": storage,
            "network": network,
            "database": databases,
            "serverless": [],
            "resources_count": len(compute) + len(storage) + len(network) + len(databases),
            "discovery_time": start,
            "provider": "vultr",
        }
