"""Authentication helpers and secret-safe configuration for Elder SDK."""

from __future__ import annotations

import base64
import json
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import httpx

from elder_sdk.exceptions import ElderAuthError, raise_for_status

DEFAULT_API_URL = "http://localhost:5000"


def mask_secret(value: str | None) -> str:
    """Mask sensitive tokens/keys so secrets never leak to stdout or logs."""
    if not value:
        return "<unset>"
    cleaned = value.strip()
    if len(cleaned) <= 8:
        return "****"
    return f"{cleaned[:4]}****{cleaned[-4:]}"


def normalize_base_url(base_url: str) -> str:
    """Normalize a base Elder API URL, stripping trailing slashes and ``/api/v1``."""
    cleaned = base_url.strip().rstrip("/")
    if not cleaned:
        raise ValueError("base_url must not be empty")
    if cleaned.endswith("/api/v1"):
        cleaned = cleaned[: -len("/api/v1")]
    return cleaned


def decode_jwt_exp(token: str) -> float | None:
    """Extract numeric ``exp`` claim from a JWT payload without verifying signature."""
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
    if not isinstance(data, dict):
        return None
    exp = data.get("exp")
    if isinstance(exp, int | float) and not isinstance(exp, bool):
        return float(exp)
    return None


@runtime_checkable
class AuthProvider(Protocol):
    """Protocol implemented by SDK authentication handlers."""

    def apply_headers(self, headers: dict[str, str]) -> dict[str, str]:
        """Return headers updated with authentication material."""


class BearerAuth:
    """Bearer JWT authentication handler with strict secret masking."""

    def __init__(
        self,
        token: str,
        *,
        refresh_token: str | None = None,
    ) -> None:
        if not token or not token.strip():
            raise ElderAuthError("Bearer token must not be empty")
        self._token = token.strip()
        self._refresh_token = (
            refresh_token.strip() if refresh_token and refresh_token.strip() else None
        )
        self._expires_at = decode_jwt_exp(self._token)

    @property
    def token(self) -> str:
        """Return the active bearer token."""
        return self._token

    @property
    def refresh_token(self) -> str | None:
        """Return the optional refresh token."""
        return self._refresh_token

    @property
    def expires_at(self) -> float | None:
        """Return decoded JWT expiration timestamp in epoch seconds, if present."""
        return self._expires_at

    def is_expired(self, leeway_seconds: float = 30.0) -> bool:
        """Return True when the JWT has an ``exp`` claim within ``leeway_seconds``."""
        if self._expires_at is None:
            return False
        return time.time() >= (self._expires_at - leeway_seconds)

    def update_tokens(
        self,
        access_token: str,
        *,
        refresh_token: str | None = None,
    ) -> None:
        """Update stored access (and optionally refresh) token."""
        if not access_token or not access_token.strip():
            raise ElderAuthError("Access token must not be empty")
        self._token = access_token.strip()
        self._expires_at = decode_jwt_exp(self._token)
        if refresh_token is not None:
            stripped = refresh_token.strip()
            self._refresh_token = stripped or None

    def apply_headers(self, headers: dict[str, str]) -> dict[str, str]:
        """Inject ``Authorization: Bearer ...`` into ``headers``."""
        updated = dict(headers)
        updated["Authorization"] = f"Bearer {self._token}"
        return updated

    def __repr__(self) -> str:
        return (
            f"BearerAuth(token={mask_secret(self._token)!r}, "
            f"has_refresh={self._refresh_token is not None})"
        )

    def __str__(self) -> str:
        return repr(self)


class ApiKeyAuth:
    """API Key authentication handler (``X-API-Key``) with strict secret masking."""

    def __init__(
        self,
        api_key: str,
        *,
        header_name: str = "X-API-Key",
    ) -> None:
        if not api_key or not api_key.strip():
            raise ElderAuthError("API key must not be empty")
        if not header_name or not header_name.strip():
            raise ValueError("header_name must not be empty")
        self._api_key = api_key.strip()
        self.header_name = header_name.strip()

    @property
    def api_key(self) -> str:
        """Return the configured API key."""
        return self._api_key

    def apply_headers(self, headers: dict[str, str]) -> dict[str, str]:
        """Inject the API key header into ``headers``."""
        updated = dict(headers)
        updated[self.header_name] = self._api_key
        return updated

    def __repr__(self) -> str:
        return (
            f"ApiKeyAuth(header_name={self.header_name!r}, "
            f"api_key={mask_secret(self._api_key)!r})"
        )

    def __str__(self) -> str:
        return repr(self)


@dataclass
class AuthConfig:
    """Resolved Elder SDK connection and authentication configuration."""

    base_url: str = DEFAULT_API_URL
    auth: BearerAuth | ApiKeyAuth | None = None
    timeout: float = 30.0
    verify_ssl: bool = True

    def __post_init__(self) -> None:
        self.base_url = normalize_base_url(self.base_url)
        if self.timeout <= 0:
            raise ValueError("timeout must be positive")

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        base_url: str | None = None,
        token: str | None = None,
        api_key: str | None = None,
        refresh_token: str | None = None,
        timeout: float = 30.0,
        verify_ssl: bool = True,
    ) -> AuthConfig:
        """Build ``AuthConfig`` from explicit overrides or ``ELDER_*`` env vars.

        Reads ``ELDER_API_URL``, ``ELDER_TOKEN``, and ``ELDER_API_KEY``.
        """
        source = os.environ if env is None else env
        resolved_url = base_url or source.get("ELDER_API_URL") or DEFAULT_API_URL
        resolved_token = (
            token if token is not None else (source.get("ELDER_TOKEN") or "")
        )
        resolved_api_key = (
            api_key if api_key is not None else (source.get("ELDER_API_KEY") or "")
        )

        auth: BearerAuth | ApiKeyAuth | None = None
        if resolved_token and resolved_token.strip():
            auth = BearerAuth(resolved_token, refresh_token=refresh_token)
        elif resolved_api_key and resolved_api_key.strip():
            auth = ApiKeyAuth(resolved_api_key)

        return cls(
            base_url=resolved_url,
            auth=auth,
            timeout=timeout,
            verify_ssl=verify_ssl,
        )

    def __repr__(self) -> str:
        return (
            f"AuthConfig(base_url={self.base_url!r}, auth={self.auth!r}, "
            f"timeout={self.timeout!r}, verify_ssl={self.verify_ssl!r})"
        )

    def __str__(self) -> str:
        return repr(self)


def refresh_access_token(
    client: httpx.Client,
    refresh_token: str,
    *,
    portal: bool = False,
) -> dict[str, object]:
    """Synchronously exchange a refresh token for a new access token."""
    if not refresh_token or not refresh_token.strip():
        raise ElderAuthError("refresh_token is required")
    path = "/api/v1/portal-auth/refresh" if portal else "/api/v1/auth/refresh"
    payload = {"refresh_token": refresh_token.strip()}
    response = client.post(path, json=payload)
    raise_for_status(response)
    data = response.json()
    if not isinstance(data, dict) or not data.get("access_token"):
        raise ElderAuthError("Token refresh response did not include access_token")
    return data


async def async_refresh_access_token(
    client: httpx.AsyncClient,
    refresh_token: str,
    *,
    portal: bool = False,
) -> dict[str, object]:
    """Asynchronously exchange a refresh token for a new access token."""
    if not refresh_token or not refresh_token.strip():
        raise ElderAuthError("refresh_token is required")
    path = "/api/v1/portal-auth/refresh" if portal else "/api/v1/auth/refresh"
    payload = {"refresh_token": refresh_token.strip()}
    response = await client.post(path, json=payload)
    raise_for_status(response)
    data = response.json()
    if not isinstance(data, dict) or not data.get("access_token"):
        raise ElderAuthError("Token refresh response did not include access_token")
    return data
