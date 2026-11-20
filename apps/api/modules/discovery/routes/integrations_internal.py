"""Internal REST endpoints for the Rust ``svc-integrations`` service.

These routes centralize Elder PostgreSQL access for 3P connectors, issue sync,
discovery job orchestration, IAM sync, and Google Workspace directory sync so
``svc-integrations`` never connects directly to Elder's database.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from quart import Blueprint, current_app, g, jsonify, request

from apps.api.auth.decorators import login_required, require_scope
from apps.api.auth.jwt_handler import is_system_service_worker
from apps.api.services.discovery import DiscoveryService

logger = structlog.get_logger(__name__)

bp = Blueprint("integrations_internal", __name__)

SCANNER_OWNED_PROVIDERS: frozenset[str] = frozenset(
    {"network", "http_screenshot", "banner"}
)
VALID_DISCOVERY_STATUSES: frozenset[str] = frozenset({"completed", "partial", "failed"})

_VALID_ISSUE_STATUSES: frozenset[str] = frozenset(
    {"OPEN", "IN_PROGRESS", "CLOSED", "RESOLVED"}
)
_VALID_ISSUE_PRIORITIES: frozenset[str] = frozenset(
    {"LOW", "MEDIUM", "HIGH", "URGENT", "CRITICAL"}
)
_VALID_ISSUE_TYPES: frozenset[str] = frozenset(
    {
        "OPERATIONS",
        "CODE",
        "CONFIG",
        "SECURITY",
        "ARCHITECTURE",
        "PROCESS",
        "APPROVAL",
        "FEATURE",
        "BUG",
        "SUPPORT",
        "OTHER",
    }
)
_ISSUE_TYPE_ALIASES: dict[str, str] = {
    "task": "OPERATIONS",
    "incident": "OPERATIONS",
    "question": "SUPPORT",
    "story": "FEATURE",
    "enhancement": "FEATURE",
}
_ISSUE_STATUS_ALIASES: dict[str, str] = {
    "open": "OPEN",
    "to_do": "OPEN",
    "todo": "OPEN",
    "new": "OPEN",
    "in_progress": "IN_PROGRESS",
    "in progress": "IN_PROGRESS",
    "active": "IN_PROGRESS",
    "closed": "CLOSED",
    "done": "CLOSED",
    "resolved": "RESOLVED",
    "completed": "RESOLVED",
}

_RESOURCE_CATEGORY_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        (
            "vpc",
            "subnet",
            "security_group",
            "load_balancer",
            "elb",
            "alb",
            "nlb",
            "nat_gateway",
            "route_table",
            "firewall",
            "network",
            "ingress",
            "gateway",
        ),
        "network",
    ),
    (("bucket", "s3", "gcs", "storage", "volume", "ebs", "disk", "blob"), "storage"),
    (
        ("db", "rds", "sql", "database", "redis", "dynamodb", "firestore", "bigquery"),
        "database",
    ),
    (("lambda", "function", "cloud_function", "serverless"), "serverless"),
    (("iam", "role", "user", "group", "policy", "service_account"), "iam"),
    (("fleetdm_security", "security", "vulnerability", "finding", "trivy"), "security"),
)


def _require_svc_integrations():
    """Enforce that the caller is the ``svc-integrations`` system service worker."""
    claims = getattr(g, "claims", None) or {}
    user = getattr(g, "current_user", None)
    if (
        not is_system_service_worker()
        or claims.get("sub") != "svc-integrations"
        or getattr(user, "username", None) != "svc-integrations"
    ):
        return (
            jsonify({"error": "Forbidden: svc-integrations service worker required"}),
            403,
        )

    raw_scope = claims.get("scope")
    if isinstance(raw_scope, str):
        scopes = set(raw_scope.split())
    elif isinstance(raw_scope, (list, tuple, set)):
        scopes = {str(item) for item in raw_scope}
    else:
        scopes = set()

    if "integrations:sync" not in scopes:
        return (
            jsonify({"error": "Forbidden: integrations:sync scope required"}),
            403,
        )
    return None


def _utc_now() -> datetime:
    """Return the current timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


def _ensure_utc(dt: datetime) -> datetime:
    """Normalize naive datetimes (e.g., SQLite) to timezone-aware UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    """Parse a datetime or ISO-8601 string into UTC datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return _ensure_utc(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return _ensure_utc(datetime.fromisoformat(text))
        except ValueError:
            return None
    return None


def _iso_or_none(value: Any) -> str | None:
    """Format a datetime or timestamp string cleanly as ISO-8601."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return _ensure_utc(value).isoformat()
    if isinstance(value, str):
        return value
    return str(value)


def _parse_json_dict(raw: Any) -> dict[str, Any]:
    """Safely decode a JSON column or dict into a shallow dict copy."""
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except (TypeError, ValueError):
            return {}
    return {}


def _decode_raw_json(raw: Any) -> Any:
    """Decode raw JSON string if valid JSON, preserving dicts or fallback strings."""
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return raw
    return {} if raw is None else raw


def _safe_int(value: Any) -> int | None:
    """Parse an integer value if present, else return None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _has_column(table: Any, column_name: str) -> bool:
    """Check whether a penguin-dal or PyDAL table defines ``column_name``."""
    inner_table = getattr(table, "table", None)
    if inner_table is not None and hasattr(inner_table, "columns"):
        return column_name in inner_table.columns
    if "fields" in getattr(table, "__dict__", {}):
        return column_name in table.fields
    try:
        getattr(table, column_name)
        return True
    except AttributeError:
        return False


def _safe_rollback(db: Any) -> None:
    """Roll back the active DB transaction when supported by the DAL adapter."""
    if "rollback" in getattr(db, "__dict__", {}) or hasattr(type(db), "rollback"):
        try:
            db.rollback()
        except Exception:
            pass


def _positive_int(value: Any, default: int) -> int:
    """Return a positive integer or the supplied default."""
    parsed = _safe_int(value)
    if parsed is not None and parsed > 0:
        return parsed
    return default


def _is_due(
    last_run_at: Any,
    interval_seconds: int,
    now: datetime,
    force: bool,
    *,
    next_run_at: Any = None,
) -> bool:
    """Determine whether a scheduled integration job/provider is due."""
    if force:
        return True
    if last_run_at is None:
        return True
    if next_run_at is not None:
        next_dt = _parse_datetime(next_run_at)
        if next_dt is not None and next_dt <= now:
            return True
    last_dt = _parse_datetime(last_run_at)
    if last_dt is None:
        return True
    return (last_dt + timedelta(seconds=interval_seconds)) <= now


def _resolve_discovery_credentials(
    db: Any,
    job: Any,
    organization_id: int,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Merge credential_id references safely into discovery job config."""
    merged = dict(config)
    cred_id = _safe_int(getattr(job, "credential_id", None))
    if cred_id is None:
        return merged

    cred_type = str(getattr(job, "credential_type", None) or "").strip().lower()
    mapping = _parse_json_dict(getattr(job, "credential_mapping", None))
    secret_data: dict[str, Any] = {}

    if cred_type in ("", "builtin_secret", "secret") and "builtin_secrets" in db.tables:
        secret = (
            db(
                (db.builtin_secrets.id == cred_id)
                & (db.builtin_secrets.is_active == True)  # noqa: E712
            )
            .select()
            .first()
        )  # tenant-scope-exempt: verified against owning organization below
        if secret is not None:
            secret_org = _safe_int(getattr(secret, "organization_id", None))
            if secret_org is None or secret_org == organization_id:
                secret_data = _parse_json_dict(getattr(secret, "secret_json", None))
                raw_val = getattr(secret, "secret_value", None)
                if not secret_data and raw_val:
                    parsed = _parse_json_dict(raw_val)
                    secret_data = parsed if parsed else {"secret_value": raw_val}
    elif cred_type == "cloud_account" and "cloud_accounts" in db.tables:
        account = (
            db(
                (db.cloud_accounts.id == cred_id) & (db.cloud_accounts.enabled == True)  # noqa: E712
            )
            .select()
            .first()
        )  # tenant-scope-exempt: verified against owning organization below
        if account is not None:
            acct_org = _safe_int(getattr(account, "organization_id", None))
            if acct_org is None or acct_org == organization_id:
                secret_data = _parse_json_dict(
                    getattr(account, "credentials_json", None)
                )

    if not secret_data:
        return merged

    if mapping:
        for target_key, source_key in mapping.items():
            if isinstance(source_key, str) and source_key in secret_data:
                merged[target_key] = secret_data[source_key]
    else:
        for key, value in secret_data.items():
            if value is not None:
                merged[key] = value
    return merged


def _serialize_issue(issue: Any, external_id: str | None = None) -> dict[str, Any]:
    """Serialize an Elder issue row cleanly for two-way integration sync."""
    return {
        "id": issue.id,
        "title": issue.title,
        "description": getattr(issue, "description", None),
        "status": getattr(issue, "status", "OPEN"),
        "priority": getattr(issue, "priority", "MEDIUM"),
        "issue_type": getattr(issue, "issue_type", "OPERATIONS"),
        "tenant_id": getattr(issue, "tenant_id", None),
        "organization_id": getattr(issue, "organization_id", None),
        "resource_type": getattr(issue, "resource_type", None),
        "resource_id": getattr(issue, "resource_id", None),
        "metadata": _parse_json_dict(getattr(issue, "metadata", None)),
        "external_id": external_id,
        "created_at": _iso_or_none(getattr(issue, "created_at", None)),
        "updated_at": _iso_or_none(getattr(issue, "updated_at", None)),
    }


def _collect_local_sync_items(
    db: Any,
    sync_config: Any,
    config: dict[str, Any],
    mappings: list[Any],
    organization_id: int | None,
    tenant_id: int | None,
) -> list[dict[str, Any]]:
    """Collect mapped and recent in-scope issues for a sync config."""
    if "issues" not in db.tables:
        return []

    by_id: dict[int, dict[str, Any]] = {}
    mapped_issue_ids: dict[int, str] = {}
    for mapping in mappings:
        if getattr(mapping, "elder_type", None) == "issue" and getattr(
            mapping, "elder_id", None
        ):
            mapped_issue_ids[int(mapping.elder_id)] = str(mapping.external_id)

    if mapped_issue_ids:
        mapped_rows = db(
            db.issues.id.belongs(list(mapped_issue_ids))
        ).select()  # tenant-scope-exempt: internal svc-integrations scoped to config
        for row in mapped_rows:
            if tenant_id is not None and getattr(row, "tenant_id", None) != tenant_id:
                continue
            by_id[row.id] = _serialize_issue(row, mapped_issue_ids.get(row.id))

    project_id = _safe_int(config.get("elder_project_id") or config.get("project_id"))
    limit = _positive_int(getattr(sync_config, "batch_size", None), default=100)

    scoped_ids_from_project: set[int] | None = None
    if project_id is not None and "issue_project_links" in db.tables:
        link_rows = db(
            db.issue_project_links.project_id == project_id
        ).select()  # tenant-scope-exempt: internal svc-integrations project link query
        scoped_ids_from_project = {int(link.issue_id) for link in link_rows}

    query: Any = None
    if organization_id is not None:
        org_cond = (db.issues.resource_type == "organization") & (
            db.issues.resource_id == organization_id
        )
        if _has_column(db.issues, "organization_id"):
            org_cond = org_cond | (db.issues.organization_id == organization_id)
        query = org_cond
    if tenant_id is not None:
        tenant_cond = db.issues.tenant_id == tenant_id
        query = tenant_cond if query is None else (query & tenant_cond)
    if scoped_ids_from_project is not None:
        if not scoped_ids_from_project:
            return list(by_id.values())
        proj_cond = db.issues.id.belongs(list(scoped_ids_from_project))
        query = proj_cond if query is None else (query & proj_cond)

    if query is not None and (
        bool(getattr(sync_config, "two_way_create", False)) or project_id is not None
    ):
        recent_rows = db(
            query
        ).select(
            orderby=~db.issues.updated_at,
            limitby=(0, limit),
        )  # tenant-scope-exempt: internal svc-integrations filtered by active org/tenant
        for row in recent_rows:
            if row.id not in by_id:
                by_id[row.id] = _serialize_issue(row, mapped_issue_ids.get(row.id))

    return list(by_id.values())


def _infer_discovery_category(resource: dict[str, Any]) -> str:
    """Infer DiscoveryService result category from explicit category or resource_type."""
    explicit = str(resource.get("category") or "").strip().lower()
    if explicit:
        return explicit
    rtype = str(resource.get("resource_type") or "").strip().lower()
    for hints, category in _RESOURCE_CATEGORY_HINTS:
        if any(hint in rtype for hint in hints):
            return category
    return "compute"


def _normalize_discovery_payload(
    job_provider: str,
    resources: list[dict[str, Any]],
    dependencies: list[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """Group flat resource items by category and attach matching dependency edges."""
    by_external_id: dict[str, dict[str, Any]] = {}
    grouped: dict[str, list[dict[str, Any]]] = {}

    for raw_item in resources:
        if not isinstance(raw_item, dict):
            continue
        item = dict(raw_item)
        ext_id = item.get("resource_id") or item.get("external_id")
        if ext_id is not None and "resource_id" not in item:
            item["resource_id"] = str(ext_id)
        if not item.get("provider"):
            item["provider"] = job_provider
        category = _infer_discovery_category(item)
        grouped.setdefault(category, []).append(item)
        if ext_id is not None:
            by_external_id[str(ext_id)] = item

    remaining_deps: list[dict[str, Any]] = []
    for dep in dependencies:
        if not isinstance(dep, dict):
            continue
        src_ext = dep.get("source_external_id")
        if src_ext is not None and str(src_ext) in by_external_id:
            src_item = by_external_id[str(src_ext)]
            relationships = list(src_item.get("relationships") or [])
            relationships.append(
                {
                    "target_external_id": dep.get("target_external_id"),
                    "edge_type": dep.get("edge_type") or "depends_on",
                    "target_kind": dep.get("target_kind") or dep.get("target_type"),
                    "metadata": dep.get("metadata") or {},
                }
            )
            src_item["relationships"] = relationships
        else:
            remaining_deps.append(dep)

    return grouped, remaining_deps


def _resolve_db_record_for_dep(
    db: Any,
    organization_id: int,
    ext_id: Any,
    kind: str | None,
) -> tuple[str, int] | None:
    """Resolve an existing entity/resource row in organization_id for standalone edges."""
    if ext_id is None:
        return None
    ext_str = str(ext_id)
    table_map = {
        "entity": ("entities", "entity"),
        "networking_resource": ("networking_resources", "networking_resource"),
        "data_store": ("data_stores", "data_store"),
        "serverless_function": ("serverless_functions", "serverless_function"),
        "service": ("services", "service"),
    }
    candidates = (
        [table_map[kind]] if kind and kind in table_map else list(table_map.values())
    )
    for table_name, dep_type in candidates:
        if table_name not in db.tables:
            continue
        table = getattr(db, table_name)
        row = (
            db(
                (table.organization_id == organization_id)
                & (table.external_id == ext_str)
            )
            .select(table.id)
            .first()
        )  # tenant-scope-exempt: internal svc-integrations scoped to job organization
        if row is not None:
            return dep_type, int(row.id)
    return None


def _normalize_issue_status(raw: Any) -> str:
    """Normalize external status strings to Elder's IssueStatus enum."""
    if not raw:
        return "OPEN"
    text = str(raw).strip()
    upper = text.upper()
    if upper in _VALID_ISSUE_STATUSES:
        return upper
    return _ISSUE_STATUS_ALIASES.get(text.lower(), "OPEN")


def _normalize_issue_priority(raw: Any) -> str:
    """Normalize priority strings to Elder's IssuePriority enum."""
    if not raw:
        return "MEDIUM"
    upper = str(raw).strip().upper()
    return upper if upper in _VALID_ISSUE_PRIORITIES else "MEDIUM"


def _normalize_issue_type(raw: Any) -> str:
    """Normalize issue type strings to Elder's IssueType enum."""
    if not raw:
        return "OPERATIONS"
    text = str(raw).strip()
    upper = text.upper()
    if upper in _VALID_ISSUE_TYPES:
        return upper
    return _ISSUE_TYPE_ALIASES.get(text.lower(), "OPERATIONS")


def _upsert_sync_mapping(
    db: Any,
    sync_config: Any,
    *,
    elder_type: str,
    elder_id: int,
    external_id: str,
    sync_method: str,
    sync_status: str,
    elder_updated_at: datetime | None,
    external_updated_at: datetime | None,
    now: datetime,
) -> int:
    """Insert or update a ``sync_mappings`` record for a config/item pair."""
    existing = (
        db(
            (db.sync_mappings.sync_config_id == sync_config.id)
            & (db.sync_mappings.elder_type == elder_type)
            & (db.sync_mappings.external_id == external_id)
        )
        .select()
        .first()
    )  # tenant-scope-exempt: internal svc-integrations scoped to sync_config
    if existing is None:
        existing = (
            db(
                (db.sync_mappings.sync_config_id == sync_config.id)
                & (db.sync_mappings.elder_type == elder_type)
                & (db.sync_mappings.elder_id == elder_id)
            )
            .select()
            .first()
        )  # tenant-scope-exempt: internal svc-integrations scoped to sync_config

    fields: dict[str, Any] = {
        "elder_type": elder_type,
        "elder_id": elder_id,
        "external_platform": sync_config.platform,
        "external_id": external_id,
        "sync_config_id": sync_config.id,
        "sync_status": sync_status,
        "sync_method": sync_method,
        "last_synced_at": now,
    }
    if elder_updated_at is not None:
        fields["elder_updated_at"] = elder_updated_at
    if external_updated_at is not None:
        fields["external_updated_at"] = external_updated_at

    if existing is not None:
        db(db.sync_mappings.id == existing.id).update(
            **fields
        )  # tenant-scope-exempt: internal svc-integrations update by mapping pk
        return int(existing.id)

    fields["created_at"] = now
    return int(db.sync_mappings.insert(**fields))


def _resolve_unique_identity_username(
    db: Any,
    preferred: str,
    provider_type: str,
    organization_id: int,
    ext_id: str,
    *,
    existing_id: int | None = None,
) -> str:
    """Pick a collision-free ``identities.username`` across tenants."""
    clash = (
        db(db.identities.username == preferred).select(db.identities.id).first()
    )  # tenant-scope-exempt: username column is globally unique
    if clash is None or (existing_id is not None and clash.id == existing_id):
        return preferred
    scoped = f"{provider_type}:{organization_id}:{ext_id}"
    clash_scoped = (
        db(db.identities.username == scoped).select(db.identities.id).first()
    )  # tenant-scope-exempt: username column is globally unique
    if clash_scoped is None or (
        existing_id is not None and clash_scoped.id == existing_id
    ):
        return scoped
    return f"{provider_type}:{organization_id}:{uuid.uuid5(uuid.NAMESPACE_URL, ext_id)}"


def _resolve_unique_group_name(
    db: Any,
    preferred: str,
    provider_type: str,
    organization_id: int,
    ext_id: str,
    *,
    existing_id: int | None = None,
) -> str:
    """Pick a collision-free ``identity_groups.name`` across organizations."""
    clash = (
        db(db.identity_groups.name == preferred).select(db.identity_groups.id).first()
    )  # tenant-scope-exempt: identity_groups.name column is globally unique
    if clash is None or (existing_id is not None and clash.id == existing_id):
        return preferred
    return f"{preferred} ({provider_type}:{organization_id}:{ext_id[:16]})"


def _upsert_directory_membership(
    db: Any,
    *,
    identity_id: int,
    group_id: int,
    provider_member_id: str | None,
    now: datetime,
) -> bool:
    """Upsert an ``identity_group_memberships`` link without duplicate rows."""
    if "identity_group_memberships" not in db.tables:
        return False
    existing = (
        db(
            (db.identity_group_memberships.identity_id == identity_id)
            & (db.identity_group_memberships.group_id == group_id)
        )
        .select()
        .first()
    )  # tenant-scope-exempt: scoped via validated identity/group ids
    if existing is not None:
        db(db.identity_group_memberships.id == existing.id).update(
            provider_synced=True,
            provider_synced_at=now,
            provider_member_id=provider_member_id or existing.provider_member_id,
        )  # tenant-scope-exempt: scoped via validated membership pk
        return True
    db.identity_group_memberships.insert(
        identity_id=identity_id,
        group_id=group_id,
        provider_synced=True,
        provider_synced_at=now,
        provider_member_id=provider_member_id,
        created_at=now,
    )
    return True


def _apply_directory_sync(
    db: Any,
    *,
    provider_type: str,
    organization_id: int,
    tenant_id: int,
    users: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    roles: list[dict[str, Any]],
    memberships: list[dict[str, Any]],
    tokenize_pii: bool,
    now: datetime,
) -> dict[str, int]:
    """Persist synced directory users, groups, roles, and memberships cleanly."""
    user_id_by_ref: dict[str, int] = {}
    group_id_by_ref: dict[str, int] = {}
    role_id_by_ref: dict[str, int] = {}
    users_synced = 0
    groups_synced = 0
    roles_synced = 0
    memberships_synced = 0

    for raw_user in users:
        if not isinstance(raw_user, dict):
            continue
        ext_id = str(
            raw_user.get("external_id")
            or raw_user.get("id")
            or raw_user.get("user_id")
            or raw_user.get("arn")
            or raw_user.get("uuid")
            or raw_user.get("token")
            or ""
        )
        if not ext_id:
            continue

        token_ref = str(
            raw_user.get("uuid")
            or raw_user.get("token")
            or (
                ""
                if tokenize_pii
                else (
                    raw_user.get("username")
                    or raw_user.get("email")
                    or raw_user.get("primaryEmail")
                    or ""
                )
            )
        )
        if tokenize_pii:
            candidate = token_ref or raw_user.get("username") or ""
            if not candidate or "@" in str(candidate):
                candidate = f"iam-{uuid.uuid5(uuid.NAMESPACE_URL, f'{provider_type}:{organization_id}:{ext_id}')}"
            preferred_username = str(candidate)
            email_val = None
            full_name_val = None
        else:
            raw_email = raw_user.get("email") or raw_user.get("primaryEmail")
            raw_name = raw_user.get("full_name")
            if raw_name is None and isinstance(raw_user.get("name"), dict):
                raw_name = raw_user["name"].get("fullName")
            preferred_username = str(
                token_ref or raw_email or f"{provider_type}:{organization_id}:{ext_id}"
            )
            email_val = str(raw_email) if raw_email else None
            full_name_val = (
                str(raw_name) if raw_name and raw_name != email_val else None
            )

        is_active = (
            not bool(raw_user["suspended"])
            if "suspended" in raw_user
            else bool(raw_user.get("is_active", True))
        )
        identity_type = str(
            raw_user.get("identity_type")
            or (
                "service_account"
                if tokenize_pii and raw_user.get("is_service_account")
                else "human"
            )
        ).lower()

        existing = (
            db(
                (db.identities.auth_provider == provider_type)
                & (db.identities.auth_provider_id == ext_id)
                & (db.identities.organization_id == organization_id)
            )
            .select()
            .first()
        )  # tenant-scope-exempt: internal svc-integrations scoped to provider organization
        if existing is None:
            existing = (
                db(
                    (db.identities.external_id == ext_id)
                    & (db.identities.organization_id == organization_id)
                )
                .select()
                .first()
            )  # tenant-scope-exempt: internal svc-integrations scoped to provider organization

        username = _resolve_unique_identity_username(
            db,
            preferred_username,
            provider_type,
            organization_id,
            ext_id,
            existing_id=existing.id if existing is not None else None,
        )
        identity_fields: dict[str, Any] = {
            "tenant_id": tenant_id,
            "organization_id": organization_id,
            "identity_type": identity_type,
            "username": username,
            "email": email_val,
            "full_name": full_name_val,
            "auth_provider": provider_type,
            "auth_provider_id": ext_id,
            "external_id": ext_id,
            "is_active": is_active,
            "is_superuser": False,
            "mfa_enabled": bool(raw_user.get("mfa_enabled", False)),
            "must_change_password": False,
            "portal_role": str(raw_user.get("portal_role") or "observer").lower(),
            "updated_at": now,
        }
        if existing is not None:
            db(db.identities.id == existing.id).update(
                **identity_fields
            )  # tenant-scope-exempt: internal svc-integrations scoped to existing pk
            ident_id = int(existing.id)
        else:
            identity_fields["do_not_sell_share"] = False
            identity_fields["created_at"] = now
            ident_id = int(db.identities.insert(**identity_fields))

        users_synced += 1
        user_id_by_ref[ext_id] = ident_id
        if raw_user.get("uuid"):
            user_id_by_ref[str(raw_user["uuid"])] = ident_id
        if not tokenize_pii and (raw_user.get("email") or raw_user.get("primaryEmail")):
            user_id_by_ref[
                str(raw_user.get("email") or raw_user.get("primaryEmail"))
            ] = ident_id

        inline_groups = raw_user.get("groups") or []
        for grp_ref in inline_groups:
            memberships.append(
                {
                    "user_external_id": ext_id,
                    "group_external_id": str(grp_ref),
                }
            )

    for raw_group in groups:
        if not isinstance(raw_group, dict):
            continue
        grp_ext_id = str(
            raw_group.get("external_id")
            or raw_group.get("id")
            or raw_group.get("group_id")
            or raw_group.get("name")
            or ""
        )
        if not grp_ext_id:
            continue

        preferred_name = str(
            raw_group.get("name")
            or raw_group.get("email")
            or f"{provider_type}-group:{organization_id}:{grp_ext_id}"
        )
        existing_grp = (
            db(
                (db.identity_groups.provider == provider_type)
                & (db.identity_groups.provider_group_id == grp_ext_id)
            )
            .select()
            .first()
        )  # tenant-scope-exempt: internal svc-integrations group upsert by provider id
        grp_name = _resolve_unique_group_name(
            db,
            preferred_name,
            provider_type,
            organization_id,
            grp_ext_id,
            existing_id=existing_grp.id if existing_grp is not None else None,
        )
        grp_fields: dict[str, Any] = {
            "name": grp_name,
            "description": raw_group.get("description"),
            "is_active": bool(raw_group.get("is_active", True)),
            "provider": provider_type,
            "provider_group_id": grp_ext_id,
            "sync_enabled": True,
            "updated_at": now,
        }
        if existing_grp is not None:
            db(db.identity_groups.id == existing_grp.id).update(
                **grp_fields
            )  # tenant-scope-exempt: internal svc-integrations group update by pk
            grp_id = int(existing_grp.id)
        else:
            grp_fields["review_enabled"] = False
            grp_fields["review_auto_apply"] = False
            grp_fields["created_at"] = now
            grp_id = int(db.identity_groups.insert(**grp_fields))

        groups_synced += 1
        group_id_by_ref[grp_ext_id] = grp_id
        if raw_group.get("name"):
            group_id_by_ref[str(raw_group["name"])] = grp_id

    if "roles" in db.tables:
        for raw_role in roles:
            if not isinstance(raw_role, dict):
                continue
            role_name = str(
                raw_role.get("name")
                or raw_role.get("role_name")
                or raw_role.get("external_id")
                or raw_role.get("id")
                or ""
            )
            if not role_name:
                continue
            role_ext_id = str(
                raw_role.get("external_id")
                or raw_role.get("arn")
                or raw_role.get("id")
                or role_name
            )
            existing_role = (
                db(db.roles.name == role_name).select().first()
            )  # tenant-scope-exempt: roles table is system RBAC catalog
            if existing_role is not None:
                role_id = int(existing_role.id)
            else:
                role_id = int(
                    db.roles.insert(
                        name=role_name,
                        description=raw_role.get("description"),
                        created_at=now,
                        updated_at=now,
                    )
                )

            if (
                tokenize_pii
                and "entities" in db.tables
                and (raw_role.get("arn") or raw_role.get("external_id"))
            ):
                existing_ent = (
                    db(
                        (db.entities.organization_id == organization_id)
                        & (db.entities.external_id == role_ext_id)
                    )
                    .select()
                    .first()
                )  # tenant-scope-exempt: internal svc-integrations scoped to organization
                ent_fields = {
                    "organization_id": organization_id,
                    "name": role_name,
                    "type": "iam_role",
                    "sub_type": provider_type,
                    "external_id": role_ext_id,
                    "cloud_provider": provider_type,
                    "status": "active",
                    "is_managed": True,
                    "last_seen_at": now,
                    "updated_at": now,
                }
                if existing_ent is not None:
                    db(db.entities.id == existing_ent.id).update(
                        **ent_fields
                    )  # tenant-scope-exempt: internal svc-integrations update by entity pk
                else:
                    ent_fields["created_at"] = now
                    db.entities.insert(**ent_fields)

            roles_synced += 1
            role_id_by_ref[role_ext_id] = role_id
            role_id_by_ref[role_name] = role_id

    for raw_mem in memberships:
        if not isinstance(raw_mem, dict):
            continue
        user_ref = str(
            raw_mem.get("user_external_id")
            or raw_mem.get("identity_external_id")
            or raw_mem.get("user_id")
            or raw_mem.get("member_email")
            or raw_mem.get("user_uuid")
            or ""
        )
        mem_ident_id: int | None = user_id_by_ref.get(user_ref)
        if mem_ident_id is None and user_ref:
            found_ident = (
                db(
                    (db.identities.organization_id == organization_id)
                    & (
                        (db.identities.auth_provider_id == user_ref)
                        | (db.identities.external_id == user_ref)
                        | (db.identities.username == user_ref)
                    )
                )
                .select(db.identities.id)
                .first()
            )  # tenant-scope-exempt: internal svc-integrations scoped to organization
            if found_ident is not None:
                mem_ident_id = int(found_ident.id)
        if mem_ident_id is None:
            continue

        group_ref = raw_mem.get("group_external_id") or raw_mem.get("group_id")
        if group_ref is not None:
            grp_key = str(group_ref)
            mem_grp_id: int | None = group_id_by_ref.get(grp_key)
            if mem_grp_id is None:
                found_grp = (
                    db(
                        (db.identity_groups.provider == provider_type)
                        & (
                            (db.identity_groups.provider_group_id == grp_key)
                            | (db.identity_groups.name == grp_key)
                        )
                    )
                    .select(db.identity_groups.id)
                    .first()
                )  # tenant-scope-exempt: internal svc-integrations group resolution
                if found_grp is not None:
                    mem_grp_id = int(found_grp.id)
            if mem_grp_id is not None and _upsert_directory_membership(
                db,
                identity_id=mem_ident_id,
                group_id=mem_grp_id,
                provider_member_id=raw_mem.get("provider_member_id") or user_ref,
                now=now,
            ):
                memberships_synced += 1

        role_ref = (
            raw_mem.get("role_external_id")
            or raw_mem.get("role_name")
            or raw_mem.get("role_id")
        )
        if role_ref is not None and "user_roles" in db.tables:
            r_id = role_id_by_ref.get(str(role_ref))
            if r_id is None and "roles" in db.tables:
                found_role = (
                    db(db.roles.name == str(role_ref)).select(db.roles.id).first()
                )  # tenant-scope-exempt: roles catalog lookup
                if found_role is not None:
                    r_id = int(found_role.id)
            if r_id is not None:
                existing_ur = (
                    db(
                        (db.user_roles.identity_id == mem_ident_id)
                        & (db.user_roles.role_id == r_id)
                        & (db.user_roles.organization_id == organization_id)
                    )
                    .select(db.user_roles.id)
                    .first()
                )  # tenant-scope-exempt: internal svc-integrations scoped to organization
                if existing_ur is None:
                    db.user_roles.insert(
                        identity_id=mem_ident_id,
                        role_id=r_id,
                        scope="ORGANIZATION",
                        scope_id=organization_id,
                        organization_id=organization_id,
                        created_at=now,
                        updated_at=now,
                    )
                memberships_synced += 1

    return {
        "users_synced": users_synced,
        "groups_synced": groups_synced,
        "roles_synced": roles_synced,
        "memberships_synced": memberships_synced,
    }


@bp.route("/pending", methods=["GET"])
@login_required
@require_scope("integrations:sync")
async def get_pending_integrations():
    """Return due discovery jobs, sync configs, IAM providers, and GWS providers."""
    auth_error = _require_svc_integrations()
    if auth_error is not None:
        return auth_error

    db = current_app.db
    now = _utc_now()
    force = request.args.get("force", "false").lower() in ("1", "true", "yes")

    try:
        active_tenants = {
            row.id: row
            for row in db(db.tenants.is_active == True).select()  # noqa: E712  # tenant-scope-exempt: system service enumerates active tenants
        }
        active_orgs = {
            row.id: row
            for row in db(db.organizations.is_active == True).select()  # noqa: E712  # tenant-scope-exempt: system service enumerates active organizations
            if row.tenant_id in active_tenants
        }

        discovery_items: list[dict[str, Any]] = []
        if "discovery_jobs" in db.tables:
            enabled_jobs = db(
                db.discovery_jobs.enabled == True  # noqa: E712
            ).select()  # tenant-scope-exempt: internal svc-integrations dispatches across active orgs
            for job in enabled_jobs:
                provider = str(job.provider or "").lower()
                if provider in SCANNER_OWNED_PROVIDERS:
                    continue
                cfg = _parse_json_dict(job.config_json)
                org_id = _safe_int(
                    cfg.get("_organization_id")
                    or cfg.get("organization_id")
                    or getattr(job, "organization_id", None)
                )
                if org_id is None or org_id not in active_orgs:
                    continue
                interval = _positive_int(job.schedule_interval, default=3600)
                if not _is_due(
                    job.last_run_at,
                    interval,
                    now,
                    force,
                    next_run_at=getattr(job, "next_run_at", None),
                ):
                    continue
                resolved_cfg = _resolve_discovery_credentials(db, job, org_id, cfg)
                discovery_items.append(
                    {
                        "id": job.id,
                        "name": job.name,
                        "provider": job.provider,
                        "organization_id": org_id,
                        "tenant_id": active_orgs[org_id].tenant_id,
                        "schedule_interval": interval,
                        "config": resolved_cfg,
                        "last_run_at": _iso_or_none(job.last_run_at),
                        "next_run_at": _iso_or_none(getattr(job, "next_run_at", None)),
                    }
                )

        sync_items: list[dict[str, Any]] = []
        if "sync_configs" in db.tables:
            enabled_syncs = db(
                db.sync_configs.enabled == True  # noqa: E712
            ).select()  # tenant-scope-exempt: internal svc-integrations dispatches due sync configs
            for sc in enabled_syncs:
                cfg = _parse_json_dict(sc.config_json)
                org_id = _safe_int(
                    getattr(sc, "organization_id", None)
                    or cfg.get("elder_organization_id")
                    or cfg.get("organization_id")
                )
                tenant_id = _safe_int(cfg.get("tenant_id"))
                if org_id is not None:
                    if org_id not in active_orgs:
                        continue
                    tenant_id = active_orgs[org_id].tenant_id
                elif tenant_id is not None:
                    if tenant_id not in active_tenants:
                        continue
                elif not active_tenants:
                    continue

                interval = _positive_int(sc.sync_interval, default=300)
                due = (
                    force
                    or sc.last_batch_sync_at is None
                    or sc.last_sync_at is None
                    or _is_due(sc.last_batch_sync_at, interval, now, force)
                )
                if not due:
                    continue

                mapping_rows = (
                    db(db.sync_mappings.sync_config_id == sc.id).select()
                    if "sync_mappings" in db.tables
                    else []
                )  # tenant-scope-exempt: internal svc-integrations mappings for sync_config
                serialized_mappings = [
                    {
                        "id": m.id,
                        "elder_type": m.elder_type,
                        "elder_id": m.elder_id,
                        "external_platform": m.external_platform,
                        "external_id": m.external_id,
                        "sync_status": m.sync_status,
                        "sync_method": m.sync_method,
                        "last_synced_at": _iso_or_none(m.last_synced_at),
                        "elder_updated_at": _iso_or_none(m.elder_updated_at),
                        "external_updated_at": _iso_or_none(m.external_updated_at),
                    }
                    for m in mapping_rows
                ]
                local_items = _collect_local_sync_items(
                    db, sc, cfg, list(mapping_rows), org_id, tenant_id
                )
                sync_items.append(
                    {
                        "id": sc.id,
                        "name": sc.name,
                        "platform": sc.platform,
                        "organization_id": org_id,
                        "tenant_id": tenant_id,
                        "sync_interval": interval,
                        "batch_fallback_enabled": bool(sc.batch_fallback_enabled),
                        "batch_size": sc.batch_size,
                        "two_way_create": bool(sc.two_way_create),
                        "webhook_enabled": bool(sc.webhook_enabled),
                        "config": cfg,
                        "last_sync_at": _iso_or_none(sc.last_sync_at),
                        "last_batch_sync_at": _iso_or_none(sc.last_batch_sync_at),
                        "mappings": serialized_mappings,
                        "local_items": local_items,
                    }
                )

        iam_items: list[dict[str, Any]] = []
        if "iam_providers" in db.tables:
            enabled_iam = (
                db(
                    db.iam_providers.enabled == True  # noqa: E712
                ).select()
            )  # tenant-scope-exempt: internal svc-integrations dispatches IAM providers
            for provider in enabled_iam:
                org_id = _safe_int(provider.organization_id)
                if org_id is None or org_id not in active_orgs:
                    continue
                cfg = _parse_json_dict(provider.config_json)
                interval = _positive_int(cfg.get("sync_interval"), default=300)
                if not _is_due(provider.last_sync_at, interval, now, force):
                    continue
                iam_items.append(
                    {
                        "id": provider.id,
                        "name": provider.name,
                        "provider_type": provider.provider_type,
                        "organization_id": org_id,
                        "tenant_id": active_orgs[org_id].tenant_id,
                        "sync_interval": interval,
                        "config": cfg,
                        "last_sync_at": _iso_or_none(provider.last_sync_at),
                    }
                )

        gws_items: list[dict[str, Any]] = []
        if "google_workspace_providers" in db.tables:
            enabled_gws = (
                db(
                    db.google_workspace_providers.enabled == True  # noqa: E712
                ).select()
            )  # tenant-scope-exempt: internal svc-integrations dispatches GWS providers
            for provider in enabled_gws:
                org_id = _safe_int(provider.organization_id)
                if org_id is None or org_id not in active_orgs:
                    continue
                raw_creds = _decode_raw_json(provider.credentials_json)
                creds_dict = raw_creds if isinstance(raw_creds, dict) else {}
                interval = _positive_int(creds_dict.get("sync_interval"), default=300)
                if not _is_due(provider.last_sync_at, interval, now, force):
                    continue
                gws_items.append(
                    {
                        "id": provider.id,
                        "name": provider.name,
                        "domain": provider.domain,
                        "admin_email": provider.admin_email,
                        "organization_id": org_id,
                        "tenant_id": active_orgs[org_id].tenant_id,
                        "sync_interval": interval,
                        "credentials": raw_creds,
                        "last_sync_at": _iso_or_none(provider.last_sync_at),
                    }
                )

        logger.info(
            "integrations_internal_pending",
            force=force,
            discovery_count=len(discovery_items),
            sync_count=len(sync_items),
            iam_count=len(iam_items),
            google_workspace_count=len(gws_items),
        )
        return (
            jsonify(
                {
                    "generated_at": now.isoformat(),
                    "discovery_jobs": discovery_items,
                    "sync_configs": sync_items,
                    "iam_providers": iam_items,
                    "google_workspace_providers": gws_items,
                }
            ),
            200,
        )
    except Exception as exc:
        logger.error(
            "integrations_internal_pending_failed",
            error_type=type(exc).__name__,
        )
        return jsonify({"error": "Failed to fetch pending integrations"}), 500


@bp.route("/discovery/<int:job_id>/start", methods=["POST"])
@login_required
@require_scope("integrations:sync")
async def start_discovery_job(job_id: int):
    """Record the start of a discovery job execution in ``discovery_history``."""
    auth_error = _require_svc_integrations()
    if auth_error is not None:
        return auth_error

    db = current_app.db
    try:
        job = (
            db(db.discovery_jobs.id == job_id).select().first()
        )  # tenant-scope-exempt: internal svc-integrations job start by id
        if job is None:
            return jsonify({"error": "Discovery job not found"}), 404

        now = _utc_now()
        history_id = int(
            db.discovery_history.insert(
                job_id=job_id,
                status="running",
                started_at=now,
                entities_discovered=0,
                entities_created=0,
                entities_updated=0,
            )
        )
        db.commit()

        logger.info(
            "integrations_internal_discovery_started",
            job_id=job_id,
            history_id=history_id,
        )
        return (
            jsonify(
                {
                    "history_id": history_id,
                    "job_id": job_id,
                    "started_at": now.isoformat(),
                }
            ),
            200,
        )
    except Exception as exc:
        _safe_rollback(db)
        logger.error(
            "integrations_internal_discovery_start_failed",
            job_id=job_id,
            error_type=type(exc).__name__,
        )
        return jsonify({"error": "Failed to start discovery job"}), 500


@bp.route("/discovery/<int:job_id>/complete", methods=["POST"])
@login_required
@require_scope("integrations:sync")
async def complete_discovery_job(job_id: int):
    """Persist discovered resources and finalize a discovery job execution."""
    auth_error = _require_svc_integrations()
    if auth_error is not None:
        return auth_error

    db = current_app.db
    data = await request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"error": "JSON object payload required"}), 400

    status = str(data.get("status") or "completed").strip().lower()
    if status not in VALID_DISCOVERY_STATUSES:
        return jsonify({"error": f"Invalid discovery status: {status}"}), 400

    try:
        job = (
            db(db.discovery_jobs.id == job_id).select().first()
        )  # tenant-scope-exempt: internal svc-integrations job complete by id
        if job is None:
            return jsonify({"error": "Discovery job not found"}), 404

        job_cfg = _parse_json_dict(job.config_json)
        org_id = _safe_int(
            data.get("organization_id")
            or job_cfg.get("_organization_id")
            or job_cfg.get("organization_id")
        )
        raw_resources = data.get("resources") or []
        raw_dependencies = data.get("dependencies") or []
        summary = data.get("summary") or {}
        error_message = data.get("error_message")
        history_id = _safe_int(data.get("history_id"))

        if isinstance(raw_resources, dict):
            flat_resources: list[dict[str, Any]] = []
            for cat, cat_items in raw_resources.items():
                if isinstance(cat_items, list):
                    for entry in cat_items:
                        if isinstance(entry, dict):
                            copy = dict(entry)
                            copy.setdefault("category", str(cat))
                            flat_resources.append(copy)
        elif isinstance(raw_resources, list):
            flat_resources = [r for r in raw_resources if isinstance(r, dict)]
        else:
            flat_resources = []

        deps_list = (
            [d for d in raw_dependencies if isinstance(d, dict)]
            if isinstance(raw_dependencies, list)
            else []
        )

        entities_discovered = _safe_int(summary.get("entities_discovered"))
        if entities_discovered is None:
            entities_discovered = len(flat_resources)
        entities_created = _safe_int(summary.get("entities_created")) or 0
        entities_updated = _safe_int(summary.get("entities_updated")) or 0
        dependencies_created = 0

        if status != "failed" and org_id is not None and (flat_resources or deps_list):
            discovery_svc = DiscoveryService(db)
            before_count = (
                db(db.entities.organization_id == org_id).count()
            )  # tenant-scope-exempt: internal svc-integrations scoped to job org
            existing_ext_ids = {
                str(r.external_id)
                for r in db(db.entities.organization_id == org_id).select(
                    db.entities.external_id
                )  # tenant-scope-exempt: internal svc-integrations scoped to job org
                if r.external_id
            }

            grouped_results, standalone_deps = _normalize_discovery_payload(
                str(job.provider or "cloud"),
                flat_resources,
                deps_list,
            )
            edge_counts = discovery_svc._store_discovered_resources(
                org_id, grouped_results
            )
            dependencies_created += int(edge_counts.get("edges_created", 0))

            if standalone_deps:
                tenant_id = discovery_svc._tenant_for_org(org_id)
                if tenant_id is not None:
                    for dep in standalone_deps:
                        src = _resolve_db_record_for_dep(
                            db,
                            org_id,
                            dep.get("source_external_id"),
                            dep.get("source_type"),
                        )
                        tgt = _resolve_db_record_for_dep(
                            db,
                            org_id,
                            dep.get("target_external_id"),
                            dep.get("target_kind") or dep.get("target_type"),
                        )
                        if src and tgt:
                            linked = discovery_svc._create_dependency_link(
                                src[0],
                                src[1],
                                tgt[0],
                                tgt[1],
                                tenant_id,
                                dep_type=str(dep.get("edge_type") or "depends_on"),
                                meta=dep.get("metadata") or {},
                            )
                            if linked:
                                dependencies_created += 1

            after_count = (
                db(db.entities.organization_id == org_id).count()
            )  # tenant-scope-exempt: internal svc-integrations scoped to job org
            created_delta = max(0, int(after_count) - int(before_count))
            entities_created = max(entities_created, created_delta)

            batch_ext_ids = {
                str(r.get("resource_id") or r.get("external_id"))
                for r in flat_resources
                if r.get("resource_id") or r.get("external_id")
            }
            matched_existing = len(batch_ext_ids & existing_ext_ids)
            entities_updated = max(entities_updated, matched_existing)

        now = _utc_now()
        results_payload = (
            summary
            if isinstance(summary, dict) and summary
            else {
                "entities_discovered": entities_discovered,
                "entities_created": entities_created,
                "entities_updated": entities_updated,
                "dependencies_created": dependencies_created,
            }
        )

        existing_history = (
            db(db.discovery_history.id == history_id).select().first()
            if history_id is not None
            else None
        )  # tenant-scope-exempt: internal svc-integrations history update by pk
        if existing_history is not None:
            db(db.discovery_history.id == history_id).update(
                status=status,
                completed_at=now,
                entities_discovered=entities_discovered,
                entities_created=entities_created,
                entities_updated=entities_updated,
                error_message=error_message,
                results_json=results_payload,
            )  # tenant-scope-exempt: internal svc-integrations history update by pk
        else:
            history_id = int(
                db.discovery_history.insert(
                    job_id=job_id,
                    status=status,
                    started_at=now,
                    completed_at=now,
                    entities_discovered=entities_discovered,
                    entities_created=entities_created,
                    entities_updated=entities_updated,
                    error_message=error_message,
                    results_json=results_payload,
                )
            )

        interval = _positive_int(job.schedule_interval, default=3600)
        db(db.discovery_jobs.id == job_id).update(
            last_run_at=now,
            next_run_at=now + timedelta(seconds=interval),
            updated_at=now,
        )  # tenant-scope-exempt: internal svc-integrations job schedule update by pk
        db.commit()

        logger.info(
            "integrations_internal_discovery_completed",
            job_id=job_id,
            history_id=history_id,
            status=status,
            entities_discovered=entities_discovered,
            entities_created=entities_created,
            entities_updated=entities_updated,
            dependencies_created=dependencies_created,
        )
        return (
            jsonify(
                {
                    "job_id": job_id,
                    "history_id": history_id,
                    "status": status,
                    "entities_discovered": entities_discovered,
                    "entities_created": entities_created,
                    "entities_updated": entities_updated,
                    "dependencies_created": dependencies_created,
                }
            ),
            200,
        )
    except Exception as exc:
        _safe_rollback(db)
        logger.error(
            "integrations_internal_discovery_complete_failed",
            job_id=job_id,
            error_type=type(exc).__name__,
        )
        return jsonify({"error": "Failed to complete discovery job"}), 500


@bp.route("/sync/<int:config_id>/apply", methods=["POST"])
@login_required
@require_scope("integrations:sync")
async def apply_sync_results(config_id: int):
    """Apply pulled issues, pushed mappings, conflicts, and history for a sync config."""
    auth_error = _require_svc_integrations()
    if auth_error is not None:
        return auth_error

    db = current_app.db
    data = await request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"error": "JSON object payload required"}), 400

    try:
        sync_config = (
            db(db.sync_configs.id == config_id).select().first()
        )  # tenant-scope-exempt: internal svc-integrations lookup by sync_config pk
        if sync_config is None:
            return jsonify({"error": "Sync configuration not found"}), 404

        now = _utc_now()
        cfg_json = _parse_json_dict(sync_config.config_json)
        org_id = _safe_int(
            data.get("organization_id")
            or cfg_json.get("elder_organization_id")
            or cfg_json.get("organization_id")
        )
        tenant_id = _safe_int(data.get("tenant_id") or cfg_json.get("tenant_id"))
        if tenant_id is None and org_id is not None and "organizations" in db.tables:
            org_row = (
                db(db.organizations.id == org_id)
                .select(db.organizations.tenant_id)
                .first()
            )  # tenant-scope-exempt: internal svc-integrations tenant lookup via org pk
            if org_row is not None:
                tenant_id = _safe_int(org_row.tenant_id)

        correlation_id = data.get("correlation_id")
        sync_type = str(data.get("sync_type") or "batch").strip() or "batch"
        success = bool(data.get("success", True))
        error_message = data.get("error_message")
        pulled_items = data.get("pulled_items") or []
        pushed_mappings = data.get("pushed_mappings") or []
        conflicts = data.get("conflicts") or []
        items_failed = int(data.get("items_failed") or 0)
        metadata = (
            data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        )

        applied_count = 0
        two_way_create = bool(getattr(sync_config, "two_way_create", False))

        for item in pulled_items:
            if not isinstance(item, dict):
                continue
            external_id = str(item.get("external_id") or item.get("id") or "").strip()
            elder_type = str(item.get("elder_type") or "issue").strip() or "issue"
            explicit_elder_id = _safe_int(item.get("elder_id"))

            existing_mapping = None
            if external_id and "sync_mappings" in db.tables:
                existing_mapping = (
                    db(
                        (db.sync_mappings.sync_config_id == config_id)
                        & (db.sync_mappings.elder_type == elder_type)
                        & (db.sync_mappings.external_id == external_id)
                    )
                    .select()
                    .first()
                )  # tenant-scope-exempt: internal svc-integrations scoped to sync_config

            issue_id = (
                int(existing_mapping.elder_id)
                if existing_mapping is not None
                else explicit_elder_id
            )
            ext_updated_at = _parse_datetime(item.get("external_updated_at"))
            status_val = _normalize_issue_status(item.get("status"))
            issue_fields: dict[str, Any] = {
                "title": str(item.get("title") or f"Synced item {external_id}")[:255],
                "description": item.get("description"),
                "status": status_val,
                "priority": _normalize_issue_priority(item.get("priority")),
                "issue_type": _normalize_issue_type(item.get("issue_type")),
                "updated_at": now,
            }
            if status_val in ("CLOSED", "RESOLVED"):
                issue_fields["closed_at"] = now
            if isinstance(item.get("metadata"), dict) and _has_column(
                db.issues, "metadata"
            ):
                issue_fields["metadata"] = item["metadata"]

            if issue_id is not None:
                existing_issue = (
                    db(db.issues.id == issue_id).select(db.issues.id).first()
                )  # tenant-scope-exempt: internal svc-integrations update mapped issue
                if existing_issue is not None:
                    db(db.issues.id == issue_id).update(
                        **issue_fields
                    )  # tenant-scope-exempt: internal svc-integrations update by pk
                else:
                    issue_id = None

            if issue_id is None:
                if not two_way_create and not bool(item.get("allow_create")):
                    continue
                if tenant_id is None:
                    items_failed += 1
                    continue
                insert_fields: dict[str, Any] = {
                    **issue_fields,
                    "tenant_id": tenant_id,
                    "resource_type": str(item.get("resource_type") or "organization"),
                    "resource_id": _safe_int(item.get("resource_id")) or (org_id or 0),
                    "is_incident": 0,
                    "reporter_id": None,
                    "created_at": now,
                }
                if org_id is not None and _has_column(db.issues, "organization_id"):
                    insert_fields["organization_id"] = org_id
                issue_id = int(db.issues.insert(**insert_fields))

            if external_id and "sync_mappings" in db.tables:
                _upsert_sync_mapping(
                    db,
                    sync_config,
                    elder_type=elder_type,
                    elder_id=issue_id,
                    external_id=external_id,
                    sync_method=sync_type,
                    sync_status=str(item.get("sync_status") or "synced"),
                    elder_updated_at=now,
                    external_updated_at=ext_updated_at or now,
                    now=now,
                )
            applied_count += 1

        if "sync_mappings" in db.tables:
            for pushed in pushed_mappings:
                if not isinstance(pushed, dict):
                    continue
                elder_id = _safe_int(pushed.get("elder_id"))
                external_id = str(pushed.get("external_id") or "").strip()
                if elder_id is None or not external_id:
                    continue
                _upsert_sync_mapping(
                    db,
                    sync_config,
                    elder_type=str(pushed.get("elder_type") or "issue"),
                    elder_id=elder_id,
                    external_id=external_id,
                    sync_method=sync_type,
                    sync_status=str(pushed.get("sync_status") or "synced"),
                    elder_updated_at=_parse_datetime(pushed.get("elder_updated_at"))
                    or now,
                    external_updated_at=_parse_datetime(
                        pushed.get("external_updated_at")
                    )
                    or now,
                    now=now,
                )
                applied_count += 1

        conflicts_recorded = 0
        if "sync_conflicts" in db.tables and "sync_mappings" in db.tables:
            for conflict in conflicts:
                if not isinstance(conflict, dict):
                    continue
                mapping_id = _safe_int(conflict.get("mapping_id"))
                if mapping_id is None:
                    ext_id = conflict.get("external_id")
                    elder_id = _safe_int(conflict.get("elder_id"))
                    elder_type = str(conflict.get("elder_type") or "issue")
                    m_row = None
                    if ext_id:
                        m_row = (
                            db(
                                (db.sync_mappings.sync_config_id == config_id)
                                & (db.sync_mappings.elder_type == elder_type)
                                & (db.sync_mappings.external_id == str(ext_id))
                            )
                            .select(db.sync_mappings.id)
                            .first()
                        )  # tenant-scope-exempt: internal svc-integrations conflict lookup
                    elif elder_id is not None:
                        m_row = (
                            db(
                                (db.sync_mappings.sync_config_id == config_id)
                                & (db.sync_mappings.elder_type == elder_type)
                                & (db.sync_mappings.elder_id == elder_id)
                            )
                            .select(db.sync_mappings.id)
                            .first()
                        )  # tenant-scope-exempt: internal svc-integrations conflict lookup
                    if m_row is not None:
                        mapping_id = int(m_row.id)
                if mapping_id is None:
                    continue

                db.sync_conflicts.insert(
                    mapping_id=mapping_id,
                    conflict_type=str(
                        conflict.get("conflict_type") or "concurrent_edit"
                    ),
                    elder_data=conflict.get("elder_data") or {},
                    external_data=conflict.get("external_data") or {},
                    resolution_strategy=conflict.get("resolution_strategy"),
                    resolved=False,
                    created_at=now,
                )
                db(db.sync_mappings.id == mapping_id).update(
                    sync_status="conflict"
                )  # tenant-scope-exempt: internal svc-integrations update conflict status
                conflicts_recorded += 1

        raw_items_synced = _safe_int(data.get("items_synced"))
        items_synced = (
            raw_items_synced if raw_items_synced is not None else applied_count
        )

        started_at = _parse_datetime(data.get("started_at")) or now
        history_id = int(
            db.sync_history.insert(
                sync_config_id=config_id,
                correlation_id=str(correlation_id) if correlation_id else None,
                sync_type=sync_type,
                items_synced=items_synced,
                items_failed=items_failed,
                started_at=started_at,
                completed_at=now,
                success=success,
                error_message=error_message,
                sync_metadata=metadata,
            )
        )

        config_updates: dict[str, Any] = {
            "last_sync_at": now,
            "updated_at": now,
        }
        if sync_type == "batch":
            config_updates["last_batch_sync_at"] = now
        db(db.sync_configs.id == config_id).update(
            **config_updates
        )  # tenant-scope-exempt: internal svc-integrations schedule update by pk
        db.commit()

        logger.info(
            "integrations_internal_sync_applied",
            sync_config_id=config_id,
            sync_type=sync_type,
            items_synced=items_synced,
            items_failed=items_failed,
            conflicts_recorded=conflicts_recorded,
            history_id=history_id,
        )
        return (
            jsonify(
                {
                    "sync_config_id": config_id,
                    "items_synced": items_synced,
                    "items_failed": items_failed,
                    "conflicts_recorded": conflicts_recorded,
                    "history_id": history_id,
                }
            ),
            200,
        )
    except Exception as exc:
        _safe_rollback(db)
        logger.error(
            "integrations_internal_sync_apply_failed",
            sync_config_id=config_id,
            error_type=type(exc).__name__,
        )
        return jsonify({"error": "Failed to apply sync results"}), 500


@bp.route("/iam/<int:provider_id>/apply", methods=["POST"])
@login_required
@require_scope("integrations:sync")
async def apply_iam_sync(provider_id: int):
    """Persist synced IAM users, groups, roles, and memberships for a provider."""
    auth_error = _require_svc_integrations()
    if auth_error is not None:
        return auth_error

    db = current_app.db
    data = await request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"error": "JSON object payload required"}), 400

    try:
        provider = (
            db(db.iam_providers.id == provider_id).select().first()
        )  # tenant-scope-exempt: internal svc-integrations lookup by provider pk
        if provider is None:
            return jsonify({"error": "IAM provider not found"}), 404

        org_row = (
            db(db.organizations.id == provider.organization_id)
            .select(db.organizations.id, db.organizations.tenant_id)
            .first()
        )  # tenant-scope-exempt: internal svc-integrations org lookup
        if org_row is None:
            return jsonify({"error": "Provider organization not found"}), 404

        now = _utc_now()
        success = bool(data.get("success", True))
        counts = {
            "users_synced": 0,
            "groups_synced": 0,
            "roles_synced": 0,
            "memberships_synced": 0,
        }
        if success:
            counts = _apply_directory_sync(
                db,
                provider_type=str(provider.provider_type or "iam"),
                organization_id=int(provider.organization_id),
                tenant_id=int(org_row.tenant_id),
                users=list(data.get("users") or []),
                groups=list(data.get("groups") or []),
                roles=list(data.get("roles") or []),
                memberships=list(data.get("memberships") or []),
                tokenize_pii=True,
                now=now,
            )

        db(db.iam_providers.id == provider_id).update(
            last_sync_at=now,
            updated_at=now,
        )  # tenant-scope-exempt: internal svc-integrations timestamp update by pk
        db.commit()

        logger.info(
            "integrations_internal_iam_applied",
            provider_id=provider_id,
            success=success,
            **counts,
        )
        return (
            jsonify(
                {
                    "provider_id": provider_id,
                    "success": success,
                    **counts,
                }
            ),
            200,
        )
    except Exception as exc:
        _safe_rollback(db)
        logger.error(
            "integrations_internal_iam_apply_failed",
            provider_id=provider_id,
            error_type=type(exc).__name__,
        )
        return jsonify({"error": "Failed to apply IAM sync results"}), 500


@bp.route("/google-workspace/<int:provider_id>/apply", methods=["POST"])
@login_required
@require_scope("integrations:sync")
async def apply_google_workspace_sync(provider_id: int):
    """Persist synced Google Workspace directory users, groups, and memberships."""
    auth_error = _require_svc_integrations()
    if auth_error is not None:
        return auth_error

    db = current_app.db
    data = await request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"error": "JSON object payload required"}), 400

    try:
        provider = (
            db(db.google_workspace_providers.id == provider_id).select().first()
        )  # tenant-scope-exempt: internal svc-integrations lookup by provider pk
        if provider is None:
            return jsonify({"error": "Google Workspace provider not found"}), 404

        org_row = (
            db(db.organizations.id == provider.organization_id)
            .select(db.organizations.id, db.organizations.tenant_id)
            .first()
        )  # tenant-scope-exempt: internal svc-integrations org lookup
        if org_row is None:
            return jsonify({"error": "Provider organization not found"}), 404

        now = _utc_now()
        success = bool(data.get("success", True))
        counts = {
            "users_synced": 0,
            "groups_synced": 0,
            "memberships_synced": 0,
        }
        if success:
            applied = _apply_directory_sync(
                db,
                provider_type="google",
                organization_id=int(provider.organization_id),
                tenant_id=int(org_row.tenant_id),
                users=list(data.get("users") or []),
                groups=list(data.get("groups") or []),
                roles=[],
                memberships=list(data.get("memberships") or []),
                tokenize_pii=False,
                now=now,
            )
            counts = {
                "users_synced": applied["users_synced"],
                "groups_synced": applied["groups_synced"],
                "memberships_synced": applied["memberships_synced"],
            }

        db(db.google_workspace_providers.id == provider_id).update(
            last_sync_at=now,
            updated_at=now,
        )  # tenant-scope-exempt: internal svc-integrations timestamp update by pk
        db.commit()

        logger.info(
            "integrations_internal_google_workspace_applied",
            provider_id=provider_id,
            success=success,
            **counts,
        )
        return (
            jsonify(
                {
                    "provider_id": provider_id,
                    "success": success,
                    **counts,
                }
            ),
            200,
        )
    except Exception as exc:
        _safe_rollback(db)
        logger.error(
            "integrations_internal_google_workspace_apply_failed",
            provider_id=provider_id,
            error_type=type(exc).__name__,
        )
        return jsonify({"error": "Failed to apply Google Workspace sync results"}), 500
