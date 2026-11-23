"""Bulk CSV import, template download, and async S3 batch job endpoints."""

# flake8: noqa: E501

from __future__ import annotations

import csv
import io
import json
import os
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import structlog
from quart import Blueprint, Response, current_app, g, jsonify, request

from apps.api.auth.decorators import login_required
from apps.api.services.csv_import.processor import (
    CSV_TEMPLATES,
    FORMULA_PREFIXES,
    MAX_BATCH_CSV_ROWS,
    MAX_CSV_BYTES,
    MAX_CSV_ROWS,
    MAX_ERRORS_RETURNED,
    process_csv_text,
    validate_csv_structure,
)
from apps.api.services.csv_import.storage import get_csv_import_storage
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.tenant_scoping import get_current_tenant_id

logger = structlog.get_logger(__name__)

bp = Blueprint("csv_import", __name__)

__all__ = [
    "CSV_TEMPLATES",
    "FORMULA_PREFIXES",
    "MAX_BATCH_CSV_ROWS",
    "MAX_CSV_BYTES",
    "MAX_CSV_ROWS",
    "MAX_ERRORS_RETURNED",
    "bp",
]


def _iso_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _parse_errors_json(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except ValueError:
            pass
    return []


def _serialize_job(row: Any) -> dict[str, Any]:
    """Serialize a ``csv_import_jobs`` row into an explicit API response DTO."""
    return {
        "job_id": row.job_id,
        "resource": row.resource,
        "filename": row.filename or "",
        "storage_backend": row.storage_backend or "s3",
        "storage_key": row.storage_key,
        "file_sha256": row.file_sha256,
        "file_size_bytes": row.file_size_bytes,
        "status": row.status,
        "total_rows": int(row.total_rows or 0),
        "imported_count": int(row.imported_count or 0),
        "skipped_count": int(row.skipped_count or 0),
        "errors": _parse_errors_json(row.errors_json),
        "error_message": row.error_message,
        "created_by_identity_id": row.created_by_identity_id,
        "worker_id": row.worker_id,
        "started_at": _iso_or_none(row.started_at),
        "completed_at": _iso_or_none(row.completed_at),
        "created_at": _iso_or_none(row.created_at),
    }


def _allow_sync_fallback() -> bool:
    """Return True only when sync fallback is explicitly enabled or in TESTING."""
    env_val = os.getenv("CSV_IMPORT_ALLOW_SYNC_FALLBACK")
    if env_val is not None and env_val.strip() != "":
        return env_val.strip().lower() in ("1", "true", "yes")
    return bool(current_app.config.get("TESTING"))


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


@bp.route("/jobs", methods=["GET"])
@login_required
async def list_csv_import_jobs():
    """List tenant-scoped CSV import jobs with pagination and optional filters."""
    db = current_app.db
    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return ApiResponse.error("Tenant context is required", 403)

    resource_filter = (request.args.get("resource") or "").strip() or None
    status_filter = (request.args.get("status") or "").strip() or None
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        return ApiResponse.error("page must be an integer", 400)
    try:
        per_page = max(1, min(100, int(request.args.get("per_page", 20))))
    except ValueError:
        return ApiResponse.error("per_page must be an integer", 400)

    def fetch_jobs() -> tuple[list[dict[str, Any]], int]:
        query = db.csv_import_jobs.tenant_id == tenant_id
        if resource_filter:
            query &= db.csv_import_jobs.resource == resource_filter
        if status_filter:
            query &= db.csv_import_jobs.status == status_filter

        total = int(db(query).count())
        offset = (page - 1) * per_page
        rows = db(query).select(
            orderby=~db.csv_import_jobs.id,
            limitby=(offset, offset + per_page),
        )
        return [_serialize_job(r) for r in rows], total

    items, total = await run_in_threadpool(fetch_jobs)
    pages = (total + per_page - 1) // per_page if total else 0
    return (
        jsonify(
            {
                "items": items,
                "total": total,
                "page": page,
                "per_page": per_page,
                "pages": pages,
            }
        ),
        200,
    )


@bp.route("/jobs/<job_id>", methods=["GET"])
@login_required
async def get_csv_import_job(job_id: str):
    """Fetch a single tenant-scoped CSV import job by its public ``job_id``."""
    db = current_app.db
    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return ApiResponse.error("Tenant context is required", 403)

    def fetch_job() -> dict[str, Any] | None:
        row = (
            db(
                (db.csv_import_jobs.job_id == job_id)
                & (db.csv_import_jobs.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        return _serialize_job(row) if row is not None else None

    job = await run_in_threadpool(fetch_job)
    if job is None:
        return ApiResponse.not_found("CSV import job", job_id)
    return jsonify(job), 200


@bp.route("/<resource>", methods=["POST"])
@login_required
async def import_csv_resource(resource: str):
    """Validate CSV headers & scope, then queue via S3 (202) or sync fallback."""
    spec = CSV_TEMPLATES.get(resource)
    if not spec:
        return ApiResponse.not_found("CSV import resource", resource)

    # Enforce per-resource OIDC write scope (matches @require_scope)
    required_scope = spec["scope"]
    claims = getattr(g, "claims", {}) or {}
    granted = set(claims.get("scope", []))
    current_user = getattr(g, "current_user", None)
    is_super = bool(current_user and getattr(current_user, "is_superuser", False))
    if not is_super and required_scope not in granted:
        return jsonify({"error": f"Missing required scope: '{required_scope}'"}), 403

    db = current_app.db
    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return ApiResponse.error("Tenant context is required", 403)

    # Read CSV text from multipart upload or JSON body (never written to disk)
    csv_text = ""
    filename = f"{resource}.csv"
    files = await request.files
    if "file" in files:
        uploaded = files["file"]
        if getattr(uploaded, "filename", None):
            filename = str(uploaded.filename)[:255]
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
        if isinstance(body.get("filename"), str) and body["filename"].strip():
            filename = body["filename"].strip()[:255]
        csv_text = body["csv_text"].lstrip("\ufeff")
        if len(csv_text.encode("utf-8")) > MAX_CSV_BYTES:
            return ApiResponse.error("CSV payload exceeds maximum 5 MB size limit", 400)

    storage = get_csv_import_storage()
    max_rows = MAX_BATCH_CSV_ROWS if storage.is_configured else MAX_CSV_ROWS

    data_rows, struct_err = validate_csv_structure(
        csv_text, resource, max_rows=max_rows
    )
    if struct_err is not None or data_rows is None:
        return jsonify(struct_err or {"error": "Invalid CSV payload"}), 400

    # Primary path: S3-backed async batch pipeline
    if storage.is_configured:
        job_id = f"csvimp-{uuid4().hex[:16]}"
        created_by_id = getattr(current_user, "id", None) if current_user else None

        def upload_and_enqueue() -> dict[str, Any]:
            stored = storage.put_csv(
                tenant_id=tenant_id,
                resource=resource,
                job_id=job_id,
                csv_text=csv_text,
            )
            now = datetime.now(UTC)
            db.csv_import_jobs.insert(
                job_id=job_id,
                tenant_id=tenant_id,
                resource=resource,
                filename=filename,
                storage_backend=stored.storage_backend,
                storage_key=stored.storage_key,
                file_sha256=stored.file_sha256,
                file_size_bytes=stored.file_size_bytes,
                status="pending",
                total_rows=len(data_rows),
                imported_count=0,
                skipped_count=0,
                errors_json=None,
                error_message=None,
                created_by_identity_id=created_by_id,
                worker_id=None,
                started_at=None,
                completed_at=None,
                created_at=now,
            )
            db.commit()
            return {
                "job_id": job_id,
                "status": "pending",
                "resource": resource,
                "storage_backend": stored.storage_backend,
                "storage_key": stored.storage_key,
                "file_sha256": stored.file_sha256,
                "total_rows": len(data_rows),
                "message": "CSV import queued for asynchronous batch processing",
            }

        try:
            queued = await run_in_threadpool(upload_and_enqueue)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "csv_import_enqueue_failed",
                resource=resource,
                tenant_id=tenant_id,
                job_id=job_id,
                error=str(exc),
            )
            return (
                jsonify(
                    {
                        "error": "Failed to upload CSV import payload to S3 storage",
                        "detail": str(exc),
                    }
                ),
                502,
            )
        return jsonify(queued), 202

    # Unconfigured S3: allow synchronous fallback ONLY when explicitly enabled
    # or running in TESTING mode; otherwise fail loudly with 503.
    if not _allow_sync_fallback():
        return (
            jsonify(
                {
                    "error": (
                        "CSV import S3 storage is not configured. Configure "
                        "CSV_IMPORT_S3_BUCKET or ELDER_S3_BUCKET (or explicitly "
                        "enable CSV_IMPORT_ALLOW_SYNC_FALLBACK=true for small "
                        "synchronous imports)."
                    )
                }
            ),
            503,
        )

    result = await run_in_threadpool(
        process_csv_text,
        db,
        tenant_id,
        resource,
        csv_text,
        max_rows=MAX_CSV_ROWS,
    )
    if result.errors:
        return (
            jsonify(
                {
                    "error": "CSV validation failed",
                    "details": result.errors[:MAX_ERRORS_RETURNED],
                }
            ),
            400,
        )

    return jsonify({"resource": resource, "imported": result.imported_count}), 201
