"""Authentication session management for the Elder MCP client."""

from __future__ import annotations

import base64
import json
import os
import time
from typing import Any

import httpx


class ElderAuthError(Exception):
    """Raised when Elder MCP authentication fails or is misconfigured."""


def _decode_jwt_exp(token: str) -> float | None:
    """Extract the numeric ``exp`` claim from a JWT without verifying signature."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload_b64 = parts[1]
    padding = "=" * (-len(payload_b64) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload_b64 + padding)
        data = json.loads(decoded.decode("utf-8"))
    except (ValueError, TypeError, UnicodeDecodeError):
        return None
    exp = data.get("exp")
    if isinstance(exp, int | float) and not isinstance(exp, bool):
        return float(exp)
    return None


class ElderSession:
    """Manages Bearer JWT or X-API-Key credentials against the Elder API."""

    def __init__(
        self,
        base_url: str,
        username: str | None = None,
        password: str | None = None,
        access_token: str | None = None,
        refresh_token: str | None = None,
        api_key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.api_key = api_key
        self._transport = transport
        self._timeout = timeout
        self._token_expires_at: float | None = (
            _decode_jwt_exp(access_token) if access_token else None
        )

    @classmethod
    def from_api_key(
        cls,
        base_url: str,
        api_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 15.0,
    ) -> ElderSession:
        """Construct a session authenticating exclusively via ``X-API-Key``."""
        if not api_key or not api_key.strip():
            raise ElderAuthError("API key must not be empty")
        return cls(
            base_url=base_url,
            api_key=api_key.strip(),
            transport=transport,
            timeout=timeout,
        )

    @classmethod
    def from_env(
        cls,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> ElderSession:
        """Construct a session from ``ELDER_*`` environment variables."""
        base_url = os.environ.get("ELDER_URL", "http://localhost:5000")
        api_key = os.environ.get("ELDER_API_KEY") or None
        token = os.environ.get("ELDER_API_TOKEN") or None
        username = os.environ.get("ELDER_USERNAME") or None
        password = os.environ.get("ELDER_PASSWORD") or None
        return cls(
            base_url=base_url,
            username=username,
            password=password,
            access_token=token,
            api_key=api_key,
            transport=transport,
        )

    def _is_token_expiring_soon(self, leeway_seconds: float = 30.0) -> bool:
        if self._token_expires_at is None:
            return False
        return time.time() >= (self._token_expires_at - leeway_seconds)

    def _make_http_client(self) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {
            "base_url": self.base_url,
            "timeout": self._timeout,
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    async def login(self) -> str:
        """Authenticate using username/password and store issued tokens."""
        if not self.username or not self.password:
            raise ElderAuthError(
                "ELDER_USERNAME and ELDER_PASSWORD are required for password login"
            )
        async with self._make_http_client() as client:
            resp = await client.post(
                "/api/v1/auth/login",
                json={"username": self.username, "password": self.password},
            )
        if resp.status_code >= 400:
            raise ElderAuthError(
                f"Elder login failed with HTTP {resp.status_code}: {resp.text[:300]}"
            )
        data = resp.json()
        token = data.get("access_token")
        if not isinstance(token, str) or not token:
            raise ElderAuthError("Elder login response did not include access_token")
        self.access_token = token
        refresh = data.get("refresh_token")
        self.refresh_token = refresh if isinstance(refresh, str) and refresh else None
        self._token_expires_at = _decode_jwt_exp(token)
        return token

    async def refresh(self) -> str:
        """Refresh the access token using ``refresh_token`` or fall back to login."""
        if not self.refresh_token:
            if self.username and self.password:
                return await self.login()
            raise ElderAuthError(
                "Cannot refresh token without refresh_token or credentials"
            )

        async with self._make_http_client() as client:
            resp = await client.post(
                "/api/v1/auth/refresh",
                headers={"Authorization": f"Bearer {self.refresh_token}"},
            )
        if resp.status_code >= 400:
            if self.username and self.password:
                return await self.login()
            raise ElderAuthError(
                f"Elder token refresh failed with HTTP {resp.status_code}: {resp.text[:300]}"
            )
        data = resp.json()
        token = data.get("access_token")
        if not isinstance(token, str) or not token:
            raise ElderAuthError("Elder refresh response did not include access_token")
        self.access_token = token
        self._token_expires_at = _decode_jwt_exp(token)
        return token

    async def get_token(self) -> str:
        """Return a valid Bearer access token, refreshing or logging in if needed."""
        if self.access_token:
            if self._is_token_expiring_soon():
                if self.refresh_token or (self.username and self.password):
                    return await self.refresh()
                raise ElderAuthError("Static ELDER_API_TOKEN is expired")
            return self.access_token
        return await self.login()

    async def auth_headers(self) -> dict[str, str]:
        """Return HTTP auth headers (Bearer JWT preferred when set, else X-API-Key)."""
        if self.access_token:
            token = await self.get_token()
            return {"Authorization": f"Bearer {token}"}
        if self.api_key:
            return {"X-API-Key": self.api_key}
        if self.username and self.password:
            token = await self.login()
            return {"Authorization": f"Bearer {token}"}
        raise ElderAuthError(
            "No Elder credentials configured: set ELDER_API_TOKEN, ELDER_API_KEY, "
            "or ELDER_USERNAME and ELDER_PASSWORD"
        )

    async def handle_unauthorized(self) -> dict[str, str] | None:
        """Attempt recovery after HTTP 401 when refresh or password credentials exist."""
        if self.refresh_token or (self.username and self.password):
            token = await self.refresh()
            return {"Authorization": f"Bearer {token}"}
        return None
