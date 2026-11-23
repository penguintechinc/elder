"""Elder Runs serverless functions API endpoints and internal svc-runworker routes."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from quart import Blueprint, current_app, g, jsonify, request
from sqlalchemy import text

from apps.api.auth.decorators import login_required, require_scope
from apps.api.auth.jwt_handler import (
    MAX_CODERUN_TTL_SECONDS,
    generate_coderun_token,
    is_system_service_worker,
)
from apps.api.modules.runs.models.runs import (
    EXECUTION_STATUSES,
    SUPPORTED_RUNTIMES,
    TERMINAL_EXECUTION_STATUSES,
    TRIGGER_TYPES,
)
from apps.api.modules.runs.permissions import (
    EgressDomainValidationError,
    ScopeValidationError,
    build_permission_catalog_payload,
    validate_egress_domains,
    validate_granted_scopes,
)
from apps.api.modules.runs.storage import (
    compute_code_sha256,
    get_run_artifact_storage,
)
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.pydal_helpers import PaginationParams
from apps.api.utils.tenant_scoping import get_current_tenant_id, get_tenant_scoped
from apps.api.utils.village_id import mint_village_id

logger = structlog.get_logger(__name__)

bp = Blueprint("runs", __name__)

DEFAULT_EXECUTION_NAMESPACE = "elder-runs"
DEFAULT_TIMEOUT_SECONDS = 60
MAX_TIMEOUT_SECONDS = 300
DEFAULT_MEMORY_MB = 256
MIN_MEMORY_MB = 32
MAX_MEMORY_MB = 16384
MAX_LIVE_LOG_BYTES = 1024 * 1024  # 1 MiB cap for in-progress log flushes


def _utc_now() -> datetime:
    """Return the current timezone-aware UTC timestamp."""
    return datetime.now(UTC)


def _ensure_utc(dt: datetime) -> datetime:
    """Normalize naive datetimes (e.g. SQLite) to timezone-aware UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _iso_or_none(value: Any) -> str | None:
    """Serialize a datetime or timestamp string to ISO-8601."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return _ensure_utc(value).isoformat()
    return str(value)


def _parse_datetime(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp string or datetime into UTC."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return _ensure_utc(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            return _ensure_utc(datetime.fromisoformat(raw))
        except ValueError:
            return None
    return None


def _parse_json_value(raw: Any) -> Any:
    """Decode JSON string columns returned by SQLite while preserving dicts/lists."""
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return raw
    return raw


def _parse_json_dict(raw: Any) -> dict[str, Any]:
    """Decode a JSON object column or dict into a shallow dictionary."""
    parsed = _parse_json_value(raw)
    if isinstance(parsed, dict):
        return dict(parsed)
    return {}


def _parse_json_list(raw: Any) -> list[str]:
    """Decode a JSON array column or list into a list of clean strings."""
    parsed = _parse_json_value(raw)
    if isinstance(parsed, list):
        return [
            str(item).strip()
            for item in parsed
            if isinstance(item, str) and item.strip()
        ]
    return []


def _caller_scopes_and_superuser() -> tuple[set[str], bool]:
    """Return ``(caller_scopes, is_superuser)`` for the authenticated caller."""
    user = getattr(g, "current_user", None)
    is_superuser = bool(getattr(user, "is_superuser", False))
    claims = getattr(g, "claims", None) or {}
    raw_scope = claims.get("scope")
    if isinstance(raw_scope, str):
        scopes = {s.strip() for s in raw_scope.split() if s.strip()}
    elif isinstance(raw_scope, (list, tuple, set)):
        scopes = {str(s).strip() for s in raw_scope if isinstance(s, str) and s.strip()}
    else:
        scopes = set()
    if not scopes and user is not None:
        user_scopes = getattr(user, "scopes", None)
        if isinstance(user_scopes, (list, tuple, set)):
            scopes = {
                str(s).strip() for s in user_scopes if isinstance(s, str) and s.strip()
            }
    return scopes, is_superuser


def _current_identity_id() -> int | None:
    """Return the authenticated caller's numeric identity ID if available."""
    user = getattr(g, "current_user", None)
    if user is None:
        return None
    raw_id = getattr(user, "id", None)
    if isinstance(raw_id, int) and raw_id > 0:
        return raw_id
    return None


def _require_runworker() -> tuple[Any, int] | None:
    """Enforce that the caller is the ``svc-runworker`` system service worker."""
    claims = getattr(g, "claims", None) or {}
    user = getattr(g, "current_user", None)
    if (
        not is_system_service_worker()
        or claims.get("sub") != "svc-runworker"
        or getattr(user, "username", None) != "svc-runworker"
    ):
        return (
            jsonify({"error": "Forbidden: svc-runworker service worker required"}),
            403,
        )

    raw_scope = claims.get("scope")
    if isinstance(raw_scope, str):
        scopes = set(raw_scope.split())
    elif isinstance(raw_scope, (list, tuple, set)):
        scopes = {str(item) for item in raw_scope}
    else:
        scopes = set()

    if "runs:invoke" not in scopes:
        return jsonify({"error": "Forbidden: runs:invoke scope required"}), 403
    return None


def _serialize_function(
    row: Any,
    *,
    latest_version: int | None = None,
    recent_executions: list[dict[str, Any]] | None = None,
    code_override: str | None = None,
) -> dict[str, Any]:
    """Explicitly serialize a ``run_functions`` row to an API response dict."""
    payload: dict[str, Any] = {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "village_id": getattr(row, "village_id", None),
        "name": row.name,
        "description": getattr(row, "description", None),
        "runtime": row.runtime,
        "code": code_override if code_override is not None else row.code,
        "storage_backend": getattr(row, "storage_backend", None) or "database",
        "storage_key": getattr(row, "storage_key", None),
        "code_sha256": getattr(row, "code_sha256", None),
        "entrypoint": getattr(row, "entrypoint", None),
        "timeout_seconds": int(
            getattr(row, "timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
            or DEFAULT_TIMEOUT_SECONDS
        ),
        "memory_mb": int(
            getattr(row, "memory_mb", DEFAULT_MEMORY_MB) or DEFAULT_MEMORY_MB
        ),
        "env_vars": _parse_json_dict(getattr(row, "env_vars_json", None)),
        "granted_scopes": _parse_json_list(getattr(row, "granted_scopes_json", None)),
        "allowed_egress_domains": _parse_json_list(
            getattr(row, "allowed_egress_domains_json", None)
        ),
        "schedule_cron": getattr(row, "schedule_cron", None),
        "execution_namespace": (
            getattr(row, "execution_namespace", None) or DEFAULT_EXECUTION_NAMESPACE
        ),
        "worker_cluster": getattr(row, "worker_cluster", None),
        "is_enabled": bool(getattr(row, "is_enabled", True)),
        "created_by_identity_id": getattr(row, "created_by_identity_id", None),
        "created_at": _iso_or_none(getattr(row, "created_at", None)),
        "updated_at": _iso_or_none(getattr(row, "updated_at", None)),
    }
    if latest_version is not None:
        payload["latest_version"] = latest_version
    if recent_executions is not None:
        payload["recent_executions"] = recent_executions
    return payload


def _truncate_log_bytes(raw: str, max_bytes: int = MAX_LIVE_LOG_BYTES) -> str:
    """Cap a log string to at most ``max_bytes`` UTF-8 bytes, keeping the tail."""
    encoded = raw.encode("utf-8")
    if len(encoded) <= max_bytes:
        return raw
    return encoded[-max_bytes:].decode("utf-8", errors="ignore")


def _serialize_execution(
    row: Any,
    *,
    func_row: Any | None = None,
) -> dict[str, Any]:
    """Explicitly serialize a ``run_executions`` row to an API response dict."""
    payload: dict[str, Any] = {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "execution_id": row.execution_id,
        "function_id": row.function_id,
        "status": row.status,
        "trigger_type": getattr(row, "trigger_type", "manual") or "manual",
        "triggered_by_identity_id": getattr(row, "triggered_by_identity_id", None),
        "worker_id": getattr(row, "worker_id", None),
        "input_data": _parse_json_value(getattr(row, "input_json", None)),
        "output_data": _parse_json_value(getattr(row, "output_json", None)),
        "stdout_log": getattr(row, "stdout_log", None),
        "stderr_log": getattr(row, "stderr_log", None),
        "exit_code": getattr(row, "exit_code", None),
        "error_message": getattr(row, "error_message", None),
        "started_at": _iso_or_none(getattr(row, "started_at", None)),
        "completed_at": _iso_or_none(getattr(row, "completed_at", None)),
        "duration_ms": getattr(row, "duration_ms", None),
        "created_at": _iso_or_none(getattr(row, "created_at", None)),
    }
    if func_row is not None:
        payload.update(
            {
                "function_name": getattr(func_row, "name", None),
                "runtime": getattr(func_row, "runtime", None),
                "storage_backend": (
                    getattr(func_row, "storage_backend", None) or "database"
                ),
                "storage_key": getattr(func_row, "storage_key", None),
                "code_sha256": getattr(func_row, "code_sha256", None),
                "execution_namespace": (
                    getattr(func_row, "execution_namespace", None)
                    or DEFAULT_EXECUTION_NAMESPACE
                ),
                "timeout_seconds": int(
                    getattr(func_row, "timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
                    or DEFAULT_TIMEOUT_SECONDS
                ),
            }
        )
    return payload


def _validate_timeout(value: Any) -> tuple[int | None, tuple[Any, int] | None]:
    """Validate timeout_seconds within [1, MAX_TIMEOUT_SECONDS]."""
    if isinstance(value, bool):
        return None, ApiResponse.validation_error(
            "timeout_seconds", "must be an integer"
        )
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None, ApiResponse.validation_error(
            "timeout_seconds", "must be an integer"
        )
    if parsed < 1 or parsed > MAX_TIMEOUT_SECONDS:
        return None, ApiResponse.validation_error(
            "timeout_seconds", f"must be between 1 and {MAX_TIMEOUT_SECONDS}"
        )
    return parsed, None


def _validate_memory(value: Any) -> tuple[int | None, tuple[Any, int] | None]:
    """Validate memory_mb within [MIN_MEMORY_MB, MAX_MEMORY_MB]."""
    if isinstance(value, bool):
        return None, ApiResponse.validation_error("memory_mb", "must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None, ApiResponse.validation_error("memory_mb", "must be an integer")
    if parsed < MIN_MEMORY_MB or parsed > MAX_MEMORY_MB:
        return None, ApiResponse.validation_error(
            "memory_mb", f"must be between {MIN_MEMORY_MB} and {MAX_MEMORY_MB}"
        )
    return parsed, None


@bp.route("/permissions/catalog", methods=["GET"])
@login_required
@require_scope("runs:read")
async def get_permissions_catalog():
    """Return the grouped grantable CodeRun scope catalog and caller eligibility."""
    caller_scopes, is_superuser = _caller_scopes_and_superuser()
    return (
        jsonify(
            build_permission_catalog_payload(caller_scopes, is_superuser=is_superuser)
        ),
        200,
    )


@bp.route("", methods=["GET"])
@login_required
@require_scope("runs:read")
async def list_run_functions():
    """List serverless functions for the current tenant with optional filters."""
    db = current_app.db
    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return ApiResponse.forbidden("Tenant context required")

    pagination = PaginationParams.from_request()
    runtime = (request.args.get("runtime") or "").strip().lower()
    search = (request.args.get("search") or "").strip()
    worker_cluster = (request.args.get("worker_cluster") or "").strip()
    is_enabled_raw = request.args.get("is_enabled")

    if runtime and runtime not in SUPPORTED_RUNTIMES:
        return ApiResponse.validation_error(
            "runtime",
            f"must be one of: {', '.join(sorted(SUPPORTED_RUNTIMES))}",
        )

    def fetch():
        query = db.run_functions.tenant_id == tenant_id
        if runtime:
            query &= db.run_functions.runtime == runtime
        if worker_cluster:
            query &= db.run_functions.worker_cluster == worker_cluster
        if is_enabled_raw is not None:
            enabled_val = is_enabled_raw.lower() in ("1", "true", "yes")
            query &= db.run_functions.is_enabled == enabled_val
        if search:
            pattern = f"%{search}%"
            query &= (db.run_functions.name.ilike(pattern)) | (
                db.run_functions.description.ilike(pattern)
            )

        total = db(query).count()
        rows = db(query).select(
            orderby=~db.run_functions.created_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )
        return total, rows

    total, rows = await run_in_threadpool(fetch)
    return (
        jsonify(
            {
                "items": [_serialize_function(row) for row in rows],
                "total": total,
                "page": pagination.page,
                "per_page": pagination.per_page,
                "pages": pagination.calculate_pages(total),
            }
        ),
        200,
    )


@bp.route("", methods=["POST"])
@login_required
@require_scope("runs:write")
async def create_run_function():
    """Create a new serverless function and record initial version 1."""
    db = current_app.db
    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return ApiResponse.forbidden("Tenant context required")

    data = await request.get_json(silent=True)
    if not isinstance(data, dict):
        return ApiResponse.error("Request body must be a JSON object", 400)

    name = str(data.get("name") or "").strip()
    if not name:
        return ApiResponse.validation_error("name", "is required")

    runtime = str(data.get("runtime") or "").strip().lower()
    if runtime not in SUPPORTED_RUNTIMES:
        return ApiResponse.validation_error(
            "runtime",
            f"must be one of: {', '.join(sorted(SUPPORTED_RUNTIMES))}",
        )

    code = data.get("code")
    if not isinstance(code, str) or not code.strip():
        return ApiResponse.validation_error("code", "is required")

    timeout_seconds, err = _validate_timeout(
        data.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    )
    if err:
        return err

    memory_mb, err = _validate_memory(data.get("memory_mb", DEFAULT_MEMORY_MB))
    if err:
        return err

    env_vars = data.get("env_vars", data.get("env_vars_json"))
    if env_vars is not None and not isinstance(env_vars, dict):
        return ApiResponse.validation_error("env_vars", "must be a JSON object")

    caller_scopes, is_superuser = _caller_scopes_and_superuser()
    raw_granted_scopes = data.get("granted_scopes", data.get("granted_scopes_json"))
    try:
        granted_scopes = validate_granted_scopes(
            raw_granted_scopes, caller_scopes, is_superuser=is_superuser
        )
    except ScopeValidationError as exc:
        extra: dict[str, Any] = {"field": "granted_scopes"}
        if exc.invalid_scopes:
            extra["invalid_scopes"] = exc.invalid_scopes
        if exc.missing_scopes:
            extra["missing_scopes"] = exc.missing_scopes
        return ApiResponse.error(exc.message, exc.status_code, **extra)

    raw_egress_domains = data.get(
        "allowed_egress_domains", data.get("allowed_egress_domains_json")
    )
    try:
        allowed_egress_domains = validate_egress_domains(raw_egress_domains)
    except EgressDomainValidationError as exc:
        return ApiResponse.error(
            exc.message,
            exc.status_code,
            field="allowed_egress_domains",
            invalid_domains=exc.invalid_domains,
        )

    description = data.get("description")
    entrypoint = data.get("entrypoint")
    schedule_cron = data.get("schedule_cron")
    raw_ns = data.get("execution_namespace")
    if raw_ns is not None:
        ns_str = str(raw_ns).strip()
        if ns_str and ns_str != DEFAULT_EXECUTION_NAMESPACE:
            return ApiResponse.validation_error(
                "execution_namespace",
                f"is operator-managed and locked to '{DEFAULT_EXECUTION_NAMESPACE}'; "
                "use worker_cluster to route to a remote worker pool",
            )
    execution_namespace = DEFAULT_EXECUTION_NAMESPACE
    worker_cluster = data.get("worker_cluster")
    is_enabled = bool(data.get("is_enabled", True))
    identity_id = _current_identity_id()
    redis_client = getattr(current_app, "redis_client", None)

    def create():
        now = _utc_now()
        vid = mint_village_id(tenant_id, redis_client)
        storage = get_run_artifact_storage()
        artifact = storage.put_code(
            tenant_id=tenant_id,
            function_ref=vid or f"fn-{int(now.timestamp())}",
            version_number=1,
            runtime=runtime,
            code=code,
        )
        stored_code = "" if artifact.storage_backend == "s3" else code
        func_id = db.run_functions.insert(
            tenant_id=tenant_id,
            village_id=vid,
            name=name,
            description=description,
            runtime=runtime,
            code=stored_code,
            storage_backend=artifact.storage_backend,
            storage_key=artifact.storage_key,
            code_sha256=artifact.code_sha256,
            entrypoint=entrypoint,
            timeout_seconds=timeout_seconds,
            memory_mb=memory_mb,
            env_vars_json=env_vars or {},
            granted_scopes_json=granted_scopes,
            allowed_egress_domains_json=allowed_egress_domains,
            schedule_cron=schedule_cron,
            execution_namespace=execution_namespace,
            worker_cluster=worker_cluster,
            is_enabled=is_enabled,
            created_by_identity_id=identity_id,
            created_at=now,
            updated_at=now,
        )
        db.run_function_versions.insert(
            tenant_id=tenant_id,
            function_id=func_id,
            version_number=1,
            code=stored_code,
            storage_backend=artifact.storage_backend,
            storage_key=artifact.storage_key,
            code_sha256=artifact.code_sha256,
            runtime=runtime,
            entrypoint=entrypoint,
            granted_scopes_json=granted_scopes,
            created_by_identity_id=identity_id,
            created_at=now,
        )
        db.commit()
        return get_tenant_scoped(db, db.run_functions, func_id, tenant_id)

    row = await run_in_threadpool(create)
    logger.info(
        "run_function_created",
        function_id=row.id,
        tenant_id=tenant_id,
        runtime=runtime,
        village_id=getattr(row, "village_id", None),
        storage_backend=getattr(row, "storage_backend", "database"),
    )
    return (
        jsonify(_serialize_function(row, latest_version=1, code_override=code)),
        201,
    )


@bp.route("/<int:func_id>", methods=["GET"])
@login_required
@require_scope("runs:read")
async def get_run_function(func_id: int):
    """Get a single serverless function along with recent executions."""
    db = current_app.db
    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return ApiResponse.forbidden("Tenant context required")

    def fetch():
        func = get_tenant_scoped(db, db.run_functions, func_id, tenant_id)
        if not func:
            return None, None, []

        latest_ver_row = (
            db(
                (db.run_function_versions.function_id == func_id)
                & (db.run_function_versions.tenant_id == tenant_id)
            )
            .select(
                db.run_function_versions.version_number,
                orderby=~db.run_function_versions.version_number,
                limitby=(0, 1),
            )
            .first()
        )
        latest_version = int(latest_ver_row.version_number) if latest_ver_row else 1

        exec_rows = db(
            (db.run_executions.function_id == func_id)
            & (db.run_executions.tenant_id == tenant_id)
        ).select(
            orderby=~db.run_executions.created_at,
            limitby=(0, 20),
        )
        hydrated_code: str | None = None
        if (
            getattr(func, "storage_backend", "database") == "s3"
            and not func.code
            and getattr(func, "storage_key", None)
        ):
            storage = get_run_artifact_storage()
            hydrated_code = storage.get_code(
                func.storage_key,
                expected_sha256=getattr(func, "code_sha256", None),
            )
        return func, latest_version, list(exec_rows), hydrated_code

    func, latest_version, exec_rows, hydrated_code = await run_in_threadpool(fetch)
    if not func:
        return ApiResponse.not_found("Run function", func_id)

    return (
        jsonify(
            _serialize_function(
                func,
                latest_version=latest_version,
                recent_executions=[_serialize_execution(r) for r in exec_rows],
                code_override=hydrated_code,
            )
        ),
        200,
    )


@bp.route("/<int:func_id>", methods=["PUT"])
@login_required
@require_scope("runs:write")
async def update_run_function(func_id: int):
    """Update a serverless function, creating a new version when code changes."""
    db = current_app.db
    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return ApiResponse.forbidden("Tenant context required")

    data = await request.get_json(silent=True)
    if not isinstance(data, dict):
        return ApiResponse.error("Request body must be a JSON object", 400)

    update_fields: dict[str, Any] = {}
    if "name" in data:
        name = str(data["name"] or "").strip()
        if not name:
            return ApiResponse.validation_error("name", "cannot be empty")
        update_fields["name"] = name

    if "description" in data:
        update_fields["description"] = data["description"]

    if "runtime" in data:
        runtime = str(data["runtime"] or "").strip().lower()
        if runtime not in SUPPORTED_RUNTIMES:
            return ApiResponse.validation_error(
                "runtime",
                f"must be one of: {', '.join(sorted(SUPPORTED_RUNTIMES))}",
            )
        update_fields["runtime"] = runtime

    if "code" in data:
        if not isinstance(data["code"], str) or not data["code"].strip():
            return ApiResponse.validation_error("code", "cannot be empty")
        update_fields["code"] = data["code"]

    if "entrypoint" in data:
        update_fields["entrypoint"] = data["entrypoint"]

    if "timeout_seconds" in data:
        timeout_seconds, err = _validate_timeout(data["timeout_seconds"])
        if err:
            return err
        update_fields["timeout_seconds"] = timeout_seconds

    if "memory_mb" in data:
        memory_mb, err = _validate_memory(data["memory_mb"])
        if err:
            return err
        update_fields["memory_mb"] = memory_mb

    if "env_vars" in data or "env_vars_json" in data:
        env_vars = data.get("env_vars", data.get("env_vars_json"))
        if env_vars is not None and not isinstance(env_vars, dict):
            return ApiResponse.validation_error("env_vars", "must be a JSON object")
        update_fields["env_vars_json"] = env_vars or {}

    if "granted_scopes" in data or "granted_scopes_json" in data:
        caller_scopes, is_superuser = _caller_scopes_and_superuser()
        raw_granted_scopes = data.get("granted_scopes", data.get("granted_scopes_json"))
        try:
            update_fields["granted_scopes_json"] = validate_granted_scopes(
                raw_granted_scopes, caller_scopes, is_superuser=is_superuser
            )
        except ScopeValidationError as exc:
            extra: dict[str, Any] = {"field": "granted_scopes"}
            if exc.invalid_scopes:
                extra["invalid_scopes"] = exc.invalid_scopes
            if exc.missing_scopes:
                extra["missing_scopes"] = exc.missing_scopes
            return ApiResponse.error(exc.message, exc.status_code, **extra)

    if "allowed_egress_domains" in data or "allowed_egress_domains_json" in data:
        raw_egress_domains = data.get(
            "allowed_egress_domains", data.get("allowed_egress_domains_json")
        )
        try:
            update_fields["allowed_egress_domains_json"] = validate_egress_domains(
                raw_egress_domains
            )
        except EgressDomainValidationError as exc:
            return ApiResponse.error(
                exc.message,
                exc.status_code,
                field="allowed_egress_domains",
                invalid_domains=exc.invalid_domains,
            )

    if "schedule_cron" in data:
        update_fields["schedule_cron"] = data["schedule_cron"]

    if "execution_namespace" in data:
        ns = str(data["execution_namespace"] or "").strip()
        if ns and ns != DEFAULT_EXECUTION_NAMESPACE:
            return ApiResponse.validation_error(
                "execution_namespace",
                f"is operator-managed and locked to '{DEFAULT_EXECUTION_NAMESPACE}'; "
                "use worker_cluster to route to a remote worker pool",
            )
        update_fields["execution_namespace"] = DEFAULT_EXECUTION_NAMESPACE

    if "worker_cluster" in data:
        update_fields["worker_cluster"] = data["worker_cluster"]

    if "is_enabled" in data:
        update_fields["is_enabled"] = bool(data["is_enabled"])

    identity_id = _current_identity_id()

    def update():
        existing = get_tenant_scoped(db, db.run_functions, func_id, tenant_id)
        if not existing:
            return None, None, None

        new_code: str | None = update_fields.get("code")
        existing_sha = getattr(existing, "code_sha256", None)
        if new_code is not None:
            new_sha = compute_code_sha256(new_code)
            code_changed = (
                new_sha != existing_sha if existing_sha else new_code != existing.code
            )
        else:
            code_changed = False
        now = _utc_now()

        latest_ver_row = (
            db(
                (db.run_function_versions.function_id == func_id)
                & (db.run_function_versions.tenant_id == tenant_id)
            )
            .select(
                db.run_function_versions.version_number,
                orderby=~db.run_function_versions.version_number,
                limitby=(0, 1),
            )
            .first()
        )
        current_version = int(latest_ver_row.version_number) if latest_ver_row else 0

        if code_changed and new_code is not None:
            next_version = current_version + 1
            eff_runtime = update_fields.get("runtime", existing.runtime)
            storage = get_run_artifact_storage()
            artifact = storage.put_code(
                tenant_id=tenant_id,
                function_ref=getattr(existing, "village_id", None) or f"fn-{func_id}",
                version_number=next_version,
                runtime=eff_runtime,
                code=new_code,
            )
            stored_code = "" if artifact.storage_backend == "s3" else new_code
            update_fields["code"] = stored_code
            update_fields["storage_backend"] = artifact.storage_backend
            update_fields["storage_key"] = artifact.storage_key
            update_fields["code_sha256"] = artifact.code_sha256
            db.run_function_versions.insert(
                tenant_id=tenant_id,
                function_id=func_id,
                version_number=next_version,
                code=stored_code,
                storage_backend=artifact.storage_backend,
                storage_key=artifact.storage_key,
                code_sha256=artifact.code_sha256,
                runtime=eff_runtime,
                entrypoint=update_fields.get("entrypoint", existing.entrypoint),
                granted_scopes_json=update_fields.get(
                    "granted_scopes_json",
                    _parse_json_list(getattr(existing, "granted_scopes_json", None)),
                ),
                created_by_identity_id=identity_id,
                created_at=now,
            )
            current_version = next_version
        elif "code" in update_fields and (
            getattr(existing, "storage_backend", "database") == "s3"
        ):
            # Code unchanged on an S3-backed function: keep stored DB column empty.
            update_fields.pop("code", None)

        if update_fields:
            update_fields["updated_at"] = now
            db(
                (db.run_functions.id == func_id)
                & (db.run_functions.tenant_id == tenant_id)
            ).update(**update_fields)

        db.commit()
        updated = get_tenant_scoped(db, db.run_functions, func_id, tenant_id)
        return updated, max(current_version, 1), new_code

    row, latest_version, updated_code = await run_in_threadpool(update)
    if not row:
        return ApiResponse.not_found("Run function", func_id)

    logger.info(
        "run_function_updated",
        function_id=func_id,
        tenant_id=tenant_id,
        latest_version=latest_version,
    )
    return (
        jsonify(
            _serialize_function(
                row, latest_version=latest_version, code_override=updated_code
            )
        ),
        200,
    )


@bp.route("/<int:func_id>", methods=["DELETE"])
@login_required
@require_scope("runs:write")
async def delete_run_function(func_id: int):
    """Delete a serverless function for the current tenant."""
    db = current_app.db
    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return ApiResponse.forbidden("Tenant context required")

    def delete():
        existing = get_tenant_scoped(db, db.run_functions, func_id, tenant_id)
        if not existing:
            return False, []
        ver_rows = db(
            (db.run_function_versions.function_id == func_id)
            & (db.run_function_versions.tenant_id == tenant_id)
        ).select(db.run_function_versions.storage_key)
        keys_to_delete: list[str] = [
            str(r.storage_key) for r in ver_rows if getattr(r, "storage_key", None)
        ]
        existing_key = getattr(existing, "storage_key", None)
        if existing_key and str(existing_key) not in keys_to_delete:
            keys_to_delete.append(str(existing_key))
        db(
            (db.run_executions.function_id == func_id)
            & (db.run_executions.tenant_id == tenant_id)
        ).delete()
        db(
            (db.run_function_versions.function_id == func_id)
            & (db.run_function_versions.tenant_id == tenant_id)
        ).delete()
        db(
            (db.run_functions.id == func_id) & (db.run_functions.tenant_id == tenant_id)
        ).delete()
        db.commit()
        return True, keys_to_delete

    deleted, keys_to_delete = await run_in_threadpool(delete)
    if not deleted:
        return ApiResponse.not_found("Run function", func_id)

    if keys_to_delete:
        storage = get_run_artifact_storage()
        if storage.is_configured:
            for key in keys_to_delete:
                await run_in_threadpool(storage.delete_object, key)

    logger.info("run_function_deleted", function_id=func_id, tenant_id=tenant_id)
    return ApiResponse.no_content()


@bp.route("/<int:func_id>/invoke", methods=["POST"])
@login_required
@require_scope("runs:invoke")
async def invoke_run_function(func_id: int):
    """Queue a pending execution for a serverless function and return 202."""
    db = current_app.db
    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return ApiResponse.forbidden("Tenant context required")

    data = await request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return ApiResponse.error("Request body must be a JSON object", 400)

    trigger_type = str(data.get("trigger_type") or "manual").strip().lower()
    if trigger_type not in TRIGGER_TYPES:
        return ApiResponse.validation_error(
            "trigger_type",
            f"must be one of: {', '.join(sorted(TRIGGER_TYPES))}",
        )

    if "input_data" in data:
        input_payload = data["input_data"]
    elif "payload" in data:
        input_payload = data["payload"]
    elif "input_json" in data:
        input_payload = data["input_json"]
    else:
        input_payload = data.get("input")

    identity_id = _current_identity_id()

    def queue_execution():
        func = get_tenant_scoped(db, db.run_functions, func_id, tenant_id)
        if not func:
            return None, "not_found"
        if not bool(getattr(func, "is_enabled", True)):
            return None, "disabled"

        execution_id = str(uuid.uuid4())
        now = _utc_now()
        row_id = db.run_executions.insert(
            tenant_id=tenant_id,
            execution_id=execution_id,
            function_id=func_id,
            status="pending",
            trigger_type=trigger_type,
            triggered_by_identity_id=identity_id,
            input_json=input_payload,
            created_at=now,
        )
        db.commit()
        row = get_tenant_scoped(db, db.run_executions, row_id, tenant_id)
        return row, None

    exec_row, reason = await run_in_threadpool(queue_execution)
    if reason == "not_found":
        return ApiResponse.not_found("Run function", func_id)
    if reason == "disabled":
        return ApiResponse.error("Function is disabled", 400)

    serialized = _serialize_execution(exec_row)
    logger.info(
        "run_execution_queued",
        execution_id=exec_row.execution_id,
        function_id=func_id,
        tenant_id=tenant_id,
        trigger_type=trigger_type,
    )
    return (
        jsonify(
            {
                "execution_id": exec_row.execution_id,
                "execution": serialized,
            }
        ),
        202,
    )


@bp.route("/executions", methods=["GET"])
@login_required
@require_scope("runs:read")
async def list_run_executions():
    """List function executions for the current tenant with optional filters."""
    db = current_app.db
    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return ApiResponse.forbidden("Tenant context required")

    pagination = PaginationParams.from_request()
    function_id = request.args.get("function_id", type=int)
    status = (request.args.get("status") or "").strip().lower()
    trigger_type = (request.args.get("trigger_type") or "").strip().lower()

    if status and status not in EXECUTION_STATUSES:
        return ApiResponse.validation_error(
            "status",
            f"must be one of: {', '.join(sorted(EXECUTION_STATUSES))}",
        )
    if trigger_type and trigger_type not in TRIGGER_TYPES:
        return ApiResponse.validation_error(
            "trigger_type",
            f"must be one of: {', '.join(sorted(TRIGGER_TYPES))}",
        )

    def fetch():
        query = db.run_executions.tenant_id == tenant_id
        if function_id is not None:
            query &= db.run_executions.function_id == function_id
        if status:
            query &= db.run_executions.status == status
        if trigger_type:
            query &= db.run_executions.trigger_type == trigger_type

        total = db(query).count()
        rows = db(query).select(
            orderby=~db.run_executions.created_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )
        return total, rows

    total, rows = await run_in_threadpool(fetch)
    return (
        jsonify(
            {
                "items": [_serialize_execution(row) for row in rows],
                "total": total,
                "page": pagination.page,
                "per_page": pagination.per_page,
                "pages": pagination.calculate_pages(total),
            }
        ),
        200,
    )


@bp.route("/executions/<execution_id>", methods=["GET"])
@login_required
@require_scope("runs:read")
async def get_run_execution(execution_id: str):
    """Get a single execution record enriched with parent function metadata."""
    db = current_app.db
    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return ApiResponse.forbidden("Tenant context required")

    def fetch():
        exec_row = (
            db(
                (db.run_executions.execution_id == execution_id)
                & (db.run_executions.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not exec_row:
            return None, None
        func_row = get_tenant_scoped(
            db, db.run_functions, exec_row.function_id, tenant_id
        )
        return exec_row, func_row

    row, func_row = await run_in_threadpool(fetch)
    if not row:
        return ApiResponse.not_found("Run execution", execution_id)

    return jsonify(_serialize_execution(row, func_row=func_row)), 200


@bp.route("/executions/<execution_id>/cancel", methods=["POST"])
@login_required
@require_scope("runs:invoke")
async def cancel_run_execution(execution_id: str):
    """Cancel a pending or running execution for the current tenant."""
    db = current_app.db
    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return ApiResponse.forbidden("Tenant context required")

    def cancel():
        exec_row = (
            db(
                (db.run_executions.execution_id == execution_id)
                & (db.run_executions.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not exec_row:
            return None, None, "not_found"

        if (exec_row.status or "").lower() in TERMINAL_EXECUTION_STATUSES:
            return exec_row, None, "already_finished"

        now = _utc_now()
        duration_ms: int | None = None
        if getattr(exec_row, "started_at", None) is not None:
            started_dt = _parse_datetime(exec_row.started_at)
            if started_dt is not None:
                duration_ms = max(0, int((now - started_dt).total_seconds() * 1000))

        db(
            (db.run_executions.execution_id == execution_id)
            & (db.run_executions.tenant_id == tenant_id)
        ).update(
            status="cancelled",
            completed_at=now,
            error_message="Cancelled by user",
            duration_ms=duration_ms,
        )
        db.commit()

        updated = (
            db(
                (db.run_executions.execution_id == execution_id)
                & (db.run_executions.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        func_row = (
            get_tenant_scoped(db, db.run_functions, updated.function_id, tenant_id)
            if updated
            else None
        )
        return updated, func_row, None

    updated, func_row, reason = await run_in_threadpool(cancel)
    if reason == "not_found":
        return ApiResponse.not_found("Run execution", execution_id)
    if reason == "already_finished":
        return ApiResponse.error("Execution is already finished", 409)

    logger.info(
        "run_execution_cancelled",
        execution_id=execution_id,
        tenant_id=tenant_id,
        duration_ms=getattr(updated, "duration_ms", None),
    )
    return jsonify(_serialize_execution(updated, func_row=func_row)), 200


@bp.route("/internal/pending", methods=["GET"])
@login_required
@require_scope("runs:invoke")
async def claim_pending_executions():
    """Atomically claim pending executions for ``svc-runworker``."""
    auth_error = _require_runworker()
    if auth_error is not None:
        return auth_error

    db = current_app.db
    raw_limit = request.args.get("limit", default=5, type=int)
    limit = max(1, min(raw_limit if raw_limit is not None else 5, 50))
    worker_cluster = (request.args.get("worker_cluster") or "").strip() or None
    worker_id = (
        request.args.get("worker_id") or "svc-runworker"
    ).strip() or "svc-runworker"

    def claim() -> list[dict[str, Any]]:
        now = _utc_now()
        claimed_items: list[dict[str, Any]] = []
        engine = getattr(db, "engine", None)
        dialect_name = getattr(getattr(engine, "dialect", None), "name", "")

        if engine is not None and dialect_name == "postgresql":
            cluster_filter = (
                "AND f.worker_cluster = :worker_cluster " if worker_cluster else ""
            )
            claim_sql = text(
                f"""
                WITH next_jobs AS (
                    SELECT e.id
                    FROM run_executions e
                    JOIN run_functions f ON f.id = e.function_id
                    WHERE e.status = 'pending'
                      AND f.is_enabled = TRUE
                      {cluster_filter}
                    ORDER BY e.created_at ASC, e.id ASC
                    FOR UPDATE OF e SKIP LOCKED
                    LIMIT :limit
                )
                UPDATE run_executions e
                SET status = 'running',
                    started_at = :started_at,
                    worker_id = :worker_id
                FROM next_jobs nj
                JOIN run_executions e2 ON e2.id = nj.id
                JOIN run_functions f ON f.id = e2.function_id
                WHERE e.id = nj.id
                RETURNING
                    e.id,
                    e.tenant_id,
                    e.execution_id,
                    e.function_id,
                    e.status,
                    e.trigger_type,
                    e.worker_id,
                    e.input_json,
                    e.started_at,
                    f.name AS function_name,
                    f.village_id,
                    f.code,
                    f.storage_backend,
                    f.storage_key,
                    f.code_sha256,
                    f.runtime,
                    f.entrypoint,
                    f.timeout_seconds,
                    f.memory_mb,
                    f.env_vars_json,
                    f.granted_scopes_json,
                    f.allowed_egress_domains_json,
                    f.execution_namespace,
                    f.worker_cluster
                """  # nosec B608 - cluster_filter is a hardcoded SQL fragment
            )
            params: dict[str, Any] = {
                "limit": limit,
                "started_at": now,
                "worker_id": worker_id,
            }
            if worker_cluster:
                params["worker_cluster"] = worker_cluster

            with engine.begin() as conn:
                rows = conn.execute(claim_sql, params).mappings().all()
            storage = get_run_artifact_storage()
            for r in rows:
                s_backend = r["storage_backend"] or "database"
                s_key = r["storage_key"]
                code_url: str | None = None
                if s_backend == "s3" and s_key and storage.is_configured:
                    code_url = storage.generate_presigned_get_url(s_key)
                claimed_items.append(
                    {
                        "id": r["id"],
                        "tenant_id": r["tenant_id"],
                        "execution_id": r["execution_id"],
                        "function_id": r["function_id"],
                        "function_name": r["function_name"],
                        "village_id": r["village_id"],
                        "status": r["status"],
                        "trigger_type": r["trigger_type"],
                        "worker_id": r["worker_id"],
                        "started_at": _iso_or_none(r["started_at"]),
                        "code": r["code"],
                        "storage_backend": s_backend,
                        "storage_key": s_key,
                        "code_sha256": r["code_sha256"],
                        "code_url": code_url,
                        "runtime": r["runtime"],
                        "entrypoint": r["entrypoint"],
                        "timeout_seconds": int(
                            r["timeout_seconds"] or DEFAULT_TIMEOUT_SECONDS
                        ),
                        "memory_mb": int(r["memory_mb"] or DEFAULT_MEMORY_MB),
                        "env_vars": _parse_json_dict(r["env_vars_json"]),
                        "granted_scopes": _parse_json_list(r["granted_scopes_json"]),
                        "allowed_egress_domains": _parse_json_list(
                            r["allowed_egress_domains_json"]
                        ),
                        "execution_namespace": (
                            r["execution_namespace"] or DEFAULT_EXECUTION_NAMESPACE
                        ),
                        "worker_cluster": r["worker_cluster"],
                        "input_data": _parse_json_value(r["input_json"]),
                    }
                )
            return claimed_items

        # Generic / SQLite fallback: select pending candidates and conditionally claim
        pending_rows = db(db.run_executions.status == "pending").select(
            orderby=db.run_executions.id,
            limitby=(0, limit * 3),
        )  # tenant-scope-exempt: internal svc-runworker claims across active tenants

        for ex in pending_rows:
            if len(claimed_items) >= limit:
                break
            func = (
                db(db.run_functions.id == ex.function_id).select().first()
            )  # tenant-scope-exempt: internal svc-runworker joins claimed execution
            if not func or not bool(getattr(func, "is_enabled", True)):
                continue
            if (
                worker_cluster
                and getattr(func, "worker_cluster", None) != worker_cluster
            ):
                continue

            updated_count = db(
                (db.run_executions.id == ex.id)
                & (db.run_executions.status == "pending")
            ).update(
                status="running",
                started_at=now,
                worker_id=worker_id,
            )  # tenant-scope-exempt: internal svc-runworker atomic status transition
            if not updated_count:
                continue

            s_backend = getattr(func, "storage_backend", None) or "database"
            s_key = getattr(func, "storage_key", None)
            code_url = None
            if s_backend == "s3" and s_key:
                storage = get_run_artifact_storage()
                if storage.is_configured:
                    code_url = storage.generate_presigned_get_url(s_key)

            claimed_items.append(
                {
                    "id": ex.id,
                    "tenant_id": ex.tenant_id,
                    "execution_id": ex.execution_id,
                    "function_id": ex.function_id,
                    "function_name": func.name,
                    "village_id": getattr(func, "village_id", None),
                    "status": "running",
                    "trigger_type": getattr(ex, "trigger_type", "manual") or "manual",
                    "worker_id": worker_id,
                    "started_at": _iso_or_none(now),
                    "code": func.code,
                    "storage_backend": s_backend,
                    "storage_key": s_key,
                    "code_sha256": getattr(func, "code_sha256", None),
                    "code_url": code_url,
                    "runtime": func.runtime,
                    "entrypoint": getattr(func, "entrypoint", None),
                    "timeout_seconds": int(
                        getattr(func, "timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
                        or DEFAULT_TIMEOUT_SECONDS
                    ),
                    "memory_mb": int(
                        getattr(func, "memory_mb", DEFAULT_MEMORY_MB)
                        or DEFAULT_MEMORY_MB
                    ),
                    "env_vars": _parse_json_dict(getattr(func, "env_vars_json", None)),
                    "granted_scopes": _parse_json_list(
                        getattr(func, "granted_scopes_json", None)
                    ),
                    "allowed_egress_domains": _parse_json_list(
                        getattr(func, "allowed_egress_domains_json", None)
                    ),
                    "execution_namespace": (
                        getattr(func, "execution_namespace", None)
                        or DEFAULT_EXECUTION_NAMESPACE
                    ),
                    "worker_cluster": getattr(func, "worker_cluster", None),
                    "input_data": _parse_json_value(getattr(ex, "input_json", None)),
                }
            )
        db.commit()
        return claimed_items

    items = await run_in_threadpool(claim)
    for item in items:
        scopes = item.get("granted_scopes") or []
        if scopes:
            ttl = min(int(item["timeout_seconds"]) + 60, MAX_CODERUN_TTL_SECONDS)
            item["elder_api_token"] = generate_coderun_token(
                tenant_id=int(item["tenant_id"]),
                function_id=int(item["function_id"]),
                execution_id=str(item["execution_id"]),
                scopes=scopes,
                ttl_seconds=ttl,
            )
    logger.info(
        "run_executions_claimed",
        claimed_count=len(items),
        worker_id=worker_id,
        worker_cluster=worker_cluster,
    )
    return jsonify({"items": items, "count": len(items)}), 200


@bp.route("/internal/executions/<execution_id>/result", methods=["POST"])
@login_required
@require_scope("runs:invoke")
async def report_execution_result(execution_id: str):
    """Record terminal execution output and logs from ``svc-runworker``."""
    auth_error = _require_runworker()
    if auth_error is not None:
        return auth_error

    data = await request.get_json(silent=True)
    if not isinstance(data, dict):
        return ApiResponse.error("Request body must be a JSON object", 400)

    raw_status = str(data.get("status") or "").strip().lower()
    _status_aliases = {
        "succeeded": "completed",
        "success": "completed",
        "timeout": "timed_out",
    }
    status = _status_aliases.get(raw_status, raw_status)
    if status not in TERMINAL_EXECUTION_STATUSES:
        return ApiResponse.validation_error(
            "status",
            f"must be one of: {', '.join(sorted(TERMINAL_EXECUTION_STATUSES))}",
        )

    exit_code = data.get("exit_code")
    if exit_code is not None and (
        isinstance(exit_code, bool) or not isinstance(exit_code, int)
    ):
        return ApiResponse.validation_error("exit_code", "must be an integer")

    output_data = (
        data["output_data"]
        if "output_data" in data
        else data.get("output_json", data.get("output"))
    )
    stdout_log = data.get("stdout_log") if "stdout_log" in data else data.get("logs")
    stderr_log = data.get("stderr_log")
    error_message = (
        data.get("error_message") if "error_message" in data else data.get("error")
    )
    completed_at = _parse_datetime(data.get("completed_at")) or _utc_now()
    duration_ms_raw = data.get("duration_ms")

    db = current_app.db

    def save_result():
        existing = (
            db(db.run_executions.execution_id == execution_id).select().first()
        )  # tenant-scope-exempt: internal svc-runworker updates by unique execution_id
        if not existing:
            return None

        duration_ms: int | None = None
        if isinstance(duration_ms_raw, int) and not isinstance(duration_ms_raw, bool):
            duration_ms = max(0, duration_ms_raw)
        elif getattr(existing, "started_at", None) is not None:
            started_dt = _parse_datetime(existing.started_at)
            if started_dt is not None:
                duration_ms = max(
                    0, int((completed_at - started_dt).total_seconds() * 1000)
                )

        db(db.run_executions.execution_id == execution_id).update(
            status=status,
            exit_code=exit_code,
            output_json=output_data,
            stdout_log=stdout_log,
            stderr_log=stderr_log,
            error_message=error_message,
            completed_at=completed_at,
            duration_ms=duration_ms,
        )  # tenant-scope-exempt: internal svc-runworker updates by unique execution_id
        db.commit()
        return (
            db(db.run_executions.execution_id == execution_id).select().first()
        )  # tenant-scope-exempt: internal svc-runworker read-back

    updated = await run_in_threadpool(save_result)
    if not updated:
        return ApiResponse.not_found("Run execution", execution_id)

    logger.info(
        "run_execution_completed",
        execution_id=execution_id,
        status=status,
        exit_code=exit_code,
        duration_ms=getattr(updated, "duration_ms", None),
    )
    return jsonify(_serialize_execution(updated)), 200


@bp.route("/internal/executions/<execution_id>/logs", methods=["POST"])
@login_required
@require_scope("runs:invoke")
async def flush_execution_logs(execution_id: str):
    """Update in-progress ``stdout_log`` for a running execution from ``svc-runworker``."""
    auth_error = _require_runworker()
    if auth_error is not None:
        return auth_error

    data = await request.get_json(silent=True)
    if not isinstance(data, dict):
        return ApiResponse.error("Request body must be a JSON object", 400)

    raw_logs = data.get("logs")
    if raw_logs is None:
        raw_logs = data.get("stdout_log")
    if not isinstance(raw_logs, str):
        return ApiResponse.validation_error("logs", "must be a string")

    worker_id = data.get("worker_id")
    capped_logs = _truncate_log_bytes(raw_logs, MAX_LIVE_LOG_BYTES)
    db = current_app.db

    def save_logs():
        existing = (
            db(db.run_executions.execution_id == execution_id).select().first()
        )  # tenant-scope-exempt: internal svc-runworker updates by unique execution_id
        if not existing:
            return None, False

        if existing.status != "running":
            return existing, False

        updated_count = db(
            (db.run_executions.execution_id == execution_id)
            & (db.run_executions.status == "running")
        ).update(
            stdout_log=capped_logs,
        )  # tenant-scope-exempt: internal svc-runworker updates by unique execution_id
        db.commit()
        return existing, bool(updated_count)

    existing, updated = await run_in_threadpool(save_logs)
    if not existing:
        return ApiResponse.not_found("Run execution", execution_id)

    log_bytes = len(capped_logs.encode("utf-8"))
    logger.debug(
        "run_execution_logs_flushed",
        execution_id=execution_id,
        worker_id=worker_id,
        status=existing.status,
        updated=updated,
        bytes=log_bytes,
    )
    return (
        jsonify(
            {
                "execution_id": execution_id,
                "status": existing.status,
                "updated": updated,
                "bytes": log_bytes,
            }
        ),
        200,
    )
