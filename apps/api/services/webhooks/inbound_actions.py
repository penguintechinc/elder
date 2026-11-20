"""Scope catalog, payload resolution, and resource action dispatcher for inbound webhooks."""

from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text

from apps.api.utils.tenant_scoping import get_tenant_scoped

DEFAULT_LOG_SCOPES: list[str] = ["events:log"]
DEFAULT_PLAYBOOK_SCOPES: list[str] = ["events:log", "playbooks:trigger"]

INBOUND_WEBHOOK_SCOPES: list[dict[str, str]] = [
    {
        "scope": "events:log",
        "label": "Log Events",
        "category": "General",
        "description": "Receive and record incoming webhook payloads in the audit event log.",
    },
    {
        "scope": "playbooks:trigger",
        "label": "Trigger Playbooks",
        "category": "Automation",
        "description": "Start linked or requested Stream playbook executions with the incoming payload.",
    },
    {
        "scope": "issues:create",
        "label": "Create Issues",
        "category": "Issues",
        "description": "Create new issues or incidents scoped to this tenant.",
    },
    {
        "scope": "issues:update",
        "label": "Update Issues",
        "category": "Issues",
        "description": "Update existing issues (status, priority, description, metadata) in this tenant.",
    },
    {
        "scope": "vulnerabilities:create",
        "label": "Create Vulnerabilities",
        "category": "Security",
        "description": "Create new CVE/vulnerability records scoped to this tenant.",
    },
    {
        "scope": "vulnerabilities:update",
        "label": "Update Vulnerabilities",
        "category": "Security",
        "description": "Update existing vulnerabilities (by id or cve_id) in this tenant.",
    },
    {
        "scope": "entities:create",
        "label": "Create Entities",
        "category": "Infrastructure",
        "description": "Create new infrastructure entities in an organization of this tenant.",
    },
    {
        "scope": "entities:update",
        "label": "Update Entities",
        "category": "Infrastructure",
        "description": "Update existing infrastructure entities in this tenant.",
    },
    {
        "scope": "data_stores:create",
        "label": "Create Data Inventory",
        "category": "Infrastructure",
        "description": "Create new data inventory records in an organization of this tenant.",
    },
    {
        "scope": "data_stores:update",
        "label": "Update Data Inventory",
        "category": "Infrastructure",
        "description": "Update existing data inventory records in this tenant.",
    },
    {
        "scope": "services:create",
        "label": "Create Services",
        "category": "Services & Software",
        "description": "Create new tracked services in an organization of this tenant.",
    },
    {
        "scope": "services:update",
        "label": "Update Services",
        "category": "Services & Software",
        "description": "Update existing tracked services in this tenant.",
    },
    {
        "scope": "software:create",
        "label": "Create Software",
        "category": "Services & Software",
        "description": "Create new software inventory entries in an organization of this tenant.",
    },
    {
        "scope": "software:update",
        "label": "Update Software",
        "category": "Services & Software",
        "description": "Update existing software inventory entries in this tenant.",
    },
    {
        "scope": "ai_resources:create",
        "label": "Create AI Resources",
        "category": "AI",
        "description": "Create new AI resource records in an organization of this tenant.",
    },
    {
        "scope": "ai_resources:update",
        "label": "Update AI Resources",
        "category": "AI",
        "description": "Update existing AI resource records in this tenant.",
    },
]

VALID_INBOUND_SCOPES: frozenset[str] = frozenset(
    entry["scope"] for entry in INBOUND_WEBHOOK_SCOPES
)

SUPPORTED_INBOUND_RESOURCES: frozenset[str] = frozenset(
    {
        "issues",
        "vulnerabilities",
        "entities",
        "data_stores",
        "services",
        "software",
        "ai_resources",
    }
)

_ISSUE_STATUSES = frozenset({"OPEN", "IN_PROGRESS", "CLOSED", "RESOLVED"})
_ISSUE_PRIORITIES = frozenset({"LOW", "MEDIUM", "HIGH", "URGENT", "CRITICAL"})
_ISSUE_TYPES = frozenset(
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
_VULN_SEVERITIES = frozenset({"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"})
_DATA_CLASSIFICATIONS = ("public", "internal", "confidential", "restricted")
_STORAGE_TYPES = (
    "database",
    "file_storage",
    "data_warehouse",
    "data_lake",
    "cache",
    "message_queue",
    "search_index",
    "time_series",
    "blob_storage",
    "other",
)
_SERVICE_STATUSES = ("active", "inactive", "deprecated")
_SOFTWARE_TYPES = ("saas", "internal", "library", "os", "container")
_AI_STATUSES = ("active", "inactive", "pending_review")


def normalize_stored_scopes(raw: Any) -> list[str]:
    """Safely normalize stored webhook scopes into a non-empty list of strings."""
    parsed = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = [s.strip() for s in raw.split(",") if s.strip()]
    if not isinstance(parsed, list):
        return list(DEFAULT_LOG_SCOPES)
    cleaned = [
        item.strip()
        for item in parsed
        if isinstance(item, str) and item.strip() in VALID_INBOUND_SCOPES
    ]
    deduped = list(dict.fromkeys(cleaned))
    return deduped or list(DEFAULT_LOG_SCOPES)


def validate_inbound_scopes(
    raw_scopes: Any,
    *,
    has_stream_id: bool = False,
) -> tuple[list[str], str | None]:
    """Validate inbound webhook permission scopes on create/update.

    When ``raw_scopes`` is ``None``, defaults to ``["events:log",
    "playbooks:trigger"]`` if a linked playbook is set, else ``["events:log"]``.
    """
    if raw_scopes is None:
        return (
            list(DEFAULT_PLAYBOOK_SCOPES) if has_stream_id else list(DEFAULT_LOG_SCOPES)
        ), None

    if not isinstance(raw_scopes, list) or not raw_scopes:
        return [], "scopes must be a non-empty array of scope strings"

    cleaned: list[str] = []
    invalid: list[str] = []
    for item in raw_scopes:
        if not isinstance(item, str) or not item.strip():
            return [], "scopes entries must be non-empty strings"
        scope = item.strip()
        if scope not in VALID_INBOUND_SCOPES:
            invalid.append(scope)
        else:
            cleaned.append(scope)

    if invalid:
        return [], f"Unsupported webhook scopes: {', '.join(sorted(set(invalid)))}"

    return list(dict.fromkeys(cleaned)), None


def _is_pos_int(val: Any) -> int | None:
    if isinstance(val, bool):
        return None
    if isinstance(val, int) and val > 0:
        return val
    return None


def _parse_pos_int_flexible(val: Any) -> int | None:
    if isinstance(val, bool) or val is None:
        return None
    if isinstance(val, int):
        return val if val > 0 else None
    if isinstance(val, str) and val.strip().isdigit():
        parsed = int(val.strip())
        return parsed if parsed > 0 else None
    return None


def resolve_inbound_action(
    hook: Any, payload: Any
) -> tuple[str, dict[str, Any] | None, str | None]:
    """Resolve the requested inbound action and its required OAuth2-like scope.

    Returns ``(required_scope, action_spec, error_message)``.
    """
    hook_scopes = normalize_stored_scopes(getattr(hook, "scopes", None))
    stream_id = getattr(hook, "stream_id", None)

    if not isinstance(payload, dict) or payload.get("action") is None:
        if stream_id and "playbooks:trigger" in hook_scopes:
            return (
                "playbooks:trigger",
                {
                    "kind": "playbook_trigger",
                    "playbook_id": None,
                    "input": payload
                    if isinstance(payload, dict)
                    else {"payload": payload},
                },
                None,
            )
        return "events:log", {"kind": "log_only"}, None

    raw_action = payload.get("action")
    if not isinstance(raw_action, str) or not raw_action.strip():
        return "", None, "action must be a non-empty string"

    action = raw_action.strip().lower()
    if action in ("events.log", "events:log", "log"):
        return "events:log", {"kind": "log_only"}, None

    if action in ("playbook.trigger", "playbooks:trigger"):
        raw_input = payload.get("input")
        if raw_input is not None and not isinstance(raw_input, dict):
            return "", None, "input must be a JSON object"
        trigger_input = raw_input if isinstance(raw_input, dict) else payload
        target_playbook_id = (
            payload["playbook_id"]
            if "playbook_id" in payload
            else payload.get("stream_id")
        )
        return (
            "playbooks:trigger",
            {
                "kind": "playbook_trigger",
                "playbook_id": target_playbook_id,
                "input": trigger_input,
            },
            None,
        )

    resource = ""
    op = ""
    if action in ("resource.create", "create"):
        op = "create"
        raw_res = payload.get("resource")
        resource = raw_res.strip().lower() if isinstance(raw_res, str) else ""
    elif action in ("resource.update", "update"):
        op = "update"
        raw_res = payload.get("resource")
        resource = raw_res.strip().lower() if isinstance(raw_res, str) else ""
    elif ":" in action:
        resource, op = action.split(":", 1)
    elif "." in action:
        resource, op = action.split(".", 1)

    if resource not in SUPPORTED_INBOUND_RESOURCES or op not in ("create", "update"):
        return "", None, f"Unsupported action '{action}' or resource"

    data = payload.get("data")
    if not isinstance(data, dict) or not data:
        return "", None, "data must be a non-empty JSON object"

    target_id = None
    cve_id = None
    if op == "update":
        raw_id = (
            payload.get("resource_id")
            if "resource_id" in payload
            else payload.get("id")
        )
        target_id = _parse_pos_int_flexible(raw_id)
        if resource == "vulnerabilities" and isinstance(payload.get("cve_id"), str):
            cve_id = payload["cve_id"].strip().upper() or None
        if target_id is None and not (resource == "vulnerabilities" and cve_id):
            hint = " or cve_id" if resource == "vulnerabilities" else ""
            return "", None, f"Valid positive integer resource_id/id{hint} is required"

    return (
        f"{resource}:{op}",
        {
            "kind": f"resource_{op}",
            "resource": resource,
            "data": data,
            "resource_id": target_id,
            "cve_id": cve_id,
        },
        None,
    )


def _err(status_code: int, message: str) -> tuple[int, str, dict[str, Any]]:
    status_label = (
        "forbidden"
        if status_code == 403
        else ("not_found" if status_code == 404 else "failed")
    )
    return status_code, status_label, {"error": message}


def _safe_str(val: Any, max_len: int) -> tuple[str | None, str | None]:
    if val is None:
        return None, None
    if not isinstance(val, str):
        return None, "must be a string"
    trimmed = val.strip()
    if len(trimmed) > max_len:
        return None, f"exceeds max length of {max_len} characters"
    return trimmed, None


def _parse_iso_dt(val: Any) -> tuple[dt.datetime | None, str | None]:
    if val is None or val == "":
        return None, None
    if not isinstance(val, str):
        return None, "must be an ISO-8601 date or datetime string"
    text_val = val.strip()
    if text_val.endswith("Z"):
        text_val = text_val[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text_val)
    except ValueError:
        try:
            parsed_date = dt.date.fromisoformat(val.strip())
            return (
                dt.datetime(
                    parsed_date.year, parsed_date.month, parsed_date.day, tzinfo=dt.UTC
                ),
                None,
            )
        except ValueError:
            return None, "must be a valid ISO-8601 date or datetime"
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.UTC), None
    return parsed.astimezone(dt.UTC), None


def _parse_int_bounds(
    val: Any, *, min_val: int, max_val: int
) -> tuple[int | None, str | None]:
    if val is None:
        return None, None
    if isinstance(val, bool) or not isinstance(val, int):
        if isinstance(val, str) and val.strip().lstrip("-").isdigit():
            val = int(val.strip())
        else:
            return None, "must be an integer"
    if val < min_val or val > max_val:
        return None, f"must be between {min_val} and {max_val}"
    return val, None


def _parse_float_bounds(
    val: Any, *, min_val: float, max_val: float
) -> tuple[float | None, str | None]:
    if val is None:
        return None, None
    if isinstance(val, bool) or not isinstance(val, (int, float, str)):
        return None, "must be a number"
    try:
        parsed = float(val)
    except ValueError:
        return None, "must be a number"
    if parsed < min_val or parsed > max_val:
        return None, f"must be between {min_val} and {max_val}"
    return parsed, None


def _validate_http_url(val: Any) -> tuple[str | None, str | None]:
    if val is None or val == "":
        return None, None
    if not isinstance(val, str):
        return None, "must be a URL string"
    cleaned = val.strip()
    parsed = urlsplit(cleaned)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None, "must be a valid http:// or https:// URL"
    return cleaned, None


def _resolve_org_id(
    db: Any, tenant_id: int, raw_org_id: Any, fallback_org_id: Any
) -> tuple[int | None, tuple[int, str, dict[str, Any]] | None]:
    candidate = raw_org_id if raw_org_id is not None else fallback_org_id
    if candidate is None:
        return None, _err(
            400,
            "organization_id is required (pass in data or configure on the webhook)",
        )
    org_id = _parse_pos_int_flexible(candidate)
    if org_id is None:
        return None, _err(400, "organization_id must be a positive integer")
    row = (
        db((db.organizations.id == org_id) & (db.organizations.tenant_id == tenant_id))
        .select(db.organizations.id)
        .first()
    )
    if not row:
        return None, _err(404, f"Organization {org_id} not found in tenant")
    return org_id, None


def _mint_village_id(tenant_id: int, prefix: str) -> str:
    from apps.api.utils.village_id import mint_village_id

    del prefix
    return mint_village_id(tenant_id)


def _ensure_ai_table(db: Any) -> None:
    """Ensure ai_resources exists and is reflected in ``db`` across test DBs."""
    from apps.api.modules.ai.routes import (
        _PG_CREATE_SQL,
        _SQLITE_CREATE_SQL,
        _ensure_ai_resources_table,
    )

    _ensure_ai_resources_table(db)
    engine = getattr(db, "engine", None)
    if engine is None:
        return
    inspector = sa_inspect(engine)
    if not inspector.has_table("ai_resources"):
        is_sqlite = getattr(getattr(engine, "dialect", None), "name", "") == "sqlite"
        with engine.begin() as conn:
            conn.execute(text(_SQLITE_CREATE_SQL if is_sqlite else _PG_CREATE_SQL))
    metadata = getattr(db, "_metadata", None)
    if metadata is not None and "ai_resources" not in metadata.tables:
        metadata.reflect(bind=engine, only=["ai_resources"])


def _execute_playbook_trigger(
    db: Any, hook: Any, spec: dict[str, Any], source_ip: str | None
) -> tuple[int, str, dict[str, Any]]:
    tenant_id = hook.tenant_id
    pinned_id = getattr(hook, "stream_id", None)
    requested_raw = spec.get("playbook_id")
    requested_id = None
    if requested_raw is not None:
        requested_id = _parse_pos_int_flexible(requested_raw)
        if requested_id is None:
            return _err(400, "playbook_id must be a positive integer")
        if pinned_id is not None and requested_id != pinned_id:
            return _err(403, "Webhook is pinned to a different playbook")

    target_id = pinned_id or requested_id
    if target_id is None:
        return _err(400, "playbook_id is required when webhook has no linked playbook")

    playbook = (
        db(
            (db.stream_playbooks.id == target_id)
            & (db.stream_playbooks.tenant_id == tenant_id)
            & (db.stream_playbooks.is_enabled == True)  # noqa: E712
        )
        .select()
        .first()
    )
    if not playbook:
        return _err(404, f"Enabled playbook {target_id} not found in tenant")

    now = dt.datetime.now(dt.UTC)
    execution_id = str(uuid.uuid4())
    raw_input = spec.get("input")
    input_data = dict(raw_input) if isinstance(raw_input, dict) else {}
    input_data["__webhook__"] = {
        "inbound_webhook_id": hook.id,
        "slug": hook.slug,
        "remote_addr": source_ip or "unknown",
        "timestamp": now.isoformat(),
    }

    db.stream_executions.insert(
        tenant_id=tenant_id,
        playbook_id=target_id,
        execution_id=execution_id,
        status="queued",
        trigger_type="inbound_webhook",
        triggered_by_identity_id=None,
        input_json=input_data,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return (
        202,
        "triggered",
        {"execution_id": execution_id, "playbook_id": target_id},
    )


def _exec_issue(
    db: Any, hook: Any, op: str, data: dict[str, Any], target_id: int | None
) -> tuple[int, str, dict[str, Any]]:
    tenant_id = hook.tenant_id
    now = dt.datetime.now(dt.UTC)

    fields: dict[str, Any] = {}
    if "title" in data:
        title, err = _safe_str(data.get("title"), 255)
        if err or not title:
            return _err(400, f"title {err or 'cannot be empty'}")
        fields["title"] = title
    if "description" in data:
        desc, err = _safe_str(data.get("description"), 20000)
        if err:
            return _err(400, f"description {err}")
        fields["description"] = desc
    if "status" in data and data["status"] is not None:
        st = str(data["status"]).strip().upper()
        if st not in _ISSUE_STATUSES:
            return _err(
                400, f"status must be one of {', '.join(sorted(_ISSUE_STATUSES))}"
            )
        fields["status"] = st
    if "priority" in data and data["priority"] is not None:
        pr = str(data["priority"]).strip().upper()
        if pr not in _ISSUE_PRIORITIES:
            return _err(
                400, f"priority must be one of {', '.join(sorted(_ISSUE_PRIORITIES))}"
            )
        fields["priority"] = pr
    if "issue_type" in data and data["issue_type"] is not None:
        it = str(data["issue_type"]).strip().upper()
        if it not in _ISSUE_TYPES:
            return _err(
                400, f"issue_type must be one of {', '.join(sorted(_ISSUE_TYPES))}"
            )
        fields["issue_type"] = it
    if "is_incident" in data and data["is_incident"] is not None:
        fields["is_incident"] = 1 if bool(data["is_incident"]) else 0
    if "metadata" in data:
        if data["metadata"] is not None and not isinstance(data["metadata"], dict):
            return _err(400, "metadata must be a JSON object")
        fields["metadata"] = data["metadata"]

    if op == "create":
        if "title" not in fields:
            return _err(400, "title is required")
        org_id, org_err = _resolve_org_id(
            db,
            tenant_id,
            data.get("organization_id"),
            getattr(hook, "organization_id", None),
        )
        if org_err:
            return org_err
        fields.setdefault("status", "OPEN")
        fields.setdefault("priority", "MEDIUM")
        fields.setdefault("issue_type", "OTHER")
        fields.setdefault("is_incident", 0)
        new_id = db.issues.insert(
            tenant_id=tenant_id,
            organization_id=org_id,
            resource_type="organization",
            resource_id=org_id,
            village_id=_mint_village_id(tenant_id, "iss"),
            created_at=now,
            updated_at=now,
            **fields,
        )
        db.commit()
        return 201, "created", {"resource": "issues", "id": new_id}

    if target_id is None:
        return _err(400, "Valid resource_id is required")
    existing = get_tenant_scoped(db, db.issues, target_id, tenant_id)
    if not existing:
        return _err(404, f"Issue {target_id} not found in tenant")
    if not fields:
        return _err(400, "No updatable issue fields supplied")
    fields["updated_at"] = now
    db((db.issues.id == target_id) & (db.issues.tenant_id == tenant_id)).update(
        **fields
    )
    db.commit()
    return 200, "updated", {"resource": "issues", "id": target_id}


def _exec_vulnerability(
    db: Any,
    hook: Any,
    op: str,
    data: dict[str, Any],
    target_id: int | None,
    lookup_cve: str | None,
) -> tuple[int, str, dict[str, Any]]:
    tenant_id = hook.tenant_id
    now = dt.datetime.now(dt.UTC)

    fields: dict[str, Any] = {}
    if "title" in data:
        title, err = _safe_str(data.get("title"), 512)
        if err or not title:
            return _err(400, f"title {err or 'cannot be empty'}")
        fields["title"] = title
    if "severity" in data and data["severity"] is not None:
        sev = str(data["severity"]).strip().upper()
        if sev not in _VULN_SEVERITIES:
            return _err(
                400, f"severity must be one of {', '.join(sorted(_VULN_SEVERITIES))}"
            )
        fields["severity"] = sev
    if "cvss_score" in data:
        cvss, err = _parse_float_bounds(data["cvss_score"], min_val=0.0, max_val=10.0)
        if err:
            return _err(400, f"cvss_score {err}")
        fields["cvss_score"] = cvss
    if "description" in data:
        desc, err = _safe_str(data.get("description"), 8000)
        if err:
            return _err(400, f"description {err}")
        fields["description"] = desc
    if "published_at" in data:
        pub, err = _parse_iso_dt(data.get("published_at"))
        if err:
            return _err(400, f"published_at {err}")
        fields["published_at"] = pub
    if "is_active" in data and data["is_active"] is not None:
        fields["is_active"] = bool(data["is_active"])

    if op == "create":
        cve_id, cve_err = _safe_str(data.get("cve_id"), 50)
        if cve_err or not cve_id:
            return _err(400, f"cve_id {cve_err or 'is required'}")
        cve_norm = cve_id.upper()
        if "title" not in fields:
            return _err(400, "title is required")
        if "severity" not in fields:
            return _err(400, "severity is required")
        if db(db.vulnerabilities.cve_id == cve_norm).select().first():
            return _err(409, f"Vulnerability '{cve_norm}' already exists")
        fields.setdefault("is_active", True)
        new_id = db.vulnerabilities.insert(
            tenant_id=tenant_id,
            cve_id=cve_norm,
            source="inbound_webhook",
            created_at=now,
            updated_at=now,
            **fields,
        )
        db.commit()
        return (
            201,
            "created",
            {"resource": "vulnerabilities", "id": new_id, "cve_id": cve_norm},
        )

    existing = None
    if target_id is not None:
        existing = get_tenant_scoped(db, db.vulnerabilities, target_id, tenant_id)
    elif lookup_cve:
        existing = (
            db(
                (db.vulnerabilities.cve_id == lookup_cve)
                & (db.vulnerabilities.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
    if not existing:
        return _err(404, "Vulnerability not found in tenant")
    if not fields:
        return _err(400, "No updatable vulnerability fields supplied")
    fields["updated_at"] = now
    db(
        (db.vulnerabilities.id == existing.id)
        & (db.vulnerabilities.tenant_id == tenant_id)
    ).update(**fields)
    db.commit()
    return (
        200,
        "updated",
        {"resource": "vulnerabilities", "id": existing.id, "cve_id": existing.cve_id},
    )


def _exec_entity(
    db: Any, hook: Any, op: str, data: dict[str, Any], target_id: int | None
) -> tuple[int, str, dict[str, Any]]:
    tenant_id = hook.tenant_id
    now = dt.datetime.now(dt.UTC)

    fields: dict[str, Any] = {}
    if "name" in data:
        name, err = _safe_str(data.get("name"), 255)
        if err or not name:
            return _err(400, f"name {err or 'cannot be empty'}")
        fields["name"] = name
    if "type" in data:
        etype, err = _safe_str(data.get("type"), 64)
        if err or not etype:
            return _err(400, f"type {err or 'cannot be empty'}")
        fields["type"] = etype
    if "sub_type" in data:
        sub_t, err = _safe_str(data.get("sub_type"), 64)
        if err:
            return _err(400, f"sub_type {err}")
        fields["sub_type"] = sub_t
    if "status" in data and data["status"] is not None:
        st, err = _safe_str(data["status"], 64)
        if err or st is None or st.lower() not in ("active", "inactive"):
            return _err(400, "status must be active or inactive")
        fields["status"] = st.lower()
    if "metadata" in data:
        if data["metadata"] is not None and not isinstance(data["metadata"], dict):
            return _err(400, "metadata must be a JSON object")
        fields["metadata"] = data["metadata"]

    if op == "create":
        if "name" not in fields:
            return _err(400, "name is required")
        if "type" not in fields:
            return _err(400, "type is required")
        org_id, org_err = _resolve_org_id(
            db,
            tenant_id,
            data.get("organization_id"),
            getattr(hook, "organization_id", None),
        )
        if org_err:
            return org_err
        fields.setdefault("status", "active")
        new_id = db.entities.insert(
            organization_id=org_id,
            is_managed=False,
            tags=[],
            created_at=now,
            updated_at=now,
            **fields,
        )
        db.commit()
        return 201, "created", {"resource": "entities", "id": new_id}

    if target_id is None:
        return _err(400, "Valid resource_id is required")
    existing = get_tenant_scoped(
        db, db.entities, target_id, tenant_id, org_fk="organization_id"
    )
    if not existing:
        return _err(404, f"Entity {target_id} not found in tenant")
    if not fields:
        return _err(400, "No updatable entity fields supplied")
    fields["updated_at"] = now
    db(db.entities.id == target_id).update(**fields)
    db.commit()
    return 200, "updated", {"resource": "entities", "id": target_id}


def _exec_data_store(
    db: Any, hook: Any, op: str, data: dict[str, Any], target_id: int | None
) -> tuple[int, str, dict[str, Any]]:
    tenant_id = hook.tenant_id
    now = dt.datetime.now(dt.UTC)

    fields: dict[str, Any] = {}
    if "name" in data:
        name, err = _safe_str(data.get("name"), 255)
        if err or not name:
            return _err(400, f"name {err or 'cannot be empty'}")
        fields["name"] = name
    if "data_classification" in data and data["data_classification"] is not None:
        cls_val = str(data["data_classification"]).strip().lower()
        if cls_val not in _DATA_CLASSIFICATIONS:
            return _err(
                400,
                f"data_classification must be one of {', '.join(_DATA_CLASSIFICATIONS)}",
            )
        fields["data_classification"] = cls_val
    if "storage_type" in data and data["storage_type"] is not None:
        stype = str(data["storage_type"]).strip().lower()
        if stype not in _STORAGE_TYPES:
            return _err(400, f"storage_type must be one of {', '.join(_STORAGE_TYPES)}")
        fields["storage_type"] = stype
    if "location_region" in data:
        reg, err = _safe_str(data.get("location_region"), 50)
        if err:
            return _err(400, f"location_region {err}")
        fields["location_region"] = reg
    if "description" in data:
        desc, err = _safe_str(data.get("description"), 4000)
        if err:
            return _err(400, f"description {err}")
        fields["description"] = desc
    if "metadata" in data:
        if data["metadata"] is not None and not isinstance(data["metadata"], dict):
            return _err(400, "metadata must be a JSON object")
        fields["metadata"] = data["metadata"]
    if "is_active" in data and data["is_active"] is not None:
        fields["is_active"] = bool(data["is_active"])

    if op == "create":
        if "name" not in fields:
            return _err(400, "name is required")
        org_id, org_err = _resolve_org_id(
            db,
            tenant_id,
            data.get("organization_id"),
            getattr(hook, "organization_id", None),
        )
        if org_err:
            return org_err
        fields.setdefault("data_classification", "internal")
        fields.setdefault("storage_type", "other")
        fields.setdefault("is_active", True)
        new_id = db.data_stores.insert(
            tenant_id=tenant_id,
            organization_id=org_id,
            created_at=now,
            updated_at=now,
            **fields,
        )
        db.commit()
        return 201, "created", {"resource": "data_stores", "id": new_id}

    if target_id is None:
        return _err(400, "Valid resource_id is required")
    existing = get_tenant_scoped(db, db.data_stores, target_id, tenant_id)
    if not existing:
        return _err(404, f"Data store {target_id} not found in tenant")
    if not fields:
        return _err(400, "No updatable data_stores fields supplied")
    fields["updated_at"] = now
    db(
        (db.data_stores.id == target_id) & (db.data_stores.tenant_id == tenant_id)
    ).update(**fields)
    db.commit()
    return 200, "updated", {"resource": "data_stores", "id": target_id}


def _exec_service(
    db: Any, hook: Any, op: str, data: dict[str, Any], target_id: int | None
) -> tuple[int, str, dict[str, Any]]:
    tenant_id = hook.tenant_id
    now = dt.datetime.now(dt.UTC)

    fields: dict[str, Any] = {}
    if "name" in data:
        name, err = _safe_str(data.get("name"), 255)
        if err or not name:
            return _err(400, f"name {err or 'cannot be empty'}")
        fields["name"] = name
    if "port" in data:
        port, err = _parse_int_bounds(data["port"], min_val=1, max_val=65535)
        if err:
            return _err(400, f"port {err}")
        fields["port"] = port
    if "status" in data and data["status"] is not None:
        st = str(data["status"]).strip().lower()
        if st not in _SERVICE_STATUSES:
            return _err(400, f"status must be one of {', '.join(_SERVICE_STATUSES)}")
        fields["status"] = st
    if "is_public" in data and data["is_public"] is not None:
        fields["is_public"] = bool(data["is_public"])
    if "description" in data:
        desc, err = _safe_str(data.get("description"), 4000)
        if err:
            return _err(400, f"description {err}")
        fields["description"] = desc

    if op == "create":
        if "name" not in fields:
            return _err(400, "name is required")
        org_id, org_err = _resolve_org_id(
            db,
            tenant_id,
            data.get("organization_id"),
            getattr(hook, "organization_id", None),
        )
        if org_err:
            return org_err
        fields.setdefault("status", "active")
        fields.setdefault("is_public", False)
        new_id = db.services.insert(
            tenant_id=tenant_id,
            organization_id=org_id,
            tags=[],
            created_at=now,
            updated_at=now,
            **fields,
        )
        db.commit()
        return 201, "created", {"resource": "services", "id": new_id}

    if target_id is None:
        return _err(400, "Valid resource_id is required")
    existing = get_tenant_scoped(db, db.services, target_id, tenant_id)
    if not existing:
        return _err(404, f"Service {target_id} not found in tenant")
    if not fields:
        return _err(400, "No updatable services fields supplied")
    fields["updated_at"] = now
    db((db.services.id == target_id) & (db.services.tenant_id == tenant_id)).update(
        **fields
    )
    db.commit()
    return 200, "updated", {"resource": "services", "id": target_id}


def _exec_software(
    db: Any, hook: Any, op: str, data: dict[str, Any], target_id: int | None
) -> tuple[int, str, dict[str, Any]]:
    tenant_id = hook.tenant_id
    now = dt.datetime.now(dt.UTC)

    fields: dict[str, Any] = {}
    if "name" in data:
        name, err = _safe_str(data.get("name"), 255)
        if err or not name:
            return _err(400, f"name {err or 'cannot be empty'}")
        fields["name"] = name
    if "software_type" in data and data["software_type"] is not None:
        stype = str(data["software_type"]).strip().lower()
        if stype not in _SOFTWARE_TYPES:
            return _err(
                400, f"software_type must be one of {', '.join(_SOFTWARE_TYPES)}"
            )
        fields["software_type"] = stype
    if "version" in data:
        ver, err = _safe_str(data.get("version"), 100)
        if err:
            return _err(400, f"version {err}")
        fields["version"] = ver
    if "description" in data:
        desc, err = _safe_str(data.get("description"), 4000)
        if err:
            return _err(400, f"description {err}")
        fields["description"] = desc
    if "is_active" in data and data["is_active"] is not None:
        fields["is_active"] = bool(data["is_active"])

    if op == "create":
        if "name" not in fields:
            return _err(400, "name is required")
        if "software_type" not in fields:
            return _err(400, "software_type is required")
        org_id, org_err = _resolve_org_id(
            db,
            tenant_id,
            data.get("organization_id"),
            getattr(hook, "organization_id", None),
        )
        if org_err:
            return org_err
        fields.setdefault("is_active", True)
        new_id = db.software.insert(
            tenant_id=tenant_id,
            organization_id=org_id,
            tags=[],
            created_at=now,
            updated_at=now,
            **fields,
        )
        db.commit()
        return 201, "created", {"resource": "software", "id": new_id}

    if target_id is None:
        return _err(400, "Valid resource_id is required")
    existing = get_tenant_scoped(db, db.software, target_id, tenant_id)
    if not existing:
        return _err(404, f"Software {target_id} not found in tenant")
    if not fields:
        return _err(400, "No updatable software fields supplied")
    fields["updated_at"] = now
    db((db.software.id == target_id) & (db.software.tenant_id == tenant_id)).update(
        **fields
    )
    db.commit()
    return 200, "updated", {"resource": "software", "id": target_id}


def _exec_ai_resource(
    db: Any, hook: Any, op: str, data: dict[str, Any], target_id: int | None
) -> tuple[int, str, dict[str, Any]]:
    _ensure_ai_table(db)
    tenant_id = hook.tenant_id
    now = dt.datetime.now(dt.UTC)

    fields: dict[str, Any] = {}
    if "name" in data:
        name, err = _safe_str(data.get("name"), 255)
        if err or not name:
            return _err(400, f"name {err or 'cannot be empty'}")
        fields["name"] = name
    if "provider" in data:
        prov, err = _safe_str(data.get("provider"), 255)
        if err or not prov:
            return _err(400, f"provider {err or 'cannot be empty'}")
        fields["provider"] = prov
    if "model_name" in data:
        mn, err = _safe_str(data.get("model_name"), 255)
        if err:
            return _err(400, f"model_name {err}")
        fields["model_name"] = mn
    if "resource_type" in data:
        rt, err = _safe_str(data.get("resource_type"), 50)
        if err or not rt:
            return _err(400, f"resource_type {err or 'cannot be empty'}")
        fields["resource_type"] = rt
    if "endpoint_url" in data:
        ep, err = _validate_http_url(data.get("endpoint_url"))
        if err:
            return _err(400, f"endpoint_url {err}")
        fields["endpoint_url"] = ep
    if "status" in data and data["status"] is not None:
        st = str(data["status"]).strip().lower()
        if st not in _AI_STATUSES:
            return _err(400, f"status must be one of {', '.join(_AI_STATUSES)}")
        fields["status"] = st
    if "description" in data:
        desc, err = _safe_str(data.get("description"), 4000)
        if err:
            return _err(400, f"description {err}")
        fields["description"] = desc
    if "metadata" in data:
        if data["metadata"] is not None and not isinstance(data["metadata"], dict):
            return _err(400, "metadata must be a JSON object")
        fields["metadata"] = data["metadata"]
    if "is_active" in data and data["is_active"] is not None:
        fields["is_active"] = bool(data["is_active"])

    if op == "create":
        if "name" not in fields:
            return _err(400, "name is required")
        if "provider" not in fields:
            return _err(400, "provider is required")
        if "resource_type" not in fields:
            return _err(400, "resource_type is required")
        org_id, org_err = _resolve_org_id(
            db,
            tenant_id,
            data.get("organization_id"),
            getattr(hook, "organization_id", None),
        )
        if org_err:
            return org_err
        fields.setdefault("status", "active")
        fields.setdefault("is_active", True)
        new_id = db.ai_resources.insert(
            tenant_id=tenant_id,
            organization_id=org_id,
            created_at=now,
            updated_at=now,
            **fields,
        )
        db.commit()
        return 201, "created", {"resource": "ai_resources", "id": new_id}

    if target_id is None:
        return _err(400, "Valid resource_id is required")
    existing = get_tenant_scoped(db, db.ai_resources, target_id, tenant_id)
    if not existing:
        return _err(404, f"AI resource {target_id} not found in tenant")
    if not fields:
        return _err(400, "No updatable ai_resources fields supplied")
    fields["updated_at"] = now
    db(
        (db.ai_resources.id == target_id) & (db.ai_resources.tenant_id == tenant_id)
    ).update(**fields)
    db.commit()
    return 200, "updated", {"resource": "ai_resources", "id": target_id}


def execute_inbound_action(
    db: Any,
    hook: Any,
    required_scope: str,
    action_spec: dict[str, Any],
    source_ip: str | None,
) -> tuple[int, str, dict[str, Any]]:
    """Execute an authorized inbound webhook action.

    Assumes caller has already verified ``required_scope`` is granted on ``hook``.
    Returns ``(http_status, event_status, result_dict)``.
    """
    kind = action_spec.get("kind")
    if kind == "log_only":
        return 202, "received", {"logged": True}

    if kind == "playbook_trigger":
        return _execute_playbook_trigger(db, hook, action_spec, source_ip)

    if kind in ("resource_create", "resource_update"):
        op = "create" if kind == "resource_create" else "update"
        resource = action_spec["resource"]
        data = action_spec["data"]
        target_id = action_spec.get("resource_id")

        if resource == "issues":
            return _exec_issue(db, hook, op, data, target_id)
        if resource == "vulnerabilities":
            return _exec_vulnerability(
                db, hook, op, data, target_id, action_spec.get("cve_id")
            )
        if resource == "entities":
            return _exec_entity(db, hook, op, data, target_id)
        if resource == "data_stores":
            return _exec_data_store(db, hook, op, data, target_id)
        if resource == "services":
            return _exec_service(db, hook, op, data, target_id)
        if resource == "software":
            return _exec_software(db, hook, op, data, target_id)
        if resource == "ai_resources":
            return _exec_ai_resource(db, hook, op, data, target_id)

    return _err(400, f"Unsupported action '{required_scope}'")
