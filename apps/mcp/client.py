"""Async HTTP client wrapping Elder REST API calls for MCP tools."""

from __future__ import annotations

import time
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


def _extract_error_detail(resp: httpx.Response) -> tuple[str, Any]:
    try:
        body = resp.json()
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
    except ValueError:
        text = resp.text.strip()
        return (text[:300] or resp.reason_phrase or "Request failed"), text


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
        try:
            resp = await client.request(
                method,
                path,
                params=cleaned_params,
                json=json,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            _record_upstream_latency(
                method, 0, (time.perf_counter() - started) * 1000.0
            )
            raise ElderAPIError(0, f"Transport error calling {path}: {exc}") from exc

        if resp.status_code == 401:
            refreshed_headers = await self.session.handle_unauthorized()
            if refreshed_headers is not None:
                retry_headers = self._build_request_headers(refreshed_headers)
                try:
                    resp = await client.request(
                        method,
                        path,
                        params=cleaned_params,
                        json=json,
                        headers=retry_headers,
                    )
                except httpx.HTTPError as exc:
                    _record_upstream_latency(
                        method, 0, (time.perf_counter() - started) * 1000.0
                    )
                    raise ElderAPIError(
                        0, f"Transport error calling {path}: {exc}"
                    ) from exc

        _record_upstream_latency(
            method, resp.status_code, (time.perf_counter() - started) * 1000.0
        )

        if resp.status_code >= 400:
            msg, detail = _extract_error_detail(resp)
            raise ElderAPIError(resp.status_code, msg, detail=detail)

        if resp.status_code == 204 or not resp.content:
            return {"status": "ok", "status_code": resp.status_code}

        try:
            return resp.json()
        except ValueError:
            return {"raw": resp.text}

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
