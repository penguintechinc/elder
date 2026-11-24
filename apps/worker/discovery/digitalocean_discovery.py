"""DigitalOcean cloud discovery client for Elder.

Discovers DigitalOcean resources via the DigitalOcean API v2.
Requires Personal Access Token authentication (DIGITALOCEAN_API_TOKEN /
DIGITALOCEAN_API_KEY env var or config['api_token'] / config['api_key']).
"""

# flake8: noqa: E501

import logging
import os
from datetime import UTC, datetime
from typing import Any

try:
    import httpx

    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

from apps.worker.discovery.base import BaseDiscoveryProvider

logger = logging.getLogger(__name__)

DIGITALOCEAN_API_BASE = "https://api.digitalocean.com/v2"
MAX_PAGINATION_PAGES = 50


def _region_slug(region: Any) -> str | None:
    """Extract a region slug from either a DigitalOcean region dict or string."""
    if isinstance(region, dict):
        return region.get("slug") or region.get("name")
    if isinstance(region, str):
        return region
    return None


class DigitalOceanDiscoveryClient(BaseDiscoveryProvider):
    """DigitalOcean cloud resource discovery via DigitalOcean API v2.

    Auth: Personal Access Token passed as Bearer token in Authorization header.
    Reads config['api_token'], config['api_key'], DIGITALOCEAN_API_TOKEN, or
    DIGITALOCEAN_API_KEY.
    """

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.provider_type = "digitalocean"
        self.api_token = (
            config.get("api_token")
            or config.get("api_key")
            or os.getenv("DIGITALOCEAN_API_TOKEN")
            or os.getenv("DIGITALOCEAN_API_KEY", "")
        )
        self._headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }

    def _get_paginated(self, path: str, key: str) -> list[dict[str, Any]]:
        """Make authenticated GET requests to DigitalOcean API following links.pages.next."""
        if not _HTTPX_AVAILABLE:
            logger.warning("httpx not available — DigitalOcean API calls disabled")
            return []
        if not self.api_token:
            logger.warning("DigitalOcean API token not configured")
            return []

        results: list[dict[str, Any]] = []
        url: str | None = (
            path if path.startswith("http") else f"{DIGITALOCEAN_API_BASE}{path}"
        )
        seen_urls: set[str] = set()

        try:
            with httpx.Client(timeout=30) as client:
                for _ in range(MAX_PAGINATION_PAGES):
                    if not url or url in seen_urls:
                        break
                    seen_urls.add(url)
                    resp = client.get(url, headers=self._headers)
                    resp.raise_for_status()
                    data = resp.json()
                    items = data.get(key) or []
                    if isinstance(items, list):
                        results.extend(items)
                    links = data.get("links") or {}
                    pages = links.get("pages") if isinstance(links, dict) else {}
                    next_url = pages.get("next") if isinstance(pages, dict) else None
                    if not next_url:
                        break
                    url = str(next_url)
        except Exception as exc:
            logger.error("DigitalOcean API error for %s: %s", path, exc)

        return results

    def test_connection(self) -> dict[str, Any]:
        """Verify DigitalOcean API token by calling GET /account."""
        if not _HTTPX_AVAILABLE:
            return {
                "connected": False,
                "provider": "digitalocean",
                "error": "httpx is not installed",
            }
        if not self.api_token:
            return {
                "connected": False,
                "provider": "digitalocean",
                "error": "DigitalOcean API token is not configured",
            }

        try:
            with httpx.Client(timeout=30) as client:
                resp = client.get(
                    f"{DIGITALOCEAN_API_BASE}/account", headers=self._headers
                )
                if resp.status_code != 200:
                    error_msg = f"HTTP {resp.status_code}"
                    try:
                        body = resp.json()
                        if isinstance(body, dict) and body.get("message"):
                            error_msg = str(body["message"])
                    except Exception:
                        pass
                    return {
                        "connected": False,
                        "provider": "digitalocean",
                        "error": error_msg,
                    }

                data = resp.json()
                account = data.get("account") if isinstance(data, dict) else None
                if not isinstance(account, dict):
                    return {
                        "connected": False,
                        "provider": "digitalocean",
                        "error": "Invalid response from DigitalOcean account endpoint",
                    }

                return {
                    "connected": True,
                    "provider": "digitalocean",
                    "status": account.get("status"),
                    "email": account.get("email"),
                    "droplet_limit": account.get("droplet_limit"),
                }
        except Exception as exc:
            logger.error("DigitalOcean connection test failed: %s", exc)
            return {
                "connected": False,
                "provider": "digitalocean",
                "error": str(exc),
            }

    def validate_config(self) -> bool:
        """Return True if an API token is configured."""
        return bool(self.api_token)

    def get_supported_services(self) -> list[str]:
        """Return the DigitalOcean resource types supported by this provider."""
        return [
            "droplets",
            "kubernetes",
            "apps",
            "volumes",
            "snapshots",
            "vpcs",
            "load_balancers",
            "reserved_ips",
            "domains",
            "firewalls",
            "databases",
            "functions",
        ]

    def discover_compute(self) -> list[dict[str, Any]]:
        """Discover DigitalOcean Droplets, Kubernetes clusters, and App Platform apps."""
        resources: list[dict[str, Any]] = []

        for droplet in self._get_paginated("/droplets", "droplets"):
            networks = droplet.get("networks") or {}
            v4 = networks.get("v4") or []
            v6 = networks.get("v6") or []
            ipv4 = [
                n.get("ip_address")
                for n in v4
                if isinstance(n, dict) and n.get("ip_address")
            ]
            ipv6 = [
                n.get("ip_address")
                for n in v6
                if isinstance(n, dict) and n.get("ip_address")
            ]
            raw_image = droplet.get("image")
            image_name = (
                raw_image.get("slug") or raw_image.get("name")
                if isinstance(raw_image, dict)
                else raw_image
            )
            resources.append(
                {
                    "id": droplet.get("id"),
                    "name": droplet.get("name") or str(droplet.get("id", "")),
                    "type": "do_droplet",
                    "region": _region_slug(droplet.get("region")),
                    "size_slug": droplet.get("size_slug"),
                    "vcpus": droplet.get("vcpus"),
                    "memory": droplet.get("memory"),
                    "disk": droplet.get("disk"),
                    "status": droplet.get("status"),
                    "networks": networks,
                    "ipv4": ipv4,
                    "ipv6": ipv6,
                    "tags": droplet.get("tags") or [],
                    "image": image_name,
                    "vpc_uuid": droplet.get("vpc_uuid"),
                    "created_at": droplet.get("created_at"),
                    "provider": "digitalocean",
                }
            )

        for cluster in self._get_paginated(
            "/kubernetes/clusters", "kubernetes_clusters"
        ):
            raw_status = cluster.get("status")
            status = (
                raw_status.get("state") if isinstance(raw_status, dict) else raw_status
            )
            node_pools = cluster.get("node_pools") or []
            resources.append(
                {
                    "id": cluster.get("id"),
                    "name": cluster.get("name") or cluster.get("id"),
                    "type": "do_kubernetes",
                    "region": _region_slug(cluster.get("region")),
                    "version": cluster.get("version"),
                    "status": status,
                    "endpoint": cluster.get("endpoint"),
                    "node_pools_count": len(node_pools),
                    "vpc_uuid": cluster.get("vpc_uuid"),
                    "tags": cluster.get("tags") or [],
                    "provider": "digitalocean",
                }
            )

        for app in self._get_paginated("/apps", "apps"):
            spec = app.get("spec") or {}
            active_deployment = app.get("active_deployment") or {}
            resources.append(
                {
                    "id": app.get("id"),
                    "name": spec.get("name") or app.get("name") or app.get("id"),
                    "type": "do_app",
                    "region": _region_slug(app.get("region")),
                    "default_ingress": app.get("default_ingress"),
                    "status": active_deployment.get("phase"),
                    "created_at": app.get("created_at"),
                    "provider": "digitalocean",
                }
            )

        return resources

    def discover_storage(self) -> list[dict[str, Any]]:
        """Discover DigitalOcean Block Storage Volumes and Snapshots."""
        resources: list[dict[str, Any]] = []

        for vol in self._get_paginated("/volumes", "volumes"):
            resources.append(
                {
                    "id": vol.get("id"),
                    "name": vol.get("name") or vol.get("id"),
                    "type": "do_volume",
                    "region": _region_slug(vol.get("region")),
                    "size_gigabytes": vol.get("size_gigabytes"),
                    "filesystem_type": vol.get("filesystem_type"),
                    "droplet_ids": vol.get("droplet_ids") or [],
                    "tags": vol.get("tags") or [],
                    "provider": "digitalocean",
                }
            )

        for snap in self._get_paginated("/snapshots", "snapshots"):
            resources.append(
                {
                    "id": snap.get("id"),
                    "name": snap.get("name") or snap.get("id"),
                    "type": "do_snapshot",
                    "regions": snap.get("regions") or [],
                    "size_gigabytes": snap.get("size_gigabytes"),
                    "resource_type": snap.get("resource_type"),
                    "created_at": snap.get("created_at"),
                    "provider": "digitalocean",
                }
            )

        return resources

    def discover_networking(self) -> list[dict[str, Any]]:
        """Discover DigitalOcean VPCs, Load Balancers, Reserved IPs, Domains, and Firewalls."""
        resources: list[dict[str, Any]] = []

        for vpc in self._get_paginated("/vpcs", "vpcs"):
            resources.append(
                {
                    "id": vpc.get("id"),
                    "name": vpc.get("name") or vpc.get("id"),
                    "type": "do_vpc",
                    "region": _region_slug(vpc.get("region")),
                    "ip_range": vpc.get("ip_range"),
                    "default": bool(vpc.get("default", False)),
                    "urn": vpc.get("urn"),
                    "provider": "digitalocean",
                }
            )

        for lb in self._get_paginated("/load_balancers", "load_balancers"):
            resources.append(
                {
                    "id": lb.get("id"),
                    "name": lb.get("name") or lb.get("id"),
                    "type": "do_load_balancer",
                    "ip": lb.get("ip"),
                    "region": _region_slug(lb.get("region")),
                    "status": lb.get("status"),
                    "size": lb.get("size"),
                    "algorithm": lb.get("algorithm"),
                    "vpc_uuid": lb.get("vpc_uuid"),
                    "droplet_ids": lb.get("droplet_ids") or [],
                    "forwarding_rules": lb.get("forwarding_rules") or [],
                    "provider": "digitalocean",
                }
            )

        for rip in self._get_paginated("/reserved_ips", "reserved_ips"):
            droplet = rip.get("droplet")
            droplet_id = (
                droplet.get("id")
                if isinstance(droplet, dict)
                else rip.get("droplet_id")
            )
            ip_addr = rip.get("ip")
            resources.append(
                {
                    "id": ip_addr,
                    "name": ip_addr,
                    "ip": ip_addr,
                    "type": "do_reserved_ip",
                    "region": _region_slug(rip.get("region")),
                    "droplet_id": droplet_id,
                    "provider": "digitalocean",
                }
            )

        for domain in self._get_paginated("/domains", "domains"):
            domain_name = domain.get("name")
            resources.append(
                {
                    "id": domain_name,
                    "name": domain_name,
                    "type": "do_domain",
                    "ttl": domain.get("ttl"),
                    "provider": "digitalocean",
                }
            )

        for fw in self._get_paginated("/firewalls", "firewalls"):
            inbound_rules = fw.get("inbound_rules") or []
            outbound_rules = fw.get("outbound_rules") or []
            resources.append(
                {
                    "id": fw.get("id"),
                    "name": fw.get("name") or fw.get("id"),
                    "type": "do_firewall",
                    "status": fw.get("status"),
                    "droplet_ids": fw.get("droplet_ids") or [],
                    "tags": fw.get("tags") or [],
                    "inbound_rules_count": len(inbound_rules),
                    "outbound_rules_count": len(outbound_rules),
                    "provider": "digitalocean",
                }
            )

        return resources

    def discover_network(self) -> list[dict[str, Any]]:
        """Alias for discover_networking to satisfy BaseDiscoveryProvider."""
        return self.discover_networking()

    def discover_databases(self) -> list[dict[str, Any]]:
        """Discover DigitalOcean Managed Databases."""
        resources: list[dict[str, Any]] = []

        for db in self._get_paginated("/databases", "databases"):
            conn = db.get("connection") or {}
            resources.append(
                {
                    "id": db.get("id"),
                    "name": db.get("name") or db.get("id"),
                    "type": "do_database",
                    "engine": db.get("engine"),
                    "version": db.get("version"),
                    "region": _region_slug(db.get("region")),
                    "size": db.get("size"),
                    "num_nodes": db.get("num_nodes"),
                    "status": db.get("status"),
                    "host": conn.get("host"),
                    "port": conn.get("port"),
                    "private_network_uuid": db.get("private_network_uuid"),
                    "tags": db.get("tags") or [],
                    "provider": "digitalocean",
                }
            )

        return resources

    def discover_serverless(self) -> list[dict[str, Any]]:
        """Discover DigitalOcean Functions Namespaces."""
        resources: list[dict[str, Any]] = []

        for ns in self._get_paginated("/functions/namespaces", "namespaces"):
            namespace_id = ns.get("namespace")
            label = ns.get("label")
            resources.append(
                {
                    "id": namespace_id,
                    "namespace": namespace_id,
                    "name": label or namespace_id,
                    "label": label,
                    "type": "do_function_namespace",
                    "region": _region_slug(ns.get("region")),
                    "api_host": ns.get("api_host"),
                    "provider": "digitalocean",
                }
            )

        return resources

    def discover_all(self) -> dict[str, Any]:
        """Discover all DigitalOcean resources across compute, storage, network, databases, and serverless."""
        start = datetime.now(UTC)
        compute = self.discover_compute()
        storage = self.discover_storage()
        network = self.discover_networking()
        databases = self.discover_databases()
        serverless = self.discover_serverless()
        return {
            "compute": compute,
            "storage": storage,
            "network": network,
            "database": databases,
            "serverless": serverless,
            "resources_count": (
                len(compute)
                + len(storage)
                + len(network)
                + len(databases)
                + len(serverless)
            ),
            "discovery_time": start,
            "provider": "digitalocean",
        }
