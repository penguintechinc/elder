"""Behavioural tests for the CodeRun restriction gateway (auth, scopes, caps, SSRF, proxying)."""

import http.client
import json
import socket
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from apps.runworker import gateway as mod
from apps.runworker.gateway import (
    RestrictionGateway,
    has_required_scope,
    map_request_to_scope,
    validate_egress_url,
)
from tests.unit._cov_f_helpers import fake_secret

PUBLIC_IP = "93.184.216.34"


def _upstream_client(
    seen: list[httpx.Request], *, status: int = 200, body: bytes = b'{"ok": true}'
) -> httpx.Client:
    """Build an httpx client whose transport records requests and returns a canned reply."""

    def handler(request: httpx.Request) -> httpx.Response:
        """Record the request and answer with the canned response."""
        seen.append(request)
        return httpx.Response(
            status,
            content=body,
            headers={"Content-Type": "application/json", "X-Secret-Leak": "no"},
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


def _failing_client() -> httpx.Client:
    """Build an httpx client whose transport always raises a connect error."""

    def handler(request: httpx.Request) -> httpx.Response:
        """Simulate an unreachable upstream."""
        raise httpx.ConnectError("boom", request=request)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _call(
    gw: RestrictionGateway,
    method: str,
    path: str,
    *,
    token: str | None = None,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> tuple[int, dict[str, str], bytes]:
    """Issue a real loopback HTTP request to the gateway and return the response."""
    hdrs = dict(headers or {})
    if token is not None:
        hdrs["Authorization"] = f"Bearer {token}"
    conn = http.client.HTTPConnection("127.0.0.1", gw.port, timeout=10)
    try:
        conn.request(method, path, body=body, headers=hdrs)
        resp = conn.getresponse()
        data = resp.read()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, data
    finally:
        conn.close()


def _make(**kwargs: Any) -> RestrictionGateway:
    """Create a gateway with sensible defaults for the tests."""
    kwargs.setdefault("elder_api_url", "http://hub.invalid:5000/")
    kwargs.setdefault("execution_id", "exec-123")
    return RestrictionGateway(**kwargs)


class TestScopeMapping:
    """Path/method to OIDC scope mapping."""

    @pytest.mark.parametrize(
        ("method", "path", "expected"),
        [
            ("GET", "/api/v1/entities", "entities:read"),
            ("head", "/api/v1/entities/5?x=1", "entities:read"),
            ("OPTIONS", "/api/v1/Entities", "entities:read"),
            ("POST", "/api/v1/entities", "entities:write"),
            ("PUT", "/api/v1/entities/5", "entities:write"),
            ("PATCH", "/api/v1/entities/5", "entities:write"),
            ("DELETE", "/api/v1/entities/5", "entities:delete"),
            ("TRACE", "/api/v1/entities", None),
            ("GET", "/other/v1/entities", None),
            ("GET", "/api/v1/", None),
            ("GET", "/api/v1/a/../b", None),
            ("GET", "/api/v1/%2e%2e/secret", None),
            ("GET", "/api/v1/a\\b", None),
            ("GET", "/api/v1/a%00b", None),
            ("GET", "/api/v1/-bad", None),
            ("", "", None),
        ],
    )
    def test_map_request_to_scope(
        self, method: str, path: str, expected: str | None
    ) -> None:
        """Valid paths map to resource:action; malformed ones yield None."""
        assert map_request_to_scope(method, path) == expected

    def test_none_method_is_rejected(self) -> None:
        """A missing method never maps to a scope."""
        assert map_request_to_scope(None, "/api/v1/entities") is None  # type: ignore[arg-type]

    def test_wrong_prefix_segments_rejected(self) -> None:
        """Paths whose first segments are not api/v1 are rejected."""
        assert map_request_to_scope("GET", "/api/v1/") is None

    @pytest.mark.parametrize(
        ("required", "granted", "expected"),
        [
            ("", ["x:read"], False),
            ("entities:read", [], False),
            ("entities", ["entities:read"], False),
            ("entities:read", ["entities:read"], True),
            ("entities:read", ["ENTITIES:READ"], True),
            ("entities:read", ["*"], True),
            ("entities:read", ["*:*"], True),
            ("entities:read", ["*:read"], True),
            ("entities:read", ["*:admin"], True),
            ("entities:read", ["*:write"], False),
            ("entities:read", ["entities:*"], True),
            ("entities:read", ["entities:admin"], True),
            ("entities:read", ["entities:write"], False),
            ("entities:read", ["other:read"], False),
            ("entities:read", ["", None, "noscope"], False),
        ],
    )
    def test_has_required_scope(
        self, required: str, granted: list[Any], expected: bool
    ) -> None:
        """Scope matching honours wildcards and rejects malformed grants."""
        assert has_required_scope(required, granted) is expected


class TestEgressValidation:
    """SSRF and allowlist enforcement for the egress proxy."""

    def _ok_resolver(self, host: str, port: int) -> list[str]:
        """Resolve every host to a public address."""
        return [PUBLIC_IP]

    @pytest.mark.parametrize(
        ("url", "domains", "reason"),
        [
            ("https://api.example.com/x", [], "egress_not_allowed"),
            ("", ["api.example.com"], "missing_egress_url"),
            (None, ["api.example.com"], "missing_egress_url"),
            ("ftp://api.example.com/x", ["api.example.com"], "unsupported_scheme"),
            ("https:///x", ["api.example.com"], "missing_hostname"),
            ("http://10.0.0.1/x", ["10.0.0.1"], "ip_literal_blocked"),
            ("http://[::1]/x", ["*"], "ip_literal_blocked"),
            ("http://localhost/x", ["localhost"], "loopback_hostname_blocked"),
            ("http://a.localhost/x", ["*.localhost"], "loopback_hostname_blocked"),
            ("https://evil.com/x", ["api.example.com"], "domain_not_allowlisted"),
            ("https://api.example.com:abc/x", ["api.example.com"], "invalid_port"),
        ],
    )
    def test_rejections(self, url: Any, domains: list[str], reason: str) -> None:
        """Each invalid URL is rejected with a stable reason code."""
        ok, detail = validate_egress_url(url, domains, dns_resolver=self._ok_resolver)
        assert (ok, detail) == (False, reason)

    def test_exact_and_wildcard_allow(self) -> None:
        """Exact and wildcard allowlist entries accept matching hosts."""
        ok, url = validate_egress_url(
            " https://api.example.com/x ",
            ["api.example.com"],
            dns_resolver=self._ok_resolver,
        )
        assert ok and url == "https://api.example.com/x"
        ok, _ = validate_egress_url(
            "http://a.b.example.com:8080/",
            ["*.example.com"],
            dns_resolver=self._ok_resolver,
        )
        assert ok

    def test_wildcard_does_not_match_apex(self) -> None:
        """A wildcard entry never matches the bare apex domain."""
        ok, reason = validate_egress_url(
            "https://example.com/", ["*.example.com"], dns_resolver=self._ok_resolver
        )
        assert (ok, reason) == (False, "domain_not_allowlisted")

    def test_blank_allowlist_entries_skipped(self) -> None:
        """Blank allowlist entries are ignored rather than matching everything."""
        ok, reason = validate_egress_url(
            "https://a.com/", ["", None, "  "], dns_resolver=self._ok_resolver
        )
        assert (ok, reason) == (False, "domain_not_allowlisted")

    def test_blocked_resolution_rejected(self) -> None:
        """A hostname resolving to a private address is blocked."""
        ok, reason = validate_egress_url(
            "https://a.com/", ["a.com"], dns_resolver=lambda h, p: ["10.1.2.3"]
        )
        assert (ok, reason) == (False, "blocked_ip_address")

    def test_mixed_resolution_rejected(self) -> None:
        """One bad address among good ones blocks the request."""
        ok, reason = validate_egress_url(
            "https://a.com/",
            ["a.com"],
            dns_resolver=lambda h, p: [PUBLIC_IP, "127.0.0.1"],
        )
        assert (ok, reason) == (False, "blocked_ip_address")

    def test_empty_resolution_rejected(self) -> None:
        """No DNS records means no egress."""
        ok, reason = validate_egress_url(
            "https://a.com/", ["a.com"], dns_resolver=lambda h, p: []
        )
        assert (ok, reason) == (False, "dns_no_records")

    def test_default_resolver_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without an injected resolver the system resolver is consulted."""
        seen: list[tuple[str, int]] = []

        def fake_getaddrinfo(host: str, port: int, **_kw: Any) -> list[Any]:
            """Return one public address and record the lookup."""
            seen.append((host, port))
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, port))]

        monkeypatch.setattr(mod.socket, "getaddrinfo", fake_getaddrinfo)
        ok, _ = validate_egress_url("https://a.com/", ["a.com"])
        assert ok and seen == [("a.com", 443)]
        ok, _ = validate_egress_url("http://a.com/", ["a.com"])
        assert ok and seen[-1] == ("a.com", 80)

    def test_default_resolver_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A DNS failure is reported as a rejection, not an exception."""

        def boom(*_a: Any, **_kw: Any) -> list[Any]:
            """Simulate a resolver failure."""
            raise socket.gaierror("nope")

        monkeypatch.setattr(mod.socket, "getaddrinfo", boom)
        ok, reason = validate_egress_url("https://a.com/", ["a.com"])
        assert (ok, reason) == (False, "dns_resolution_failed")

    @pytest.mark.parametrize(
        ("ip", "blocked"),
        [
            ("8.8.8.8", False),
            ("10.0.0.1", True),
            ("127.0.0.1", True),
            ("169.254.169.254", True),
            ("224.0.0.1", True),
            ("0.0.0.0", True),  # noqa: S104
            ("100.64.0.1", True),
            ("::1", True),
            ("::ffff:127.0.0.1", True),
            ("fe80::1%eth0", True),
            ("not-an-ip", True),
            ("2606:4700:4700::1111", False),
        ],
    )
    def test_is_blocked_ip(self, ip: str, blocked: bool) -> None:
        """Non-global, special-use and unparsable addresses are blocked."""
        assert mod._is_blocked_ip(ip) is blocked

    def test_cgnat_range_is_blocked_independently(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The explicit carrier-grade NAT check blocks its range even if global."""
        import ipaddress

        monkeypatch.setattr(mod, "_CGNAT_NETWORK", ipaddress.ip_network("8.8.8.0/24"))
        assert mod._is_blocked_ip("8.8.8.8") is True

    def test_is_ip_literal(self) -> None:
        """IPv4, bracketed IPv6 are literals; names are not."""
        assert mod._is_ip_literal("1.2.3.4")
        assert mod._is_ip_literal("[::1]")
        assert not mod._is_ip_literal("example.com")

    def test_domain_match_edge_cases(self) -> None:
        """Empty and localhost hosts never match, trailing dots are ignored."""
        assert not mod._domain_matches_allowlist("", ["a.com"])
        assert not mod._domain_matches_allowlist("localhost", ["localhost"])
        assert mod._domain_matches_allowlist("A.com.", ["a.com."])


class TestLifecycle:
    """Construction, start/stop and properties."""

    def test_defaults_and_normalisation(self) -> None:
        """Constructor normalises inputs and generates a random token."""
        gw = RestrictionGateway(
            elder_api_url="",
            granted_scopes=[" a:read ", "", "b:write"],
            allowed_egress_domains=["Example.COM.", "", " "],
            max_requests=0,
            max_body_bytes=0,
            upstream_timeout=0,
        )
        assert gw.elder_api_url == "http://hub-api:5000"
        assert gw.granted_scopes == ("a:read", "b:write")
        assert gw.allowed_egress_domains == ("example.com",)
        assert gw.max_requests == 1 and gw.max_body_bytes == 1
        assert gw.upstream_timeout == 1.0
        assert len(gw.gateway_token) >= 32
        assert gw.port == 0
        assert "RestrictionGateway(" in repr(gw)
        assert gw.gateway_token not in repr(gw)
        gw.stop()

    def test_url_requires_start(self) -> None:
        """The URL is unavailable before the server is started."""
        gw = _make()
        with pytest.raises(RuntimeError):
            _ = gw.url
        gw.stop()

    def test_start_is_idempotent_and_context_manager(self) -> None:
        """Starting twice returns the same running server; context exit stops it."""
        with _make(http_client=_upstream_client([])) as gw:
            port = gw.port
            assert port > 0 and gw.url == f"http://127.0.0.1:{port}"
            assert gw.start() is gw and gw.port == port
        # stopped: connection should now be refused
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=1).close()

    def test_stop_without_start_closes_owned_client(self) -> None:
        """An internally created httpx client is closed on stop."""
        gw = _make()
        gw.stop()
        assert gw._client.is_closed

    def test_stop_joins_live_thread(self) -> None:
        """A still-running server thread is joined with a bounded timeout."""
        joined: list[float] = []
        fake = SimpleNamespace(
            is_alive=lambda: True, join=lambda timeout: joined.append(timeout)
        )
        gw = _make(http_client=_upstream_client([]))
        gw._thread = fake  # type: ignore[assignment]
        gw.stop()
        assert joined == [3.0]

    def test_stop_keeps_external_client_open(self) -> None:
        """A caller-supplied httpx client is left open on stop."""
        client = _upstream_client([])
        gw = _make(http_client=client)
        gw.stop()
        assert not client.is_closed
        client.close()


class TestRequestHandling:
    """End-to-end behaviour over the loopback server."""

    def test_unauthorized_variants(self) -> None:
        """Missing, malformed, empty or wrong bearer tokens all yield 401."""
        with _make(http_client=_upstream_client([])) as gw:
            assert _call(gw, "GET", "/api/v1/x")[0] == 401
            assert (
                _call(gw, "GET", "/api/v1/x", headers={"Authorization": "Basic zz"})[0]
                == 401
            )
            assert (
                _call(gw, "GET", "/api/v1/x", headers={"Authorization": "Bearer "})[0]
                == 401
            )
            assert _call(gw, "GET", "/api/v1/x", token=fake_secret("wrong"))[0] == 401
            assert gw.request_count == 0
            assert all(e["reason"] == "unauthorized" for e in gw.audit_log)
            assert len(gw.audit_log) == 4

    def test_path_not_allowed(self) -> None:
        """Paths outside /api/v1 are refused and audited."""
        with _make(granted_scopes=["*"], http_client=_upstream_client([])) as gw:
            status, _, body = _call(gw, "GET", "/etc/passwd", token=gw.gateway_token)
            assert status == 403 and json.loads(body)["error"] == "path_not_allowed"
            assert gw.audit_log[-1]["reason"] == "path_not_allowed"

    def test_scope_not_granted(self) -> None:
        """A missing scope yields 403 naming the required scope."""
        seen: list[httpx.Request] = []
        with _make(
            granted_scopes=["entities:read"], http_client=_upstream_client(seen)
        ) as gw:
            status, _, body = _call(
                gw, "POST", "/api/v1/entities", token=gw.gateway_token, body=b"{}"
            )
            assert status == 403
            assert json.loads(body)["required_scope"] == "entities:write"
            assert seen == []

    def test_proxies_with_real_token_and_strips_headers(self) -> None:
        """The real API token replaces the gateway token on forwarded requests."""
        api_token = fake_secret("api", "token")
        seen: list[httpx.Request] = []
        with _make(
            elder_api_token=api_token,
            granted_scopes=["entities:*"],
            http_client=_upstream_client(seen),
        ) as gw:
            status, headers, body = _call(
                gw,
                "POST",
                "/api/v1/entities?a=1",
                token=gw.gateway_token,
                headers={"X-Custom": "yes", "Content-Type": "application/json"},
                body=b'{"k": 1}',
            )
            assert status == 200 and json.loads(body) == {"ok": True}
            assert headers.get("content-type") == "application/json"
            assert "x-secret-leak" not in headers
            req = seen[0]
            assert str(req.url) == "http://hub.invalid:5000/api/v1/entities?a=1"
            assert req.headers["authorization"] == f"Bearer {api_token}"
            assert req.headers["x-custom"] == "yes"
            assert req.content == b'{"k": 1}'
            assert gw.gateway_token not in json.dumps(dict(req.headers))
            assert gw.request_count == 1
            entry = gw.audit_log[-1]
            assert entry["allowed"] and entry["status_code"] == 200
            assert entry["path"] == "/api/v1/entities"
            assert api_token not in json.dumps(gw.audit_log)

    def test_proxy_without_api_token_omits_authorization(self) -> None:
        """With no real token configured no Authorization header is forwarded."""
        seen: list[httpx.Request] = []
        with _make(granted_scopes=["*"], http_client=_upstream_client(seen)) as gw:
            _call(gw, "GET", "/api/v1/entities", token=gw.gateway_token)
            assert "authorization" not in seen[0].headers

    def test_head_request_has_no_body(self) -> None:
        """HEAD responses carry headers but no body."""
        with _make(granted_scopes=["*"], http_client=_upstream_client([])) as gw:
            status, headers, body = _call(
                gw, "HEAD", "/api/v1/entities", token=gw.gateway_token
            )
            assert status == 200 and body == b""
            assert int(headers["content-length"]) > 0

    def test_head_rejection_has_no_body(self) -> None:
        """A rejected HEAD request returns no JSON body."""
        with _make(http_client=_upstream_client([])) as gw:
            status, _, body = _call(gw, "HEAD", "/api/v1/x")
            assert status == 401 and body == b""

    def test_empty_upstream_body(self) -> None:
        """An empty upstream body is relayed as empty."""
        with _make(
            granted_scopes=["*"], http_client=_upstream_client([], status=204, body=b"")
        ) as gw:
            status, _, body = _call(
                gw, "DELETE", "/api/v1/entities/1", token=gw.gateway_token
            )
            assert status == 204 and body == b""

    def test_upstream_error_is_502(self) -> None:
        """Network failures to hub-api become a 502 without leaking details."""
        with _make(granted_scopes=["*"], http_client=_failing_client()) as gw:
            status, _, body = _call(
                gw, "GET", "/api/v1/entities", token=gw.gateway_token
            )
            assert status == 502 and json.loads(body) == {
                "error": "upstream_unreachable"
            }
            assert gw.audit_log[-1]["reason"] == "upstream_error"

    def test_oversized_upstream_response_is_502(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Responses beyond the cap are replaced with a 502."""
        monkeypatch.setattr(mod, "MAX_RESPONSE_BYTES", 4)
        with _make(
            granted_scopes=["*"], http_client=_upstream_client([], body=b"123456789")
        ) as gw:
            status, _, body = _call(
                gw, "GET", "/api/v1/entities", token=gw.gateway_token
            )
            assert status == 502
            assert json.loads(body)["error"] == "upstream_response_too_large"

    def test_rate_limit(self) -> None:
        """Requests beyond the per-execution cap are rejected with 429."""
        with _make(
            granted_scopes=["*"], max_requests=2, http_client=_upstream_client([])
        ) as gw:
            codes = [
                _call(gw, "GET", "/api/v1/e", token=gw.gateway_token)[0]
                for _ in range(3)
            ]
            assert codes == [200, 200, 429]
            assert gw.request_count == 2
            assert gw.audit_log[-1]["reason"] == "rate_limit_exceeded"

    def test_payload_too_large(self) -> None:
        """A declared body over the cap is rejected with 413."""
        with _make(
            granted_scopes=["*"], max_body_bytes=8, http_client=_upstream_client([])
        ) as gw:
            status, _, body = _call(
                gw, "POST", "/api/v1/e", token=gw.gateway_token, body=b"x" * 64
            )
            assert status == 413 and json.loads(body)["error"] == "payload_too_large"

    @pytest.mark.parametrize("raw", ["abc", "-5"])
    def test_invalid_content_length(self, raw: str) -> None:
        """Garbage or negative Content-Length values are rejected with 400."""
        with _make(granted_scopes=["*"], http_client=_upstream_client([])) as gw:
            conn = http.client.HTTPConnection("127.0.0.1", gw.port, timeout=10)
            try:
                conn.putrequest("POST", "/api/v1/e")
                conn.putheader("Authorization", f"Bearer {gw.gateway_token}")
                conn.putheader("Content-Length", raw)
                conn.endheaders()
                resp = conn.getresponse()
                assert resp.status == 400
                assert json.loads(resp.read())["error"] == "invalid_content_length"
            finally:
                conn.close()

    def test_zero_content_length(self) -> None:
        """An explicit zero-length body is accepted and forwarded without content."""
        seen: list[httpx.Request] = []
        with _make(granted_scopes=["*"], http_client=_upstream_client(seen)) as gw:
            status, _, _ = _call(
                gw, "POST", "/api/v1/e", token=gw.gateway_token, body=b""
            )
            assert status == 200 and seen[0].content == b""

    def test_read_body_rejects_overlong_read(self) -> None:
        """A stream yielding more than the cap is rejected defensively."""
        gw = _make(max_body_bytes=4)
        handler = SimpleNamespace(
            headers={"Content-Length": "3"},
            rfile=SimpleNamespace(read=lambda n: b"x" * 10),
        )
        assert gw._read_request_body(handler) == (None, "payload_too_large")
        gw.stop()


class TestEgress:
    """The /_egress proxy route."""

    def _gw(self, seen: list[httpx.Request], **kw: Any) -> RestrictionGateway:
        """Build a gateway allowing api.example.com with a public resolver."""
        kw.setdefault("http_client", _upstream_client(seen))
        return _make(
            allowed_egress_domains=["api.example.com"],
            elder_api_token=fake_secret("api"),
            dns_resolver=lambda h, p: [PUBLIC_IP],
            **kw,
        )

    def test_header_url_proxied_without_elder_token(self) -> None:
        """Egress requests forward without the Elder credential."""
        seen: list[httpx.Request] = []
        with self._gw(seen) as gw:
            status, _, body = _call(
                gw,
                "POST",
                "/_egress",
                token=gw.gateway_token,
                headers={"X-Elder-Egress-Url": "https://api.example.com/v1/x"},
                body=b"hi",
            )
            assert status == 200 and json.loads(body) == {"ok": True}
            req = seen[0]
            assert str(req.url) == "https://api.example.com/v1/x"
            assert "authorization" not in req.headers
            assert "x-elder-egress-url" not in req.headers
            assert gw.audit_log[-1]["route_type"] == "egress"

    def test_query_url_proxied(self) -> None:
        """The target may be supplied via the url query parameter."""
        seen: list[httpx.Request] = []
        with self._gw(seen) as gw:
            status, _, _ = _call(
                gw,
                "GET",
                "/_egress/p?url=https://api.example.com/q",
                token=gw.gateway_token,
            )
            assert status == 200 and str(seen[0].url) == "https://api.example.com/q"

    def test_missing_url_forbidden(self) -> None:
        """No target URL yields a 403 with a reason."""
        seen: list[httpx.Request] = []
        with self._gw(seen) as gw:
            status, _, body = _call(gw, "GET", "/_egress", token=gw.gateway_token)
            assert status == 403
            assert json.loads(body)["reason"] == "missing_egress_url"
            assert seen == []

    def test_disallowed_domain_forbidden(self) -> None:
        """A non-allowlisted target is blocked and audited as rejected."""
        seen: list[httpx.Request] = []
        with self._gw(seen) as gw:
            status, _, body = _call(
                gw,
                "GET",
                "/_egress?url=https://evil.example.net/",
                token=gw.gateway_token,
            )
            assert status == 403
            assert json.loads(body)["error"] == "egress_forbidden"
            assert gw.audit_log[-1]["allowed"] is False

    def test_upstream_failure_is_502(self) -> None:
        """Egress network failures become 502."""
        with self._gw([], http_client=_failing_client()) as gw:
            status, _, body = _call(
                gw,
                "GET",
                "/_egress?url=https://api.example.com/",
                token=gw.gateway_token,
            )
            assert status == 502
            assert json.loads(body) == {"error": "egress_upstream_unreachable"}
