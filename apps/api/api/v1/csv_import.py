"""Bulk CSV import and exact template validation endpoints (max 1,000 rows)."""

# flake8: noqa: E501

import csv
import io
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from quart import Blueprint, Response, current_app, jsonify, request

from apps.api.auth.decorators import login_required, require_scope
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.tenant_scoping import get_current_tenant_id, get_tenant_scoped

bp = Blueprint("csv_import", __name__)

MAX_CSV_ROWS = 1000
MAX_CSV_BYTES = 5 * 1024 * 1024
MAX_ERRORS_RETURNED = 50
FORMULA_PREFIXES = ("=", "+", "@", "\t", "\r")


def _safe_str(val: str | None, max_len: int = 512) -> tuple[str, str | None]:
    """Strip string and reject CSV formula injection / overlong values."""
    cleaned = (val or "").strip()
    if not cleaned:
        return "", None
    if cleaned[0] in FORMULA_PREFIXES:
        return "", "contains disallowed leading formula character"
    if len(cleaned) > max_len:
        return "", f"exceeds max length of {max_len}"
    return cleaned, None


def _parse_int(
    val: str | None, min_val: int = 1, max_val: int | None = None
) -> tuple[int | None, str | None]:
    cleaned = (val or "").strip()
    if not cleaned:
        return None, None
    try:
        parsed = int(cleaned)
    except ValueError:
        return None, "must be an integer"
    if parsed < min_val or (max_val is not None and parsed > max_val):
        bound = f"{min_val}..{max_val}" if max_val is not None else f">= {min_val}"
        return None, f"must be {bound}"
    return parsed, None


def _parse_float(
    val: str | None, min_val: float, max_val: float
) -> tuple[float | None, str | None]:
    cleaned = (val or "").strip()
    if not cleaned:
        return None, None
    try:
        parsed = float(cleaned)
    except ValueError:
        return None, "must be a valid number"
    if parsed < min_val or parsed > max_val:
        return None, f"must be between {min_val} and {max_val}"
    return parsed, None


def _parse_bool(val: str | None, default: bool = False) -> tuple[bool, str | None]:
    cleaned = (val or "").strip().lower()
    if not cleaned:
        return default, None
    if cleaned in ("true", "1", "yes"):
        return True, None
    if cleaned in ("false", "0", "no"):
        return False, None
    return default, "must be true or false"


def _parse_url(val: str | None) -> tuple[str | None, str | None]:
    cleaned, err = _safe_str(val, max_len=1024)
    if err or not cleaned:
        return None, err
    parsed = urlparse(cleaned)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None, "must be a valid http/https URL"
    return cleaned, None


def _parse_iso_datetime(val: str | None) -> tuple[datetime | None, str | None]:
    cleaned = (val or "").strip()
    if not cleaned:
        return None, None
    try:
        return datetime.fromisoformat(cleaned.replace("Z", "+00:00")), None
    except ValueError:
        return None, "must be a valid ISO-8601 date (YYYY-MM-DD) or datetime"


CSV_TEMPLATES: dict[str, dict[str, Any]] = {
    "vulnerabilities": {
        "label": "Vulnerabilities",
        "scope": "sbom:write",
        "headers": [
            "cve_id",
            "title",
            "severity",
            "cvss_score",
            "description",
            "published_at",
        ],
        "required": ["cve_id", "title", "severity"],
        "rules": {
            "cve_id": "Required, unique identifier (e.g. CVE-2026-1234), max 50 chars",
            "title": "Required, max 512 chars",
            "severity": "Required: critical, high, medium, low, or info",
            "cvss_score": "Optional number 0.0 - 10.0",
            "description": "Optional summary",
            "published_at": "Optional ISO-8601 date (YYYY-MM-DD)",
        },
        "example": [
            "CVE-2026-9999",
            "Example buffer overflow in libfoo",
            "high",
            "7.8",
            "Remote code execution via crafted packet",
            "2026-10-01",
        ],
    },
    "entities": {
        "label": "Entities",
        "scope": "infrastructure:write",
        "headers": [
            "name",
            "type",
            "organization_id",
            "sub_type",
            "status",
        ],
        "required": ["name", "type", "organization_id"],
        "rules": {
            "name": "Required, max 255 chars",
            "type": "Required (e.g. system, application, host, container, cloud_account)",
            "organization_id": "Required integer Organization ID belonging to your tenant",
            "sub_type": "Optional sub-type",
            "status": "Optional: active or inactive (default: active)",
        },
        "example": ["prod-api-cluster", "system", "1", "kubernetes", "active"],
    },
    "data_stores": {
        "label": "Data Inventory",
        "scope": "infrastructure:write",
        "headers": [
            "name",
            "organization_id",
            "data_classification",
            "storage_type",
            "location_region",
            "description",
        ],
        "required": ["name", "organization_id", "data_classification", "storage_type"],
        "rules": {
            "name": "Required, max 255 chars",
            "organization_id": "Required integer Organization ID",
            "data_classification": "Required: public, internal, confidential, or restricted",
            "storage_type": "Required: database, file_storage, data_warehouse, data_lake, cache, message_queue, search_index, time_series, blob_storage, or other",
            "location_region": "Optional region code (e.g. us-east-1)",
            "description": "Optional description",
        },
        "example": [
            "customer-billing-db",
            "1",
            "restricted",
            "database",
            "us-east-1",
            "Primary billing PostgreSQL database",
        ],
    },
    "services": {
        "label": "Services",
        "scope": "sbom:write",
        "headers": [
            "name",
            "organization_id",
            "port",
            "status",
            "is_public",
        ],
        "required": ["name", "organization_id"],
        "rules": {
            "name": "Required, max 255 chars",
            "organization_id": "Required integer Organization ID",
            "port": "Optional integer 1 - 65535",
            "status": "Optional: active, inactive, or deprecated (default: active)",
            "is_public": "Optional: true or false (default: false)",
        },
        "example": ["auth-gateway", "1", "8443", "active", "false"],
    },
    "software": {
        "label": "Software",
        "scope": "sbom:write",
        "headers": [
            "name",
            "organization_id",
            "software_type",
            "version",
            "description",
        ],
        "required": ["name", "organization_id", "software_type"],
        "rules": {
            "name": "Required, max 255 chars",
            "organization_id": "Required integer Organization ID",
            "software_type": "Required: saas, internal, library, os, or container",
            "version": "Optional version string",
            "description": "Optional description",
        },
        "example": [
            "Nginx Ingress",
            "1",
            "container",
            "1.11.0",
            "Cluster ingress controller",
        ],
    },
    "ai_resources": {
        "label": "AI Resources",
        "scope": "infrastructure:write",
        "headers": [
            "name",
            "organization_id",
            "provider",
            "model_name",
            "resource_type",
            "endpoint_url",
            "status",
            "description",
        ],
        "required": ["name", "organization_id", "provider", "resource_type"],
        "rules": {
            "name": "Required, max 255 chars",
            "organization_id": "Required integer Organization ID",
            "provider": "Required (e.g. openai, anthropic, google, local)",
            "model_name": "Optional model name (e.g. gpt-4o)",
            "resource_type": "Required (e.g. llm, embedding, agent, vector_db)",
            "endpoint_url": "Optional valid http/https URL",
            "status": "Optional: active or inactive (default: active)",
            "description": "Optional description",
        },
        "example": [
            "Internal Support Assistant",
            "1",
            "anthropic",
            "claude-sonnet",
            "llm",
            "https://api.anthropic.com",
            "active",
            "Support triage model",
        ],
    },
}


@bp.route("/templates", methods=["GET"])
@login_required
def list_csv_templates():
    """Return all supported CSV import templates and their exact column rules."""
    templates = []
    for key, spec in CSV_TEMPLATES.items():
        templates.append(
            {
                "resource": key,
                "label": spec["label"],
                "max_rows": MAX_CSV_ROWS,
                "headers": spec["headers"],
                "required": spec["required"],
                "rules": spec["rules"],
                "example": spec["example"],
            }
        )
    return jsonify({"max_rows": MAX_CSV_ROWS, "templates": templates}), 200


@bp.route("/templates/<resource>/download", methods=["GET"])
@login_required
def download_csv_template(resource: str):
    """Download an exact CSV template with header row and example row."""
    spec = CSV_TEMPLATES.get(resource)
    if not spec:
        return ApiResponse.not_found("CSV template", resource)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(spec["headers"])
    writer.writerow(spec["example"])
    return Response(
        buf.getvalue(),
        status=200,
        headers={
            "Content-Type": "text/csv; charset=utf-8",
            "Content-Disposition": f'attachment; filename="{resource}_template.csv"',
        },
    )


def _validate_org(db, org_cache: dict[int, bool], org_id: int, tenant_id: int) -> bool:
    if org_id in org_cache:
        return org_cache[org_id]
    org = get_tenant_scoped(db, db.organizations, org_id, tenant_id)
    ok = org is not None
    org_cache[org_id] = ok
    return ok


@bp.route("/<resource>", methods=["POST"])
@login_required
async def import_csv_resource(resource: str):
    """Validate an exact CSV against the resource template (max 1000 rows) and import."""
    spec = CSV_TEMPLATES.get(resource)
    if not spec:
        return ApiResponse.not_found("CSV import resource", resource)

    db = current_app.db
    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return ApiResponse.error("Tenant context is required", 403)

    # Read CSV text from multipart upload or JSON body
    csv_text = ""
    files = await request.files
    if "file" in files:
        uploaded = files["file"]
        raw_bytes = uploaded.read(MAX_CSV_BYTES + 1)
        if len(raw_bytes) > MAX_CSV_BYTES:
            return ApiResponse.error("CSV file exceeds maximum 5 MB size limit", 400)
        try:
            csv_text = raw_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            return ApiResponse.error("CSV file must be UTF-8 encoded", 400)
    else:
        body = await request.get_json(silent=True)
        if not isinstance(body, dict) or not isinstance(body.get("csv_text"), str):
            return ApiResponse.error(
                "Provide a multipart 'file' or JSON { csv_text }", 400
            )
        csv_text = body["csv_text"].lstrip("\ufeff")
        if len(csv_text.encode("utf-8")) > MAX_CSV_BYTES:
            return ApiResponse.error("CSV payload exceeds maximum 5 MB size limit", 400)

    reader = csv.reader(io.StringIO(csv_text))
    all_rows = list(reader)
    if not all_rows:
        return ApiResponse.error("CSV file is empty", 400)

    actual_headers = [h.strip() for h in all_rows[0]]
    expected_headers = spec["headers"]
    if actual_headers != expected_headers:
        return jsonify(
            {
                "error": "CSV headers do not match the exact template",
                "expected_headers": expected_headers,
                "actual_headers": actual_headers,
            }
        ), 400

    data_rows = [r for r in all_rows[1:] if any(cell.strip() for cell in r)]
    if not data_rows:
        return ApiResponse.error("CSV contains no data rows", 400)
    if len(data_rows) > MAX_CSV_ROWS:
        return ApiResponse.error(
            f"CSV exceeds maximum limit of {MAX_CSV_ROWS} rows (got {len(data_rows)})",
            400,
        )

    def validate_and_insert():
        errors: list[str] = []
        prepared: list[dict[str, Any]] = []
        org_cache: dict[int, bool] = {}
        seen_cves: set[str] = set()
        now = datetime.now(UTC)

        for idx, raw_row in enumerate(data_rows, start=2):
            if len(raw_row) != len(expected_headers):
                errors.append(
                    f"Row {idx}: expected {len(expected_headers)} columns, got {len(raw_row)}"
                )
                continue
            row = dict(zip(expected_headers, raw_row))

            if resource == "vulnerabilities":
                cve_id, e1 = _safe_str(row["cve_id"], 50)
                cve_id = cve_id.upper()
                title, e2 = _safe_str(row["title"], 512)
                sev, e3 = _safe_str(row["severity"], 20)
                sev = sev.upper()
                cvss, e4 = _parse_float(row["cvss_score"], 0.0, 10.0)
                desc, e5 = _safe_str(row["description"], 4000)
                pub, e6 = _parse_iso_datetime(row["published_at"])
                for field, err in (
                    ("cve_id", e1 or (None if cve_id else "is required")),
                    ("title", e2 or (None if title else "is required")),
                    (
                        "severity",
                        e3
                        or (
                            None
                            if sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO")
                            else "must be critical/high/medium/low/info"
                        ),
                    ),
                    ("cvss_score", e4),
                    ("description", e5),
                    ("published_at", e6),
                ):
                    if err:
                        errors.append(f"Row {idx} ({field}): {err}")
                if cve_id:
                    if (
                        cve_id in seen_cves
                        or db(db.vulnerabilities.cve_id == cve_id).select().first()
                    ):
                        errors.append(
                            f"Row {idx} (cve_id): '{cve_id}' already exists or is duplicated"
                        )
                    seen_cves.add(cve_id)
                prepared.append(
                    {
                        "tenant_id": tenant_id,
                        "cve_id": cve_id,
                        "title": title,
                        "severity": sev,
                        "cvss_score": cvss,
                        "description": desc or None,
                        "published_at": pub,
                        "source": "csv_import",
                        "is_active": True,
                        "created_at": now,
                        "updated_at": now,
                    }
                )
            else:
                org_id, org_err = _parse_int(row.get("organization_id"), min_val=1)
                if org_err or org_id is None:
                    errors.append(
                        f"Row {idx} (organization_id): {org_err or 'is required'}"
                    )
                elif not _validate_org(db, org_cache, org_id, tenant_id):
                    errors.append(
                        f"Row {idx} (organization_id): organization {org_id} not found in tenant"
                    )

                name, name_err = _safe_str(row.get("name"), 255)
                if name_err or not name:
                    errors.append(f"Row {idx} (name): {name_err or 'is required'}")

                if resource == "entities":
                    etype, et_err = _safe_str(row.get("type"), 100)
                    sub_type, st_err = _safe_str(row.get("sub_type"), 100)
                    status, s_err = _safe_str(row.get("status"), 50)
                    status = (status or "active").lower()
                    for field, err in (
                        ("type", et_err or (None if etype else "is required")),
                        ("sub_type", st_err),
                        (
                            "status",
                            s_err
                            or (
                                None
                                if status in ("active", "inactive")
                                else "must be active or inactive"
                            ),
                        ),
                    ):
                        if err:
                            errors.append(f"Row {idx} ({field}): {err}")
                    prepared.append(
                        {
                            "name": name,
                            "type": etype,
                            "organization_id": org_id,
                            "sub_type": sub_type or None,
                            "status": status,
                            "is_managed": False,
                            "tags": [],
                            "created_at": now,
                            "updated_at": now,
                        }
                    )
                elif resource == "data_stores":
                    classif, c_err = _safe_str(row.get("data_classification"), 50)
                    classif = classif.lower()
                    stype, st_err = _safe_str(row.get("storage_type"), 50)
                    stype = stype.lower()
                    region, r_err = _safe_str(row.get("location_region"), 100)
                    desc, d_err = _safe_str(row.get("description"), 2000)
                    valid_classifs = (
                        "public",
                        "internal",
                        "confidential",
                        "restricted",
                    )
                    valid_stypes = (
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
                    for field, err in (
                        (
                            "data_classification",
                            c_err
                            or (
                                None
                                if classif in valid_classifs
                                else f"must be one of {', '.join(valid_classifs)}"
                            ),
                        ),
                        (
                            "storage_type",
                            st_err
                            or (
                                None
                                if stype in valid_stypes
                                else f"must be one of {', '.join(valid_stypes)}"
                            ),
                        ),
                        ("location_region", r_err),
                        ("description", d_err),
                    ):
                        if err:
                            errors.append(f"Row {idx} ({field}): {err}")
                    prepared.append(
                        {
                            "name": name,
                            "organization_id": org_id,
                            "tenant_id": tenant_id,
                            "data_classification": classif,
                            "storage_type": stype,
                            "location_region": region or None,
                            "description": desc or None,
                            "is_active": True,
                            "created_at": now,
                            "updated_at": now,
                        }
                    )
                elif resource == "services":
                    port, p_err = _parse_int(row.get("port"), min_val=1, max_val=65535)
                    status, s_err = _safe_str(row.get("status"), 50)
                    status = (status or "active").lower()
                    is_pub, pub_err = _parse_bool(row.get("is_public"), default=False)
                    for field, err in (
                        ("port", p_err),
                        (
                            "status",
                            s_err
                            or (
                                None
                                if status in ("active", "inactive", "deprecated")
                                else "must be active, inactive, or deprecated"
                            ),
                        ),
                        ("is_public", pub_err),
                    ):
                        if err:
                            errors.append(f"Row {idx} ({field}): {err}")
                    prepared.append(
                        {
                            "name": name,
                            "organization_id": org_id,
                            "tenant_id": tenant_id,
                            "port": port,
                            "status": status,
                            "is_public": is_pub,
                            "tags": [],
                            "created_at": now,
                            "updated_at": now,
                        }
                    )
                elif resource == "software":
                    stype, st_err = _safe_str(row.get("software_type"), 50)
                    stype = stype.lower()
                    ver, v_err = _safe_str(row.get("version"), 100)
                    desc, d_err = _safe_str(row.get("description"), 2000)
                    for field, err in (
                        (
                            "software_type",
                            st_err
                            or (
                                None
                                if stype
                                in ("saas", "internal", "library", "os", "container")
                                else "must be saas, internal, library, os, or container"
                            ),
                        ),
                        ("version", v_err),
                        ("description", d_err),
                    ):
                        if err:
                            errors.append(f"Row {idx} ({field}): {err}")
                    prepared.append(
                        {
                            "name": name,
                            "organization_id": org_id,
                            "tenant_id": tenant_id,
                            "software_type": stype,
                            "version": ver or None,
                            "description": desc or None,
                            "created_at": now,
                            "updated_at": now,
                        }
                    )
                elif resource == "ai_resources":
                    prov, pr_err = _safe_str(row.get("provider"), 100)
                    model_n, mn_err = _safe_str(row.get("model_name"), 255)
                    rtype, rt_err = _safe_str(row.get("resource_type"), 100)
                    ep_url, ep_err = _parse_url(row.get("endpoint_url"))
                    status, s_err = _safe_str(row.get("status"), 50)
                    status = (status or "active").lower()
                    desc, d_err = _safe_str(row.get("description"), 2000)
                    for field, err in (
                        ("provider", pr_err or (None if prov else "is required")),
                        ("model_name", mn_err),
                        ("resource_type", rt_err or (None if rtype else "is required")),
                        ("endpoint_url", ep_err),
                        (
                            "status",
                            s_err
                            or (
                                None
                                if status in ("active", "inactive")
                                else "must be active or inactive"
                            ),
                        ),
                        ("description", d_err),
                    ):
                        if err:
                            errors.append(f"Row {idx} ({field}): {err}")
                    prepared.append(
                        {
                            "name": name,
                            "organization_id": org_id,
                            "tenant_id": tenant_id,
                            "provider": prov,
                            "model_name": model_n or None,
                            "resource_type": rtype,
                            "endpoint_url": ep_url,
                            "status": status,
                            "description": desc or None,
                            "is_active": True,
                            "created_at": now,
                            "updated_at": now,
                        }
                    )

            if len(errors) >= MAX_ERRORS_RETURNED:
                break

        if errors:
            return 0, errors

        table = getattr(db, resource)
        for item in prepared:
            table.insert(**item)
        db.commit()
        return len(prepared), []

    imported_count, errors = await run_in_threadpool(validate_and_insert)
    if errors:
        return jsonify(
            {
                "error": "CSV validation failed",
                "details": errors[:MAX_ERRORS_RETURNED],
            }
        ), 400

    return jsonify({"resource": resource, "imported": imported_count}), 201
