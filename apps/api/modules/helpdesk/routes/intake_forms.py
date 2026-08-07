"""Admin CRUD + public submit routes for configurable intake forms.

Intake forms (hd_intake_forms) are the CRM-facing entry point into the
unified Issues model: a public submission against one of these forms creates
a native Issue rather than an hd_tickets row (see HdTicketForm for that
older path). `bp` covers admin management (login + helpdesk:admin scope);
`bp_public` covers the unauthenticated GET/submit routes a form's own public
page uses, mounted separately at `/api/v1/intake` so the two never share a
URL prefix or an auth posture.
"""

import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from quart import Blueprint, current_app, g, jsonify, request

from apps.api.auth.decorators import login_required, require_scope
from apps.api.modules.helpdesk.services.altcha import create_challenge, verify_solution
from apps.api.modules.helpdesk.services.form_validation import validate_submission
from apps.api.modules.helpdesk.services.intake_submit import (
    create_support_issue_from_form,
    upsert_customer_contact,
)
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.pydal_helpers import PaginationParams

logger = logging.getLogger(__name__)

bp = Blueprint("helpdesk_intake_forms", __name__)
bp_public = Blueprint("helpdesk_intake_public", __name__)


def _get_tenant_id() -> int | None:
    """Extract tenant_id from g.claims (populated by before_request)."""
    claims = getattr(g, "claims", {}) or {}
    tenant_str = claims.get("tenant", "")
    if not tenant_str:
        return None
    try:
        return int(tenant_str)
    except (ValueError, TypeError):
        return None


def _serialize(form_row) -> dict:
    """Render a penguin-dal intake form Row as a JSON-safe dict."""
    fields = json.loads(form_row.fields) if form_row.fields else []
    metadata = json.loads(form_row.metadata) if form_row.metadata else None
    return {
        "id": form_row.id,
        "village_id": form_row.village_id,
        "name": form_row.name,
        "slug": form_row.slug,
        "description": form_row.description,
        "fields": fields,
        "issue_type": form_row.issue_type,
        "default_assignee_type": form_row.default_assignee_type,
        "default_assignee_id": form_row.default_assignee_id,
        "organization_id": form_row.organization_id,
        "is_public": form_row.is_public,
        "captcha_required": form_row.captcha_required,
        "is_active": form_row.is_active,
        "metadata": metadata,
        "created_at": (
            form_row.created_at.isoformat() if form_row.created_at else None
        ),
        "updated_at": (
            form_row.updated_at.isoformat() if form_row.updated_at else None
        ),
    }


@bp.route("", methods=["GET"])
@login_required
@require_scope("helpdesk:admin")
async def list_forms():
    """
    List intake forms for the caller's tenant with pagination.

    Query Parameters:
        - page: Page number (default: 1)
        - per_page: Items per page (default: 20, max: 100)
        - is_active: Filter by active status (true/false)

    Returns:
        200: Paginated list of forms
        403: Tenant not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    pagination = PaginationParams.from_request()

    query = db.hd_intake_forms.tenant_id == tenant_id

    if request.args.get("is_active"):
        is_active_str = request.args.get("is_active").lower()
        is_active = is_active_str in ("true", "1", "yes")
        query &= db.hd_intake_forms.is_active == is_active

    def get_forms():
        total = db(query).count()
        rows = db(query).select(
            orderby=~db.hd_intake_forms.created_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )
        return total, rows

    total, rows = await run_in_threadpool(get_forms)

    forms = [_serialize(r) for r in rows]

    return (
        jsonify(
            {
                "items": forms,
                "pagination": {
                    "page": pagination.page,
                    "per_page": pagination.per_page,
                    "total": total,
                    "pages": (total + pagination.per_page - 1) // pagination.per_page,
                },
            }
        ),
        200,
    )


@bp.route("", methods=["POST"])
@login_required
@require_scope("helpdesk:admin")
async def create_form():
    """
    Create a new intake form.

    Request body:
        {
            "name": "Support Request",
            "slug": "support-request",
            "description": "General support inquiry form",
            "fields": [{"id": "email", "label": "Email", "type": "email", "required": true}],
            "issue_type": "support",
            "default_assignee_type": "identity",
            "default_assignee_id": 1,
            "organization_id": 1,
            "is_public": true,
            "captcha_required": true,
            "is_active": true,
            "metadata": {}
        }

    Returns:
        201: Created form
        400: Validation error
        403: Tenant not found
        409: Slug already in use (globally unique)
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    data = await request.get_json() or {}

    name = (data.get("name") or "").strip()
    slug = (data.get("slug") or "").strip().lower()
    fields = data.get("fields")

    if not name or not slug:
        return ApiResponse.validation_error("name and slug", "are required")
    if not isinstance(fields, list) or not fields:
        return ApiResponse.validation_error("fields", "must be a non-empty array")

    redis_client = getattr(current_app, "redis_client", None)

    def create():
        from shared.utils.village_id import generate_village_id

        now = datetime.now(timezone.utc)

        # Slug must be GLOBALLY unique (public URL /api/v1/intake/<slug> has
        # no tenant component). Reject collisions across ALL tenants.
        existing = db(db.hd_intake_forms.slug == slug).select().first()
        if existing:
            return None, "duplicate_slug"

        if redis_client:
            village_id = generate_village_id(tenant_id, redis_client)
        else:
            # Fallback for test environments without a live Redis connection.
            village_id = f"test-{uuid4().hex[:8]}"

        metadata = data.get("metadata")

        # penguin-dal insert() does not apply SQLAlchemy Column(default=...),
        # so every NOT-NULL column is passed explicitly.
        insert_data = {
            "tenant_id": tenant_id,
            "village_id": village_id,
            "name": name,
            "slug": slug,
            "description": data.get("description"),
            "fields": json.dumps(fields),
            "issue_type": data.get("issue_type", "support"),
            "default_assignee_type": data.get("default_assignee_type"),
            "default_assignee_id": data.get("default_assignee_id"),
            "organization_id": data.get("organization_id"),
            "is_public": data.get("is_public", False),
            "captcha_required": data.get("captcha_required", False),
            "is_active": data.get("is_active", True),
            "metadata": json.dumps(metadata) if metadata is not None else None,
            "created_at": now,
            "updated_at": now,
        }

        form_id = db.hd_intake_forms.insert(**insert_data)
        db.commit()

        return db(db.hd_intake_forms.id == form_id).select().first(), None

    form_row, error = await run_in_threadpool(create)

    if error == "duplicate_slug":
        return ApiResponse.conflict("Form slug must be globally unique")

    if not form_row:
        return ApiResponse.error("Failed to create form", 400)

    return jsonify(_serialize(form_row)), 201


@bp.route("/<int:form_id>", methods=["GET"])
@login_required
@require_scope("helpdesk:admin")
async def get_form(form_id):
    """
    Get a single intake form by ID (tenant-scoped).

    Returns:
        200: Form details
        403: Tenant not found
        404: Form not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    def fetch():
        return (
            db(
                (db.hd_intake_forms.id == form_id)
                & (db.hd_intake_forms.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

    form_row = await run_in_threadpool(fetch)

    if not form_row:
        return ApiResponse.not_found("Form")

    return jsonify(_serialize(form_row))


@bp.route("/<int:form_id>", methods=["PATCH"])
@login_required
@require_scope("helpdesk:admin")
async def update_form(form_id):
    """
    Update an intake form (tenant-scoped). Slug is immutable once created.

    Returns:
        200: Updated form
        403: Tenant not found
        404: Form not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    data = await request.get_json() or {}

    def update():
        form_row = (
            db(
                (db.hd_intake_forms.id == form_id)
                & (db.hd_intake_forms.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not form_row:
            return None

        now = datetime.now(timezone.utc)
        updates = {"updated_at": now}

        if "name" in data:
            updates["name"] = (data["name"] or "").strip()
        if "description" in data:
            updates["description"] = data["description"]
        if "fields" in data:
            updates["fields"] = json.dumps(data["fields"])
        if "issue_type" in data:
            updates["issue_type"] = data["issue_type"]
        if "default_assignee_type" in data:
            updates["default_assignee_type"] = data["default_assignee_type"]
        if "default_assignee_id" in data:
            updates["default_assignee_id"] = data["default_assignee_id"]
        if "organization_id" in data:
            updates["organization_id"] = data["organization_id"]
        if "is_public" in data:
            updates["is_public"] = data["is_public"]
        if "captcha_required" in data:
            updates["captcha_required"] = data["captcha_required"]
        if "is_active" in data:
            updates["is_active"] = data["is_active"]
        if "metadata" in data:
            metadata = data["metadata"]
            updates["metadata"] = json.dumps(metadata) if metadata is not None else None

        db(db.hd_intake_forms.id == form_id).update(**updates)
        db.commit()

        return db(db.hd_intake_forms.id == form_id).select().first()

    form_row = await run_in_threadpool(update)

    if not form_row:
        return ApiResponse.not_found("Form")

    return jsonify(_serialize(form_row))


@bp.route("/<int:form_id>", methods=["DELETE"])
@login_required
@require_scope("helpdesk:admin")
async def delete_form(form_id):
    """
    Delete an intake form (tenant-scoped).

    Returns:
        204: Form deleted
        403: Tenant not found
        404: Form not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    def delete():
        form_row = (
            db(
                (db.hd_intake_forms.id == form_id)
                & (db.hd_intake_forms.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not form_row:
            return False

        db(db.hd_intake_forms.id == form_id).delete()
        db.commit()
        return True

    deleted = await run_in_threadpool(delete)

    if not deleted:
        return ApiResponse.not_found("Form")

    return "", 204


# ---------------------------------------------------------------------------
# Public routes (no auth) — mounted at /api/v1/intake via bp_public.
# ---------------------------------------------------------------------------


def _fetch_public_form(db, slug: str):
    """Return the active+public form matching `slug`, or None.

    Shared by both public routes below. A single "not active AND public"
    filter (rather than checking each independently) means a private or
    inactive form 404s exactly like a nonexistent slug — the public route
    never distinguishes "doesn't exist" from "exists but isn't public".
    """
    return (
        db(
            (db.hd_intake_forms.slug == slug)
            & (db.hd_intake_forms.is_active == True)  # noqa: E712
            & (db.hd_intake_forms.is_public == True)  # noqa: E712
        )
        .select()
        .first()
    )


@bp_public.route("/<slug>", methods=["GET"])
async def get_public_intake_form(slug):
    """
    Get a public intake form's schema for rendering a submission UI (no auth).

    Path parameters:
        slug: Form slug (globally unique)

    Returns:
        200: {name, description, fields, captcha_required, altcha_challenge?}
             (altcha_challenge is present only when captcha_required)
        404: Form not found, inactive, or not public
    """
    db = current_app.db

    form_row = await run_in_threadpool(lambda: _fetch_public_form(db, slug))

    if not form_row:
        return ApiResponse.not_found("Form")

    fields = json.loads(form_row.fields) if form_row.fields else []

    response = {
        "name": form_row.name,
        "description": form_row.description,
        "fields": fields,
        "captcha_required": form_row.captcha_required,
    }
    if form_row.captcha_required:
        response["altcha_challenge"] = create_challenge()

    return jsonify(response), 200


@bp_public.route("/<slug>/submit", methods=["POST"])
async def submit_public_intake_form(slug):
    """
    Submit a public intake form (no auth): creates a customer_contact + Issue.

    Request body:
        {
            "fields": {"email": "...", "subject": "...", ...},
            "altcha": {...}   # required iff the form has captcha_required=True
        }

    Returns:
        201: {"status": "created", "reference": "<issue village_id>"}
        400: CAPTCHA verification failed, field validation failed, or the
             form has no resolvable "email" field/value for the contact
        404: Form not found, inactive, or not public

    The response deliberately omits internal ids and the tenant — only the
    issue's public village_id reference is returned to an unauthenticated
    caller.
    """
    db = current_app.db
    data = await request.get_json() or {}

    form_row = await run_in_threadpool(lambda: _fetch_public_form(db, slug))

    if not form_row:
        return ApiResponse.not_found("Form")

    if form_row.captcha_required:
        if not verify_solution(data.get("altcha")):
            return ApiResponse.error("CAPTCHA verification failed", 400)

    fields_spec = json.loads(form_row.fields) if form_row.fields else []
    submitted = data.get("fields") or {}

    try:
        validated, errors = validate_submission(fields_spec, submitted)
    except ValueError as exc:
        return ApiResponse.error(f"Unsupported field in form: {exc}", 400)

    if errors:
        return ApiResponse.error("Validation failed", 400, messages=errors)

    email = (validated.get("email") or "").strip().lower()
    if not email:
        return ApiResponse.error("email is required to submit this form", 400)

    tenant_id = form_row.tenant_id
    redis_client = getattr(current_app, "redis_client", None)
    details = {key: value for key, value in validated.items() if key != "email"}

    def submit():
        contact_id = upsert_customer_contact(
            db, tenant_id, email, details, redis_client
        )
        return create_support_issue_from_form(
            db, form_row, validated, contact_id, redis_client
        )

    try:
        village_id = await run_in_threadpool(submit)
    except ValueError as exc:
        logger.error(f"Intake form submit failed for slug={slug}: {exc}")
        return ApiResponse.error("Unable to create support issue", 400)

    return jsonify({"status": "created", "reference": village_id}), 201
