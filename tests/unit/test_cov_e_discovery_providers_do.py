"""Gap-filling coverage tests for the DigitalOcean discovery client.

The httpx client is replaced at the module boundary; complements
test_digitalocean_discovery.
"""

import tests.unit.conftest_worker_stubs  # noqa: F401  isort: skip

from unittest.mock import MagicMock, patch

import pytest

from apps.worker.discovery import digitalocean_discovery as do

TOKEN = "-".join(("cove", "do1"))


def _client(**cfg):
    """Build a client with a fake token unless overridden."""
    cfg.setdefault("api_token", TOKEN)
    return do.DigitalOceanDiscoveryClient(cfg)


def _resp(payload=None, status=200, json_error=False):
    """Build a fake httpx response."""
    r = MagicMock()
    r.status_code = status
    if json_error:
        r.json.side_effect = ValueError("bad")
    else:
        r.json.return_value = payload
    return r


def _patch_http(get):
    """Patch httpx.Client so client.get delegates to `get`."""
    m = MagicMock()
    m.__enter__.return_value = m
    m.__exit__.return_value = False
    m.get.side_effect = get
    return patch.object(do.httpx, "Client", return_value=m), m


class TestRegionSlug:
    """_region_slug handles dict, str and other values."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ({"slug": "nyc1"}, "nyc1"),
            ({"name": "NYC"}, "NYC"),
            ({}, None),
            ("sfo2", "sfo2"),
            (None, None),
            (5, None),
        ],
    )
    def test_slug(self, raw, expected):
        """Region value normalises to a slug or None."""
        assert do._region_slug(raw) == expected


class TestPaginated:
    """_get_paginated guard rails and link following."""

    def test_httpx_unavailable(self, monkeypatch):
        """Without httpx no request is made."""
        monkeypatch.setattr(do, "_HTTPX_AVAILABLE", False)
        assert _client()._get_paginated("/x", "k") == []

    def test_no_token(self, monkeypatch):
        """Without a token no request is made."""
        monkeypatch.delenv("DIGITALOCEAN_API_TOKEN", raising=False)
        monkeypatch.delenv("DIGITALOCEAN_API_KEY", raising=False)
        assert do.DigitalOceanDiscoveryClient({})._get_paginated("/x", "k") == []

    def test_follows_next_links_and_absolute_urls(self):
        """next links (absolute URLs) are followed until exhausted."""
        pages = {
            f"{do.DIGITALOCEAN_API_BASE}/a": _resp(
                {"k": [1], "links": {"pages": {"next": "https://api.example.com/p2"}}}
            ),
            "https://api.example.com/p2": _resp({"k": [2], "links": {"pages": {}}}),
        }
        p, m = _patch_http(lambda url, headers=None: pages[url])
        with p:
            assert _client()._get_paginated("/a", "k") == [1, 2]
        assert m.get.call_count == 2
        p, m = _patch_http(lambda url, headers=None: _resp({"k": [3]}))
        with p:
            assert _client()._get_paginated("https://abs.example.com/x", "k") == [3]

    def test_loop_and_odd_payloads(self):
        """A repeating next link stops; non-list items and bad links are tolerated."""
        loop = _resp(
            {"k": [1], "links": {"pages": {"next": f"{do.DIGITALOCEAN_API_BASE}/a"}}}
        )
        p, m = _patch_http(lambda url, headers=None: loop)
        with p:
            assert _client()._get_paginated("/a", "k") == [1]
        assert m.get.call_count == 1
        for payload in (
            {"k": "notalist", "links": "bad"},
            {"k": None, "links": {"pages": "bad"}},
        ):
            p, _ = _patch_http(lambda url, headers=None, pl=payload: _resp(pl))
            with p:
                assert _client()._get_paginated("/a", "k") == []

    def test_error_is_swallowed(self):
        """Transport errors yield what was gathered so far."""
        p, _ = _patch_http(
            lambda url, headers=None: (_ for _ in ()).throw(RuntimeError("x"))
        )
        with p:
            assert _client()._get_paginated("/a", "k") == []

    def test_page_cap(self, monkeypatch):
        """The page cap stops runaway pagination."""
        monkeypatch.setattr(do, "MAX_PAGINATION_PAGES", 2)
        counter = iter(range(100))
        p, m = _patch_http(
            lambda url, headers=None: _resp(
                {
                    "k": [1],
                    "links": {
                        "pages": {"next": f"https://x.example.com/{next(counter)}"}
                    },
                }
            )
        )
        with p:
            assert _client()._get_paginated("/a", "k") == [1, 1]


class TestConnection:
    """test_connection result shapes."""

    def test_unavailable_and_no_token(self, monkeypatch):
        """Guards report why the connection cannot be tested."""
        monkeypatch.setattr(do, "_HTTPX_AVAILABLE", False)
        assert "httpx" in _client().test_connection()["error"]
        monkeypatch.setattr(do, "_HTTPX_AVAILABLE", True)
        monkeypatch.delenv("DIGITALOCEAN_API_TOKEN", raising=False)
        monkeypatch.delenv("DIGITALOCEAN_API_KEY", raising=False)
        assert (
            "not configured"
            in do.DigitalOceanDiscoveryClient({}).test_connection()["error"]
        )

    def test_http_errors(self):
        """Non-200 uses the message, or HTTP status when no usable body."""
        for resp, expected in (
            (_resp({"message": "bad token"}, 401), "bad token"),
            (_resp({"other": 1}, 500), "HTTP 500"),
            (_resp(["x"], 500), "HTTP 500"),
            (_resp(None, 502, json_error=True), "HTTP 502"),
        ):
            p, _ = _patch_http(lambda url, headers=None, r=resp: r)
            with p:
                out = _client().test_connection()
            assert out["connected"] is False and out["error"] == expected

    def test_invalid_account_and_success(self):
        """Missing account object fails; a valid one succeeds."""
        for payload in ({"account": "x"}, ["list"]):
            p, _ = _patch_http(lambda url, headers=None, pl=payload: _resp(pl))
            with p:
                assert "Invalid" in _client().test_connection()["error"]
        acct = {
            "account": {
                "status": "active",
                "email": "a@example.com",
                "droplet_limit": 5,
            }
        }
        p, _ = _patch_http(lambda url, headers=None: _resp(acct))
        with p:
            out = _client().test_connection()
        assert out["connected"] is True and out["droplet_limit"] == 5

    def test_exception(self):
        """Exceptions are reported in the result."""
        p, _ = _patch_http(
            lambda url, headers=None: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        with p:
            assert _client().test_connection()["error"] == "boom"


class TestDiscover:
    """discover_* mapping with sparse and rich payloads."""

    def _routes(self, routes):
        """Patch _get_paginated to return canned lists keyed by path."""
        c = _client()
        c._get_paginated = lambda path, key: routes.get(path, [])  # type: ignore[method-assign]
        return c

    def test_compute(self):
        """Droplets, clusters and apps handle dict/str and missing fields."""
        c = self._routes(
            {
                "/droplets": [
                    {
                        "id": 1,
                        "name": "d1",
                        "region": {"slug": "nyc1"},
                        "networks": {
                            "v4": [{"ip_address": "10.0.0.1"}, {}, "junk"],
                            "v6": [{"ip_address": "::1"}],
                        },
                        "image": {"slug": "ubuntu"},
                    },
                    {"id": 2, "image": "plain"},
                    {"id": 3, "image": {"name": "named"}},
                ],
                "/kubernetes/clusters": [
                    {"id": "k1", "status": {"state": "running"}, "node_pools": [1, 2]},
                    {"id": "k2", "name": "n", "status": "ok"},
                ],
                "/apps": [
                    {
                        "id": "a1",
                        "spec": {"name": "s"},
                        "active_deployment": {"phase": "ACTIVE"},
                    },
                    {"id": "a2", "name": "n"},
                    {"id": "a3"},
                ],
            }
        )
        out = c.discover_compute()
        d = [r for r in out if r["type"] == "do_droplet"]
        assert d[0]["ipv4"] == ["10.0.0.1"] and d[0]["ipv6"] == ["::1"]
        assert [x["image"] for x in d] == ["ubuntu", "plain", "named"]
        assert d[1]["name"] == "2"
        k = [r for r in out if r["type"] == "do_kubernetes"]
        assert [x["status"] for x in k] == ["running", "ok"]
        assert k[0]["name"] == "k1" and k[0]["node_pools_count"] == 2
        a = [r for r in out if r["type"] == "do_app"]
        assert [x["name"] for x in a] == ["s", "n", "a3"]
        assert a[2]["status"] is None

    def test_storage(self):
        """Volumes and snapshots map names with id fallback."""
        c = self._routes(
            {
                "/volumes": [{"id": "v1", "name": "n"}, {"id": "v2"}],
                "/snapshots": [{"id": "s1"}, {"id": "s2", "name": "sn"}],
            }
        )
        out = c.discover_storage()
        assert [r["name"] for r in out] == ["n", "v2", "s1", "sn"]

    def test_networking(self):
        """All networking categories map with sparse variants."""
        c = self._routes(
            {
                "/vpcs": [{"id": "v", "default": True}, {"name": "n"}],
                "/load_balancers": [
                    {"id": "l"},
                    {"name": "ln", "forwarding_rules": [1]},
                ],
                "/reserved_ips": [
                    {"ip": "1.2.3.4", "droplet": {"id": 7}},
                    {"ip": "1.2.3.5", "droplet_id": 8},
                ],
                "/domains": [{"name": "example.com", "ttl": 30}],
                "/firewalls": [
                    {"id": "f", "inbound_rules": [1, 2], "outbound_rules": [1]},
                    {"name": "fn"},
                ],
            }
        )
        out = c.discover_network()
        by = {}
        for r in out:
            by.setdefault(r["type"], []).append(r)
        assert (
            by["do_vpc"][0]["default"] is True and by["do_vpc"][1]["default"] is False
        )
        assert [x["droplet_id"] for x in by["do_reserved_ip"]] == [7, 8]
        assert by["do_firewall"][0]["inbound_rules_count"] == 2
        assert by["do_firewall"][1]["name"] == "fn"
        assert by["do_domain"][0]["id"] == "example.com"
        assert by["do_load_balancer"][0]["name"] == "l"

    def test_databases_serverless_and_all(self):
        """Databases, function namespaces and the aggregate result."""
        c = self._routes(
            {
                "/databases": [
                    {"id": "d", "connection": {"host": "h", "port": 1}},
                    {"name": "dn"},
                ],
                "/functions/namespaces": [
                    {"namespace": "ns1", "label": "L"},
                    {"namespace": "ns2"},
                ],
            }
        )
        dbs = c.discover_databases()
        assert dbs[0]["host"] == "h" and dbs[1]["host"] is None
        sl = c.discover_serverless()
        assert [r["name"] for r in sl] == ["L", "ns2"]
        out = c.discover_all()
        assert out["resources_count"] == 4 and out["provider"] == "digitalocean"
        assert "droplets" in c.get_supported_services()
        assert c.validate_config() is True
