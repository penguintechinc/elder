"""Helpdesk ticket form designer and public submission endpoints using penguin-dal."""

# flake8: noqa: E501

import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

import httpx
from quart import Blueprint, current_app, g, jsonify, request

from apps.api.auth.decorators import login_required
from apps.api.logging_config import log_error_and_respond
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.pydal_helpers import PaginationParams, commit_db

logger = logging.getLogger(__name__)

bp = Blueprint("helpdesk_ticket_forms", __name__)


def _get_tenant_id() -> int:
    """Extract tenant_id from g.claims (populated by before_request)."""
    claims = getattr(g, "claims", {}) or {}
    tenant_str = claims.get("tenant", "")
    if not tenant_str:
        return None
    try:
        return int(tenant_str)
    except (ValueError, TypeError):
        return None


async def _verify_captcha(
    provider: str, secret_key: str, token: str, remote_ip: str | None = None
) -> dict:
    """Verify CAPTCHA token with provider (Turnstile or reCAPTCHA).

    Args:
        provider: 'turnstile' or 'recaptcha'
        secret_key: Server-side secret key
        token: Client-side CAPTCHA response token
        remote_ip: Optional client IP for additional verification

    Returns:
        dict: {success: bool, error_codes: list[str]}
    """
    if provider == "turnstile":
        url = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
    elif provider == "recaptcha":
        url = "https://www.google.com/recaptcha/api/siteverify"
    else:
        return {"success": False, "error_codes": ["unknown-provider"]}

    payload = {"secret": secret_key, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, data=payload)
            data = resp.json()
            return {
                "success": data.get("success", False),
                "error_codes": data.get("error-codes", []),
            }
    except Exception as e:
        logger.error(f"CAPTCHA verification error: {str(e)}")
        return {"success": False, "error_codes": [f"verification-error: {str(e)}"]}


@bp.route("", methods=["GET"])
@login_required
async def list_forms():
    """
    List ticket forms for tenant with pagination.

    Query Parameters:
        - page: Page number (default: 1)
        - per_page: Items per page (default: 20, max: 100)
        - is_active: Filter by active status (true/false)

    Returns:
        200: Paginated list of forms (no CAPTCHA secrets included)
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    # Extract pagination params
    pagination = PaginationParams.from_request()

    # Build query
    query = db.hd_ticket_forms.tenant_id == tenant_id

    # Apply filters
    if request.args.get("is_active"):
        is_active_str = request.args.get("is_active").lower()
        is_active = is_active_str in ("true", "1", "yes")
        query &= db.hd_ticket_forms.is_active == is_active

    def get_forms():
        total = db(query).count()
        rows = db(query).select(
            orderby=~db.hd_ticket_forms.created_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )
        return total, rows

    total, rows = await run_in_threadpool(get_forms)

    forms = [
        {
            "id": r.id,
            "name": r.name,
            "slug": r.slug,
            "description": r.description,
            "is_default": r.is_default,
            "is_active": r.is_active,
            "captcha_provider": r.captcha_provider,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in rows
    ]

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
async def create_form():
    """
    Create a new ticket form.

    Request body:
        {
            "name": "Contact Support",
            "slug": "contact-support",
            "description": "General support inquiry form",
            "is_default": false,
            "captcha_provider": "turnstile",
            "captcha_site_key": "site_key_...",
            "captcha_secret_ref": "penguin-sal-ref",
            "fields": [
                {
                    "id": "subject",
                    "label": "Subject",
                    "type": "text",
                    "required": true
                }
            ]
        }

    Returns:
        201: Created form
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    data = await request.get_json() or {}

    # Validate required fields
    name = data.get("name", "").strip()
    slug = data.get("slug", "").strip().lower()

    if not name or not slug:
        return ApiResponse.validation_error("name and slug", "are required")

    def create():
        now = datetime.now(timezone.utc)

        # Slug must be GLOBALLY unique (public URL /public/<slug> has no tenant
        # component). Reject collisions across ALL tenants before insert.
        existing = db(db.hd_ticket_forms.slug == slug).select().first()

        if existing:
            return None, "duplicate_slug"

        # Build insert data
        insert_data = {
            "tenant_id": tenant_id,
            "name": name,
            "slug": slug,
            "description": data.get("description", ""),
            "is_default": data.get("is_default", False),
            "is_active": data.get("is_active", True),
            "captcha_provider": data.get("captcha_provider", "none"),
            "captcha_site_key": data.get("captcha_site_key"),
            "captcha_secret_ref": data.get("captcha_secret_ref"),
            "fields": json.dumps(data.get("fields", [])),
            "created_at": now,
            "updated_at": now,
        }

        # Insert form
        form_id = db.hd_ticket_forms.insert(**insert_data)
        db.commit()

        # Fetch the form to return
        return db(db.hd_ticket_forms.id == form_id).select().first(), None

    form_row, error = await run_in_threadpool(create)

    if error == "duplicate_slug":
        return ApiResponse.error("Form slug must be globally unique", 409)

    if not form_row:
        return ApiResponse.error("Failed to create form", 400)

    fields = json.loads(form_row.fields) if form_row.fields else []

    return (
        jsonify(
            {
                "id": form_row.id,
                "name": form_row.name,
                "slug": form_row.slug,
                "description": form_row.description,
                "is_default": form_row.is_default,
                "is_active": form_row.is_active,
                "captcha_provider": form_row.captcha_provider,
                "captcha_site_key": form_row.captcha_site_key,
                "fields": fields,
                "created_at": (
                    form_row.created_at.isoformat() if form_row.created_at else None
                ),
            }
        ),
        201,
    )


@bp.route("/<int:form_id>", methods=["GET"])
@login_required
async def get_form(form_id):
    """
    Get a single ticket form by ID.

    Path parameters:
        form_id: Form ID

    Returns:
        200: Form details (no CAPTCHA secrets)
        404: Form not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    def fetch():
        return (
            db(
                (db.hd_ticket_forms.id == form_id)
                & (db.hd_ticket_forms.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

    form_row = await run_in_threadpool(fetch)

    if not form_row:
        return ApiResponse.not_found("Form")

    fields = json.loads(form_row.fields) if form_row.fields else []

    return jsonify(
        {
            "id": form_row.id,
            "name": form_row.name,
            "slug": form_row.slug,
            "description": form_row.description,
            "is_default": form_row.is_default,
            "is_active": form_row.is_active,
            "captcha_provider": form_row.captcha_provider,
            "captcha_site_key": form_row.captcha_site_key,
            "fields": fields,
            "created_at": (
                form_row.created_at.isoformat() if form_row.created_at else None
            ),
            "updated_at": (
                form_row.updated_at.isoformat() if form_row.updated_at else None
            ),
        }
    )


@bp.route("/<int:form_id>", methods=["PATCH"])
@login_required
async def update_form(form_id):
    """
    Update a ticket form.

    Path parameters:
        form_id: Form ID

    Request body:
        {
            "name": "Updated Name",
            "description": "Updated description",
            "is_default": true,
            "is_active": false,
            "captcha_provider": "recaptcha",
            "captcha_site_key": "new_key",
            "captcha_secret_ref": "new_ref",
            "fields": [...]
        }

    Returns:
        200: Updated form
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
                (db.hd_ticket_forms.id == form_id)
                & (db.hd_ticket_forms.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not form_row:
            return None

        now = datetime.now(timezone.utc)
        updates = {"updated_at": now}

        # Only update provided fields
        if "name" in data:
            updates["name"] = data["name"].strip()
        if "description" in data:
            updates["description"] = data["description"]
        if "is_default" in data:
            updates["is_default"] = data["is_default"]
        if "is_active" in data:
            updates["is_active"] = data["is_active"]
        if "captcha_provider" in data:
            updates["captcha_provider"] = data["captcha_provider"]
        if "captcha_site_key" in data:
            updates["captcha_site_key"] = data["captcha_site_key"]
        if "captcha_secret_ref" in data:
            updates["captcha_secret_ref"] = data["captcha_secret_ref"]
        if "fields" in data:
            updates["fields"] = json.dumps(data["fields"])

        db(db.hd_ticket_forms.id == form_id).update(**updates)
        db.commit()

        return db(db.hd_ticket_forms.id == form_id).select().first()

    form_row = await run_in_threadpool(update)

    if not form_row:
        return ApiResponse.not_found("Form")

    fields = json.loads(form_row.fields) if form_row.fields else []

    return jsonify(
        {
            "id": form_row.id,
            "name": form_row.name,
            "slug": form_row.slug,
            "description": form_row.description,
            "is_default": form_row.is_default,
            "is_active": form_row.is_active,
            "captcha_provider": form_row.captcha_provider,
            "captcha_site_key": form_row.captcha_site_key,
            "fields": fields,
            "created_at": (
                form_row.created_at.isoformat() if form_row.created_at else None
            ),
            "updated_at": (
                form_row.updated_at.isoformat() if form_row.updated_at else None
            ),
        }
    )


@bp.route("/<int:form_id>", methods=["DELETE"])
@login_required
async def delete_form(form_id):
    """
    Delete a ticket form.

    Path parameters:
        form_id: Form ID

    Returns:
        204: Form deleted
        404: Form not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    def delete():
        form_row = (
            db(
                (db.hd_ticket_forms.id == form_id)
                & (db.hd_ticket_forms.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not form_row:
            return False

        db(db.hd_ticket_forms.id == form_id).delete()
        db.commit()
        return True

    deleted = await run_in_threadpool(delete)

    if not deleted:
        return ApiResponse.not_found("Form")

    return "", 204


@bp.route("/public/<slug>", methods=["GET"])
async def get_public_form(slug):
    """
    Get ticket form schema for public form submission (no auth required).

    This endpoint returns form structure for customer submission without exposing
    CAPTCHA secrets.

    Path parameters:
        slug: Form slug (tenant-scoped, unique)

    Returns:
        200: Form schema with captcha config (site key only, no secret)
        404: Form not found
    """
    db = current_app.db

    def fetch():
        # Query by slug only—public forms are discoverable
        return (
            db(
                (db.hd_ticket_forms.slug == slug)
                & (db.hd_ticket_forms.is_active == True)
            )
            .select()
            .first()
        )

    form_row = await run_in_threadpool(fetch)

    if not form_row:
        return ApiResponse.not_found("Form")

    fields = json.loads(form_row.fields) if form_row.fields else []

    # Don't expose CAPTCHA secret reference
    return jsonify(
        {
            "id": form_row.id,
            "name": form_row.name,
            "slug": form_row.slug,
            "description": form_row.description,
            "fields": fields,
            "captcha_provider": form_row.captcha_provider,
            "captcha_site_key": form_row.captcha_site_key,
        }
    )


@bp.route("/public/<slug>/submit", methods=["POST"])
async def submit_public_form(slug):
    """
    Submit ticket via form (public endpoint, no auth required).

    Request body:
        {
            "captcha_token": "...",
            "fields": {
                "subject": "Issue title",
                "description": "Issue details",
                "email": "user@example.com",
                ...
            }
        }

    Returns:
        201: Created ticket with village_id
        400: Validation or CAPTCHA error
        404: Form not found
    """
    db = current_app.db
    data = await request.get_json() or {}

    def fetch_form():
        return (
            db(
                (db.hd_ticket_forms.slug == slug)
                & (db.hd_ticket_forms.is_active == True)
            )
            .select()
            .first()
        )

    form_row = await run_in_threadpool(fetch_form)

    if not form_row:
        return ApiResponse.error("Form not found", 404)

    tenant_id = form_row.tenant_id

    # Validate CAPTCHA if configured
    if form_row.captcha_provider != "none":
        captcha_token = data.get("captcha_token")
        if not captcha_token:
            return ApiResponse.error("CAPTCHA token required", 400)

        # Get secret from penguin-sal or config
        # For now, store secret_ref as the key and use it to look up
        captcha_secret_ref = form_row.captcha_secret_ref
        if not captcha_secret_ref:
            logger.error(
                f"Form {form_row.id} has CAPTCHA enabled but no secret_ref configured"
            )
            return ApiResponse.error("CAPTCHA not properly configured", 500)

        # TODO: Retrieve secret from penguin-sal using secret_ref
        # For testing, we'll use the ref directly (in prod, would be actual secret)
        captcha_secret = captcha_secret_ref

        # Verify CAPTCHA
        captcha_result = await _verify_captcha(
            form_row.captcha_provider,
            captcha_secret,
            captcha_token,
            request.remote_addr,
        )

        if not captcha_result["success"]:
            return ApiResponse.error(
                f"CAPTCHA verification failed: {captcha_result.get('error_codes', [])}",
                400,
            )

    # Validate required fields from form definition
    submitted_fields = data.get("fields", {})
    fields_config = json.loads(form_row.fields) if form_row.fields else []

    for field in fields_config:
        if field.get("required") and not submitted_fields.get(field.get("id")):
            return ApiResponse.error(
                f"Required field missing: {field.get('label')}", 400
            )

    db = current_app.db
    redis_client = getattr(current_app, "redis_client", None)

    def create_ticket():
        from shared.utils.village_id import generate_village_id

        now = datetime.now(timezone.utc)

        # Generate village_id
        if redis_client:
            village_id = generate_village_id(tenant_id, redis_client)
        else:
            # Fallback: use simple incremental ID for testing
            village_id = f"test-{uuid4().hex[:8]}"

        # Create ticket from form submission
        # Use submitted email as display name if available
        subject = submitted_fields.get("subject", "Form Submission")
        body = submitted_fields.get("description", "")

        # An anonymous public submission has no internal identity. Resolve (or
        # create) a tenant-scoped CRM contact from the submitted email and make
        # the ticket's requester that contact — never a hardcoded admin identity.
        requester_contact_id = None
        requester_email = (submitted_fields.get("email") or "").strip().lower()
        if requester_email:
            existing = (
                db(
                    (db.hd_contacts.tenant_id == tenant_id)
                    & (db.hd_contacts.email == requester_email)
                )
                .select()
                .first()
            )
            if existing:
                requester_contact_id = existing.id
            else:
                if redis_client:
                    contact_vid = generate_village_id(tenant_id, redis_client)
                else:
                    contact_vid = f"test-c-{uuid4().hex[:8]}"
                requester_contact_id = db.hd_contacts.insert(
                    tenant_id=tenant_id,
                    village_id=contact_vid,
                    email=requester_email,
                    first_name=submitted_fields.get("first_name"),
                    last_name=submitted_fields.get("last_name"),
                    created_at=now,
                    updated_at=now,
                )
                db.commit()

        # Insert ticket — requester_identity_id stays NULL for guest submissions.
        ticket_id = db.hd_tickets.insert(
            tenant_id=tenant_id,
            village_id=village_id,
            subject=subject,
            status="new",
            priority=submitted_fields.get("priority", "medium"),
            channel="web",
            requester_identity_id=None,
            requester_contact_id=requester_contact_id,
            category=submitted_fields.get("category"),
            tags=json.dumps(submitted_fields.get("tags", [])),
            created_at=now,
            updated_at=now,
        )
        db.commit()

        # Fetch the ticket to apply SLA policy
        ticket_row = db(db.hd_tickets.id == ticket_id).select().first()

        # Apply SLA policy if exists
        from apps.api.modules.helpdesk.services.sla import apply_sla_policy

        apply_sla_policy(db, tenant_id, ticket_row)
        db.commit()

        # Return created ticket
        return db(db.hd_tickets.id == ticket_id).select().first()

    ticket_row = await run_in_threadpool(create_ticket)

    return (
        jsonify(
            {
                "id": ticket_row.id,
                "village_id": ticket_row.village_id,
                "subject": ticket_row.subject,
                "status": ticket_row.status,
                "created_at": (
                    ticket_row.created_at.isoformat() if ticket_row.created_at else None
                ),
            }
        ),
        201,
    )
