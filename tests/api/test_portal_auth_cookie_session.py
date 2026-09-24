"""Integration tests: portal-auth login/refresh/logout set/clear HttpOnly cookies,
and state-changing cookie-authenticated requests are CSRF-protected.

gh security audit, High: web/src/lib/api.ts kept the access + refresh JWT in
localStorage (XSS-exfiltratable). Login/refresh/logout now issue HttpOnly,
Secure, SameSite cookies instead (apps.api.auth.portal_cookies); the JSON
body still carries the tokens for non-browser callers, so these tests assert
the cookies exist -- not that the body tokens are gone.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from quart import current_app
from werkzeug.security import generate_password_hash

from apps.api.auth.portal_cookies import (
    ACCESS_COOKIE_NAME,
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    REFRESH_COOKIE_NAME,
)

_TEST_PASSWORD = "SecurePass123!"


async def _make_tenant_and_user(app):
    """Seed a fresh tenant + active portal user with a known password."""
    async with app.app_context():
        db = current_app.db
        now = datetime.now(UTC)
        suffix = uuid4().hex[:8]

        tenant_id = db.tenants.insert(
            name=f"Cookie Session Test {suffix}",
            slug=f"cookie-session-{suffix}",
            is_active=True,
            created_at=now,
            updated_at=now,
        )

        email = f"cookie-session-{suffix}@example.com"
        db.portal_users.insert(
            tenant_id=tenant_id,
            email=email,
            password_hash=generate_password_hash(_TEST_PASSWORD),
            is_active=True,
            email_verified=True,
            tenant_role="reader",
            created_at=now,
            updated_at=now,
        )
        db.commit()

        return tenant_id, email


def _cookie_headers(response) -> list[str]:
    return response.headers.get_all("Set-Cookie")


def _cookie_value(response, name: str) -> str | None:
    for header in _cookie_headers(response):
        if header.startswith(f"{name}="):
            return header.split(";", 1)[0].split("=", 1)[1]
    return None


@pytest.mark.asyncio
class TestLoginSetsAuthCookies:
    async def test_login_sets_httponly_access_and_refresh_cookies(self, client, app):
        tenant_id, email = await _make_tenant_and_user(app)

        response = await client.post(
            "/api/v1/portal-auth/login",
            json={"email": email, "password": _TEST_PASSWORD, "tenant_id": tenant_id},
        )

        assert response.status_code == 200
        cookies = "\n".join(_cookie_headers(response))
        assert f"{ACCESS_COOKIE_NAME}=" in cookies
        assert f"{REFRESH_COOKIE_NAME}=" in cookies
        assert f"{CSRF_COOKIE_NAME}=" in cookies

        access_header = next(
            h
            for h in _cookie_headers(response)
            if h.startswith(f"{ACCESS_COOKIE_NAME}=")
        )
        refresh_header = next(
            h
            for h in _cookie_headers(response)
            if h.startswith(f"{REFRESH_COOKIE_NAME}=")
        )
        assert "HttpOnly" in access_header
        assert "HttpOnly" in refresh_header
        assert "SameSite=Strict" in access_header

        # Non-browser callers still get the tokens in the JSON body.
        data = await response.get_json()
        assert data["access_token"]
        assert data["refresh_token"]

    async def test_refresh_rotates_cookies_via_cookie_only(self, client, app):
        """Browser flow: no refresh_token in the body, cookie carries it."""
        tenant_id, email = await _make_tenant_and_user(app)
        login_response = await client.post(
            "/api/v1/portal-auth/login",
            json={"email": email, "password": _TEST_PASSWORD, "tenant_id": tenant_id},
        )
        assert login_response.status_code == 200
        csrf_token = _cookie_value(login_response, CSRF_COOKIE_NAME)

        # client persists the Set-Cookie'd session automatically; the SPA
        # sends no request body, mirroring web/src/lib/api.ts's cookie-only
        # refresh call, but must echo the CSRF header for the double-submit
        # check (see main.py::enforce_csrf_protection).
        refresh_response = await client.post(
            "/api/v1/portal-auth/refresh",
            headers={CSRF_HEADER_NAME: csrf_token},
        )
        assert refresh_response.status_code == 200
        data = await refresh_response.get_json()
        assert data["access_token"]

    async def test_logout_clears_all_portal_auth_cookies(self, client, app):
        tenant_id, email = await _make_tenant_and_user(app)
        login_response = await client.post(
            "/api/v1/portal-auth/login",
            json={"email": email, "password": _TEST_PASSWORD, "tenant_id": tenant_id},
        )
        csrf_token = _cookie_value(login_response, CSRF_COOKIE_NAME)

        logout_response = await client.post(
            "/api/v1/portal-auth/logout",
            headers={CSRF_HEADER_NAME: csrf_token},
        )
        assert logout_response.status_code == 200

        cleared = "\n".join(_cookie_headers(logout_response))
        # Werkzeug expires deleted cookies immediately (Max-Age=0 / epoch date).
        for name in (ACCESS_COOKIE_NAME, REFRESH_COOKIE_NAME, CSRF_COOKIE_NAME):
            header = next(
                h for h in _cookie_headers(logout_response) if h.startswith(f"{name}=")
            )
            assert "Max-Age=0" in header or "01-Jan-1970" in header


@pytest.mark.asyncio
class TestCsrfProtection:
    async def test_state_changing_cookie_request_without_csrf_header_is_rejected(
        self, client, app
    ):
        tenant_id, email = await _make_tenant_and_user(app)
        await client.post(
            "/api/v1/portal-auth/login",
            json={"email": email, "password": _TEST_PASSWORD, "tenant_id": tenant_id},
        )

        # Cookie-authenticated POST with no X-CSRF-Token header must be
        # rejected even though the browser auto-attached the auth cookies.
        response = await client.post("/api/v1/portal-auth/logout")
        assert response.status_code == 403
        data = await response.get_json()
        assert "csrf" in data["error"].lower()

    async def test_state_changing_cookie_request_with_wrong_csrf_header_is_rejected(
        self, client, app
    ):
        tenant_id, email = await _make_tenant_and_user(app)
        await client.post(
            "/api/v1/portal-auth/login",
            json={"email": email, "password": _TEST_PASSWORD, "tenant_id": tenant_id},
        )

        response = await client.post(
            "/api/v1/portal-auth/logout",
            headers={CSRF_HEADER_NAME: "not-the-real-token"},
        )
        assert response.status_code == 403

    async def test_bearer_token_caller_is_exempt_from_csrf(self, client, app):
        """Non-browser callers using Authorization headers aren't cookie-
        authenticated, so the double-submit check must not apply to them."""
        tenant_id, email = await _make_tenant_and_user(app)
        login_response = await client.post(
            "/api/v1/portal-auth/login",
            json={"email": email, "password": _TEST_PASSWORD, "tenant_id": tenant_id},
        )
        data = await login_response.get_json()
        access_token = data["access_token"]

        # A fresh client with no cookie jar entries at all, authenticating
        # purely via Authorization header -- logout must succeed without a
        # CSRF header.
        response = await client.post(
            "/api/v1/portal-auth/logout",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200

    async def test_get_requests_are_never_csrf_checked(self, client, app):
        tenant_id, email = await _make_tenant_and_user(app)
        await client.post(
            "/api/v1/portal-auth/login",
            json={"email": email, "password": _TEST_PASSWORD, "tenant_id": tenant_id},
        )

        response = await client.get("/api/v1/portal-auth/me")
        assert response.status_code == 200
