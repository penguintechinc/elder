"""Unit tests for apps/api/auth/portal_cookies.py.

gh security audit, High: the SPA previously kept the access + refresh JWT in
localStorage (web/src/lib/api.ts), which is readable by any injected script.
These tests pin the HttpOnly cookie contract that replaced it -- see
apps.api.api.v1.portal_auth for the routes that call these helpers.
"""

import pytest

from apps.api.auth.portal_cookies import (
    ACCESS_COOKIE_NAME,
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    REFRESH_COOKIE_NAME,
    REFRESH_COOKIE_PATH,
    clear_portal_auth_cookies,
    get_access_token_from_cookie,
    get_refresh_token_from_cookie,
    is_csrf_valid,
    set_portal_auth_cookies,
)


def _set_cookie_headers(response) -> list[str]:
    """Collect every Set-Cookie header value off a Quart/Werkzeug response."""
    return response.headers.get_all("Set-Cookie")


@pytest.mark.asyncio
class TestSetPortalAuthCookies:
    """set_portal_auth_cookies: HttpOnly/Secure/SameSite contract."""

    async def test_access_and_refresh_cookies_are_httponly(self, app):
        async with app.test_request_context("/"):
            from quart import Response

            response = Response("")
            set_portal_auth_cookies(
                response,
                "access-tok",
                "refresh-tok",
                access_max_age=3600,
                refresh_max_age=86400,
                secure=True,
            )
            cookies = "\n".join(_set_cookie_headers(response))

            assert f"{ACCESS_COOKIE_NAME}=access-tok" in cookies
            assert f"{REFRESH_COOKIE_NAME}=refresh-tok" in cookies

            for header in _set_cookie_headers(response):
                if header.startswith(f"{ACCESS_COOKIE_NAME}=") or header.startswith(
                    f"{REFRESH_COOKIE_NAME}="
                ):
                    assert "HttpOnly" in header, header
                    assert "Secure" in header, header
                    assert "SameSite=Strict" in header, header

    async def test_csrf_cookie_is_not_httponly(self, app):
        """The CSRF cookie must be JS-readable so the SPA can echo it back."""
        async with app.test_request_context("/"):
            from quart import Response

            response = Response("")
            set_portal_auth_cookies(
                response,
                "access-tok",
                "refresh-tok",
                access_max_age=3600,
                refresh_max_age=86400,
                secure=True,
            )
            csrf_header = next(
                h
                for h in _set_cookie_headers(response)
                if h.startswith(f"{CSRF_COOKIE_NAME}=")
            )
            assert "HttpOnly" not in csrf_header
            assert "Secure" in csrf_header
            assert "SameSite=Strict" in csrf_header

    async def test_refresh_cookie_is_path_scoped(self, app):
        """Refresh cookie only leaves the browser for portal-auth's own routes."""
        async with app.test_request_context("/"):
            from quart import Response

            response = Response("")
            set_portal_auth_cookies(
                response,
                "access-tok",
                "refresh-tok",
                access_max_age=3600,
                refresh_max_age=86400,
                secure=True,
            )
            refresh_header = next(
                h
                for h in _set_cookie_headers(response)
                if h.startswith(f"{REFRESH_COOKIE_NAME}=")
            )
            assert f"Path={REFRESH_COOKIE_PATH}" in refresh_header

    async def test_returns_a_fresh_csrf_token_each_call(self, app):
        async with app.test_request_context("/"):
            from quart import Response

            token_a = set_portal_auth_cookies(
                Response(""), "a", "b", access_max_age=60, refresh_max_age=60
            )
            token_b = set_portal_auth_cookies(
                Response(""), "a", "b", access_max_age=60, refresh_max_age=60
            )
            assert token_a != token_b


@pytest.mark.asyncio
class TestClearPortalAuthCookies:
    async def test_deletes_all_three_cookies(self, app):
        async with app.test_request_context("/"):
            from quart import Response

            response = Response("")
            clear_portal_auth_cookies(response)
            cookies = "\n".join(_set_cookie_headers(response))

            for name in (ACCESS_COOKIE_NAME, REFRESH_COOKIE_NAME, CSRF_COOKIE_NAME):
                assert f"{name}=" in cookies


@pytest.mark.asyncio
class TestReadCookies:
    async def test_get_access_token_from_cookie_reads_request_cookie(self, app):
        async with app.test_request_context(
            "/", headers={"Cookie": f"{ACCESS_COOKIE_NAME}=my-access-token"}
        ):
            assert get_access_token_from_cookie() == "my-access-token"

    async def test_get_access_token_from_cookie_absent(self, app):
        async with app.test_request_context("/"):
            assert get_access_token_from_cookie() is None

    async def test_get_refresh_token_from_cookie_reads_request_cookie(self, app):
        async with app.test_request_context(
            "/", headers={"Cookie": f"{REFRESH_COOKIE_NAME}=my-refresh-token"}
        ):
            assert get_refresh_token_from_cookie() == "my-refresh-token"


@pytest.mark.asyncio
class TestCsrfDoubleSubmit:
    async def test_valid_when_header_matches_cookie(self, app):
        async with app.test_request_context(
            "/",
            headers={
                "Cookie": f"{CSRF_COOKIE_NAME}=matching-token",
                CSRF_HEADER_NAME: "matching-token",
            },
        ):
            assert is_csrf_valid() is True

    async def test_invalid_when_header_missing(self, app):
        async with app.test_request_context(
            "/", headers={"Cookie": f"{CSRF_COOKIE_NAME}=matching-token"}
        ):
            assert is_csrf_valid() is False

    async def test_invalid_when_cookie_missing(self, app):
        async with app.test_request_context(
            "/", headers={CSRF_HEADER_NAME: "matching-token"}
        ):
            assert is_csrf_valid() is False

    async def test_invalid_when_values_differ(self, app):
        async with app.test_request_context(
            "/",
            headers={
                "Cookie": f"{CSRF_COOKIE_NAME}=token-a",
                CSRF_HEADER_NAME: "token-b",
            },
        ):
            assert is_csrf_valid() is False
