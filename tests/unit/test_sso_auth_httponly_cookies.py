"""HttpOnly cookie contract for the SSO callbacks and local /auth/login (gh #285).

gh #285 (security follow-up to the httpOnly-cookie migration): the SAML ACS
and OIDC callback routes in apps/api/api/v1/sso.py, and the local
apps/api/api/v1/auth.py /auth/login route, returned the access/refresh JWTs in
the JSON body with no cookie-setting (login only set the older SSR
``elder_session`` cookie). These tests pin that all three now ALSO carry the
portal-auth HttpOnly/Secure/SameSite access + refresh cookies plus the
double-submit CSRF cookie, exactly like portal-auth's login/refresh responses.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from apps.api.auth.portal_cookies import (
    ACCESS_COOKIE_NAME,
    CSRF_COOKIE_NAME,
    REFRESH_COOKIE_NAME,
    REFRESH_COOKIE_PATH,
)
from apps.api.services.sso.oidc_state import OIDCLoginRecord

_TOKENS = {
    "access_token": "acc-jwt",
    "refresh_token": "ref-jwt",
    "token_type": "Bearer",
    "expires_in": 3600,
}


def _set_cookie_headers(response) -> list[str]:
    """Collect every Set-Cookie header value off a Quart/Werkzeug response."""
    return response.headers.get_all("Set-Cookie")


def _assert_portal_auth_cookies(response) -> None:
    """Assert the access/refresh/CSRF portal-auth cookie contract on a response."""
    headers = _set_cookie_headers(response)
    joined = "\n".join(headers)

    assert f"{ACCESS_COOKIE_NAME}=" in joined
    assert f"{REFRESH_COOKIE_NAME}=" in joined
    assert f"{CSRF_COOKIE_NAME}=" in joined

    for header in headers:
        if header.startswith(f"{ACCESS_COOKIE_NAME}="):
            assert "HttpOnly" in header, header
            assert "Secure" in header, header
            assert "SameSite=Strict" in header, header
        elif header.startswith(f"{REFRESH_COOKIE_NAME}="):
            assert "HttpOnly" in header, header
            assert "Secure" in header, header
            assert "SameSite=Strict" in header, header
            # Refresh cookie stays scoped to portal-auth's own routes.
            assert f"Path={REFRESH_COOKIE_PATH}" in header, header
        elif header.startswith(f"{CSRF_COOKIE_NAME}="):
            # Double-submit token must be JS-readable to echo back in a header.
            assert "HttpOnly" not in header, header
            assert "Secure" in header, header
            assert "SameSite=Strict" in header, header


class TestSamlAcsSetsHttpOnlyCookies:
    @pytest.mark.asyncio
    @patch("apps.api.api.v1.sso.generate_tokens", return_value=dict(_TOKENS))
    @patch("apps.api.api.v1.sso.SAMLService.process_saml_response")
    async def test_saml_acs_carries_auth_cookies(
        self, mock_process, _mock_tokens, async_client
    ):
        mock_process.return_value = {"id": 7, "tenant_id": 1, "email": "a@b.co"}

        resp = await async_client.post(
            "/api/v1/sso/saml/acs/1",
            form={"SAMLResponse": "fake-saml", "RelayState": "/"},
        )

        assert resp.status_code == 200, (await resp.get_data()).decode()[:200]
        _assert_portal_auth_cookies(resp)
        # Body still carries the tokens for non-browser callers (portal-auth parity).
        body = await resp.get_json()
        assert body["access_token"] == _TOKENS["access_token"]

    @pytest.mark.asyncio
    @patch("apps.api.api.v1.sso.SAMLService.process_saml_response")
    async def test_saml_acs_error_sets_no_cookies(self, mock_process, async_client):
        mock_process.return_value = {"error": "bad assertion"}

        resp = await async_client.post(
            "/api/v1/sso/saml/acs/1", form={"SAMLResponse": "fake-saml"}
        )

        assert resp.status_code == 400
        joined = "\n".join(_set_cookie_headers(resp))
        assert ACCESS_COOKIE_NAME not in joined
        assert REFRESH_COOKIE_NAME not in joined


class TestOidcCallbackSetsHttpOnlyCookies:
    """The callback's cookie contract; the state/nonce binding that gates it is
    covered end-to-end (real Redis) in ``test_sso_oidc_login_hardening.py``, so
    the login-state lookup is stubbed here."""

    @pytest.mark.asyncio
    @patch(
        "apps.api.api.v1.sso.oidc_state.consume_login",
        return_value=OIDCLoginRecord(
            idp_id=1,
            tenant_id=1,
            redirect_uri="https://app.example/cb",
            nonce="n",
            code_verifier="v",
        ),
    )
    @patch("apps.api.api.v1.sso.generate_tokens", return_value=dict(_TOKENS))
    @patch("apps.api.api.v1.sso.OIDCService.jit_provision_user")
    @patch("apps.api.api.v1.sso.OIDCService.get_idp_config")
    @patch("apps.api.api.v1.sso.OIDCService.get_userinfo")
    @patch("apps.api.api.v1.sso.OIDCService.validate_id_token")
    @patch("apps.api.api.v1.sso.OIDCService.exchange_code_for_tokens")
    async def test_oidc_callback_carries_auth_cookies(
        self,
        mock_exchange,
        mock_validate,
        mock_userinfo,
        mock_idp,
        mock_jit,
        _mock_tokens,
        _mock_consume,
        async_client,
    ):
        mock_exchange.return_value = {
            "id_token": "idt",
            "access_token": "idp-acc",
        }
        mock_validate.return_value = {"sub": "idp-sub", "email": "a@b.co"}
        mock_userinfo.return_value = {"email": "a@b.co"}
        mock_idp.return_value = {"tenant_id": 1, "jit_provisioning_enabled": True}
        mock_jit.return_value = {"id": 7, "tenant_id": 1, "email": "a@b.co"}

        resp = await async_client.get(
            "/api/v1/sso/oidc/callback",
            query_string={
                "code": "authz-code",
                "state": "stubbed-state",
                "idp_id": 1,
                "redirect_uri": "https://app.example/cb",
            },
            headers={"Cookie": "elder_oidc_binding=stubbed-binding"},
        )

        assert resp.status_code == 200, (await resp.get_data()).decode()[:200]
        _assert_portal_auth_cookies(resp)
        body = await resp.get_json()
        assert body["refresh_token"] == _TOKENS["refresh_token"]


class TestAuthLoginSetsHttpOnlyCookies:
    @pytest.mark.asyncio
    async def test_login_carries_portal_auth_cookies(self, async_client):
        identity = SimpleNamespace(
            id=42,
            username="user@example.com",
            email="user@example.com",
            full_name="User",
            is_superuser=False,
        )

        with (
            patch(
                "apps.api.api.v1.auth.run_in_threadpool",
                new=AsyncMock(return_value=(identity, None, None)),
            ),
            patch(
                "apps.api.api.v1.auth.generate_token",
                side_effect=lambda _identity, token_type="access": f"{token_type}-tok",
            ),
        ):
            resp = await async_client.post(
                "/api/v1/auth/login",
                json={"username": "user@example.com", "password": "pw-placeholder"},
            )

        assert resp.status_code == 200, (await resp.get_data()).decode()[:200]
        _assert_portal_auth_cookies(resp)
        # The older SSR elder_session cookie must still be set alongside.
        joined = "\n".join(_set_cookie_headers(resp))
        assert "elder_session=" in joined
        body = await resp.get_json()
        assert body["access_token"] == "access-tok"
        assert body["refresh_token"] == "refresh-tok"
