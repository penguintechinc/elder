"""Ephemeral per-execution Tool & Network Restriction Gateway for CodeRuns.

Runs inside ``svc-runworker`` bound strictly to ``127.0.0.1:0`` (OS-assigned
ephemeral port) for the duration of a single execution. Untrusted CodeRun
subprocesses receive only a random per-execution ``gateway_token`` and talk to
this local HTTP proxy — the real ``elder_api_token`` NEVER enters the CodeRun
process environment.

Defense-in-depth protections enforced on every request:
- Constant-time ``Authorization: Bearer <gateway_token>`` verification.
- Per-execution request cap (default 60) and body size cap (2 MiB).
- OIDC scope mapping ``(method, /api/v1/<resource>...) -> <resource>:<action>``
  enforced locally against ``granted_scopes`` before forwarding to ``hub-api``.
- Optional ``/_egress`` proxy strictly restricted to ``allowed_egress_domains``
  with full SSRF protection (rejects IP literals, loopback, RFC1918,
  link-local ``169.254.0.0/16``, private/reserved DNS resolutions, and
  redirects).
- Structured audit log entry per gateway call (never logging tokens).
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import re
import secrets
import socket
import threading
import time
from collections.abc import Callable, Collection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import httpx

logger = logging.getLogger("apps.runworker.gateway")

DEFAULT_MAX_REQUESTS = 60
MAX_BODY_BYTES = 2 * 1024 * 1024  # 2 MiB
MAX_RESPONSE_BYTES = 4 * 1024 * 1024  # 4 MiB
DEFAULT_UPSTREAM_TIMEOUT = 25.0

_RESOURCE_SEGMENT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_HOP_BY_HOP_HEADERS = frozenset(
    {
        "authorization",
        "connection",
        "content-length",
        "host",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "x-elder-egress-url",
    }
)
_SAFE_RESPONSE_HEADERS = frozenset(
    {
        "content-type",
        "cache-control",
        "etag",
        "last-modified",
        "x-request-id",
        "x-ratelimit-limit",
        "x-ratelimit-remaining",
        "x-ratelimit-reset",
    }
)
_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH"})
_DELETE_METHODS = frozenset({"DELETE"})
_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")


def map_request_to_scope(method: str, path: str) -> str | None:
    """Map ``(HTTP method, /api/v1/<resource>...)`` to a required OIDC scope.

    Returns ``None`` if the path is malformed, contains traversal segments, or
    does not target ``/api/v1/<resource>``.
    """
    raw_path = unquote(urlsplit(path or "").path)
    if not raw_path.startswith("/api/v1/"):
        return None
    if "\\" in raw_path or "\x00" in raw_path:
        return None

    segments = [seg for seg in raw_path.split("/") if seg]
    if any(seg in (".", "..") for seg in segments):
        return None
    if len(segments) < 3 or segments[0] != "api" or segments[1] != "v1":
        return None

    resource = segments[2].lower()
    if not _RESOURCE_SEGMENT_RE.match(resource):
        return None

    upper_method = (method or "").upper()
    if upper_method in _READ_METHODS:
        action = "read"
    elif upper_method in _WRITE_METHODS:
        action = "write"
    elif upper_method in _DELETE_METHODS:
        action = "delete"
    else:
        return None

    return f"{resource}:{action}"


def has_required_scope(required_scope: str, granted_scopes: Collection[str]) -> bool:
    """Return True if ``granted_scopes`` satisfies ``required_scope``."""
    if not required_scope or not granted_scopes:
        return False

    normalized_required = required_scope.strip().lower()
    if ":" not in normalized_required:
        return False
    req_resource, req_action = normalized_required.split(":", 1)

    for raw in granted_scopes:
        scope = (raw or "").strip().lower()
        if not scope:
            continue
        if scope in ("*", "*:*", normalized_required):
            return True
        if ":" not in scope:
            continue
        g_resource, g_action = scope.split(":", 1)
        if g_resource == "*" and g_action in ("*", "admin", req_action):
            return True
        if g_resource == req_resource and g_action in ("*", "admin", req_action):
            return True
    return False


def _is_ip_literal(hostname: str) -> bool:
    """Return True if ``hostname`` parses as an IPv4 or IPv6 literal."""
    cleaned = hostname.strip("[]")
    try:
        ipaddress.ip_address(cleaned)
        return True
    except ValueError:
        return False


def _is_blocked_ip(ip_str: str) -> bool:
    """Return True if an IP address is private, loopback, link-local, or non-global."""
    try:
        addr = ipaddress.ip_address(ip_str.split("%", 1)[0])
    except ValueError:
        return True

    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped

    if (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
        or not addr.is_global
    ):
        return True

    if isinstance(addr, ipaddress.IPv4Address) and addr in _CGNAT_NETWORK:
        return True

    return False


def _domain_matches_allowlist(hostname: str, allowed_domains: Collection[str]) -> bool:
    """Return True if ``hostname`` matches an entry in ``allowed_domains``."""
    host = hostname.strip().lower().rstrip(".")
    if not host or host == "localhost" or host.endswith(".localhost"):
        return False
    for raw in allowed_domains:
        pattern = (raw or "").strip().lower().rstrip(".")
        if not pattern:
            continue
        if pattern.startswith("*."):
            suffix = pattern[1:]  # ".example.com"
            if host.endswith(suffix) and len(host) > len(suffix):
                return True
        elif host == pattern:
            return True
    return False


def validate_egress_url(
    target_url: str,
    allowed_domains: Collection[str],
    *,
    dns_resolver: Callable[[str, int], list[str]] | None = None,
) -> tuple[bool, str]:
    """Validate an outbound ``/_egress`` URL against domain allowlist and SSRF rules.

    Returns ``(True, normalized_url)`` on success, or ``(False, reason)`` on rejection.
    """
    if not allowed_domains:
        return False, "egress_not_allowed"

    raw = (target_url or "").strip()
    if not raw:
        return False, "missing_egress_url"

    parsed = urlsplit(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        return False, "unsupported_scheme"

    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    if not hostname:
        return False, "missing_hostname"

    if _is_ip_literal(hostname):
        return False, "ip_literal_blocked"

    if hostname == "localhost" or hostname.endswith(".localhost"):
        return False, "loopback_hostname_blocked"

    if not _domain_matches_allowlist(hostname, allowed_domains):
        return False, "domain_not_allowlisted"

    try:
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError:
        return False, "invalid_port"

    if dns_resolver is not None:
        resolved_ips = dns_resolver(hostname, port)
    else:
        try:
            infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
            resolved_ips = [str(info[4][0]) for info in infos]
        except OSError:
            return False, "dns_resolution_failed"

    if not resolved_ips:
        return False, "dns_no_records"

    for ip_str in resolved_ips:
        if _is_blocked_ip(ip_str):
            return False, "blocked_ip_address"

    return True, raw


class RestrictionGateway:
    """Ephemeral per-execution HTTP proxy enforcing auth, scopes, caps, and SSRF rules."""

    def __init__(
        self,
        *,
        elder_api_url: str,
        elder_api_token: str = "",
        granted_scopes: Collection[str] = (),
        allowed_egress_domains: Collection[str] = (),
        execution_id: str = "",
        max_requests: int = DEFAULT_MAX_REQUESTS,
        max_body_bytes: int = MAX_BODY_BYTES,
        upstream_timeout: float = DEFAULT_UPSTREAM_TIMEOUT,
        http_client: httpx.Client | None = None,
        dns_resolver: Callable[[str, int], list[str]] | None = None,
    ) -> None:
        self.elder_api_url = (elder_api_url or "http://hub-api:5000").rstrip("/")
        self._elder_api_token = elder_api_token or ""
        self.granted_scopes: tuple[str, ...] = tuple(
            s.strip() for s in granted_scopes if s and s.strip()
        )
        self.allowed_egress_domains: tuple[str, ...] = tuple(
            d.strip().lower().rstrip(".")
            for d in allowed_egress_domains
            if d and d.strip()
        )
        self.execution_id = execution_id
        self.max_requests = max(1, int(max_requests))
        self.max_body_bytes = max(1, int(max_body_bytes))
        self.upstream_timeout = max(1.0, float(upstream_timeout))
        self.gateway_token: str = secrets.token_urlsafe(32)
        self._dns_resolver = dns_resolver

        self._external_client = http_client is not None
        self._client = http_client or httpx.Client(
            timeout=self.upstream_timeout,
            follow_redirects=False,
        )
        self._lock = threading.Lock()
        self._request_count = 0
        self.audit_log: list[dict[str, Any]] = []

        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._port: int = 0

    def __repr__(self) -> str:
        return (
            f"RestrictionGateway(execution_id={self.execution_id!r}, "
            f"port={self._port}, scopes={len(self.granted_scopes)}, "
            f"egress_domains={len(self.allowed_egress_domains)})"
        )

    @property
    def port(self) -> int:
        """Bound ephemeral localhost port (0 before ``start()``)."""
        return self._port

    @property
    def url(self) -> str:
        """Base URL of the local gateway (``http://127.0.0.1:<port>``)."""
        if not self._port:
            raise RuntimeError("RestrictionGateway has not been started")
        return f"http://127.0.0.1:{self._port}"

    @property
    def request_count(self) -> int:
        """Number of authenticated requests processed so far."""
        with self._lock:
            return self._request_count

    def start(self) -> RestrictionGateway:
        """Bind to ``127.0.0.1:0`` and start serving in a background daemon thread."""
        if self._server is not None:
            return self

        gateway = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                # Suppress default stderr HTTP access logs; structured audit logs
                # are emitted explicitly without token exposure.
                return

            def _dispatch(self) -> None:
                gateway._handle_http_request(self)

            do_GET = _dispatch
            do_POST = _dispatch
            do_PUT = _dispatch
            do_PATCH = _dispatch
            do_DELETE = _dispatch
            do_HEAD = _dispatch
            do_OPTIONS = _dispatch

        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        server.daemon_threads = True
        self._server = server
        self._port = int(server.server_address[1])
        self._thread = threading.Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": 0.1},
            name=f"elder-gw-{self.execution_id[:12] or 'run'}",
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        """Shut down the local HTTP proxy and close owned HTTP client."""
        server = self._server
        self._server = None
        if server is not None:
            try:
                server.shutdown()
            finally:
                server.server_close()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)
        if not self._external_client:
            self._client.close()

    def __enter__(self) -> RestrictionGateway:
        return self.start()

    def __exit__(self, *args: object) -> None:
        self.stop()

    def _record_audit(
        self,
        *,
        method: str,
        path: str,
        route_type: str,
        required_scope: str | None,
        status_code: int,
        allowed: bool,
        reason: str,
        started: float,
        request_index: int,
    ) -> None:
        duration_ms = int((time.monotonic() - started) * 1000)
        sanitized_path = urlsplit(path or "").path
        entry: dict[str, Any] = {
            "execution_id": self.execution_id,
            "method": method,
            "path": sanitized_path,
            "route_type": route_type,
            "required_scope": required_scope,
            "status_code": status_code,
            "allowed": allowed,
            "reason": reason,
            "duration_ms": duration_ms,
            "request_index": request_index,
        }
        with self._lock:
            self.audit_log.append(entry)
        logger.info("runworker_gateway_request", extra=entry)

    def _send_json(
        self,
        handler: BaseHTTPRequestHandler,
        status_code: int,
        payload: dict[str, Any],
    ) -> None:
        body = json.dumps(payload).encode("utf-8")
        handler.send_response(status_code)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Connection", "close")
        handler.end_headers()
        if handler.command != "HEAD":
            handler.wfile.write(body)

    def _verify_auth(self, handler: BaseHTTPRequestHandler) -> bool:
        auth_header = handler.headers.get("Authorization") or ""
        if not auth_header.startswith("Bearer "):
            return False
        supplied = auth_header[7:].strip()
        if not supplied:
            return False
        return hmac.compare_digest(supplied, self.gateway_token)

    def _read_request_body(
        self,
        handler: BaseHTTPRequestHandler,
    ) -> tuple[bytes | None, str | None]:
        raw_len = handler.headers.get("Content-Length")
        if raw_len is None:
            return b"", None
        try:
            content_length = int(raw_len)
        except ValueError:
            return None, "invalid_content_length"
        if content_length < 0:
            return None, "invalid_content_length"
        if content_length > self.max_body_bytes:
            return None, "payload_too_large"
        if content_length == 0:
            return b"", None
        body = handler.rfile.read(content_length)
        if len(body) > self.max_body_bytes:
            return None, "payload_too_large"
        return body, None

    def _handle_http_request(self, handler: BaseHTTPRequestHandler) -> None:
        started = time.monotonic()
        method = handler.command.upper()
        raw_path = handler.path

        if not self._verify_auth(handler):
            self._record_audit(
                method=method,
                path=raw_path,
                route_type="rejected",
                required_scope=None,
                status_code=401,
                allowed=False,
                reason="unauthorized",
                started=started,
                request_index=self.request_count,
            )
            self._send_json(handler, 401, {"error": "unauthorized"})
            return

        with self._lock:
            if self._request_count >= self.max_requests:
                req_idx = self._request_count
                over_limit = True
            else:
                self._request_count += 1
                req_idx = self._request_count
                over_limit = False

        if over_limit:
            self._record_audit(
                method=method,
                path=raw_path,
                route_type="rejected",
                required_scope=None,
                status_code=429,
                allowed=False,
                reason="rate_limit_exceeded",
                started=started,
                request_index=req_idx,
            )
            self._send_json(
                handler,
                429,
                {
                    "error": "rate_limit_exceeded",
                    "max_requests": self.max_requests,
                },
            )
            return

        body, body_err = self._read_request_body(handler)
        if body_err is not None or body is None:
            status = 413 if body_err == "payload_too_large" else 400
            self._record_audit(
                method=method,
                path=raw_path,
                route_type="rejected",
                required_scope=None,
                status_code=status,
                allowed=False,
                reason=body_err or "invalid_body",
                started=started,
                request_index=req_idx,
            )
            self._send_json(handler, status, {"error": body_err or "invalid_body"})
            return

        parsed = urlsplit(raw_path)
        if parsed.path == "/_egress" or parsed.path.startswith("/_egress/"):
            self._handle_egress(handler, method, raw_path, body, started, req_idx)
            return

        self._handle_elder_api(handler, method, raw_path, body, started, req_idx)

    def _forward_headers(
        self,
        handler: BaseHTTPRequestHandler,
        *,
        include_elder_token: bool,
    ) -> dict[str, str]:
        headers: dict[str, str] = {}
        for key, value in handler.headers.items():
            if key.lower() in _HOP_BY_HOP_HEADERS:
                continue
            headers[key] = value
        if include_elder_token and self._elder_api_token:
            headers["Authorization"] = f"Bearer {self._elder_api_token}"
        return headers

    def _relay_response(
        self,
        handler: BaseHTTPRequestHandler,
        upstream: httpx.Response,
    ) -> None:
        resp_bytes = upstream.content
        if len(resp_bytes) > MAX_RESPONSE_BYTES:
            self._send_json(handler, 502, {"error": "upstream_response_too_large"})
            return

        handler.send_response(upstream.status_code)
        for key, value in upstream.headers.items():
            if key.lower() in _SAFE_RESPONSE_HEADERS:
                handler.send_header(key, value)
        handler.send_header("Content-Length", str(len(resp_bytes)))
        handler.send_header("Connection", "close")
        handler.end_headers()
        if handler.command != "HEAD" and resp_bytes:
            handler.wfile.write(resp_bytes)

    def _handle_elder_api(
        self,
        handler: BaseHTTPRequestHandler,
        method: str,
        raw_path: str,
        body: bytes,
        started: float,
        req_idx: int,
    ) -> None:
        required_scope = map_request_to_scope(method, raw_path)
        if required_scope is None:
            self._record_audit(
                method=method,
                path=raw_path,
                route_type="elder_api",
                required_scope=None,
                status_code=403,
                allowed=False,
                reason="path_not_allowed",
                started=started,
                request_index=req_idx,
            )
            self._send_json(handler, 403, {"error": "path_not_allowed"})
            return

        if not has_required_scope(required_scope, self.granted_scopes):
            self._record_audit(
                method=method,
                path=raw_path,
                route_type="elder_api",
                required_scope=required_scope,
                status_code=403,
                allowed=False,
                reason="scope_not_granted",
                started=started,
                request_index=req_idx,
            )
            self._send_json(
                handler,
                403,
                {
                    "error": "forbidden",
                    "required_scope": required_scope,
                },
            )
            return

        target_url = f"{self.elder_api_url}{raw_path}"
        fwd_headers = self._forward_headers(handler, include_elder_token=True)
        try:
            upstream = self._client.request(
                method=method,
                url=target_url,
                headers=fwd_headers,
                content=body if body else None,
            )
        except httpx.HTTPError:
            self._record_audit(
                method=method,
                path=raw_path,
                route_type="elder_api",
                required_scope=required_scope,
                status_code=502,
                allowed=True,
                reason="upstream_error",
                started=started,
                request_index=req_idx,
            )
            self._send_json(handler, 502, {"error": "upstream_unreachable"})
            return

        self._record_audit(
            method=method,
            path=raw_path,
            route_type="elder_api",
            required_scope=required_scope,
            status_code=upstream.status_code,
            allowed=True,
            reason="ok",
            started=started,
            request_index=req_idx,
        )
        self._relay_response(handler, upstream)

    def _handle_egress(
        self,
        handler: BaseHTTPRequestHandler,
        method: str,
        raw_path: str,
        body: bytes,
        started: float,
        req_idx: int,
    ) -> None:
        target_url = (handler.headers.get("X-Elder-Egress-Url") or "").strip()
        if not target_url:
            query = parse_qs(urlsplit(raw_path).query)
            urls = query.get("url") or []
            target_url = urls[0].strip() if urls else ""

        ok, detail = validate_egress_url(
            target_url,
            self.allowed_egress_domains,
            dns_resolver=self._dns_resolver,
        )
        if not ok:
            self._record_audit(
                method=method,
                path=raw_path,
                route_type="egress",
                required_scope=None,
                status_code=403,
                allowed=False,
                reason=detail,
                started=started,
                request_index=req_idx,
            )
            self._send_json(
                handler, 403, {"error": "egress_forbidden", "reason": detail}
            )
            return

        fwd_headers = self._forward_headers(handler, include_elder_token=False)
        try:
            upstream = self._client.request(
                method=method,
                url=detail,
                headers=fwd_headers,
                content=body if body else None,
            )
        except httpx.HTTPError:
            self._record_audit(
                method=method,
                path=raw_path,
                route_type="egress",
                required_scope=None,
                status_code=502,
                allowed=True,
                reason="egress_upstream_error",
                started=started,
                request_index=req_idx,
            )
            self._send_json(handler, 502, {"error": "egress_upstream_unreachable"})
            return

        self._record_audit(
            method=method,
            path=raw_path,
            route_type="egress",
            required_scope=None,
            status_code=upstream.status_code,
            allowed=True,
            reason="ok",
            started=started,
            request_index=req_idx,
        )
        self._relay_response(handler, upstream)
