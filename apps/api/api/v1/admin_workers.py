"""Remote Workers fleet registry, onboarding/offboarding, live config, and heartbeats."""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from typing import Any

import structlog
from quart import Blueprint, current_app, g, jsonify, request

from apps.api.auth.decorators import login_required, require_scope
from apps.api.auth.jwt_handler import (
    SERVICE_WORKER_TOKEN_TYPES,
    WORKER_TOKEN_PREFIX,
    WORKER_TYPE_TO_SERVICE_SUB,
    get_token_from_header,
    hash_worker_token,
    verify_token,
    warn_platform_wide_worker_once,
)
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.tenant_scoping import get_current_tenant_id

logger = structlog.get_logger(__name__)

bp = Blueprint("admin_workers", __name__)

VALID_WORKER_TYPES = frozenset({"runworker", "scanner"})
VALID_STATUSES = frozenset({"online", "stale", "offline", "revoked"})
# Worker kinds: a platform-wide worker (tenant_id NULL, authenticates as the
# system tenant) serves every tenant's queue; a tenant worker serves only its
# own tenant. Only a super-admin may mint or manage platform-wide workers.
WORKER_KIND_PLATFORM_WIDE = "platform_wide"
WORKER_KIND_TENANT = "tenant"
DEFAULT_POLL_INTERVAL_SECONDS = 30.0
_SERVICE_SUB_TO_WORKER_TYPE = {
    sub: worker_type for worker_type, sub in WORKER_TYPE_TO_SERVICE_SUB.items()
}


def _parse_json_field(raw: Any, default: Any = None) -> Any:
    """Normalize a JSON column value that may be a dict/list or JSON string."""
    if raw is None:
        return default
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return default
    return default


def _parse_datetime_utc(raw: Any) -> datetime | None:
    """Normalize a datetime or ISO-8601 string into a timezone-aware UTC datetime."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.replace(tzinfo=UTC) if raw.tzinfo is None else raw.astimezone(UTC)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
            return (
                parsed.replace(tzinfo=UTC)
                if parsed.tzinfo is None
                else parsed.astimezone(UTC)
            )
        except ValueError:
            return None
    return None


def _iso_or_none(value: Any) -> str | None:
    """Format a datetime value as an ISO-8601 string or return None."""
    dt = _parse_datetime_utc(value)
    return dt.isoformat() if dt is not None else None


def _extract_poll_interval(row: Any) -> float:
    """Extract poll interval (seconds) from a worker's config_json with safe fallback."""
    cfg = _parse_json_field(getattr(row, "config_json", None), {})
    if isinstance(cfg, dict):
        for key in ("poll_interval_seconds", "poll_interval"):
            raw_val = cfg.get(key)
            if raw_val is not None and not isinstance(raw_val, bool):
                try:
                    val = float(raw_val)
                    if val >= 0:
                        return val
                except (TypeError, ValueError):
                    pass
    return DEFAULT_POLL_INTERVAL_SECONDS


def compute_worker_status(row: Any, now: datetime | None = None) -> str:
    """Compute liveness status for a remote_workers row.

    Status rules:
    - ``revoked`` if ``revoked_at`` is set or ``is_enabled`` is False
    - ``offline`` if ``last_heartbeat_at`` is None
    - ``threshold = max(2 * poll_interval + 60, 600)``
    - ``online`` if ``age <= threshold``
    - ``stale`` if ``age <= threshold * 3``
    - ``offline`` otherwise
    """
    revoked_at = getattr(row, "revoked_at", None)
    is_enabled = getattr(row, "is_enabled", True)
    if revoked_at is not None or not bool(is_enabled):
        return "revoked"

    last_hb = _parse_datetime_utc(getattr(row, "last_heartbeat_at", None))
    if last_hb is None:
        return "offline"

    ref_now = _parse_datetime_utc(now) if now is not None else datetime.now(UTC)
    assert ref_now is not None  # noqa: S101
    poll_interval = _extract_poll_interval(row)
    threshold = max(2.0 * poll_interval + 60.0, 600.0)
    age = max(0.0, (ref_now - last_hb).total_seconds())

    if age <= threshold:
        return "online"
    if age <= threshold * 3.0:
        return "stale"
    return "offline"


def _serialize_worker(row: Any, now: datetime | None = None) -> dict[str, Any]:
    """Serialize a ``remote_workers`` row into an explicit API DTO (never exposes token_hash)."""
    config = _parse_json_field(getattr(row, "config_json", None), {})
    capabilities = _parse_json_field(getattr(row, "capabilities_json", None), {})
    return {
        "id": row.id,
        "worker_id": row.worker_id,
        "tenant_id": row.tenant_id,
        "worker_type": row.worker_type,
        "kind": WORKER_KIND_PLATFORM_WIDE
        if row.tenant_id is None
        else WORKER_KIND_TENANT,
        "name": row.name,
        "cluster": row.cluster,
        "token_prefix": row.token_prefix,
        "config": config if isinstance(config, dict) else {},
        "capabilities": capabilities if isinstance(capabilities, (dict, list)) else {},
        "hostname": row.hostname,
        "version": row.version,
        "is_enabled": bool(row.is_enabled),
        "status": compute_worker_status(row, now),
        "last_heartbeat_at": _iso_or_none(row.last_heartbeat_at),
        "revoked_at": _iso_or_none(row.revoked_at),
        "created_by_identity_id": row.created_by_identity_id,
        "created_at": _iso_or_none(row.created_at),
        "updated_at": _iso_or_none(row.updated_at),
    }


def _generate_worker_token() -> tuple[str, str, str]:
    """Generate a one-time worker token and its (token, sha256_hex, prefix) tuple."""
    token = f"{WORKER_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
    return token, hash_worker_token(token), token[:12]


def _build_onboarding_snippets(
    *,
    token: str,
    worker_id: str,
    worker_type: str,
    cluster: str | None,
) -> dict[str, Any]:
    """Build copy-pasteable Docker env and Helm --set onboarding instructions."""
    api_url = (
        current_app.config.get("ELDER_PUBLIC_URL")
        or request.host_url.rstrip("/")
        or "https://elder.example.com"
    )
    docker_env: dict[str, str] = {
        "ELDER_API_URL": api_url,
        "ELDER_INTERNAL_TOKEN": token,
        "ELDER_WORKER_ID": worker_id,
        "ELDER_WORKER_TYPE": worker_type,
    }
    if cluster:
        cluster_env_key = (
            "RUNS_WORKER_CLUSTER"
            if worker_type == "runworker"
            else "ELDER_WORKER_CLUSTER"
        )
        docker_env[cluster_env_key] = cluster

    helm_set = [
        f"worker.apiUrl={api_url}",
        f"worker.token={token}",
        f"worker.id={worker_id}",
        f"worker.type={worker_type}",
    ]
    if cluster:
        helm_set.append(f"worker.cluster={cluster}")

    return {
        "docker_env": docker_env,
        "helm_set": helm_set,
    }


def _caller_context() -> tuple[int | None, bool, int | None]:
    """Return ``(tenant_id, is_superuser, identity_id)`` for the authenticated admin."""
    tenant_id = get_current_tenant_id()
    user = getattr(g, "current_user", None)
    is_superuser = bool(getattr(user, "is_superuser", False))
    raw_id = getattr(user, "id", None)
    identity_id = (
        raw_id
        if isinstance(raw_id, int) and not isinstance(raw_id, bool) and raw_id > 0
        else None
    )
    return tenant_id, is_superuser, identity_id


def _row_visible_to_caller(
    row: Any,
    *,
    caller_tenant_id: int | None,
    super_admin: bool,
) -> bool:
    """Return True when the caller may see or manage ``row``.

    A super-admin sees every worker. Everyone else (tenant admins and
    superusers homed outside the system tenant) sees only workers of their
    own tenant: platform-wide and other tenants' workers are invisible.
    """
    if super_admin:
        return True
    if caller_tenant_id is None:
        return False
    return getattr(row, "tenant_id", None) == caller_tenant_id


def _platform_tenant_id(db: Any) -> int | None:
    """Return the deployment's operator (platform) tenant id, or None.

    The slug comes from ``PLATFORM_TENANT_SLUG`` (default ``system``). Only when
    ``PLATFORM_TENANT_ALLOW_DEFAULT_FALLBACK`` is set and that tenant is
    missing does the bootstrap ``default`` tenant act as the platform tenant.
    """
    cfg = current_app.config
    slug = str(cfg.get("PLATFORM_TENANT_SLUG") or "system").strip() or "system"
    row = db(db.tenants.slug == slug).select().first()
    if row is None and bool(cfg.get("PLATFORM_TENANT_ALLOW_DEFAULT_FALLBACK", False)):
        row = db(db.tenants.slug == "default").select().first()
    return int(row.id) if row is not None else None


def _caller_is_super_admin(
    db: Any, *, caller_tenant_id: int | None, is_superuser: bool
) -> bool:
    """Return True only for a superuser whose verified token is in the platform tenant."""
    if not is_superuser or caller_tenant_id is None:
        return False
    return caller_tenant_id == _platform_tenant_id(db)


async def _authorize_caller(
    db: Any, caller_tenant_id: int | None, is_superuser: bool
) -> tuple[tuple[Any, int] | None, bool]:
    """Return ``(error_response, super_admin)`` for an admin-workers request.

    Fails closed: a caller without a tenant in its verified token is refused
    outright (a super-admin always carries the platform tenant).
    """
    if caller_tenant_id is None:
        return ApiResponse.error("Tenant context required", 403), False
    super_admin = await run_in_threadpool(
        _caller_is_super_admin,
        db,
        caller_tenant_id=caller_tenant_id,
        is_superuser=is_superuser,
    )
    return None, bool(super_admin)


def _fetch_worker_by_pk(db: Any, worker_pk: int) -> Any | None:
    """Fetch a single remote_workers row by primary key using an explicit query."""
    return db(db.remote_workers.id == worker_pk).select().first()


@bp.route("", methods=["GET"])
@login_required
@require_scope("settings:read")
async def list_workers() -> tuple[Any, int]:
    """List registered remote and in-cluster workers with filtering and status counts."""
    db = current_app.db
    caller_tenant_id, is_superuser, _ = _caller_context()
    auth_error, super_admin = await _authorize_caller(
        db, caller_tenant_id, is_superuser
    )
    if auth_error is not None:
        return auth_error

    worker_type = (request.args.get("worker_type") or "").strip().lower() or None
    if worker_type and worker_type not in VALID_WORKER_TYPES:
        return ApiResponse.validation_error(
            "worker_type", "must be 'runworker' or 'scanner'"
        )

    cluster = (request.args.get("cluster") or "").strip() or None
    status_filter = (request.args.get("status") or "").strip().lower() or None
    if status_filter and status_filter not in VALID_STATUSES:
        return ApiResponse.validation_error(
            "status", "must be one of: online, stale, offline, revoked"
        )

    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        return ApiResponse.error("page must be an integer", 400)
    try:
        per_page = max(1, min(100, int(request.args.get("per_page", 25))))
    except ValueError:
        return ApiResponse.error("per_page must be an integer", 400)

    def _query() -> dict[str, Any]:
        query = db.remote_workers.id > 0
        if worker_type:
            query &= db.remote_workers.worker_type == worker_type
        if cluster:
            query &= db.remote_workers.cluster == cluster

        rows = db(query).select(orderby=~db.remote_workers.id)
        now = datetime.now(UTC)

        counts_by_status = {"online": 0, "stale": 0, "offline": 0, "revoked": 0}
        filtered: list[dict[str, Any]] = []
        for row in rows:
            if not _row_visible_to_caller(
                row, caller_tenant_id=caller_tenant_id, super_admin=super_admin
            ):
                continue
            serialized = _serialize_worker(row, now)
            status = serialized["status"]
            counts_by_status[status] = counts_by_status.get(status, 0) + 1
            if status_filter is None or status == status_filter:
                filtered.append(serialized)

        total = len(filtered)
        pages = (total + per_page - 1) // per_page if total else 0
        start = (page - 1) * per_page
        items = filtered[start : start + per_page]
        return {
            "items": items,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": pages,
            "counts_by_status": counts_by_status,
        }

    result = await run_in_threadpool(_query)
    return jsonify(result), 200


@bp.route("", methods=["POST"])
@login_required
@require_scope("settings:write")
async def create_worker() -> tuple[Any, int]:
    """Onboard a new remote worker and return its one-time Bearer token."""
    db = current_app.db
    caller_tenant_id, is_superuser, identity_id = _caller_context()
    auth_error, super_admin = await _authorize_caller(
        db, caller_tenant_id, is_superuser
    )
    if auth_error is not None:
        return auth_error

    body = await request.get_json(silent=True)
    if not isinstance(body, dict):
        return ApiResponse.error("JSON request body is required", 400)

    name = body.get("name")
    if not isinstance(name, str) or not name.strip():
        return ApiResponse.validation_error("name", "must be a non-empty string")
    name = name.strip()
    if len(name) > 255:
        return ApiResponse.validation_error("name", "must be at most 255 characters")

    worker_type = body.get("worker_type")
    if (
        not isinstance(worker_type, str)
        or worker_type.strip().lower() not in VALID_WORKER_TYPES
    ):
        return ApiResponse.validation_error(
            "worker_type", "must be 'runworker' or 'scanner'"
        )
    worker_type = worker_type.strip().lower()

    raw_worker_id = body.get("worker_id")
    if raw_worker_id is not None:
        if not isinstance(raw_worker_id, str) or not raw_worker_id.strip():
            return ApiResponse.validation_error(
                "worker_id", "must be a non-empty string when provided"
            )
        worker_id = raw_worker_id.strip()
        if len(worker_id) > 64:
            return ApiResponse.validation_error(
                "worker_id", "must be at most 64 characters"
            )
    else:
        worker_id = f"wrk-{worker_type[:3]}-{secrets.token_hex(6)}"

    cluster = body.get("cluster")
    if cluster is not None:
        if not isinstance(cluster, str):
            return ApiResponse.validation_error("cluster", "must be a string")
        cluster = cluster.strip() or None
        if cluster and len(cluster) > 128:
            return ApiResponse.validation_error(
                "cluster", "must be at most 128 characters"
            )

    config = body.get("config")
    if config is not None and not isinstance(config, dict):
        return ApiResponse.validation_error("config", "must be a JSON object")

    capabilities = body.get("capabilities")
    if capabilities is not None and not isinstance(capabilities, (dict, list)):
        return ApiResponse.validation_error(
            "capabilities", "must be a JSON object or array"
        )

    platform_wide = body.get("platform_wide", False)
    if not isinstance(platform_wide, bool):
        return ApiResponse.validation_error("platform_wide", "must be a boolean")

    tenant_id = body.get("tenant_id")
    if platform_wide and tenant_id is not None:
        return ApiResponse.validation_error(
            "tenant_id", "must be omitted when platform_wide is true"
        )
    if platform_wide:
        if not super_admin:
            return ApiResponse.error(
                "Only a super-admin (system tenant superuser) can provision a "
                "platform-wide worker",
                403,
            )
    elif tenant_id is None:
        if is_superuser:
            return ApiResponse.validation_error(
                "tenant_id",
                "superusers must pass tenant_id or platform_wide: true explicitly",
            )
        # Default for tenant admins: a worker scoped to the caller's own tenant.
        tenant_id = caller_tenant_id
    elif tenant_id is not None:
        if (
            isinstance(tenant_id, bool)
            or not isinstance(tenant_id, int)
            or tenant_id <= 0
        ):
            return ApiResponse.validation_error(
                "tenant_id", "must be a positive integer or null"
            )
        # Another tenant's worker claims that tenant's runs (and mints coderun
        # tokens for them): only a super-admin may provision one.
        if tenant_id != caller_tenant_id and not super_admin:
            return ApiResponse.error(
                "Only a super-admin can provision a worker for another tenant", 403
            )

    token, token_hash, token_prefix = _generate_worker_token()

    def _insert() -> tuple[dict[str, Any] | None, str | None]:
        existing = db(db.remote_workers.worker_id == worker_id).select().first()
        if existing is not None:
            return None, "worker_id already exists"
        now = datetime.now(UTC)
        new_id = db.remote_workers.insert(
            worker_id=worker_id,
            tenant_id=tenant_id,
            worker_type=worker_type,
            name=name,
            cluster=cluster,
            token_hash=token_hash,
            token_prefix=token_prefix,
            config_json=config if config is not None else {},
            capabilities_json=capabilities if capabilities is not None else {},
            is_enabled=True,
            created_by_identity_id=identity_id,
            created_at=now,
            updated_at=now,
        )
        db.commit()
        row = _fetch_worker_by_pk(db, int(new_id))
        return _serialize_worker(row, now), None

    worker_dto, err = await run_in_threadpool(_insert)
    if err is not None:
        return ApiResponse.error(err, 409)

    onboarding = _build_onboarding_snippets(
        token=token,
        worker_id=worker_id,
        worker_type=worker_type,
        cluster=cluster,
    )
    logger.info(
        "remote_worker_created",
        worker_id=worker_id,
        worker_type=worker_type,
        kind=worker_dto["kind"] if worker_dto else None,
        tenant_id=tenant_id,
        cluster=cluster,
        token_prefix=token_prefix,
    )
    return (
        jsonify(
            {
                "worker": worker_dto,
                "token": token,
                "onboarding": onboarding,
            }
        ),
        201,
    )


@bp.route("/<int:id>", methods=["GET"])
@login_required
@require_scope("settings:read")
async def get_worker(id: int) -> tuple[Any, int]:
    """Retrieve a single remote worker by numeric ID."""
    db = current_app.db
    caller_tenant_id, is_superuser, _ = _caller_context()
    auth_error, super_admin = await _authorize_caller(
        db, caller_tenant_id, is_superuser
    )
    if auth_error is not None:
        return auth_error

    def _fetch() -> dict[str, Any] | None:
        row = _fetch_worker_by_pk(db, id)
        if row is None or not _row_visible_to_caller(
            row, caller_tenant_id=caller_tenant_id, super_admin=super_admin
        ):
            return None
        return _serialize_worker(row, datetime.now(UTC))

    worker_dto = await run_in_threadpool(_fetch)
    if worker_dto is None:
        return ApiResponse.not_found("Worker", id)
    return jsonify({"worker": worker_dto}), 200


@bp.route("/<int:id>", methods=["PUT"])
@login_required
@require_scope("settings:write")
async def update_worker(id: int) -> tuple[Any, int]:
    """Update a worker's metadata, live config, capabilities, or enabled state."""
    db = current_app.db
    caller_tenant_id, is_superuser, _ = _caller_context()
    auth_error, super_admin = await _authorize_caller(
        db, caller_tenant_id, is_superuser
    )
    if auth_error is not None:
        return auth_error

    body = await request.get_json(silent=True)
    if not isinstance(body, dict):
        return ApiResponse.error("JSON request body is required", 400)

    updates: dict[str, Any] = {}
    if "name" in body:
        name = body["name"]
        if not isinstance(name, str) or not name.strip():
            return ApiResponse.validation_error("name", "must be a non-empty string")
        name = name.strip()
        if len(name) > 255:
            return ApiResponse.validation_error(
                "name", "must be at most 255 characters"
            )
        updates["name"] = name

    if "cluster" in body:
        cluster = body["cluster"]
        if cluster is not None:
            if not isinstance(cluster, str):
                return ApiResponse.validation_error(
                    "cluster", "must be a string or null"
                )
            cluster = cluster.strip() or None
            if cluster and len(cluster) > 128:
                return ApiResponse.validation_error(
                    "cluster", "must be at most 128 characters"
                )
        updates["cluster"] = cluster

    if "config" in body:
        config = body["config"]
        if config is not None and not isinstance(config, dict):
            return ApiResponse.validation_error(
                "config", "must be a JSON object or null"
            )
        updates["config_json"] = config if config is not None else {}

    if "capabilities" in body:
        capabilities = body["capabilities"]
        if capabilities is not None and not isinstance(capabilities, (dict, list)):
            return ApiResponse.validation_error(
                "capabilities", "must be a JSON object, array, or null"
            )
        updates["capabilities_json"] = capabilities if capabilities is not None else {}

    if "is_enabled" in body:
        is_enabled = body["is_enabled"]
        if not isinstance(is_enabled, bool):
            return ApiResponse.validation_error("is_enabled", "must be a boolean")
        updates["is_enabled"] = is_enabled

    def _update() -> tuple[dict[str, Any] | None, str | None]:
        row = _fetch_worker_by_pk(db, id)
        if row is None or not _row_visible_to_caller(
            row, caller_tenant_id=caller_tenant_id, super_admin=super_admin
        ):
            return None, "not_found"
        now = datetime.now(UTC)
        updates["updated_at"] = now
        db(db.remote_workers.id == id).update(**updates)
        db.commit()
        updated = _fetch_worker_by_pk(db, id)
        return _serialize_worker(updated, now), None

    worker_dto, err = await run_in_threadpool(_update)
    if worker_dto is None:
        return ApiResponse.not_found("Worker", id)

    logger.info("remote_worker_updated", worker_id=worker_dto["worker_id"])
    return jsonify({"worker": worker_dto}), 200


@bp.route("/<int:id>/rotate-token", methods=["POST"])
@login_required
@require_scope("settings:write")
async def rotate_worker_token(id: int) -> tuple[Any, int]:
    """Rotate a worker's Bearer token hash and return the new one-time token."""
    db = current_app.db
    caller_tenant_id, is_superuser, _ = _caller_context()
    auth_error, super_admin = await _authorize_caller(
        db, caller_tenant_id, is_superuser
    )
    if auth_error is not None:
        return auth_error

    token, token_hash, token_prefix = _generate_worker_token()

    def _rotate() -> tuple[dict[str, Any] | None, str | None]:
        row = _fetch_worker_by_pk(db, id)
        if row is None or not _row_visible_to_caller(
            row, caller_tenant_id=caller_tenant_id, super_admin=super_admin
        ):
            return None, "not_found"
        if getattr(row, "revoked_at", None) is not None:
            return None, "revoked"
        now = datetime.now(UTC)
        db(db.remote_workers.id == id).update(
            token_hash=token_hash,
            token_prefix=token_prefix,
            updated_at=now,
        )
        db.commit()
        updated = _fetch_worker_by_pk(db, id)
        return _serialize_worker(updated, now), None

    worker_dto, err = await run_in_threadpool(_rotate)
    if err == "not_found":
        return ApiResponse.not_found("Worker", id)
    if err == "revoked":
        return ApiResponse.error("Cannot rotate token for a revoked worker", 409)

    logger.info(
        "remote_worker_token_rotated",
        worker_id=worker_dto["worker_id"],
        token_prefix=token_prefix,
    )
    return jsonify({"worker": worker_dto, "token": token}), 200


@bp.route("/<int:id>", methods=["DELETE"])
@login_required
@require_scope("settings:write")
async def revoke_worker(id: int) -> tuple[Any, int]:
    """Offboard and revoke a remote worker, clearing its stored token hash."""
    db = current_app.db
    caller_tenant_id, is_superuser, _ = _caller_context()
    auth_error, super_admin = await _authorize_caller(
        db, caller_tenant_id, is_superuser
    )
    if auth_error is not None:
        return auth_error

    def _revoke() -> tuple[dict[str, Any] | None, str | None]:
        row = _fetch_worker_by_pk(db, id)
        if row is None or not _row_visible_to_caller(
            row, caller_tenant_id=caller_tenant_id, super_admin=super_admin
        ):
            return None, "not_found"
        now = datetime.now(UTC)
        db(db.remote_workers.id == id).update(
            revoked_at=now,
            is_enabled=False,
            token_hash=None,
            updated_at=now,
        )
        db.commit()
        updated = _fetch_worker_by_pk(db, id)
        return _serialize_worker(updated, now), None

    worker_dto, err = await run_in_threadpool(_revoke)
    if worker_dto is None:
        return ApiResponse.not_found("Worker", id)

    logger.info("remote_worker_revoked", worker_id=worker_dto["worker_id"])
    return jsonify({"revoked": True, "worker": worker_dto}), 200


@bp.route("/heartbeat", methods=["POST"])
async def worker_heartbeat() -> tuple[Any, int]:
    """Record a worker heartbeat and return its current live configuration.

    Authenticates via either:
    - A provisioned ``ewk_...`` Bearer token (matched by SHA-256 ``token_hash``;
      revoked or disabled workers are rejected with 403), OR
    - An in-cluster ``service_worker`` JWT (``svc-runworker`` / ``svc-scanner``),
      which auto-upserts an in-cluster worker record by ``worker_id``.
    """
    token = get_token_from_header()
    if not token:
        return ApiResponse.error("Authentication required", 401)

    body = await request.get_json(silent=True)
    if body is None:
        body = {}
    if not isinstance(body, dict):
        return ApiResponse.error("JSON body must be an object", 400)

    hostname = body.get("hostname")
    if hostname is not None:
        hostname = str(hostname).strip()[:255] or None
    version = body.get("version")
    if version is not None:
        version = str(version).strip()[:64] or None
    capabilities = body.get("capabilities")
    if capabilities is not None and not isinstance(capabilities, (dict, list)):
        capabilities = None
    body_cluster = body.get("cluster")
    if body_cluster is not None:
        body_cluster = str(body_cluster).strip()[:128] or None
    body_worker_id = body.get("worker_id")
    if body_worker_id is not None:
        body_worker_id = str(body_worker_id).strip()[:64] or None
    body_name = body.get("name")
    if body_name is not None:
        body_name = str(body_name).strip()[:255] or None

    db = current_app.db

    if token.startswith(WORKER_TOKEN_PREFIX):
        token_hash = hash_worker_token(token)
        token_prefix = token[:12]

        def _handle_provisioned() -> tuple[dict[str, Any] | None, int, str | None]:
            row = db(db.remote_workers.token_hash == token_hash).select().first()
            if row is None:
                revoked_match = (
                    db(
                        (db.remote_workers.token_prefix == token_prefix)
                        & (
                            (db.remote_workers.revoked_at != None)  # noqa: E711
                            | (db.remote_workers.is_enabled == False)  # noqa: E712
                        )
                    )
                    .select()
                    .first()
                )
                if revoked_match is None and body_worker_id:
                    candidate = (
                        db(db.remote_workers.worker_id == body_worker_id)
                        .select()
                        .first()
                    )
                    if candidate is not None and (
                        getattr(candidate, "revoked_at", None) is not None
                        or not getattr(candidate, "is_enabled", True)
                    ):
                        revoked_match = candidate
                if revoked_match is not None:
                    return None, 403, "Worker has been revoked or disabled"
                return None, 401, "Invalid worker token"

            if getattr(row, "revoked_at", None) is not None or not getattr(
                row, "is_enabled", False
            ):
                return None, 403, "Worker has been revoked or disabled"
            if getattr(row, "tenant_id", None) is None:
                warn_platform_wide_worker_once(row.worker_id, "heartbeat")

            now = datetime.now(UTC)
            updates: dict[str, Any] = {
                "last_heartbeat_at": now,
                "updated_at": now,
            }
            if hostname is not None:
                updates["hostname"] = hostname
            if version is not None:
                updates["version"] = version
            if capabilities is not None:
                updates["capabilities_json"] = capabilities
            if body_cluster is not None and not row.cluster:
                updates["cluster"] = body_cluster

            db(db.remote_workers.id == row.id).update(**updates)
            db.commit()
            updated = _fetch_worker_by_pk(db, row.id)
            cfg = _parse_json_field(getattr(updated, "config_json", None), {})
            return (
                {
                    "ok": True,
                    "status": "active",
                    "worker_id": updated.worker_id,
                    "config": cfg if isinstance(cfg, dict) else {},
                },
                200,
                None,
            )

        resp_body, status_code, err_msg = await run_in_threadpool(_handle_provisioned)
        if err_msg is not None:
            return ApiResponse.error(err_msg, status_code)
        return jsonify(resp_body), 200

    # Fallback: in-cluster service_worker JWT (svc-runworker / svc-scanner)
    payload = verify_token(token, SERVICE_WORKER_TOKEN_TYPES)
    if not payload:
        return ApiResponse.error("Invalid or expired token", 401)
    if payload.get("type") != "service_worker":
        return ApiResponse.error(
            "Forbidden: service_worker or provisioned worker token required", 403
        )
    sub = payload.get("sub", "")
    worker_type = _SERVICE_SUB_TO_WORKER_TYPE.get(sub)
    if not worker_type:
        return ApiResponse.error(
            "Forbidden: only svc-runworker and svc-scanner may send heartbeats", 403
        )

    # In-cluster JWT heartbeats are platform workers only; a tenant-scoped
    # worker must use its provisioned ewk_ token (handled above).
    if payload.get("tenant") != "system":
        return ApiResponse.error(
            "Forbidden: in-cluster heartbeats require a platform service worker", 403
        )

    effective_worker_id = (body_worker_id or hostname or sub)[:64]

    def _handle_in_cluster() -> tuple[dict[str, Any] | None, int, str | None]:
        now = datetime.now(UTC)
        row = db(db.remote_workers.worker_id == effective_worker_id).select().first()
        if row is not None:
            if getattr(row, "revoked_at", None) is not None or not getattr(
                row, "is_enabled", False
            ):
                return None, 403, "Worker has been revoked or disabled"
            # Never let an in-cluster JWT overwrite a provisioned or
            # tenant-scoped worker record that shares its worker_id.
            if (
                getattr(row, "token_hash", None) is not None
                or getattr(row, "tenant_id", None) is not None
            ):
                return None, 403, "worker_id belongs to a provisioned worker"
            updates: dict[str, Any] = {
                "last_heartbeat_at": now,
                "updated_at": now,
            }
            if hostname is not None:
                updates["hostname"] = hostname
            if version is not None:
                updates["version"] = version
            if capabilities is not None:
                updates["capabilities_json"] = capabilities
            if body_cluster is not None:
                updates["cluster"] = body_cluster
            db(db.remote_workers.id == row.id).update(**updates)
            db.commit()
            updated = _fetch_worker_by_pk(db, row.id)
        else:
            new_id = db.remote_workers.insert(
                worker_id=effective_worker_id,
                tenant_id=None,
                worker_type=worker_type,
                name=body_name or effective_worker_id,
                cluster=body_cluster or "in-cluster",
                token_hash=None,
                token_prefix=None,
                config_json={},
                capabilities_json=capabilities if capabilities is not None else {},
                hostname=hostname,
                version=version,
                is_enabled=True,
                last_heartbeat_at=now,
                created_at=now,
                updated_at=now,
            )
            db.commit()
            updated = _fetch_worker_by_pk(db, int(new_id))

        cfg = _parse_json_field(getattr(updated, "config_json", None), {})
        return (
            {
                "ok": True,
                "status": "active",
                "worker_id": updated.worker_id,
                "config": cfg if isinstance(cfg, dict) else {},
            },
            200,
            None,
        )

    resp_body, status_code, err_msg = await run_in_threadpool(_handle_in_cluster)
    if err_msg is not None:
        return ApiResponse.error(err_msg, status_code)
    return jsonify(resp_body), 200
