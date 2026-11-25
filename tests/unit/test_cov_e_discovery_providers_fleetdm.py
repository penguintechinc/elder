"""Coverage tests for the FleetDM discovery client.

The HTTP boundary is replaced with ``httpx.MockTransport`` so the real
httpx request/response objects flow through the client.
"""

import tests.unit.conftest_worker_stubs  # noqa: F401  isort: skip

from unittest.mock import MagicMock, patch

import httpx
import pytest

from apps.worker.discovery import fleetdm_discovery as fd

URL = "https://fleet.example.com"


def _client(**cfg):
    """Build a FleetDM client with a fake token."""
    token = "-".join(("cove", "tok1"))
    base = {"url": URL, "api_token": token}
    base.update(cfg)
    return fd.FleetDMDiscoveryClient(base)


def _http(handler):
    """Return an httpx.Client backed by a MockTransport handler."""
    return httpx.Client(base_url=URL, transport=httpx.MockTransport(handler))


def _resp(status=200, body=None, headers=None):
    """Build an httpx.Response with a JSON body."""
    return httpx.Response(status, json=body, headers=headers or {})


class _Seq:
    """Fake client whose get() replays queued results (responses or errors)."""

    def __init__(self, items):
        """Store the queue of results to replay."""
        self.items = list(items)
        self.calls = []

    def get(self, path, params=None):
        """Pop the next result; raise it if it is an exception."""
        self.calls.append(path)
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def _nosleep(monkeypatch):
    """Disable real sleeping in retry loops."""
    monkeypatch.setattr(fd.time, "sleep", lambda s: None)


class TestInit:
    """Configuration parsing and trivial methods."""

    def test_env_fallbacks(self, monkeypatch):
        """URL and token fall back to environment variables."""
        tok = "-".join(("cove", "env"))
        monkeypatch.setenv("FLEETDM_URL", " https://env.example.com/ ")
        monkeypatch.setenv("FLEETDM_API_TOKEN", f" {tok} ")
        c = fd.FleetDMDiscoveryClient({})
        assert c.url == "https://env.example.com" and c.api_token == tok

    def test_alt_keys(self):
        """Alternate config keys are accepted."""
        c = fd.FleetDMDiscoveryClient(
            {"fleetdm_url": URL + "/", "fleetdm_api_token": "t"}
        )
        assert c.url == URL and c.api_token == "t"
        c2 = fd.FleetDMDiscoveryClient({"url": URL, "token": "t2"})
        assert c2.api_token == "t2"

    @pytest.mark.parametrize(
        "raw,expected",
        [("abc", 100), (None, 100), (0, 100), (9999, 500), (-5, 1), (25, 25)],
    )
    def test_per_page(self, raw, expected):
        """per_page is coerced into [1, 500]."""
        assert _client(per_page=raw).per_page == expected

    def test_flags(self):
        """verify_ssl, sync_mdm, sync_labels are booleans."""
        c = _client(verify_ssl=False, sync_mdm=0, sync_labels=0)
        assert (c.verify_ssl, c.sync_mdm, c.sync_labels) == (False, False, False)

    def test_validate_config(self):
        """Missing url or token raises."""
        assert _client().validate_config() is True
        with pytest.raises(ValueError, match="url"):
            fd.FleetDMDiscoveryClient({"api_token": "t"}).validate_config()
        with pytest.raises(ValueError, match="api_token"):
            fd.FleetDMDiscoveryClient({"url": URL}).validate_config()

    def test_supported_and_empty(self):
        """Static category methods return their constants."""
        c = _client()
        assert "hosts" in c.get_supported_services()
        assert c.discover_storage() == [] and c.discover_network() == []
        assert c.discover_databases() == [] and c.discover_serverless() == []


class TestConnection:
    """test_connection variants."""

    def test_unconfigured(self):
        """No url/token returns False without HTTP."""
        assert fd.FleetDMDiscoveryClient({}).test_connection() is False

    @pytest.mark.parametrize("status,expected", [(200, True), (401, False)])
    def test_status(self, status, expected):
        """Only HTTP 200 counts as connected."""
        c = _client()
        real = httpx.Client
        handler = lambda req: _resp(status, {})  # noqa: E731
        with patch.object(
            fd.httpx,
            "Client",
            lambda **kw: real(
                base_url=kw["base_url"], transport=httpx.MockTransport(handler)
            ),
        ):
            assert c.test_connection() is expected

    def test_exception(self):
        """Transport errors yield False."""
        c = _client()
        with patch.object(fd.httpx, "Client", side_effect=RuntimeError("x")):
            assert c.test_connection() is False


class TestGet:
    """_get retry and fallback behaviour."""

    def test_ok(self):
        """A 200 returns parsed JSON."""
        assert _client()._get(_Seq([_resp(200, {"a": 1})]), "/p") == (200, {"a": 1})

    def test_204(self):
        """204 returns None data."""
        assert _client()._get(_Seq([httpx.Response(204)]), "/p") == (204, None)

    def test_request_error_retries_then_gives_up(self):
        """Three transport errors yield (0, None)."""
        err = httpx.ConnectError("boom")
        s = _Seq([err, err, err])
        assert _client()._get(s, "/p") == (0, None)
        assert len(s.calls) == 3

    def test_request_error_then_success(self):
        """A transient transport error is retried."""
        s = _Seq([httpx.ConnectError("x"), _resp(200, {"ok": 1})])
        assert _client()._get(s, "/p") == (200, {"ok": 1})

    def test_429_retry_then_exhaust(self):
        """429 is retried, final 429 is returned."""
        s = _Seq([_resp(429, {}, {"Retry-After": "1"})] * 3)
        assert _client()._get(s, "/p")[0] == 429
        s2 = _Seq([_resp(429, {}), _resp(200, {"x": 1})])
        assert _client()._get(s2, "/p") == (200, {"x": 1})

    def test_5xx_retry_then_exhaust(self):
        """5xx is retried; final 5xx is returned."""
        assert _client()._get(_Seq([_resp(503, {})] * 3), "/p")[0] == 503
        s = _Seq([_resp(500, {}), _resp(200, {"x": 1})])
        assert _client()._get(s, "/p") == (200, {"x": 1})

    def test_fallback_path(self):
        """404 on the primary path falls back to the next path."""
        s = _Seq([_resp(404, {}), _resp(200, {"y": 1})])
        assert _client()._get(s, "/a", fallback_paths=["/b"]) == (200, {"y": 1})
        assert s.calls == ["/a", "/b"]

    def test_404_without_fallback(self):
        """404 with no fallback is returned as-is."""
        assert _client()._get(_Seq([_resp(404, {})]), "/a")[0] == 404

    def test_all_paths_exhausted(self):
        """404/410 on every path ends in (404, None)."""
        s = _Seq([_resp(404, {}), _resp(410, {})])
        # last path has no next fallback, so its status is returned directly
        assert _client()._get(s, "/a", fallback_paths=["/b"])[0] == 410


class TestPaginate:
    """_paginate termination conditions."""

    def _c(self, pages):
        """Return client and a patched _get that replays (status, data) pages."""
        c = _client(per_page=2)
        c._get = MagicMock(side_effect=list(pages))
        return c

    def test_non_200_or_non_dict(self):
        """Non-200 or non-dict bodies stop pagination."""
        assert self._c([(500, {})])._paginate(None, "/p", "k") == []
        assert self._c([(200, [])])._paginate(None, "/p", "k") == []

    def test_missing_or_empty_items(self):
        """Missing or empty result lists stop pagination."""
        assert self._c([(200, {})])._paginate(None, "/p", "k") == []
        assert self._c([(200, {"k": []})])._paginate(None, "/p", "k") == []

    def test_fallback_result_key(self):
        """fallback_result_key is used when the primary key is absent."""
        c = self._c([(200, {"alt": [1], "meta": {"has_next_results": False}})])
        assert c._paginate(None, "/p", "k", fallback_result_key="alt") == [1]

    def test_has_next_results_true_then_false(self):
        """meta.has_next_results drives multi-page reads."""
        c = self._c(
            [
                (200, {"k": [1, 2], "meta": {"has_next_results": True}}),
                (200, {"k": [3], "meta": {"has_next_results": False}}),
            ]
        )
        assert c._paginate(None, "/p", "k") == [1, 2, 3]

    def test_short_page_without_meta(self):
        """Without meta, a short page ends pagination."""
        c = self._c([(200, {"k": [1, 2]}), (200, {"k": [3]})])
        assert c._paginate(None, "/p", "k") == [1, 2, 3]

    def test_meta_none_flag_uses_length(self):
        """A null has_next_results falls back to the length heuristic."""
        c = self._c([(200, {"k": [1], "meta": {"has_next_results": None}})])
        assert c._paginate(None, "/p", "k") == [1]

    def test_max_pages(self):
        """max_pages bounds the loop."""
        c = _client(per_page=1)
        c._get = MagicMock(return_value=(200, {"k": [1]}))
        assert c._paginate(None, "/p", "k", max_pages=3) == [1, 1, 1]


def _fleet_handler(extra=None):
    """Return a MockTransport handler serving a small Fleet API."""
    routes = {
        "/api/v1/fleet/hosts": {
            "hosts": [
                {
                    "id": 1,
                    "hostname": "h1",
                    "platform": "ubuntu",
                    "os_version_id": 7,
                    "mdm": {"name": "x", "enrollment_status": "On"},
                    "fleet_id": 3,
                },
                {"id": 2, "computer_name": "cn", "team_id": 4},
                {"id": 3, "display_name": "dn"},
                {"id": 4},
                {"hostname": "noid"},
            ],
            "meta": {"has_next_results": False},
        },
        "/api/v1/fleet/software/versions": {
            "software": [
                {"id": 1, "name": "curl", "version": "8", "source": "deb"},
                {"id": 2},
                {"name": "noid"},
            ]
        },
        "/api/v1/fleet/os_versions": {
            "os_versions": [
                {"os_version_id": 7, "name": "Ubuntu 22", "platform": "ubuntu"},
                {"id": 8, "name_only": "macOS", "version": "14"},
                {"id": 9},
                {"name": "noid"},
            ]
        },
        "/api/v1/fleet/labels": {
            "labels": [
                {"id": 1, "name": "builtin", "label_type": "builtin"},
                {"id": 2, "name": "custom"},
                {"name": "noid"},
            ]
        },
        "/api/v1/fleet/labels/2/hosts": {"hosts": [{"id": 1}, {"name": "x"}]},
        "/api/v1/fleet/vulnerabilities": {
            "vulnerabilities": [
                {"cve": "CVE-2024-0001", "cvss_score": 9.8, "published": "d"},
                {"cve": "CVE-2024-0002"},
                {"no": "cve"},
            ]
        },
        "/api/v1/fleet/policies": {
            "policies": [
                {"id": 1, "name": "p1", "platform": "linux", "critical": True},
                {"id": 2},
                {"name": "noid"},
            ]
        },
        "/api/v1/fleet/configuration_profiles": {
            "profiles": [
                {"profile_uuid": "pu1", "name": "prof", "platform": "darwin"},
                {"uuid": "pu2"},
                {"id": 5},
                {"name": "nouuid"},
            ]
        },
    }
    routes.update(extra or {})

    def handler(request: httpx.Request) -> httpx.Response:
        """Serve the routed JSON, or 404."""
        body = routes.get(request.url.path)
        if body is None:
            return httpx.Response(404, json={})
        return httpx.Response(200, json=body)

    return handler


def _with_transport(c, handler):
    """Patch _new_client on c to use a mock transport."""
    c._new_client = lambda: _http(handler)  # type: ignore[method-assign]
    return c


class TestDiscovery:
    """Compute, security and discover_all against the mocked API."""

    def test_compute(self):
        """Hosts, software, OS versions and labels are formatted."""
        c = _with_transport(_client(), _fleet_handler())
        out = c.discover_compute()
        by_type = {}
        for r in out:
            by_type.setdefault(r["resource_type"], []).append(r)
        hosts = by_type["fleetdm_host"]
        assert [h["name"] for h in hosts] == ["h1", "cn", "dn", "Host-4"]
        assert hosts[0]["relationships"][0]["target_external_id"] == "fleetdm:os:7"
        assert hosts[0]["metadata"]["fleet_id"] == 3
        assert hosts[1]["metadata"]["fleet_id"] == 4
        assert hosts[3]["relationships"] == []
        assert [s["name"] for s in by_type["fleetdm_software"]] == [
            "curl 8",
            "Software-2",
        ]
        osv = by_type["fleetdm_os_version"]
        assert [o["resource_id"] for o in osv] == ["os-7", "os-8", "os-9"]
        assert osv[1]["name"] == "macOS 14"
        labels = by_type["fleetdm_label"]
        assert len(labels) == 2
        assert labels[0]["relationships"] == []
        assert labels[1]["relationships"][0]["edge_type"] == "manages"
        assert len(labels[1]["relationships"]) == 1

    def test_compute_labels_disabled(self):
        """sync_labels=False skips label discovery."""
        c = _with_transport(_client(sync_labels=False), _fleet_handler())
        assert not [
            r for r in c.discover_compute() if r["resource_type"] == "fleetdm_label"
        ]

    def test_software_fallback_path(self):
        """The Fleet 4.x software path is used when versions 404s."""
        h2 = _fleet_handler(
            {"/api/v1/fleet/software": {"software": [{"id": 5, "name": "x"}]}}
        )

        def handler(req):
            """404 the versions path, delegate the rest."""
            if req.url.path == "/api/v1/fleet/software/versions":
                return httpx.Response(404, json={})
            return h2(req)

        c = _with_transport(_client(), handler)
        names = [
            r["name"] for r in c.discover_compute() if "software" in r["resource_type"]
        ]
        assert names == ["x"]

    def test_security(self):
        """Vulnerabilities, policies and MDM profiles are formatted."""
        c = _with_transport(_client(), _fleet_handler())
        with c._new_client() as client:
            out = c._discover_security_with_client(client)
        types = [r["resource_type"] for r in out]
        assert types.count("fleetdm_vulnerability") == 2
        assert types.count("fleetdm_policy") == 2
        assert types.count("fleetdm_mdm_profile") == 3
        assert out[0]["metadata"]["severity"] == "critical"
        assert out[1]["metadata"]["severity"] == "unknown"
        assert out[0]["metadata"]["cve_published"] == "d"
        ids = [
            r["resource_id"] for r in out if r["resource_type"] == "fleetdm_mdm_profile"
        ]
        assert ids == ["profile-pu1", "profile-pu2", "profile-5"]

    def test_security_mdm_disabled_and_policy_fallback(self):
        """sync_mdm=False skips profiles; policies fall back to the 4.x path."""
        routes_no_policies = _fleet_handler(
            {"/api/v1/fleet/global/policies": {"policies": [{"id": 9}]}}
        )

        def handler(req):
            """404 the unified policies path."""
            if req.url.path == "/api/v1/fleet/policies":
                return httpx.Response(404, json={})
            return routes_no_policies(req)

        c = _with_transport(_client(sync_mdm=False), handler)
        with c._new_client() as client:
            out = c._discover_security_with_client(client)
        assert not [r for r in out if r["resource_type"] == "fleetdm_mdm_profile"]
        assert [
            r["resource_id"] for r in out if r["resource_type"] == "fleetdm_policy"
        ] == ["policy-9"]

    def test_discover_all(self):
        """discover_all combines compute and security and counts them."""
        c = _with_transport(_client(), _fleet_handler())
        out = c.discover_all()
        assert out["resources_count"] == len(out["compute"]) + len(out["security"])
        assert out["storage"] == [] and out["compute"] and out["security"]

    def test_discover_requires_config(self):
        """Discovery without url/token raises ValueError."""
        with pytest.raises(ValueError):
            fd.FleetDMDiscoveryClient({}).discover_compute()
        with pytest.raises(ValueError):
            fd.FleetDMDiscoveryClient({}).discover_all()

    def test_new_client_real(self):
        """_new_client builds an httpx client with auth headers."""
        c = _client()
        with c._new_client() as client:
            assert client.headers["Authorization"].startswith("Bearer ")
            assert str(client.base_url).startswith(URL)

    def test_discover_compute_uses_context_manager(self):
        """discover_compute goes through _new_client end to end."""
        c = _with_transport(_client(), _fleet_handler())
        assert c.discover_compute()
