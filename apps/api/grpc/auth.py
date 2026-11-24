"""Caller authentication for the Elder gRPC server.

Every ElderService RPC except the token-issuing ones and the health check
must carry a short-lived signed JWT in the ``authorization`` metadata
(``Bearer <token>``). The token is verified with the same code the REST API
uses (``apps.api.auth.jwt_handler.verify_token``) and must additionally
carry ``iss``, ``aud``, ``exp`` and ``tenant`` claims. The verified caller is
exposed to the servicer through a context variable so every RPC can scope
its queries to the caller's tenant; nothing in the request message is ever
trusted for tenancy.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import grpc
import structlog

from apps.api.auth.jwt_handler import (
    SERVICE_WORKER_ALLOWED_SCOPES,
    WORKER_TOKEN_PREFIX,
    verify_token,
)

logger = structlog.get_logger(__name__)

SERVICE_PREFIX = "/elder.ElderService/"

# RPCs reachable without a bearer token: the two that issue tokens (they
# authenticate with a password or refresh token in the request body) and the
# liveness probe, which returns no data.
PUBLIC_METHODS: frozenset[str] = frozenset(
    {
        f"{SERVICE_PREFIX}Login",
        f"{SERVICE_PREFIX}RefreshToken",
        f"{SERVICE_PREFIX}HealthCheck",
    }
)

# Token types accepted as gRPC credentials: interactive identity access
# tokens and short-lived service machine JWTs. Refresh tokens, portal web
# session tokens and opaque remote-worker tokens are rejected.
ACCEPTED_TOKEN_TYPES: frozenset[str] = frozenset({"access", "service_worker"})

SYSTEM_TENANT = "system"


@dataclass(slots=True, frozen=True)
class GrpcCaller:
    """Verified identity of the peer making a gRPC call.

    ``tenant_id`` is ``None`` only for platform service identities issued
    for the ``system`` tenant; every tenant-owned RPC treats that as "no
    tenant" and denies the call rather than widening its scope.
    """

    subject: str
    token_type: str
    tenant_id: int | None
    scopes: frozenset[str]
    is_superuser: bool = False

    @property
    def is_system(self) -> bool:
        """Return True for a platform identity bound to the system tenant."""
        return self.tenant_id is None

    def has_any_scope(self, accepted: Iterable[str]) -> bool:
        """Return True when the caller holds at least one accepted scope.

        Superusers bypass scope checks, matching the REST ``require_scope``
        decorator; tenant isolation still applies to them.
        """
        if self.is_superuser:
            return True
        return any(scope in self.scopes for scope in accepted)


_current_caller: ContextVar[GrpcCaller | None] = ContextVar(
    "elder_grpc_caller", default=None
)


def current_caller() -> GrpcCaller | None:
    """Return the caller verified for the RPC executing on this thread."""
    return _current_caller.get()


def run_in_app_context(app: Any, fn: Callable[..., Any], *args: Any) -> Any:
    """Call ``fn`` inside the Quart application context.

    The shared JWT helpers read ``current_app.config``; gRPC handlers run on
    plain worker threads with no event loop, so a short-lived loop pushes the
    (async) Quart app context around the synchronous call.
    """

    async def _call() -> Any:
        async with app.app_context():
            return fn(*args)

    return asyncio.run(_call())


def extract_bearer_token(metadata: Iterable[tuple[str, Any]] | None) -> str | None:
    """Return the bearer token from gRPC invocation metadata, if well formed.

    A malformed ``authorization`` value is treated as absent so it fails
    authentication instead of being partially trusted.
    """
    for key, value in metadata or ():
        if key.lower() != "authorization":
            continue
        if isinstance(value, bytes):
            try:
                value = value.decode("ascii")
            except UnicodeDecodeError:
                return None
        parts = str(value).split()
        if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1]:
            return parts[1]
        return None
    return None


def _positive_int(raw: Any) -> int | None:
    """Parse a strictly positive integer claim, rejecting bools and junk."""
    if isinstance(raw, bool) or raw is None:
        return None
    text = str(raw).strip()
    if not (text.isascii() and text.isdigit()):
        return None
    value = int(text)
    return value if value > 0 else None


def _scope_set(raw: Any) -> frozenset[str]:
    """Normalize a ``scope`` claim (list or space-separated string)."""
    if isinstance(raw, str):
        items: list[Any] = raw.split()
    elif isinstance(raw, list):
        items = raw
    else:
        return frozenset()
    return frozenset(s.strip() for s in items if isinstance(s, str) and s.strip())


def resolve_caller(app: Any, token: str | None) -> GrpcCaller | None:
    """Verify ``token`` and build the caller it represents, or return None.

    Signature, expiry and any present issuer/audience are checked by the
    shared REST verifier. On top of that the gRPC surface requires the
    ``iss``/``aud``/``exp``/``tenant`` claims to be present, accepts only
    access and service-worker tokens, and re-reads identity tokens from the
    database so a disabled or re-tenanted identity is refused immediately.
    """
    if not token or token.startswith(WORKER_TOKEN_PREFIX):
        return None

    payload = run_in_app_context(app, verify_token, token)
    if not payload:
        return None

    config = app.config
    if payload.get("iss") != config.get("JWT_ISSUER", "elder-api"):
        return None
    if payload.get("aud") != config.get("JWT_AUDIENCE", "elder-api"):
        return None
    exp = payload.get("exp")
    if isinstance(exp, bool) or not isinstance(exp, int | float):
        return None

    # The claim must be explicit: an untyped token is never assumed to be an
    # access token.
    token_type = payload.get("type")
    if token_type not in ACCEPTED_TOKEN_TYPES:
        return None

    scopes = _scope_set(payload.get("scope"))

    if token_type == "service_worker":
        sub = payload.get("sub")
        allowed = (
            SERVICE_WORKER_ALLOWED_SCOPES.get(sub) if isinstance(sub, str) else None
        )
        if not allowed:
            return None
        scopes = frozenset(s for s in scopes if s in allowed)
        tenant_raw = payload.get("tenant")
        if tenant_raw == SYSTEM_TENANT:
            tenant_id = None
        else:
            tenant_id = _positive_int(tenant_raw)
            if tenant_id is None:
                return None
        if not scopes:
            return None
        return GrpcCaller(
            subject=sub,
            token_type=token_type,
            tenant_id=tenant_id,
            scopes=scopes,
        )

    claim_tenant = _positive_int(payload.get("tenant"))
    identity_id = _positive_int(payload.get("sub"))
    if claim_tenant is None or identity_id is None:
        return None

    db = app.db
    identity = db(db.identities.id == identity_id).select().first()
    if not identity or not identity.is_active:
        return None
    if _positive_int(getattr(identity, "tenant_id", None)) != claim_tenant:
        return None

    return GrpcCaller(
        subject=str(identity_id),
        token_type=token_type,
        tenant_id=claim_tenant,
        scopes=scopes,
        is_superuser=bool(identity.is_superuser),
    )


def _deny_handler(code: grpc.StatusCode, details: str) -> grpc.RpcMethodHandler:
    """Build a unary handler that rejects the call with ``code``."""

    def _deny(request: Any, context: grpc.ServicerContext) -> Any:
        context.abort(code, details)

    return grpc.unary_unary_rpc_method_handler(_deny)


class GrpcAuthInterceptor(grpc.ServerInterceptor):
    """Reject unauthenticated ElderService calls before they reach the servicer.

    Public methods pass through untouched; every other ElderService method
    must present a valid bearer token, and the verified caller is bound to a
    context variable for the duration of the call.
    """

    def __init__(self, app: Any) -> None:
        """Keep the application whose config and database verify tokens."""
        self._app = app

    def intercept_service(
        self,
        continuation: Callable[[grpc.HandlerCallDetails], grpc.RpcMethodHandler],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler | None:
        """Wrap protected unary handlers with bearer-token verification."""
        handler = continuation(handler_call_details)
        method = handler_call_details.method or ""
        if handler is None or not method.startswith(SERVICE_PREFIX):
            return handler
        if method in PUBLIC_METHODS:
            return handler
        if handler.unary_unary is None:
            return _deny_handler(
                grpc.StatusCode.UNIMPLEMENTED, "streaming RPCs are not supported"
            )

        inner = handler.unary_unary
        app = self._app

        def _authenticated(request: Any, context: grpc.ServicerContext) -> Any:
            token = extract_bearer_token(context.invocation_metadata())
            try:
                caller = resolve_caller(app, token)
            except Exception:  # noqa: BLE001 - any verifier failure is a deny
                logger.warning("grpc_auth_verification_error", method=method)
                caller = None
            if caller is None:
                logger.info("grpc_auth_rejected", method=method)
                context.abort(
                    grpc.StatusCode.UNAUTHENTICATED,
                    "valid bearer token required",
                )
            reset_token = _current_caller.set(caller)
            try:
                return inner(request, context)
            finally:
                _current_caller.reset(reset_token)

        return grpc.unary_unary_rpc_method_handler(
            _authenticated,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )
