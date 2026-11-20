"""Async HTTP client wrapping Elder REST API calls for MCP tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from apps.mcp.auth import ElderSession
else:
    try:
        from apps.mcp.auth import ElderSession
    except ImportError:
        from auth import ElderSession


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
        self._client: httpx.AsyncClient | None = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            kwargs: dict[str, Any] = {
                "base_url": self.session.base_url,
                "timeout": self._timeout,
            }
            if self._transport is not None:
                kwargs["transport"] = self._transport
            self._client = httpx.AsyncClient(**kwargs)
        return self._client

    async def __aenter__(self) -> ElderClient:
        self._ensure_client()
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the underlying ``httpx.AsyncClient`` if open."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        """Execute an authenticated Elder API request and parse JSON response."""
        client = self._ensure_client()
        cleaned_params = (
            {k: v for k, v in params.items() if v is not None} if params else None
        )
        headers = await self.session.auth_headers()
        try:
            resp = await client.request(
                method,
                path,
                params=cleaned_params,
                json=json,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise ElderAPIError(0, f"Transport error calling {path}: {exc}") from exc

        if resp.status_code == 401:
            refreshed_headers = await self.session.handle_unauthorized()
            if refreshed_headers is not None:
                try:
                    resp = await client.request(
                        method,
                        path,
                        params=cleaned_params,
                        json=json,
                        headers=refreshed_headers,
                    )
                except httpx.HTTPError as exc:
                    raise ElderAPIError(
                        0, f"Transport error calling {path}: {exc}"
                    ) from exc

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
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Send an authenticated POST request."""
        return await self.request("POST", path, params=params, json=json)

    async def patch(
        self,
        path: str,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Send an authenticated PATCH request."""
        return await self.request("PATCH", path, params=params, json=json)

    async def put(
        self,
        path: str,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Send an authenticated PUT request."""
        return await self.request("PUT", path, params=params, json=json)

    async def delete(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Send an authenticated DELETE request."""
        return await self.request("DELETE", path, params=params)
