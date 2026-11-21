"""HttpOnly cookie helpers for the portal-auth (SPA) session.

gh security audit, High: the SPA's login/refresh responses returned the JWT
access + refresh tokens as JSON body fields that `web/src/lib/api.ts` copied
into `localStorage` -- readable by any injected script (XSS-exfiltratable
session tokens). Both tokens are now ALSO carried as HttpOnly/Secure/
SameSite cookies; the JSON body is kept only for non-browser callers that
predate this change and must not be relied on by the web client going
forward -- see docs/security/httponly-token-storage.md remediation notes.
"""

import secrets

from quart import request

ACCESS_COOKIE_NAME = "elder_access_token"
REFRESH_COOKIE_NAME = "elder_refresh_token"
CSRF_COOKIE_NAME = "elder_csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"

# The refresh cookie only ever needs to leave the browser for portal-auth's
# own endpoints (refresh/logout) -- scoping its Path means a cookie header
# leak (misconfigured proxy, another route's verbose logging, etc.) on any
# other path can't include it.
REFRESH_COOKIE_PATH = "/api/v1/portal-auth"


def set_portal_auth_cookies(
    response,
    access_token: str,
    refresh_token: str,
    access_max_age: int,
    refresh_max_age: int,
    secure: bool = True,
) -> str:
    """Attach the access/refresh/CSRF cookies for a portal-auth session.

    Mutates `response` in place and returns the newly issued CSRF token
    value (callers don't need it beyond tests -- the SPA reads it back off
    the response's Set-Cookie header via document.cookie).
    """
    response.set_cookie(
        ACCESS_COOKIE_NAME,
        access_token,
        max_age=access_max_age,
        httponly=True,
        secure=secure,
        samesite="Strict",
        path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        refresh_token,
        max_age=refresh_max_age,
        httponly=True,
        secure=secure,
        samesite="Strict",
        path=REFRESH_COOKIE_PATH,
    )

    # Double-submit CSRF token: deliberately NOT HttpOnly -- the SPA must be
    # able to read it via document.cookie to echo it back in the
    # X-CSRF-Token header on state-changing requests. A cross-site attacker
    # can force the browser to *send* the HttpOnly auth cookies but can't
    # read this cookie's value (same-origin policy), so it can't forge a
    # matching header -- see enforce_csrf_protection() in main.py.
    csrf_token = secrets.token_urlsafe(32)
    response.set_cookie(
        CSRF_COOKIE_NAME,
        csrf_token,
        max_age=access_max_age,
        httponly=False,
        secure=secure,
        samesite="Strict",
        path="/",
    )
    return csrf_token


def clear_portal_auth_cookies(response) -> None:
    """Delete all portal-auth cookies (logout, or a failed/expired refresh)."""
    response.delete_cookie(ACCESS_COOKIE_NAME, path="/")
    response.delete_cookie(REFRESH_COOKIE_NAME, path=REFRESH_COOKIE_PATH)
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")


def get_access_token_from_cookie() -> str | None:
    """Read the HttpOnly access-token cookie from the current request, if any."""
    return request.cookies.get(ACCESS_COOKIE_NAME)


def get_refresh_token_from_cookie() -> str | None:
    """Read the HttpOnly refresh-token cookie from the current request, if any."""
    return request.cookies.get(REFRESH_COOKIE_NAME)


def is_csrf_valid() -> bool:
    """Double-submit CSRF check: the header value must match the cookie value.

    Only meaningful for cookie-authenticated requests -- Bearer-token callers
    aren't subject to CSRF (browsers don't auto-attach an Authorization
    header cross-site) and are exempted by the caller, not here.
    """
    cookie_value = request.cookies.get(CSRF_COOKIE_NAME)
    header_value = request.headers.get(CSRF_HEADER_NAME)
    if not cookie_value or not header_value:
        return False
    return secrets.compare_digest(cookie_value, header_value)
