"""Async HTTP client wrapping Elder REST API calls for MCP tools."""

from __future__ import annotations

import json as _json
import logging
import os
import re
import time
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from apps.mcp.auth import (
        CallerCredentials,
        ElderSession,
        get_caller_credentials,
        resolve_elder_base_url,
    )
else:
    try:
        from apps.mcp.auth import (
            CallerCredentials,
            ElderSession,
            get_caller_credentials,
            resolve_elder_base_url,
        )
    except ImportError:
        from auth import (
            CallerCredentials,
            ElderSession,
            get_caller_credentials,
            resolve_elder_base_url,
        )


class _NoCookies(httpx.Cookies):
    """Cookie jar stub preventing cross-caller Set-Cookie persistence on pooled clients."""

    def extract_cookies(self, response: httpx.Response) -> None:
        return

    def set_cookie_header(self, request: httpx.Request) -> None:
        return


def create_pooled_http_client(
    base_url: str | None = None,
    *,
    timeout: float = 30.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """Create a stateless, cookie-free pooled ``httpx.AsyncClient`` with zero default auth."""
    kwargs: dict[str, Any] = {
        "base_url": resolve_elder_base_url(base_url),
        "timeout": timeout,
        "cookies": _NoCookies(),
        "headers": {},
    }
    if transport is not None:
        kwargs["transport"] = transport
    return httpx.AsyncClient(**kwargs)


_log = logging.getLogger("hub-mcp.client")

DEFAULT_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_SECRET_RE = re.compile(
    r"(?i)(bearer\s+|authorization[=:]\s*(?:bearer\s+)?|x-api-key[=:]\s*|"
    r"(?:token|api[_-]?key|secret|password|jwt)=)[^\s&\"',;]+"
)


def max_response_bytes() -> int:
    """Return the per-response upstream byte cap from ``MCP_MAX_RESPONSE_BYTES`` (default 4 MiB)."""
    raw = os.getenv("MCP_MAX_RESPONSE_BYTES", "").strip()
    try:
        value = int(raw) if raw else DEFAULT_MAX_RESPONSE_BYTES
    except ValueError:
        return DEFAULT_MAX_RESPONSE_BYTES
    return value if value > 0 else DEFAULT_MAX_RESPONSE_BYTES


def mask_secrets(text: str) -> str:
    """Redact bearer tokens, API keys and credential-like query values from log text."""
    return _SECRET_RE.sub(lambda m: f"{m.group(1)}****", text)


def public_error_message(status_code: int) -> str:
    """Return a short, upstream-body-free message for a status code (never internal detail)."""
    if status_code <= 0:
        return "Upstream request failed"
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return "Upstream request failed"


@dataclass(frozen=True)
class _CappedResponse:
    """Upstream response whose body was read with a hard byte cap."""

    status_code: int
    reason_phrase: str
    body: bytes
    too_large: bool


class ElderAPIError(Exception):
    """Raised when the Elder API returns an HTTP 4xx/5xx or transport failure."""

    def __init__(
        self,
        status_code: int,
        message: str,
        detail: Any = None,
    ) -> None:
        self.status_code = status_code
        self.message = message
        self.detail = detail
        super().__init__(f"Elder API error {status_code}: {message}")


class ElderResponseTooLargeError(ElderAPIError):
    """Raised when an upstream response body exceeds ``MCP_MAX_RESPONSE_BYTES``."""

    def __init__(self, limit: int) -> None:
        super().__init__(
            502,
            f"Upstream response exceeded the {limit}-byte limit and was not returned; "
            "narrow the request (filters, pagination) and retry",
        )


def _extract_error_detail(resp: _CappedResponse) -> tuple[str, Any]:
    text = resp.body.decode("utf-8", errors="replace")
    try:
        body = _json.loads(text)
    except ValueError:
        stripped = text.strip()
        return (stripped[:300] or resp.reason_phrase or "Request failed"), stripped
    if isinstance(body, dict):
        msg = str(
            body.get("message")
            or body.get("error")
            or body.get("detail")
            or resp.reason_phrase
            or "Request failed"
        )
        return msg, body
    return str(body), body


def _inject_otel_trace_headers(headers: dict[str, str]) -> None:
    """Best-effort W3C trace context propagation when OpenTelemetry is installed."""
    try:
        from opentelemetry.propagate import inject
    except ImportError:
        return
    if "traceparent" not in headers:
        carrier: dict[str, str] = {}
        inject(carrier)
        for key in ("traceparent", "tracestate"):
            if key in carrier and key not in headers:
                headers[key] = carrier[key]


def _record_upstream_latency(method: str, status_code: int, elapsed_ms: float) -> None:
    try:
        from opentelemetry import metrics
    except ImportError:
        return
    try:
        meter = metrics.get_meter("hub-mcp")
        hist = meter.create_histogram(
            name="mcp.upstream.duration_ms",
            unit="ms",
            description="Latency of upstream hub-api requests",
        )
        hist.record(elapsed_ms, {"method": method, "status_code": status_code})
    except Exception:
        return


class ElderClient:
    """HTTP client authenticated with Bearer JWT or ``X-API-Key``."""

    def __init__(
        self,
        session: ElderSession | None = None,
        base_url: str | None = None,
        access_token: str | None = None,
        api_key: str | None = None,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
        http_client: httpx.AsyncClient | None = None,
        caller_credentials: CallerCredentials | None = None,
    ) -> None:
        if session is not None:
            self.session = session
        elif base_url is not None:
            self.session = ElderSession(
                base_url=base_url,
                access_token=access_token,
                api_key=api_key,
                transport=transport,
                timeout=timeout,
            )
        else:
            self.session = ElderSession.from_env(transport=transport)
            if access_token is not None:
                self.session.access_token = access_token
            if api_key is not None:
                self.session.api_key = api_key

        self._timeout = timeout
        self._transport = transport or self.session._transport
        self._client: httpx.AsyncClient | None = http_client
        self._owns_client = http_client is None
        self._caller_credentials = caller_credentials

    def __repr__(self) -> str:
        return f"ElderClient(session={self.session!r}, timeout={self._timeout!r})"

    __str__ = __repr__

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = create_pooled_http_client(
                self.session.base_url,
                timeout=self._timeout,
                transport=self._transport,
            )
            self._owns_client = True
        return self._client

    async def __aenter__(self) -> ElderClient:
        self._ensure_client()
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the owned ``httpx.AsyncClient`` if open (pooled clients remain open)."""
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    def _build_request_headers(self, auth_headers: dict[str, str]) -> dict[str, str]:
        headers: dict[str, str] = {}
        creds = self._caller_credentials or get_caller_credentials()
        if creds is not None:
            headers.update(creds.propagation_headers())
        _inject_otel_trace_headers(headers)
        headers.update(auth_headers)
        return headers

    async def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
    ) -> Any:
        """Execute an authenticated Elder API request and parse JSON response."""
        client = self._ensure_client()
        cleaned_params = (
            {k: v for k, v in params.items() if v is not None} if params else None
        )
        auth_headers = await self.session.auth_headers()
        headers = self._build_request_headers(auth_headers)

        started = time.perf_counter()
        limit = max_response_bytes()
        try:
            resp = await self._fetch_capped(
                client, method, path, cleaned_params, json, headers, limit
            )
        except httpx.HTTPError as exc:
            _record_upstream_latency(
                method, 0, (time.perf_counter() - started) * 1000.0
            )
            _log.warning(
                "mcp_upstream_transport_error path=%s error=%s",
                path,
                mask_secrets(f"{type(exc).__name__}: {exc}"),
            )
            raise ElderAPIError(0, public_error_message(0)) from None

        if resp.status_code == 401:
            refreshed_headers = await self.session.handle_unauthorized()
            if refreshed_headers is not None:
                retry_headers = self._build_request_headers(refreshed_headers)
                try:
                    resp = await self._fetch_capped(
                        client,
                        method,
                        path,
                        cleaned_params,
                        json,
                        retry_headers,
                        limit,
                    )
                except httpx.HTTPError as exc:
                    _record_upstream_latency(
                        method, 0, (time.perf_counter() - started) * 1000.0
                    )
                    _log.warning(
                        "mcp_upstream_transport_error path=%s error=%s",
                        path,
                        mask_secrets(f"{type(exc).__name__}: {exc}"),
                    )
                    raise ElderAPIError(0, public_error_message(0)) from None

        _record_upstream_latency(
            method, resp.status_code, (time.perf_counter() - started) * 1000.0
        )

        if resp.too_large:
            _log.warning(
                "mcp_upstream_response_too_large path=%s status=%d limit=%d",
                path,
                resp.status_code,
                limit,
            )
            raise ElderResponseTooLargeError(limit)

        if resp.status_code >= 400:
            msg, detail = _extract_error_detail(resp)
            _log.warning(
                "mcp_upstream_error path=%s status=%d detail=%s",
                path,
                resp.status_code,
                mask_secrets(msg)[:300],
            )
            raise ElderAPIError(resp.status_code, msg, detail=detail)

        if resp.status_code == 204 or not resp.body:
            return {"status": "ok", "status_code": resp.status_code}

        text = resp.body.decode("utf-8", errors="replace")
        try:
            return _json.loads(text)
        except ValueError:
            return {"raw": text}

    @staticmethod
    async def _fetch_capped(
        client: httpx.AsyncClient,
        method: str,
        path: str,
        params: dict[str, Any] | None,
        json_body: Any | None,
        headers: dict[str, str],
        limit: int,
    ) -> _CappedResponse:
        """Stream an upstream response, stopping once ``limit`` decoded bytes are exceeded."""
        async with client.stream(
            method, path, params=params, json=json_body, headers=headers
        ) as resp:
            declared = resp.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > limit:
                return _CappedResponse(
                    resp.status_code, resp.reason_phrase, b"", too_large=True
                )
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > limit:
                    return _CappedResponse(
                        resp.status_code, resp.reason_phrase, b"", too_large=True
                    )
                chunks.append(chunk)
            return _CappedResponse(
                resp.status_code, resp.reason_phrase, b"".join(chunks), too_large=False
            )

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Send an authenticated GET request."""
        return await self.request("GET", path, params=params)

    async def post(
        self,
        path: str,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Send an authenticated POST request."""
        return await self.request("POST", path, params=params, json=json)

    async def patch(
        self,
        path: str,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Send an authenticated PATCH request."""
        return await self.request("PATCH", path, params=params, json=json)

    async def put(
        self,
        path: str,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Send an authenticated PUT request."""
        return await self.request("PUT", path, params=params, json=json)

    async def delete(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
    ) -> Any:
        """Send an authenticated DELETE request."""
        return await self.request("DELETE", path, params=params, json=json)
