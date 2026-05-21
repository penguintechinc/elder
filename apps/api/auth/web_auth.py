"""JWT cookie authentication for server-rendered web UI routes."""

from functools import wraps

from quart import g, redirect, request, url_for

from apps.api.auth.jwt_handler import verify_token

COOKIE_NAME = "elder_session"
COOKIE_MAX_AGE = 60 * 60 * 8  # 8 hours, matching access token lifetime


def get_web_user():
    """Return the identity payload from the JWT cookie, or None if absent/invalid."""
    if hasattr(g, "_web_user"):
        return g._web_user
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        g._web_user = None
        return None
    payload = verify_token(token)
    g._web_user = payload
    return payload


def web_login_required(f):
    """Decorator for SSR routes: redirect to /login instead of returning 401."""

    @wraps(f)
    async def decorated(*args, **kwargs):
        if get_web_user() is None:
            return redirect(url_for("web.login"))
        return await f(*args, **kwargs)

    return decorated


def clear_session_cookie(response):
    """Delete the elder_session cookie from a response object."""
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


def set_session_cookie(response, token: str, secure: bool = True) -> None:
    """Set the elder_session JWT cookie on a response object (mutates in place)."""
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        secure=secure,
        samesite="Strict",
        path="/",
    )
