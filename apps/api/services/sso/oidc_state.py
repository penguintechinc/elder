"""Single-use, browser-bound login state for the OIDC authorization-code flow.

regression: oidc-login-csrf -- the callback used to read ``state`` and discard
it and never checked the ID token ``nonce``, so any unauthenticated GET carrying
an attacker's ``code`` set auth cookies in the victim's browser (login CSRF).

``begin_login`` mints four independent secrets for one login attempt:

* ``state``   -- echoed by the IdP on the redirect; the Redis key is its SHA-256,
  so the raw value is never stored.
* ``nonce``   -- must come back inside the signed ID token.
* PKCE verifier -- kept server-side; only its S256 challenge goes to the IdP.
* ``binding`` -- handed to the initiating browser as an HttpOnly cookie. Only
  its SHA-256 is stored, so a state value alone (e.g. one lifted from a URL)
  is useless without the cookie of the browser that started the flow.

``consume_login`` reads and deletes the record in one MULTI/EXEC, so a state is
single use even under concurrent replays. Everything here fails closed: a Redis
outage or a missing/foreign/replayed state is a specific 4xx/503, never a
fallback to "no CSRF check".
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass

import redis
from quart import current_app
from redis.exceptions import RedisError

STATE_STORE_EXT = "oidc_login_state_store"
BINDING_COOKIE_NAME = "elder_oidc_binding"
BINDING_COOKIE_PATH = "/api/v1/sso/oidc"
STATE_TTL_SECONDS = 600

_KEY_PREFIX = "elder:oidc:login"
_REDIS_TIMEOUT_SECONDS = 2
_MAX_TOKEN_CHARS = 256


class OIDCLoginError(Exception):
    """A login attempt was refused; carries a stable machine code and HTTP status.

    ``message`` is a fixed, caller-safe sentence -- never upstream error text,
    request values or secrets.
    """

    def __init__(self, code: str, status: int, message: str) -> None:
        """Remember the machine code, HTTP status and safe message."""
        super().__init__(message)
        self.code = code
        self.status = status
        self.message = message


@dataclass(slots=True, frozen=True)
class OIDCLoginStart:
    """Secrets minted for one login attempt (``verifier`` stays server-side)."""

    state: str
    nonce: str
    code_challenge: str
    binding: str


@dataclass(slots=True, frozen=True)
class OIDCLoginRecord:
    """What the callback may trust: the context bound to a consumed ``state``."""

    idp_id: int
    tenant_id: int
    redirect_uri: str
    nonce: str
    code_verifier: str


def _unavailable() -> OIDCLoginError:
    """The error for any state-store outage (fail closed, 503)."""
    return OIDCLoginError(
        "oidc_state_store_unavailable",
        503,
        "Login state store is unavailable; try again shortly",
    )


def _digest(value: str) -> str:
    """Hex SHA-256 of a secret -- the only form that ever reaches Redis."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _key(state: str) -> str:
    """Redis key for a login state (derived from the state's hash)."""
    return f"{_KEY_PREFIX}:{_digest(state)}"


def _challenge(verifier: str) -> str:
    """PKCE S256 ``code_challenge`` for ``verifier`` (RFC 7636 section 4.2)."""
    raw = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _client() -> redis.Redis:
    """Return the dedicated state-store Redis client, building it lazily.

    Short timeouts keep a dead Redis from stalling logins; the client is cached
    on the app and dropped (see ``_drop_client``) after any failure so the next
    request reconnects instead of reusing a poisoned connection.

    Raises:
        OIDCLoginError: ``oidc_state_store_unavailable`` (503) if Redis is not
            configured or unreachable.
    """
    cached: redis.Redis | None = current_app.extensions.get(STATE_STORE_EXT)
    if cached is not None:
        return cached
    url = current_app.config.get("REDIS_URL") or ""
    if not url:
        raise _unavailable()
    try:
        client = redis.from_url(
            url,
            socket_connect_timeout=_REDIS_TIMEOUT_SECONDS,
            socket_timeout=_REDIS_TIMEOUT_SECONDS,
            health_check_interval=30,
        )
        client.ping()
    except (RedisError, OSError, ValueError) as exc:
        current_app.logger.warning(
            "oidc_state_store_connect_failed error=%s", type(exc).__name__
        )
        raise _unavailable() from exc
    current_app.extensions[STATE_STORE_EXT] = client
    return client


def _drop_client(exc: Exception) -> None:
    """Forget the cached client after a Redis failure (logs the class only)."""
    current_app.extensions.pop(STATE_STORE_EXT, None)
    current_app.logger.warning("oidc_state_store_error error=%s", type(exc).__name__)


def begin_login(idp_id: int, tenant_id: int, redirect_uri: str) -> OIDCLoginStart:
    """Record a new login attempt and return the secrets the caller needs.

    Args:
        idp_id: The IdP configuration this login is for.
        tenant_id: The already-resolved (never NULL) tenant the session will
            belong to -- fixed here so the callback cannot be steered elsewhere.
        redirect_uri: The redirect URI sent to the IdP; the callback must match.

    Returns:
        The ``state``/``nonce``/PKCE challenge for the authorization URL and
        the ``binding`` value for the browser cookie.

    Raises:
        OIDCLoginError: ``oidc_state_store_unavailable`` (503).
    """
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    binding = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    record = json.dumps(
        {
            "idp_id": idp_id,
            "tenant_id": tenant_id,
            "redirect_uri": redirect_uri,
            "nonce": nonce,
            "code_verifier": verifier,
            "binding_sha256": _digest(binding),
        }
    )
    client = _client()
    try:
        client.set(_key(state), record, ex=STATE_TTL_SECONDS, nx=True)
    except (RedisError, OSError) as exc:
        _drop_client(exc)
        raise _unavailable() from exc
    return OIDCLoginStart(
        state=state,
        nonce=nonce,
        code_challenge=_challenge(verifier),
        binding=binding,
    )


def consume_login(state: str | None, binding: str | None) -> OIDCLoginRecord:
    """Validate and burn a login ``state`` presented at the callback.

    The record is deleted whether or not the binding matches, so a wrong cookie
    cannot be used to probe a state repeatedly; the legitimate user restarts.

    Args:
        state: The ``state`` query parameter from the IdP redirect.
        binding: The browser's ``elder_oidc_binding`` cookie value.

    Returns:
        The context recorded when the login began.

    Raises:
        OIDCLoginError: ``oidc_state_missing``/``oidc_binding_missing`` (400),
            ``oidc_state_invalid`` (unknown/expired/replayed, 400),
            ``oidc_binding_mismatch`` (400), or 503 if Redis is unavailable.
    """
    if not state:
        raise OIDCLoginError("oidc_state_missing", 400, "state is required")
    if not binding:
        raise OIDCLoginError(
            "oidc_binding_missing",
            400,
            "Login must be completed in the browser that started it",
        )
    if len(state) > _MAX_TOKEN_CHARS or len(binding) > _MAX_TOKEN_CHARS:
        raise OIDCLoginError("oidc_state_invalid", 400, "Login state is invalid")

    client = _client()
    key = _key(state)
    try:
        pipe = client.pipeline(transaction=True)
        pipe.get(key)
        pipe.delete(key)
        raw, _deleted = pipe.execute()
    except (RedisError, OSError) as exc:
        # Any store failure mid-consume is "unavailable" (503), never a 400 that
        # would tell the user their (still valid) login state was bad.
        _drop_client(exc)
        raise _unavailable() from exc
    if raw is None:
        raise OIDCLoginError("oidc_state_invalid", 400, "Login state is invalid")

    try:
        data = json.loads(raw)
        stored_binding = str(data["binding_sha256"])
        record = OIDCLoginRecord(
            idp_id=int(data["idp_id"]),
            tenant_id=int(data["tenant_id"]),
            redirect_uri=str(data["redirect_uri"]),
            nonce=str(data["nonce"]),
            code_verifier=str(data["code_verifier"]),
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise OIDCLoginError(
            "oidc_state_invalid", 400, "Login state is invalid"
        ) from exc
    if not hmac.compare_digest(_digest(binding), stored_binding):
        raise OIDCLoginError(
            "oidc_binding_mismatch",
            400,
            "Login must be completed in the browser that started it",
        )
    return record
