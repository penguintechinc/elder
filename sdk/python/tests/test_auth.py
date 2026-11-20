"""Unit tests for elder_sdk.auth."""

from __future__ import annotations

import base64
import json
import time

import httpx
import pytest

from elder_sdk.auth import (
    DEFAULT_API_URL,
    ApiKeyAuth,
    AuthConfig,
    BearerAuth,
    async_refresh_access_token,
    decode_jwt_exp,
    mask_secret,
    normalize_base_url,
    refresh_access_token,
)
from elder_sdk.exceptions import ElderAuthError


def _fake_jwt(payload: dict[str, object]) -> str:
    header = (
        base64.urlsafe_b64encode(json.dumps({"alg": "HS256"}).encode())
        .decode()
        .rstrip("=")
    )
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"{header}.{body}.sig123"


def test_mask_secret_never_leaks_full_value() -> None:
    assert mask_secret(None) == "<unset>"
    assert mask_secret("") == "<unset>"
    assert mask_secret("short") == "****"
    assert mask_secret("12345678") == "****"
    assert mask_secret("super_secret_token_9999") == "supe****9999"


def test_normalize_base_url() -> None:
    assert (
        normalize_base_url("https://elder.example.com/") == "https://elder.example.com"
    )
    assert (
        normalize_base_url("https://elder.example.com/api/v1/")
        == "https://elder.example.com"
    )
    with pytest.raises(ValueError, match="base_url"):
        normalize_base_url("   ")


def test_decode_jwt_exp_variants() -> None:
    future = int(time.time()) + 3600
    tok = _fake_jwt({"sub": "1", "exp": future})
    assert decode_jwt_exp(tok) == float(future)
    assert decode_jwt_exp("not-a-jwt") is None
    assert decode_jwt_exp("a.!!!.c") is None
    assert decode_jwt_exp(_fake_jwt({"exp": True})) is None
    non_dict = (
        "hdr." + base64.urlsafe_b64encode(b"[1, 2, 3]").decode().rstrip("=") + ".sig"
    )
    assert decode_jwt_exp(non_dict) is None


def test_bearer_auth_masking_and_lifecycle() -> None:
    with pytest.raises(ElderAuthError):
        BearerAuth("   ")

    expired_jwt = _fake_jwt({"exp": time.time() - 10})
    auth = BearerAuth(expired_jwt, refresh_token="refresh_secret_value_abc")
    assert auth.token == expired_jwt
    assert auth.refresh_token == "refresh_secret_value_abc"
    assert auth.expires_at is not None
    assert auth.is_expired() is True
    assert "refresh_secret_value_abc" not in repr(auth)
    assert expired_jwt not in repr(auth)
    assert "has_refresh=True" in str(auth)

    headers = auth.apply_headers({"X-Custom": "1"})
    assert headers == {"X-Custom": "1", "Authorization": f"Bearer {expired_jwt}"}

    future_jwt = _fake_jwt({"exp": time.time() + 7200})
    auth.update_tokens(future_jwt, refresh_token="")
    assert auth.is_expired() is False
    assert auth.refresh_token is None

    opaque = BearerAuth("opaque-token-value-1234")
    assert opaque.is_expired() is False
    with pytest.raises(ElderAuthError):
        opaque.update_tokens("")


def test_api_key_auth_masking_and_headers() -> None:
    with pytest.raises(ElderAuthError):
        ApiKeyAuth("")
    with pytest.raises(ValueError):
        ApiKeyAuth("dummy-key-placeholder", header_name="  ")

    auth = ApiKeyAuth("dummy-key-placeholder")
    assert auth.api_key == "dummy-key-placeholder"
    assert auth.apply_headers({}) == {"X-API-Key": "dummy-key-placeholder"}
    assert "dummy-key-placeholder" not in repr(auth)
    assert "dumm****lder" in str(auth)


def test_auth_config_from_env_and_validation() -> None:
    env = {
        "ELDER_API_URL": "https://api.elder.local/api/v1/",
        "ELDER_TOKEN": "jwt_secret_token_abcd",
        "ELDER_API_KEY": "elder_key_ignored_when_token_set",
    }
    cfg = AuthConfig.from_env(env)
    assert cfg.base_url == "https://api.elder.local"
    assert isinstance(cfg.auth, BearerAuth)
    assert "jwt_secret_token_abcd" not in repr(cfg)
    assert "jwt_secret_token_abcd" not in str(cfg)

    cfg_key = AuthConfig.from_env({"ELDER_API_KEY": "dummy-env-placeholder"})
    assert cfg_key.base_url == DEFAULT_API_URL
    assert isinstance(cfg_key.auth, ApiKeyAuth)
    assert "dummy-env-placeholder" not in repr(cfg_key)

    cfg_none = AuthConfig.from_env({})
    assert cfg_none.auth is None

    with pytest.raises(ValueError, match="timeout"):
        AuthConfig(base_url="https://api.elder.local", timeout=0)


def test_refresh_access_token_sync_and_async() -> None:
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        if request.url.path == "/api/v1/auth/refresh":
            return httpx.Response(200, json={"access_token": "new-tok-123"})
        if request.url.path == "/api/v1/portal-auth/refresh":
            return httpx.Response(200, json={"unexpected": True})
        return httpx.Response(401, json={"error": "invalid"})

    transport = httpx.MockTransport(handler)
    with httpx.Client(base_url="https://elder.example.com", transport=transport) as c:
        with pytest.raises(ElderAuthError):
            refresh_access_token(c, "  ")
        res = refresh_access_token(c, "ref-123")
        assert res["access_token"] == "new-tok-123"
        with pytest.raises(ElderAuthError, match="access_token"):
            refresh_access_token(c, "ref-123", portal=True)

    assert seen_paths == ["/api/v1/auth/refresh", "/api/v1/portal-auth/refresh"]


async def test_async_refresh_access_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/portal-auth/refresh":
            return httpx.Response(200, json={"access_token": "portal-tok-456"})
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        base_url="https://elder.example.com", transport=transport
    ) as ac:
        with pytest.raises(ElderAuthError):
            await async_refresh_access_token(ac, "")
        res = await async_refresh_access_token(ac, "ref-1", portal=True)
        assert res["access_token"] == "portal-tok-456"
        with pytest.raises(ElderAuthError, match="access_token"):
            await async_refresh_access_token(ac, "ref-1", portal=False)
