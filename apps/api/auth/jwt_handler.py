"""JWT token handling for Elder authentication using PyDAL."""

# flake8: noqa: E501

import hashlib
import logging
from collections.abc import Collection
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from penguin_dal import Row
from quart import current_app, g, request
from werkzeug.security import check_password_hash

logger = logging.getLogger(__name__)

MAX_SERVICE_RESEARCHER_TTL_SECONDS = 600
MAX_SERVICE_WORKER_TTL_SECONDS = 600
MAX_CODERUN_TTL_SECONDS = 360

SERVICE_WORKER_ALLOWED_SCOPES: dict[str, frozenset[str]] = {
    "svc-scanner": frozenset(
        {
            "discovery:read",
            "discovery:write",
            "sbom:read",
            "sbom:write",
            "vulnerabilities:write",
        }
    ),
    "svc-integrations": frozenset(
        {
            "discovery:read",
            "discovery:write",
            "entities:read",
            "entities:write",
            "organizations:read",
            "organizations:write",
            "dependencies:read",
            "dependencies:write",
            "identities:read",
            "identities:write",
            "issues:read",
            "issues:write",
            "projects:read",
            "projects:write",
            "integrations:sync",
        }
    ),
    "svc-runworker": frozenset(
        {
            "runs:read",
            "runs:write",
            "runs:invoke",
        }
    ),
}

WORKER_TOKEN_PREFIX = "ewk_"

# Token-type allow-lists, one per verification context. Every verifier names
# the exact `type` values it accepts; a missing or unrecognised type is never
# defaulted to an identity access token.
IDENTITY_ACCESS_TOKEN_TYPES: frozenset[str] = frozenset({"access"})
IDENTITY_REFRESH_TOKEN_TYPES: frozenset[str] = frozenset({"refresh"})
PORTAL_ACCESS_TOKEN_TYPES: frozenset[str] = frozenset({"portal_user"})
PORTAL_REFRESH_TOKEN_TYPES: frozenset[str] = frozenset({"portal_refresh"})
SERVICE_WORKER_TOKEN_TYPES: frozenset[str] = frozenset({"service_worker"})
CODERUN_TOKEN_TYPES: frozenset[str] = frozenset({"coderun"})
SERVICE_RESEARCHER_TOKEN_TYPES: frozenset[str] = frozenset({"service_researcher"})
# Types accepted as an API credential (Authorization bearer / access cookie).
# Refresh tokens are deliberately absent: they are only valid at their
# refresh endpoint.
API_BEARER_TOKEN_TYPES: frozenset[str] = (
    IDENTITY_ACCESS_TOKEN_TYPES
    | PORTAL_ACCESS_TOKEN_TYPES
    | SERVICE_WORKER_TOKEN_TYPES
    | CODERUN_TOKEN_TYPES
    | SERVICE_RESEARCHER_TOKEN_TYPES
)

PRINCIPAL_IDENTITY = "identity"
PRINCIPAL_PORTAL_USER = "portal_user"
PRINCIPAL_SERVICE = "service"

# Principal kind each token type is bound to. The user loader resolves `sub`
# only in the table matching this kind.
TOKEN_TYPE_PRINCIPAL_KIND: dict[str, str] = {
    "access": PRINCIPAL_IDENTITY,
    "refresh": PRINCIPAL_IDENTITY,
    "portal_user": PRINCIPAL_PORTAL_USER,
    "portal_refresh": PRINCIPAL_PORTAL_USER,
    "service_worker": PRINCIPAL_SERVICE,
    "coderun": PRINCIPAL_SERVICE,
    "service_researcher": PRINCIPAL_SERVICE,
}


def token_principal_kind(payload: dict[str, Any]) -> str | None:
    """Return the principal kind a decoded token is bound to, or None.

    Derived strictly from the token `type`. When the token also carries an
    explicit `principal_kind` claim it must agree with the derived kind;
    any ambiguity (unknown type, mismatched claim) yields None (reject).
    """
    token_type = payload.get("type")
    if not isinstance(token_type, str):
        return None
    derived = TOKEN_TYPE_PRINCIPAL_KIND.get(token_type)
    if derived is None:
        return None
    explicit = payload.get("principal_kind")
    if explicit is not None and explicit != derived:
        return None
    return derived


WORKER_TYPE_TO_SERVICE_SUB: dict[str, str] = {
    "runworker": "svc-runworker",
    "scanner": "svc-scanner",
}


def hash_worker_token(token: str) -> str:
    """Return the lowercase SHA-256 hex digest of a provisioned worker token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def resolve_remote_worker_token(token: str) -> dict[str, Any] | None:
    """Resolve a provisioned ``ewk_`` Bearer token into ``service_worker`` claims.

    Looks up the SHA-256 hash in ``remote_workers`` and rejects unknown,
    disabled, or revoked workers without ever logging token material.
    """
    if not isinstance(token, str) or not token.startswith(WORKER_TOKEN_PREFIX):
        return None
    db = getattr(current_app, "db", None)
    table = getattr(db, "remote_workers", None) if db is not None else None
    if table is None:
        return None
    try:
        row = db(table.token_hash == hash_worker_token(token)).select().first()
    except Exception:
        logger.warning("Failed to look up remote worker token")
        try:
            db.rollback()
        except Exception:
            pass
        return None
    if row is None:
        logger.warning("Rejected remote worker token: unknown token")
        return None
    if getattr(row, "revoked_at", None) is not None or not getattr(
        row, "is_enabled", False
    ):
        logger.warning("Rejected remote worker token: worker revoked or disabled")
        return None
    worker_type = getattr(row, "worker_type", None)
    sub = WORKER_TYPE_TO_SERVICE_SUB.get(worker_type)
    if not sub:
        logger.warning("Rejected remote worker token: unsupported worker_type")
        return None
    allowed = SERVICE_WORKER_ALLOWED_SCOPES.get(sub)
    if not allowed:
        return None
    raw_tenant = getattr(row, "tenant_id", None)
    tenant_str = "system" if raw_tenant is None else str(int(raw_tenant))
    return {
        "sub": sub,
        "type": "service_worker",
        "tenant": tenant_str,
        "roles": ["service_worker"],
        "scope": sorted(allowed),
        "remote_worker_id": getattr(row, "worker_id", None),
        "remote_worker_db_id": getattr(row, "id", None),
        "remote_worker_type": worker_type,
        "remote_worker_cluster": getattr(row, "cluster", None),
    }


class ServiceWorkerIdentity:
    """Synthetic identity representing an authenticated machine service worker."""

    def __init__(
        self,
        worker_name: str,
        tid: int | None,
        scopes: list[str],
        *,
        remote_worker_id: str | None = None,
    ) -> None:
        self.id = 0
        self.username = worker_name
        self.email = None
        self.display_name = f"Service Worker ({worker_name})"
        self.is_active = True
        self.is_superuser = False
        self.tenant_id = tid
        self.portal_role = "service_worker"
        self.token_type = "service_worker"
        self.scopes = scopes
        self.remote_worker_id = remote_worker_id

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


class CodeRunIdentity:
    """Synthetic identity representing an ephemeral CodeRun function execution."""

    def __init__(
        self,
        tid: int,
        function_id: int,
        execution_id: str,
        scopes: list[str],
    ) -> None:
        self.id = 0
        self.username = f"coderun:{tid}:{function_id}"
        self.email = None
        self.display_name = f"CodeRun Function #{function_id}"
        self.is_active = True
        self.is_superuser = False
        self.tenant_id = tid
        self.function_id = function_id
        self.execution_id = execution_id
        self.portal_role = "coderun"
        self.token_type = "coderun"
        self.scopes = list(scopes)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


def is_system_service_worker() -> bool:
    """Return True if the active request is authenticated as a system-tenant service_worker."""
    claims = getattr(g, "claims", {}) or {}
    if claims.get("type") != "service_worker" or claims.get("tenant") != "system":
        return False
    user = getattr(g, "current_user", None)
    return bool(
        user is not None
        and getattr(user, "token_type", None) == "service_worker"
        and getattr(user, "tenant_id", None) is None
        and getattr(user, "username", None) in SERVICE_WORKER_ALLOWED_SCOPES
    )


def _validate_service_worker_claims(
    payload: dict[str, Any], expected_issuer: str, expected_audience: str
) -> dict[str, Any] | None:
    """Validate and normalize claims for a ``service_worker`` machine JWT."""
    if payload.get("iss") != expected_issuer or payload.get("aud") != expected_audience:
        logger.warning("Rejected service_worker token: missing or invalid iss/aud")
        return None

    exp_raw = payload.get("exp")
    iat_raw = payload.get("iat")
    if (
        isinstance(exp_raw, bool)
        or not isinstance(exp_raw, int | float)
        or isinstance(iat_raw, bool)
        or not isinstance(iat_raw, int | float)
    ):
        logger.warning("Rejected service_worker token: missing or invalid exp/iat")
        return None

    now_ts = datetime.now(UTC).timestamp()
    lifetime = float(exp_raw) - float(iat_raw)
    if lifetime <= 0 or lifetime > MAX_SERVICE_WORKER_TTL_SECONDS:
        logger.warning("Rejected service_worker token: invalid issued lifetime")
        return None
    if float(exp_raw) - now_ts > MAX_SERVICE_WORKER_TTL_SECONDS:
        logger.warning("Rejected service_worker token: TTL exceeds 10 minutes")
        return None

    sub = payload.get("sub")
    if not isinstance(sub, str) or sub not in SERVICE_WORKER_ALLOWED_SCOPES:
        logger.warning("Rejected service_worker token: invalid sub")
        return None
    allowed_scopes = SERVICE_WORKER_ALLOWED_SCOPES[sub]

    raw_scopes = payload.get("scope")
    if isinstance(raw_scopes, str):
        candidate_scopes: list[Any] = raw_scopes.split()
    elif isinstance(raw_scopes, list):
        candidate_scopes = raw_scopes
    else:
        logger.warning("Rejected service_worker token: invalid scope claim")
        return None

    validated_scopes: list[str] = []
    for scope in candidate_scopes:
        if not isinstance(scope, str):
            logger.warning("Rejected service_worker token: non-string scope")
            return None
        cleaned = scope.strip()
        if not cleaned or cleaned not in allowed_scopes:
            logger.warning("Rejected service_worker token: disallowed scope")
            return None
        if cleaned not in validated_scopes:
            validated_scopes.append(cleaned)
    if not validated_scopes:
        logger.warning("Rejected service_worker token: empty scope claim")
        return None

    tenant_raw = payload.get("tenant")
    if isinstance(tenant_raw, bool) or not isinstance(tenant_raw, str):
        logger.warning("Rejected service_worker token: missing or invalid tenant")
        return None
    tenant_clean = tenant_raw.strip()
    if tenant_clean == "system":
        normalized_tenant = "system"
    elif tenant_clean.isascii() and tenant_clean.isdigit() and int(tenant_clean) > 0:
        normalized_tenant = str(int(tenant_clean))
    else:
        logger.warning("Rejected service_worker token: invalid tenant value")
        return None

    payload["tenant"] = normalized_tenant
    payload["roles"] = ["service_worker"]
    payload["scope"] = validated_scopes
    return payload


def _validate_coderun_claims(
    payload: dict[str, Any], expected_issuer: str, expected_audience: str
) -> dict[str, Any] | None:
    """Validate and normalize claims for an ephemeral ``coderun`` execution JWT."""
    from apps.api.modules.runs.permissions import ALL_GRANTABLE_SCOPES

    if payload.get("iss") != expected_issuer or payload.get("aud") != expected_audience:
        logger.warning("Rejected coderun token: missing or invalid iss/aud")
        return None

    exp_raw = payload.get("exp")
    iat_raw = payload.get("iat")
    if (
        isinstance(exp_raw, bool)
        or not isinstance(exp_raw, int | float)
        or isinstance(iat_raw, bool)
        or not isinstance(iat_raw, int | float)
    ):
        logger.warning("Rejected coderun token: missing or invalid exp/iat")
        return None

    now_ts = datetime.now(UTC).timestamp()
    lifetime = float(exp_raw) - float(iat_raw)
    if lifetime <= 0 or lifetime > MAX_CODERUN_TTL_SECONDS:
        logger.warning("Rejected coderun token: invalid issued lifetime")
        return None
    if float(exp_raw) - now_ts > MAX_CODERUN_TTL_SECONDS:
        logger.warning("Rejected coderun token: TTL exceeds max coderun TTL")
        return None

    tenant_raw = payload.get("tenant")
    if isinstance(tenant_raw, bool) or tenant_raw is None:
        logger.warning("Rejected coderun token: missing tenant")
        return None
    tenant_str = str(tenant_raw).strip()
    if not (tenant_str.isascii() and tenant_str.isdigit() and int(tenant_str) > 0):
        logger.warning("Rejected coderun token: non-positive or invalid tenant")
        return None
    tenant_id = int(tenant_str)

    function_id_raw = payload.get("function_id")
    if (
        isinstance(function_id_raw, bool)
        or not isinstance(function_id_raw, int)
        or function_id_raw <= 0
    ):
        logger.warning("Rejected coderun token: invalid function_id")
        return None

    execution_id_raw = payload.get("execution_id")
    if not isinstance(execution_id_raw, str) or not execution_id_raw.strip():
        logger.warning("Rejected coderun token: missing or invalid execution_id")
        return None

    raw_scopes = payload.get("scope")
    if isinstance(raw_scopes, str):
        candidate_scopes: list[Any] = raw_scopes.split()
    elif isinstance(raw_scopes, list):
        candidate_scopes = raw_scopes
    else:
        logger.warning("Rejected coderun token: invalid scope claim")
        return None

    validated_scopes: list[str] = []
    for scope in candidate_scopes:
        if not isinstance(scope, str):
            logger.warning("Rejected coderun token: non-string scope")
            return None
        cleaned = scope.strip()
        if not cleaned or cleaned not in ALL_GRANTABLE_SCOPES:
            logger.warning("Rejected coderun token: disallowed scope")
            return None
        if cleaned not in validated_scopes:
            validated_scopes.append(cleaned)
    if not validated_scopes:
        logger.warning("Rejected coderun token: empty scope claim")
        return None

    payload["tenant"] = str(tenant_id)
    payload["function_id"] = function_id_raw
    payload["execution_id"] = execution_id_raw.strip()
    payload["roles"] = ["coderun"]
    payload["scope"] = validated_scopes
    return payload


def generate_coderun_token(
    tenant_id: int,
    function_id: int,
    execution_id: str,
    scopes: list[str] | tuple[str, ...],
    ttl_seconds: int = MAX_CODERUN_TTL_SECONDS,
) -> str:
    """Mint an ephemeral, tenant-locked, scope-restricted ``coderun`` JWT."""
    from apps.api.modules.runs.permissions import ALL_GRANTABLE_SCOPES

    if isinstance(tenant_id, bool) or not isinstance(tenant_id, int) or tenant_id <= 0:
        raise ValueError("tenant_id must be a positive integer")
    if (
        isinstance(function_id, bool)
        or not isinstance(function_id, int)
        or function_id <= 0
    ):
        raise ValueError("function_id must be a positive integer")
    if (
        isinstance(execution_id, bool)
        or not isinstance(execution_id, str)
        or not execution_id.strip()
    ):
        raise ValueError("execution_id must be a non-empty string")
    if (
        isinstance(ttl_seconds, bool)
        or not isinstance(ttl_seconds, int)
        or ttl_seconds <= 0
        or ttl_seconds > MAX_CODERUN_TTL_SECONDS
    ):
        raise ValueError(
            f"coderun token TTL must be between 1 and {MAX_CODERUN_TTL_SECONDS} seconds"
        )
    if scopes is None or isinstance(scopes, (str, bytes)):
        raise ValueError("scopes must be a non-empty sequence of grantable scopes")

    validated_scopes: list[str] = []
    seen: set[str] = set()
    for s in scopes:
        if not isinstance(s, str) or not s.strip():
            raise ValueError(f"Invalid coderun scope: {s!r}")
        cleaned = s.strip()
        if cleaned not in ALL_GRANTABLE_SCOPES:
            raise ValueError(f"Scope is not grantable to CodeRun: {cleaned!r}")
        if cleaned not in seen:
            seen.add(cleaned)
            validated_scopes.append(cleaned)
    if not validated_scopes:
        raise ValueError("At least one grantable scope is required")

    exec_id_clean = execution_id.strip()
    now = datetime.now(UTC)
    payload = {
        "sub": f"coderun:{tenant_id}:{function_id}:{exec_id_clean}",
        "type": "coderun",
        "iat": now,
        "exp": now + timedelta(seconds=ttl_seconds),
        "tenant": str(tenant_id),
        "function_id": function_id,
        "execution_id": exec_id_clean,
        "roles": ["coderun"],
        "scope": validated_scopes,
        "iss": current_app.config.get("JWT_ISSUER", "elder-api"),
        "aud": current_app.config.get("JWT_AUDIENCE", "elder-api"),
    }

    secret = current_app.config["JWT_SECRET_KEY"] or current_app.config["SECRET_KEY"]
    algorithm = current_app.config["JWT_ALGORITHM"]
    return jwt.encode(payload, secret, algorithm=algorithm)


def _is_valid_read_scope(scope: Any) -> bool:
    """Return True if *scope* is a strictly read-only ``<resource>:read`` claim."""
    if not isinstance(scope, str):
        return False
    cleaned = scope.strip()
    if ":" not in cleaned:
        return False
    resource, action = cleaned.split(":", 1)
    return bool(resource) and resource != "*" and action == "read"


def get_service_researcher_read_scopes() -> list[str]:
    """Return the deduplicated, sorted catalog of strictly read-only scopes."""
    from apps.api.auth.rbac import _ADMIN_SCOPES, _EDITOR_SCOPES, _OBSERVER_SCOPES
    from apps.api.modules import MODULES

    scopes: set[str] = {
        s
        for s in (*_OBSERVER_SCOPES, *_EDITOR_SCOPES, *_ADMIN_SCOPES)
        if _is_valid_read_scope(s)
    }
    for manifest in MODULES:
        for scope in manifest.scopes:
            if _is_valid_read_scope(scope):
                scopes.add(scope)
    return sorted(scopes)


def generate_service_researcher_token(
    tenant_id: int,
    scopes: list[str] | tuple[str, ...],
    ttl_seconds: int = 300,
    *,
    caller_sub: str,
    caller_username: str | None = None,
) -> str:
    """Mint a short-lived, strictly read-only ``service_researcher`` JWT.

    DEPRECATED: the AI Researcher no longer uses this; it forwards the caller's own
    token to hub-mcp. Retained only so previously issued tokens still validate; no
    production code path mints one.

    Args:
        tenant_id: Positive tenant ID to bind this token to.
        scopes: Required non-empty sequence of ``<resource>:read`` scopes
            intersected with the calling user's own permissions.
        ttl_seconds: Lifetime in seconds (1..600, max 10 minutes).
        caller_sub: Authenticated caller subject delegating this token.
        caller_username: Optional authenticated caller username for audit trails.

    Returns:
        Signed JWT string.

    Raises:
        ValueError: If tenant_id, ttl_seconds, caller_sub, or any scope is invalid.
    """
    if isinstance(tenant_id, bool) or not isinstance(tenant_id, int) or tenant_id <= 0:
        raise ValueError("tenant_id must be a positive integer")
    if (
        isinstance(ttl_seconds, bool)
        or not isinstance(ttl_seconds, int)
        or ttl_seconds <= 0
        or ttl_seconds > MAX_SERVICE_RESEARCHER_TTL_SECONDS
    ):
        raise ValueError(
            f"service_researcher token TTL must be between 1 and {MAX_SERVICE_RESEARCHER_TTL_SECONDS} seconds"
        )
    if isinstance(caller_sub, bool) or caller_sub is None:
        raise ValueError("caller_sub must be a non-empty string")
    caller_sub_clean = str(caller_sub).strip()
    if not caller_sub_clean:
        raise ValueError("caller_sub must be a non-empty string")

    if scopes is None or isinstance(scopes, (str, bytes)):
        raise ValueError("scopes must be a non-empty sequence of read-only scopes")
    raw_validated: list[str] = []
    for s in scopes:
        if not _is_valid_read_scope(s):
            raise ValueError(
                f"service_researcher tokens only allow '<resource>:read' scopes, got: {s!r}"
            )
        raw_validated.append(s.strip())
    validated_scopes = sorted(set(raw_validated))
    if not validated_scopes:
        raise ValueError("At least one read-only scope is required")

    act_claim: dict[str, str] = {"sub": caller_sub_clean}
    if isinstance(caller_username, str) and caller_username.strip():
        act_claim["username"] = caller_username.strip()

    now = datetime.now(UTC)
    payload = {
        "sub": f"service-researcher:{tenant_id}:{caller_sub_clean}",
        "delegated_by": caller_sub_clean,
        "act": act_claim,
        "username": f"service-researcher-{tenant_id}",
        "type": "service_researcher",
        "iat": now,
        "exp": now + timedelta(seconds=ttl_seconds),
        "tenant": str(tenant_id),
        "roles": ["viewer"],
        "scope": validated_scopes,
        "iss": current_app.config.get("JWT_ISSUER", "elder-api"),
        "aud": current_app.config.get("JWT_AUDIENCE", "elder-api"),
    }

    secret = current_app.config["JWT_SECRET_KEY"] or current_app.config["SECRET_KEY"]
    algorithm = current_app.config["JWT_ALGORITHM"]
    return jwt.encode(payload, secret, algorithm=algorithm)


def generate_token(identity: Row, token_type: str = "access") -> str:
    """
    Generate JWT token for an identity.

    Args:
        identity: Identity model instance
        token_type: 'access' or 'refresh'

    Returns:
        JWT token string
    """
    if token_type == "access":
        expires_delta = current_app.config["JWT_ACCESS_TOKEN_EXPIRES"]
    else:
        expires_delta = current_app.config["JWT_REFRESH_TOKEN_EXPIRES"]

    from apps.api.auth.rbac import role_scopes

    portal_role = getattr(identity, "portal_role", "observer") or "observer"
    tenant = str(getattr(identity, "tenant_id", "") or "")

    now = datetime.now(UTC)
    payload = {
        "sub": str(identity.id),
        "username": identity.username,
        "type": token_type,
        "principal_kind": PRINCIPAL_IDENTITY,
        "iat": now,
        "exp": now + expires_delta,
        # penguin-aaa compatible claims — enables RBAC and tenant middleware
        "tenant": tenant,
        "roles": [portal_role],
        "scope": role_scopes(portal_role) if token_type == "access" else [],
        # iss/aud -- verify_token() rejects a token whose issuer/audience is
        # present but doesn't match (forged/wrong-service token).
        "iss": current_app.config.get("JWT_ISSUER", "elder-api"),
        "aud": current_app.config.get("JWT_AUDIENCE", "elder-api"),
    }

    secret = current_app.config["JWT_SECRET_KEY"] or current_app.config["SECRET_KEY"]
    algorithm = current_app.config["JWT_ALGORITHM"]

    token = jwt.encode(payload, secret, algorithm=algorithm)
    return token


def verify_token(
    token: str, expected_types: Collection[str] = API_BEARER_TOKEN_TYPES
) -> dict[str, Any] | None:
    """
    Verify and decode JWT token.

    Only tokens whose `type` is in ``expected_types`` are accepted; the
    default is the API-bearer allow-list, so refresh tokens and any missing
    or unrecognised type are rejected unless a caller explicitly asks for
    them (e.g. the refresh endpoint passes ``IDENTITY_REFRESH_TOKEN_TYPES``).
    The token must also bind unambiguously to a principal kind.

    Rejects a token whose `iss`/`aud` claim is PRESENT but does not match
    this service's configured `JWT_ISSUER`/`JWT_AUDIENCE` -- a mismatch
    means the token was not issued by this service (or was tampered with)
    and must never be trusted, regardless of a valid signature.

    Claims are not hard-required yet: several other token-issuing paths in
    this codebase (portal_auth's `generate_jwt_claims`, the gRPC servicer,
    and multiple test fixtures owned by other in-flight work) do not yet
    stamp `iss`/`aud`, and this function is the single shared verifier used
    by both API-key/identity tokens and portal-user tokens. Hard-rejecting
    "missing" here today would break portal login and every one of those
    call sites in one step; PyJWT's `issuer=`/`audience=` decode params are
    deliberately NOT used for that reason. Follow-up: add matching `iss`/
    `aud` to those other issuers, then tighten this to reject missing too.

    Args:
        token: JWT token string

    Returns:
        Decoded payload dict or None if invalid
    """
    if isinstance(token, str) and token.startswith(WORKER_TOKEN_PREFIX):
        return _enforce_token_type(resolve_remote_worker_token(token), expected_types)

    try:
        secret = (
            current_app.config["JWT_SECRET_KEY"] or current_app.config["SECRET_KEY"]
        )
        algorithm = current_app.config.get("JWT_ALGORITHM", "HS256")

        # Never log the signing secret or token contents, at any level --
        # both are credential material (gh security review finding #4).
        #
        # verify_aud disabled: PyJWT auto-rejects ANY token carrying an
        # `aud` claim unless the caller also passes `audience=` -- which
        # would force strict enforcement for every token, undoing the
        # graceful "validate only if present" behavior documented above.
        # The manual checks below do the real validation instead.
        payload = jwt.decode(
            token, secret, algorithms=[algorithm], options={"verify_aud": False}
        )

        expected_issuer = current_app.config.get("JWT_ISSUER", "elder-api")
        token_issuer = payload.get("iss")
        if token_issuer is not None and token_issuer != expected_issuer:
            logger.warning("Rejected token: issuer mismatch")
            return None

        expected_audience = current_app.config.get("JWT_AUDIENCE", "elder-api")
        token_audience = payload.get("aud")
        if token_audience is not None and token_audience != expected_audience:
            logger.warning("Rejected token: audience mismatch")
            return None

        if _enforce_token_type(payload, expected_types) is None:
            return None

        if payload.get("type") == "service_researcher":
            if token_issuer != expected_issuer or token_audience != expected_audience:
                logger.warning(
                    "Rejected service_researcher token: missing or invalid iss/aud"
                )
                return None

            exp_raw = payload.get("exp")
            if isinstance(exp_raw, bool) or not isinstance(exp_raw, int | float):
                logger.warning(
                    "Rejected service_researcher token: missing or invalid exp"
                )
                return None
            now_ts = datetime.now(UTC).timestamp()
            if float(exp_raw) - now_ts > MAX_SERVICE_RESEARCHER_TTL_SECONDS:
                logger.warning(
                    "Rejected service_researcher token: TTL exceeds 10 minutes"
                )
                return None
            iat_raw = payload.get("iat")
            if iat_raw is not None:
                if isinstance(iat_raw, bool) or not isinstance(iat_raw, int | float):
                    logger.warning("Rejected service_researcher token: invalid iat")
                    return None
                if float(exp_raw) - float(iat_raw) > MAX_SERVICE_RESEARCHER_TTL_SECONDS:
                    logger.warning(
                        "Rejected service_researcher token: issued lifetime exceeds 10 minutes"
                    )
                    return None

            tenant_raw = payload.get("tenant")
            if isinstance(tenant_raw, bool) or tenant_raw is None:
                logger.warning("Rejected service_researcher token: missing tenant")
                return None
            try:
                tenant_id = int(str(tenant_raw).strip())
            except (TypeError, ValueError):
                logger.warning("Rejected service_researcher token: non-integer tenant")
                return None
            if tenant_id <= 0:
                logger.warning("Rejected service_researcher token: non-positive tenant")
                return None

            raw_scopes = payload.get("scope")
            if isinstance(raw_scopes, str):
                candidate_scopes = raw_scopes.split()
            elif isinstance(raw_scopes, list):
                candidate_scopes = raw_scopes
            else:
                logger.warning("Rejected service_researcher token: invalid scope claim")
                return None
            if not candidate_scopes or not all(
                _is_valid_read_scope(s) for s in candidate_scopes
            ):
                logger.warning(
                    "Rejected service_researcher token: non-read or empty scope claim"
                )
                return None

            delegated_by = payload.get("delegated_by")
            if not isinstance(delegated_by, str) or not delegated_by.strip():
                logger.warning(
                    "Rejected service_researcher token: missing delegated_by claim"
                )
                return None

            payload["tenant"] = str(tenant_id)
            payload["roles"] = ["viewer"]
            payload["scope"] = [s.strip() for s in candidate_scopes]
            payload["delegated_by"] = delegated_by.strip()
        elif payload.get("type") == "service_worker":
            if (
                _validate_service_worker_claims(
                    payload, expected_issuer, expected_audience
                )
                is None
            ):
                return None
        elif payload.get("type") == "coderun":
            if (
                _validate_coderun_claims(payload, expected_issuer, expected_audience)
                is None
            ):
                return None

        return payload
    except jwt.ExpiredSignatureError:
        logger.warning("Token expired")
        return None
    except jwt.InvalidTokenError:
        logger.warning("Invalid token")
        return None


def _enforce_token_type(
    payload: dict[str, Any] | None, expected_types: Collection[str]
) -> dict[str, Any] | None:
    """Return ``payload`` only if its type is allowed here and its principal is unambiguous.

    Fails closed: a missing, unknown or out-of-context `type` is rejected
    rather than being treated as an identity access token.
    """
    if payload is None:
        return None
    token_type = payload.get("type")
    if not isinstance(token_type, str) or token_type not in expected_types:
        logger.warning("Rejected token: type not accepted in this context")
        return None
    if token_principal_kind(payload) is None:
        logger.warning("Rejected token: ambiguous principal kind")
        return None
    return payload


def get_token_from_header() -> str | None:
    """
    Extract the JWT, preferring the Authorization header but falling back to
    the SPA's HttpOnly access-token cookie when no header is present.

    The browser client no longer keeps the access token in localStorage (gh
    security audit, High: XSS-exfiltratable tokens) -- it relies on the
    HttpOnly cookie set by /portal-auth/login|refresh instead, so every
    caller of this function (login_required, populate_claims, etc.) needs to
    accept either credential form transparently. A malformed header still
    hard-fails rather than falling back -- that's very likely a genuine
    caller error, not a cookie-only browser request.

    Returns:
        Token string or None
    """
    auth_header = request.headers.get("Authorization")

    if auth_header:
        parts = auth_header.split()

        # Never log the header value itself -- a malformed header may still
        # be (or contain a fragment of) a real bearer token/credential.
        if len(parts) != 2 or parts[0].lower() != "bearer":
            logger.debug("Invalid Authorization header format")
            return None

        return parts[1]

    from apps.api.auth.portal_cookies import get_access_token_from_cookie

    cookie_token = get_access_token_from_cookie()
    if not cookie_token:
        logger.debug("No Authorization header or access-token cookie found")
    return cookie_token


def get_current_user() -> Any:
    """
    Get current authenticated user from request context.

    Supports identity tokens, portal_user tokens (web UI auth), and read-only
    service_researcher tokens. For synthetic users, returns an identity-like object.

    Returns:
        Identity row, synthetic identity object, or None
    """
    logger.debug("=== get_current_user called ===")

    if hasattr(g, "current_user"):
        logger.debug(f"Returning cached user: {g.current_user.username}")
        return g.current_user

    token = get_token_from_header()
    if not token:
        logger.debug("No token found in header, returning None")
        return None

    payload = verify_token(token)
    if not payload:
        logger.debug("Token verification failed, returning None")
        return None

    logger.debug(
        f"Token verified, looking up user with ID: {payload.get('sub')}, type: {payload.get('type')}"
    )
    db = current_app.db

    try:
        token_type = payload.get("type")
        if token_type == "service_researcher":
            tenant_id = int(str(payload["tenant"]).strip())
            read_scopes = [
                s for s in (payload.get("scope") or []) if _is_valid_read_scope(s)
            ]
            delegated_by_raw = payload.get("delegated_by")
            if (
                tenant_id <= 0
                or not read_scopes
                or not isinstance(delegated_by_raw, str)
                or not delegated_by_raw.strip()
            ):
                logger.warning(
                    "Rejected service_researcher token with invalid tenant, scopes, or delegated_by"
                )
                return None
            delegated_by = delegated_by_raw.strip()
            act = payload.get("act")

            class ServiceResearcherIdentity:
                def __init__(
                    self,
                    tid: int,
                    scopes: list[str],
                    delegator: str,
                    act_claim: Any,
                ) -> None:
                    self.id = 0
                    self.username = f"service-researcher-{tid}"
                    self.email = None
                    self.display_name = "AI Researcher (Read-Only Service)"
                    self.is_active = True
                    self.is_superuser = False
                    self.tenant_id = tid
                    self.portal_role = "viewer"
                    self.token_type = "service_researcher"
                    self.scopes = scopes
                    self.delegated_by = delegator
                    self.act = act_claim

                def get(self, key: str, default: Any = None) -> Any:
                    return getattr(self, key, default)

            service_identity = ServiceResearcherIdentity(
                tenant_id, read_scopes, delegated_by, act
            )
            g.current_user = service_identity
            return service_identity

        if token_type == "service_worker":
            sub = payload.get("sub", "")
            allowed = SERVICE_WORKER_ALLOWED_SCOPES.get(sub)
            if not allowed:
                logger.warning("Rejected service_worker token with invalid sub")
                return None
            tenant_raw = payload.get("tenant")
            if tenant_raw == "system":
                tid = None
            elif (
                isinstance(tenant_raw, str)
                and tenant_raw.isascii()
                and tenant_raw.isdigit()
                and int(tenant_raw) > 0
            ):
                tid = int(tenant_raw)
            else:
                logger.warning("Rejected service_worker token with invalid tenant")
                return None
            scopes = [s for s in (payload.get("scope") or []) if s in allowed]
            if not scopes:
                logger.warning("Rejected service_worker token with empty scopes")
                return None
            worker_identity = ServiceWorkerIdentity(
                sub,
                tid,
                scopes,
                remote_worker_id=payload.get("remote_worker_id"),
            )
            g.current_user = worker_identity
            return worker_identity

        if token_type == "coderun":
            from apps.api.modules.runs.permissions import ALL_GRANTABLE_SCOPES

            tenant_raw = payload.get("tenant")
            if not (
                isinstance(tenant_raw, str)
                and tenant_raw.isascii()
                and tenant_raw.isdigit()
                and int(tenant_raw) > 0
            ):
                logger.warning("Rejected coderun token with invalid tenant")
                return None
            tid = int(tenant_raw)

            function_id_raw = payload.get("function_id")
            if (
                isinstance(function_id_raw, bool)
                or not isinstance(function_id_raw, int)
                or function_id_raw <= 0
            ):
                logger.warning("Rejected coderun token with invalid function_id")
                return None

            execution_id_raw = payload.get("execution_id")
            if not isinstance(execution_id_raw, str) or not execution_id_raw.strip():
                logger.warning("Rejected coderun token with invalid execution_id")
                return None

            scopes = [
                s for s in (payload.get("scope") or []) if s in ALL_GRANTABLE_SCOPES
            ]
            if not scopes:
                logger.warning("Rejected coderun token with empty scopes")
                return None

            coderun_identity = CodeRunIdentity(
                tid, function_id_raw, execution_id_raw.strip(), scopes
            )
            g.current_user = coderun_identity
            return coderun_identity

        # Fail closed: only an explicit identity or portal-user access token
        # may resolve to a stored principal, and only in its own table.
        principal_kind = token_principal_kind(payload)
        if (
            token_type in PORTAL_ACCESS_TOKEN_TYPES
            and principal_kind == PRINCIPAL_PORTAL_USER
        ):
            lookup_table = "portal_users"
        elif (
            token_type in IDENTITY_ACCESS_TOKEN_TYPES
            and principal_kind == PRINCIPAL_IDENTITY
        ):
            lookup_table = "identities"
        else:
            logger.warning("Rejected token: type cannot authenticate a user")
            return None

        user_id = int(payload["sub"])  # Convert string back to integer

        # Handle portal_user tokens (from web UI login)
        if lookup_table == "portal_users":
            portal_user = db.portal_users[user_id]
            if not portal_user:
                logger.warning(f"Portal user not found with ID: {user_id}")
                return None

            # Check is_active - use truthy check like the portal_auth service
            if not portal_user.is_active:
                logger.warning(f"Portal user {portal_user.id} is not active")
                return None

            # Create a synthetic identity-like object from portal_user for API compatibility
            # This allows portal users to access APIs without a linked identity
            class PortalUserIdentity:
                def __init__(self, pu):
                    self.id = pu.id
                    self.username = (
                        pu.email.split("@")[0] if pu.email else f"user_{pu.id}"
                    )
                    self.email = pu.email
                    self.display_name = pu.full_name or self.username
                    self.is_active = bool(pu.is_active)
                    self.is_superuser = pu.global_role == "admin"
                    self.tenant_id = pu.tenant_id
                    self.portal_role = pu.global_role or "observer"
                    self._portal_user = pu

                def get(self, key, default=None):
                    return getattr(self, key, default)

            synthetic_identity = PortalUserIdentity(portal_user)
            logger.debug(
                f"Created synthetic identity for portal user id: {synthetic_identity.id}"
            )
            g.current_user = synthetic_identity
            return synthetic_identity

        # Handle regular identity tokens
        identity = db.identities[user_id]

        if not identity:
            logger.warning(f"User not found in database with ID: {payload.get('sub')}")
            return None

        if not identity.is_active:
            logger.warning(f"User {identity.username} is not active")
            return None

        logger.debug(f"Authentication successful for user: {identity.username}")
        g.current_user = identity
        return identity
    except Exception as e:
        import traceback

        logger.error(f"Error looking up user: {e} - {traceback.format_exc()}")
        # Rollback transaction on error to prevent transaction state issues
        try:
            db.rollback()
        except Exception as rollback_error:
            logger.error(f"Error rolling back transaction: {rollback_error}")
        return None


def verify_password(identity: Row, password: str) -> bool:
    """
    Verify password for local authentication.

    Args:
        identity: PyDAL Row representing identity
        password: Plain text password

    Returns:
        True if password is correct
    """
    if not identity.password_hash:
        return False

    return check_password_hash(identity.password_hash, password)
