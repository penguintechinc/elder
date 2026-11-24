"""Unit tests for DigitalOcean cloud discovery provider and registration."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from apps.worker.discovery import DigitalOceanDiscoveryClient
from apps.worker.discovery.digitalocean_discovery import (
    DIGITALOCEAN_API_BASE,
    MAX_PAGINATION_PAGES,
)
from apps.worker.discovery.executor import CLOUD_PROVIDERS
from apps.worker.discovery.service import DiscoveryService


def _mock_response(payload: dict[str, Any], status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    else:
        resp.raise_for_status.return_value = None
    return resp


def _patch_httpx_client(responses_by_url: dict[str, MagicMock]):
    """Patch httpx.Client so client.get(url, ...) dispatches by exact URL."""
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False

    def _get(url: str, headers: dict[str, str] | None = None):
        if url in responses_by_url:
            return responses_by_url[url]
        return _mock_response({}, status_code=404)

    mock_client.get.side_effect = _get
    return patch(
        "apps.worker.discovery.digitalocean_discovery.httpx.Client",
        return_value=mock_client,
    ), mock_client


class TestDigitalOceanAuthAndConfig:
    """Auth token resolution and provider metadata tests."""

    def test_token_resolution_priority(self, monkeypatch):
        monkeypatch.setenv("DIGITALOCEAN_API_TOKEN", "env-token")
        monkeypatch.setenv("DIGITALOCEAN_API_KEY", "env-key")

        c1 = DigitalOceanDiscoveryClient(
            {"api_token": "cfg-token", "api_key": "cfg-key"}
        )
        assert c1.api_token == "cfg-token"
        assert c1._headers["Authorization"] == "Bearer cfg-token"
        assert c1.provider_type == "digitalocean"
        assert c1.validate_config() is True

        c2 = DigitalOceanDiscoveryClient({"api_key": "cfg-key"})
        assert c2.api_token == "cfg-key"

        c3 = DigitalOceanDiscoveryClient({})
        assert c3.api_token == "env-token"

        monkeypatch.delenv("DIGITALOCEAN_API_TOKEN")
        c4 = DigitalOceanDiscoveryClient({})
        assert c4.api_token == "env-key"

        monkeypatch.delenv("DIGITALOCEAN_API_KEY")
        c5 = DigitalOceanDiscoveryClient({})
        assert c5.api_token == ""
        assert c5.validate_config() is False

    def test_supported_services_and_registration(self):
        client = DigitalOceanDiscoveryClient({"api_token": "tok"})
        services = client.get_supported_services()
        assert "droplets" in services
        assert "kubernetes" in services
        assert "databases" in services
        assert "functions" in services

        provider = DiscoveryService.get_provider(
            "digitalocean", {"api_token": "dop_v1_test"}
        )
        assert isinstance(provider, DigitalOceanDiscoveryClient)
        assert provider.provider_type == "digitalocean"
        assert "digitalocean" in CLOUD_PROVIDERS


class TestDigitalOceanTestConnection:
    """Tests for test_connection() success, auth failure, and missing token."""

    def test_connection_success(self):
        client = DigitalOceanDiscoveryClient({"api_token": "dop_v1_valid"})
        ctx, mock_http = _patch_httpx_client(
            {
                f"{DIGITALOCEAN_API_BASE}/account": _mock_response(
                    {
                        "account": {
                            "status": "active",
                            "email": "ops@penguintech.io",
                            "droplet_limit": 25,
                        }
                    },
                    status_code=200,
                )
            }
        )
        with ctx:
            result = client.test_connection()

        assert result == {
            "connected": True,
            "provider": "digitalocean",
            "status": "active",
            "email": "ops@penguintech.io",
            "droplet_limit": 25,
        }
        mock_http.get.assert_called_once()

    def test_connection_auth_failure(self):
        client = DigitalOceanDiscoveryClient({"api_token": "dop_v1_bad"})
        ctx, _ = _patch_httpx_client(
            {
                f"{DIGITALOCEAN_API_BASE}/account": _mock_response(
                    {"id": "unauthorized", "message": "Unable to authenticate you"},
                    status_code=401,
                )
            }
        )
        with ctx:
            result = client.test_connection()

        assert result["connected"] is False
        assert result["provider"] == "digitalocean"
        assert "Unable to authenticate you" in result["error"]

    def test_connection_missing_token(self, monkeypatch):
        monkeypatch.delenv("DIGITALOCEAN_API_TOKEN", raising=False)
        monkeypatch.delenv("DIGITALOCEAN_API_KEY", raising=False)
        client = DigitalOceanDiscoveryClient({})
        result = client.test_connection()

        assert result["connected"] is False
        assert result["provider"] == "digitalocean"
        assert "not configured" in result["error"].lower()


class TestDigitalOceanPagination:
    """Tests for _get_paginated following links.pages.next and enforcing safety cap."""

    def test_pagination_follows_next_links(self):
        client = DigitalOceanDiscoveryClient({"api_token": "dop_v1_valid"})
        page1_url = f"{DIGITALOCEAN_API_BASE}/droplets"
        page2_url = f"{DIGITALOCEAN_API_BASE}/droplets?page=2"
        page3_url = f"{DIGITALOCEAN_API_BASE}/droplets?page=3"

        ctx, mock_http = _patch_httpx_client(
            {
                page1_url: _mock_response(
                    {
                        "droplets": [{"id": 1, "name": "web-1"}],
                        "links": {"pages": {"next": page2_url}},
                    }
                ),
                page2_url: _mock_response(
                    {
                        "droplets": [{"id": 2, "name": "web-2"}],
                        "links": {"pages": {"next": page3_url}},
                    }
                ),
                page3_url: _mock_response(
                    {
                        "droplets": [{"id": 3, "name": "web-3"}],
                        "links": {"pages": {}},
                    }
                ),
            }
        )
        with ctx:
            items = client._get_paginated("/droplets", "droplets")

        assert [d["id"] for d in items] == [1, 2, 3]
        assert mock_http.get.call_count == 3

    def test_pagination_stops_at_max_pages_cap(self):
        client = DigitalOceanDiscoveryClient({"api_token": "dop_v1_valid"})
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.__exit__.return_value = False

        call_num = 0

        def _endless_get(url: str, headers: dict[str, str] | None = None):
            nonlocal call_num
            call_num += 1
            return _mock_response(
                {
                    "droplets": [{"id": call_num, "name": f"d-{call_num}"}],
                    "links": {
                        "pages": {
                            "next": f"{DIGITALOCEAN_API_BASE}/droplets?page={call_num + 1}"
                        }
                    },
                }
            )

        mock_client.get.side_effect = _endless_get
        with patch(
            "apps.worker.discovery.digitalocean_discovery.httpx.Client",
            return_value=mock_client,
        ):
            items = client._get_paginated("/droplets", "droplets")

        assert len(items) == MAX_PAGINATION_PAGES
        assert mock_client.get.call_count == MAX_PAGINATION_PAGES


class TestDigitalOceanResourceDiscovery:
    """Tests for compute, storage, networking, database, and serverless discovery."""

    def test_discover_compute(self):
        client = DigitalOceanDiscoveryClient({"api_token": "dop_v1_valid"})
        ctx, _ = _patch_httpx_client(
            {
                f"{DIGITALOCEAN_API_BASE}/droplets": _mock_response(
                    {
                        "droplets": [
                            {
                                "id": 101,
                                "name": "api-node-1",
                                "region": {"slug": "nyc3", "name": "New York 3"},
                                "size_slug": "s-2vcpu-4gb",
                                "vcpus": 2,
                                "memory": 4096,
                                "disk": 80,
                                "status": "active",
                                "networks": {
                                    "v4": [
                                        {"ip_address": "104.131.1.10", "type": "public"}
                                    ],
                                    "v6": [
                                        {"ip_address": "2604:a880::1", "type": "public"}
                                    ],
                                },
                                "tags": ["prod", "api"],
                                "image": {
                                    "slug": "ubuntu-24-04-x64",
                                    "name": "Ubuntu 24.04",
                                },
                                "vpc_uuid": "vpc-111",
                                "created_at": "2026-01-10T00:00:00Z",
                            }
                        ]
                    }
                ),
                f"{DIGITALOCEAN_API_BASE}/kubernetes/clusters": _mock_response(
                    {
                        "kubernetes_clusters": [
                            {
                                "id": "k8s-abc",
                                "name": "prod-doks",
                                "region": "nyc3",
                                "version": "1.31.1-do.0",
                                "status": {"state": "running"},
                                "endpoint": "https://k8s-abc.k8s.ondigitalocean.com",
                                "node_pools": [{"id": "np-1"}, {"id": "np-2"}],
                                "vpc_uuid": "vpc-111",
                                "tags": ["k8s"],
                            }
                        ]
                    }
                ),
                f"{DIGITALOCEAN_API_BASE}/apps": _mock_response(
                    {
                        "apps": [
                            {
                                "id": "app-xyz",
                                "spec": {"name": "marketing-site"},
                                "region": {"slug": "nyc"},
                                "default_ingress": "https://marketing-site.ondigitalocean.app",
                                "active_deployment": {"phase": "ACTIVE"},
                                "created_at": "2026-02-01T12:00:00Z",
                            }
                        ]
                    }
                ),
            }
        )
        with ctx:
            items = client.discover_compute()

        assert len(items) == 3
        droplet, k8s, app = items
        assert droplet["type"] == "do_droplet"
        assert droplet["region"] == "nyc3"
        assert droplet["ipv4"] == ["104.131.1.10"]
        assert droplet["ipv6"] == ["2604:a880::1"]
        assert droplet["image"] == "ubuntu-24-04-x64"
        assert droplet["vpc_uuid"] == "vpc-111"

        assert k8s["type"] == "do_kubernetes"
        assert k8s["status"] == "running"
        assert k8s["node_pools_count"] == 2
        assert k8s["endpoint"] == "https://k8s-abc.k8s.ondigitalocean.com"

        assert app["type"] == "do_app"
        assert app["name"] == "marketing-site"
        assert app["status"] == "ACTIVE"
        assert app["default_ingress"] == "https://marketing-site.ondigitalocean.app"

    def test_discover_storage(self):
        client = DigitalOceanDiscoveryClient({"api_token": "dop_v1_valid"})
        ctx, _ = _patch_httpx_client(
            {
                f"{DIGITALOCEAN_API_BASE}/volumes": _mock_response(
                    {
                        "volumes": [
                            {
                                "id": "vol-1",
                                "name": "pg-data",
                                "region": {"slug": "nyc3"},
                                "size_gigabytes": 100,
                                "filesystem_type": "ext4",
                                "droplet_ids": [101],
                                "tags": ["db"],
                            }
                        ]
                    }
                ),
                f"{DIGITALOCEAN_API_BASE}/snapshots": _mock_response(
                    {
                        "snapshots": [
                            {
                                "id": "snap-1",
                                "name": "daily-backup",
                                "regions": ["nyc3", "sfo3"],
                                "size_gigabytes": 25.5,
                                "resource_type": "droplet",
                                "created_at": "2026-03-01T00:00:00Z",
                            }
                        ]
                    }
                ),
            }
        )
        with ctx:
            items = client.discover_storage()

        assert len(items) == 2
        vol, snap = items
        assert vol["type"] == "do_volume"
        assert vol["size_gigabytes"] == 100
        assert vol["filesystem_type"] == "ext4"
        assert vol["droplet_ids"] == [101]

        assert snap["type"] == "do_snapshot"
        assert snap["regions"] == ["nyc3", "sfo3"]
        assert snap["resource_type"] == "droplet"

    def test_discover_networking(self):
        client = DigitalOceanDiscoveryClient({"api_token": "dop_v1_valid"})
        ctx, _ = _patch_httpx_client(
            {
                f"{DIGITALOCEAN_API_BASE}/vpcs": _mock_response(
                    {
                        "vpcs": [
                            {
                                "id": "vpc-111",
                                "name": "default-nyc3",
                                "region": "nyc3",
                                "ip_range": "10.108.0.0/20",
                                "default": True,
                                "urn": "do:vpc:vpc-111",
                            }
                        ]
                    }
                ),
                f"{DIGITALOCEAN_API_BASE}/load_balancers": _mock_response(
                    {
                        "load_balancers": [
                            {
                                "id": "lb-1",
                                "name": "edge-lb",
                                "ip": "159.65.1.2",
                                "region": {"slug": "nyc3"},
                                "status": "active",
                                "size": "lb-small",
                                "algorithm": "round_robin",
                                "vpc_uuid": "vpc-111",
                                "droplet_ids": [101],
                                "forwarding_rules": [
                                    {"entry_protocol": "https", "entry_port": 443}
                                ],
                            }
                        ]
                    }
                ),
                f"{DIGITALOCEAN_API_BASE}/reserved_ips": _mock_response(
                    {
                        "reserved_ips": [
                            {
                                "ip": "167.99.5.6",
                                "region": {"slug": "nyc3"},
                                "droplet": {"id": 101},
                            }
                        ]
                    }
                ),
                f"{DIGITALOCEAN_API_BASE}/domains": _mock_response(
                    {"domains": [{"name": "example.com", "ttl": 1800}]}
                ),
                f"{DIGITALOCEAN_API_BASE}/firewalls": _mock_response(
                    {
                        "firewalls": [
                            {
                                "id": "fw-1",
                                "name": "web-fw",
                                "status": "succeeded",
                                "droplet_ids": [101],
                                "tags": ["web"],
                                "inbound_rules": [{"ports": "443"}, {"ports": "80"}],
                                "outbound_rules": [{"ports": "0"}],
                            }
                        ]
                    }
                ),
            }
        )
        with ctx:
            items = client.discover_networking()
            alias_items = client.discover_network()

        assert len(items) == 5
        assert len(alias_items) == 5
        by_type = {i["type"]: i for i in items}
        assert by_type["do_vpc"]["ip_range"] == "10.108.0.0/20"
        assert by_type["do_vpc"]["default"] is True
        assert by_type["do_load_balancer"]["algorithm"] == "round_robin"
        assert by_type["do_reserved_ip"]["droplet_id"] == 101
        assert by_type["do_domain"]["ttl"] == 1800
        assert by_type["do_firewall"]["inbound_rules_count"] == 2
        assert by_type["do_firewall"]["outbound_rules_count"] == 1

    def test_discover_databases_and_serverless_and_all(self):
        client = DigitalOceanDiscoveryClient({"api_token": "dop_v1_valid"})
        ctx, _ = _patch_httpx_client(
            {
                f"{DIGITALOCEAN_API_BASE}/databases": _mock_response(
                    {
                        "databases": [
                            {
                                "id": "db-1",
                                "name": "prod-pg",
                                "engine": "pg",
                                "version": "16",
                                "region": "nyc3",
                                "size": "db-s-2vcpu-4gb",
                                "num_nodes": 2,
                                "status": "online",
                                "connection": {
                                    "host": "prod-pg.db.ondigitalocean.com",
                                    "port": 25060,
                                },
                                "private_network_uuid": "vpc-111",
                                "tags": ["prod"],
                            }
                        ]
                    }
                ),
                f"{DIGITALOCEAN_API_BASE}/functions/namespaces": _mock_response(
                    {
                        "namespaces": [
                            {
                                "namespace": "fn-1234",
                                "label": "webhooks-fn",
                                "region": "nyc1",
                                "api_host": "https://faas-nyc1.doserverless.co",
                            }
                        ]
                    }
                ),
                f"{DIGITALOCEAN_API_BASE}/droplets": _mock_response({"droplets": []}),
                f"{DIGITALOCEAN_API_BASE}/kubernetes/clusters": _mock_response(
                    {"kubernetes_clusters": []}
                ),
                f"{DIGITALOCEAN_API_BASE}/apps": _mock_response({"apps": []}),
                f"{DIGITALOCEAN_API_BASE}/volumes": _mock_response({"volumes": []}),
                f"{DIGITALOCEAN_API_BASE}/snapshots": _mock_response({"snapshots": []}),
                f"{DIGITALOCEAN_API_BASE}/vpcs": _mock_response({"vpcs": []}),
                f"{DIGITALOCEAN_API_BASE}/load_balancers": _mock_response(
                    {"load_balancers": []}
                ),
                f"{DIGITALOCEAN_API_BASE}/reserved_ips": _mock_response(
                    {"reserved_ips": []}
                ),
                f"{DIGITALOCEAN_API_BASE}/domains": _mock_response({"domains": []}),
                f"{DIGITALOCEAN_API_BASE}/firewalls": _mock_response({"firewalls": []}),
            }
        )
        with ctx:
            dbs = client.discover_databases()
            fns = client.discover_serverless()
            all_res = client.discover_all()

        assert len(dbs) == 1
        assert dbs[0]["type"] == "do_database"
        assert dbs[0]["engine"] == "pg"
        assert dbs[0]["host"] == "prod-pg.db.ondigitalocean.com"
        assert dbs[0]["port"] == 25060

        assert len(fns) == 1
        assert fns[0]["type"] == "do_function_namespace"
        assert fns[0]["namespace"] == "fn-1234"
        assert fns[0]["api_host"] == "https://faas-nyc1.doserverless.co"

        assert all_res["provider"] == "digitalocean"
        assert all_res["resources_count"] == 2
        assert isinstance(all_res["discovery_time"], datetime)


class TestDigitalOceanDiscoveryServiceIntegration:
    """Verify DiscoveryService create_job, redaction, and test_job with digitalocean."""

    def test_create_and_test_digitalocean_job(self, tmp_path):
        from penguin_dal import DAL, Field

        db = DAL(f"sqlite:///{tmp_path / 'do.db'}", migrate=True)
        db.define_table(
            "organizations",
            Field("name", "string"),
            Field("tenant_id", "integer"),
            Field("created_at", "datetime"),
            Field("updated_at", "datetime"),
        )
        db.define_table(
            "discovery_jobs",
            Field("name", "string", notnull=True),
            Field("provider", "string", notnull=True),
            Field("config_json", "json", notnull=True),
            Field("schedule_interval", "integer", notnull=True),
            Field("enabled", "boolean", notnull=True),
            Field("last_run_at", "datetime"),
            Field("next_run_at", "datetime"),
            Field("village_id", "string"),
            Field("created_at", "datetime"),
            Field("updated_at", "datetime"),
        )
        now = datetime.now(UTC)
        db.organizations.insert(
            name="DO Org", tenant_id=1, created_at=now, updated_at=now
        )
        db.commit()

        svc = DiscoveryService(db)
        job = svc.create_job(
            name="DigitalOcean Prod",
            provider="digitalocean",
            config={"api_token": "dop_v1_secret_token"},
            organization_id=1,
            schedule_interval=3600,
        )
        assert job["provider"] == "digitalocean"
        assert job["config_json"]["api_token"] == "***REDACTED***"

        ctx, _ = _patch_httpx_client(
            {
                f"{DIGITALOCEAN_API_BASE}/account": _mock_response(
                    {"id": "unauthorized", "message": "Invalid token"},
                    status_code=401,
                )
            }
        )
        with ctx:
            test_res = svc.test_job(job["id"])

        assert test_res["success"] is False
        assert test_res["error"] == "Invalid token"
