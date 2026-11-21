"""Authentication session management and per-caller context for Elder MCP."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from collections.abc import Iterable, Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

import httpx


class ElderAuthError(Exception):
    """Raised when Elder MCP authentication fails or is misconfigured."""


def resolve_elder_base_url(base_url: str | None = None) -> str:
    """Resolve the Elder API base URL from argument or ``ELDER_URL`` / ``ELDER_API_URL``."""
    if base_url is not None and base_url.strip():
        return base_url.strip().rstrip("/")
    env_url = (os.environ.get("ELDER_URL") or "").strip() or (
        os.environ.get("ELDER_API_URL") or ""
    ).strip()
    return (env_url or "http://localhost:5000").rstrip("/")


def mask_secret(value: str | None) -> str | None:
    """Return a masked representation of a token, API key, or password."""
    if value is None:
        return None
    if not value:
        return ""
    if len(value) >= 16:
        return f"{value[:4]}****{value[-4:]}"
    return "****"


def _sanitize_header_value(value: str | None) -> str | None:
    """Strip and validate a single-line header value against CRLF injection."""
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned or any(ch in cleaned for ch in ("\r", "\n", "\x00")):
        return None
    return cleaned


@dataclass(frozen=True)
class CallerCredentials:
    """Validated per-request caller credentials and trace propagation metadata."""

    access_token: str | None = None
    api_key: str | None = None
    request_id: str | None = None
    traceparent: str | None = None
    tracestate: str | None = None

    def __repr__(self) -> str:
        return (
            "CallerCredentials("
            f"access_token={mask_secret(self.access_token)!r}, "
            f"api_key={mask_secret(self.api_key)!r}, "
            f"request_id={self.request_id!r})"
        )

    __str__ = __repr__

    def credential_fingerprint(self) -> str:
        """Return a non-reversible SHA-256 digest identifying the caller credential."""
        material = (
            f"bearer:{self.access_token}"
            if self.access_token
            else f"apikey:{self.api_key or ''}"
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def propagation_headers(self) -> dict[str, str]:
        """Return safe correlation and trace propagation headers for ``hub-api``."""
        headers: dict[str, str] = {}
        if self.request_id:
            headers["X-Request-ID"] = self.request_id
        if self.traceparent:
            headers["traceparent"] = self.traceparent
        if self.tracestate:
            headers["tracestate"] = self.tracestate
        return headers


_caller_credentials_var: ContextVar[CallerCredentials | None] = ContextVar(
    "elder_mcp_caller_credentials",
    default=None,
)
_hosted_mode_var: ContextVar[bool] = ContextVar(
    "elder_mcp_hosted_mode",
    default=False,
)


def get_caller_credentials() -> CallerCredentials | None:
    """Return the active request-scoped caller credentials, if any."""
    return _caller_credentials_var.get()


def is_hosted_mode() -> bool:
    """Return True if executing inside a hosted HTTP/SSE request or transport."""
    if _hosted_mode_var.get():
        return True
    transport = os.environ.get("MCP_TRANSPORT", "stdio").strip().lower()
    return transport in ("sse", "streamable-http")


def set_caller_context(
    credentials: CallerCredentials | None,
    *,
    hosted: bool = True,
) -> tuple[Token[CallerCredentials | None], Token[bool]]:
    """Bind caller credentials and hosted flag into the current async context."""
    cred_token = _caller_credentials_var.set(credentials)
    hosted_token = _hosted_mode_var.set(hosted)
    return cred_token, hosted_token


def reset_caller_context(
    tokens: tuple[Token[CallerCredentials | None], Token[bool]],
) -> None:
    """Restore previous caller context tokens."""
    cred_token, hosted_token = tokens
    _caller_credentials_var.reset(cred_token)
    _hosted_mode_var.reset(hosted_token)


def _normalize_headers(
    headers: Mapping[str, str] | Iterable[tuple[bytes | str, bytes | str]],
) -> dict[str, str]:
    normalized: dict[str, str] = {}
    items = headers.items() if isinstance(headers, Mapping) else headers
    for raw_key, raw_val in items:
        key = (
            raw_key.decode("latin-1") if isinstance(raw_key, bytes) else str(raw_key)
        ).lower()
        val = raw_val.decode("latin-1") if isinstance(raw_val, bytes) else str(raw_val)
        normalized[key] = val
    return normalized


def extract_caller_credentials(
    headers: Mapping[str, str] | Iterable[tuple[bytes | str, bytes | str]],
) -> CallerCredentials:
    """Extract and validate ``Authorization: Bearer`` or ``X-API-Key`` headers.

    Fails closed with ``ElderAuthError`` if credentials are missing, empty, or malformed.
    Never falls back to environment variables and never extracts tenant override headers.
    """
    norm = _normalize_headers(headers)

    access_token: str | None = None
    api_key: str | None = None

    if "authorization" in norm:
        auth_val = _sanitize_header_value(norm["authorization"])
        if not auth_val:
            raise ElderAuthError("Authentication required")
        parts = auth_val.split()
        if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
            raise ElderAuthError("Invalid Authorization header; expected Bearer token")
        access_token = parts[1]
    elif "x-api-key" in norm:
        raw_key = _sanitize_header_value(norm["x-api-key"])
        if not raw_key or any(ch.isspace() for ch in raw_key):
            raise ElderAuthError("Invalid X-API-Key header")
        api_key = raw_key
    else:
        raise ElderAuthError("Authentication required")

    return CallerCredentials(
        access_token=access_token,
        api_key=api_key,
        request_id=_sanitize_header_value(norm.get("x-request-id")),
        traceparent=_sanitize_header_value(norm.get("traceparent")),
        tracestate=_sanitize_header_value(norm.get("tracestate")),
    )


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
        *,
        enforce_local_expiry: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.api_key = api_key
        self._transport = transport
        self._timeout = timeout
        self._enforce_local_expiry = enforce_local_expiry
        self._token_expires_at: float | None = (
            _decode_jwt_exp(access_token) if access_token else None
        )

    def __repr__(self) -> str:
        return (
            f"ElderSession(base_url={self.base_url!r}, "
            f"username={self.username!r}, "
            f"password={mask_secret(self.password)!r}, "
            f"access_token={mask_secret(self.access_token)!r}, "
            f"refresh_token={mask_secret(self.refresh_token)!r}, "
            f"api_key={mask_secret(self.api_key)!r})"
        )

    __str__ = __repr__

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
    def from_caller(
        cls,
        base_url: str,
        credentials: CallerCredentials,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
    ) -> ElderSession:
        """Construct a stateless per-request session bound strictly to caller credentials."""
        if not credentials.access_token and not credentials.api_key:
            raise ElderAuthError("Caller credentials are required")
        return cls(
            base_url=base_url,
            access_token=credentials.access_token,
            api_key=credentials.api_key,
            transport=transport,
            timeout=timeout,
            enforce_local_expiry=False,
        )

    @classmethod
    def from_env(
        cls,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> ElderSession:
        """Construct a session from ``ELDER_*`` environment variables (stdio mode only)."""
        base_url = resolve_elder_base_url()
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
        if not self._enforce_local_expiry or self._token_expires_at is None:
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
