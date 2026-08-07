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
from typing import Any, Optional
from uuid import uuid4

from quart import Blueprint, current_app, g, jsonify, request
from sqlalchemy.exc import IntegrityError

from apps.api.auth.decorators import login_required, require_scope
from apps.api.modules.helpdesk.common import identity_in_tenant
from apps.api.modules.helpdesk.services.altcha import (
    create_challenge,
    extract_challenge,
    verify_solution,
)
from apps.api.modules.helpdesk.services.form_validation import validate_submission
from apps.api.modules.helpdesk.services.intake_submit import (
    ContactResolutionError,
    create_support_issue_from_form,
    upsert_customer_contact,
)
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.pydal_helpers import PaginationParams

logger = logging.getLogger(__name__)

bp = Blueprint("helpdesk_intake_forms", __name__)
bp_public = Blueprint("helpdesk_intake_public", __name__)

#: Only these polymorphic assignee target types are resolvable/valid — any
#: other value (or a `default_assignee_id` submitted without one of these)
#: is rejected rather than persisted unvalidated.
_VALID_ASSIGNEE_TYPES = ("identity", "org_unit")

#: Mirrors `IssueType` (apps/api/modules/issues/models/issue.py) — the
#: strict Enum column `issues.issue_type` is inserted into by
#: `create_support_issue_from_form`. `hd_intake_forms.issue_type` itself is
#: an unconstrained String(30), so without this allow-list an admin could
#: save a form with an out-of-enum value (e.g. "ticket") that then 500s
#: every public submit at the DB layer (security review regression fix).
_VALID_ISSUE_TYPES = (
    "operations",
    "code",
    "config",
    "security",
    "architecture",
    "process",
    "approval",
    "feature",
    "bug",
    "support",
    "other",
)

#: Postgres's auto-generated name for the inline `unique=True` constraint on
#: `hd_intake_forms.village_id` (see `VillageIDMixin`,
#: apps/api/models/base.py). Used to scope the mint-collision retry in
#: `_insert_form_with_unique_village_id` to village_id ONLY — any other
#: IntegrityError (notably `uq_intake_form_slug`, the explicit constraint
#: name for the slug column) must propagate unretried.
_VILLAGE_ID_UNIQUE_CONSTRAINT = "hd_intake_forms_village_id_key"

#: Cap on re-mint attempts in `_insert_form_with_unique_village_id` before
#: giving up and re-raising the last collision. Each attempt re-`INCR`s the
#: per-tenant Redis counter, so a bounded number of attempts is always
#: sufficient to walk past a counter that's merely behind the max persisted
#: village_id — an unbounded retry would only be needed for a pathological
#: Redis state that keeps resetting concurrently, which is not a case this
#: guards against.
_MAX_VILLAGE_ID_MINT_ATTEMPTS = 5


def _is_village_id_conflict(exc: IntegrityError) -> bool:
    """Return True only if `exc` is the village_id unique-constraint violation.

    Inspects the DB driver's reported constraint name (psycopg2 exposes it
    at `exc.orig.diag.constraint_name`) so this never matches a different
    constraint on the same table — notably `uq_intake_form_slug` — which
    must propagate unretried to preserve the existing duplicate-slug 409
    behavior. Falls back to a substring check on the exception text for
    drivers/backends that don't expose `.diag`.
    """
    orig = getattr(exc, "orig", None)
    constraint_name = getattr(getattr(orig, "diag", None), "constraint_name", None)
    if constraint_name is not None:
        return constraint_name == _VILLAGE_ID_UNIQUE_CONSTRAINT
    return _VILLAGE_ID_UNIQUE_CONSTRAINT in str(exc)


def _insert_form_with_unique_village_id(
    db: Any, tenant_id: int, redis_client: Any, insert_data: dict[str, Any]
) -> int:
    """Insert `insert_data` into hd_intake_forms with a collision-safe village_id.

    `generate_village_id` mints via a per-tenant Redis INCR counter
    (`elder:vid:{tenant:08x}`). If that counter is ever behind the max
    village_id already persisted for the tenant — a Redis restart/eviction
    in production, or a flushdb in the shared-DB test suite — INCR can
    return a sequence value an existing row already holds, and the insert
    raises a unique-constraint IntegrityError on `village_id`. This mints a
    fresh village_id and retries (INCR walks forward past the collision) up
    to `_MAX_VILLAGE_ID_MINT_ATTEMPTS` times before re-raising the last
    error. Each `TableProxy.insert()` call runs in its own short-lived
    SQLAlchemy session (opened and closed within the call), so a failed
    attempt needs no explicit rollback before the next attempt reuses `db`.

    Any IntegrityError NOT on the village_id constraint (e.g. a concurrent
    slug collision) propagates immediately, unretried.

    Returns:
        The inserted row's primary key.
    """
    from shared.utils.village_id import generate_village_id

    last_error: Optional[IntegrityError] = None
    for _ in range(_MAX_VILLAGE_ID_MINT_ATTEMPTS):
        if redis_client:
            village_id = generate_village_id(tenant_id, redis_client)
        else:
            # Fallback for test environments without a live Redis connection.
            village_id = f"test-{uuid4().hex[:8]}"

        try:
            return db.hd_intake_forms.insert(village_id=village_id, **insert_data)
        except IntegrityError as exc:
            if not _is_village_id_conflict(exc):
                raise
            last_error = exc

    assert last_error is not None  # loop always executes >= 1 iteration
    raise last_error


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


def _validate_organization_ref(db, tenant_id: int, organization_id: Any) -> bool:
    """Return True if `organization_id` is None or belongs to `tenant_id`.

    Guards `create_form`/`update_form` against a cross-tenant IDOR: without
    this, an admin in tenant A could point a form's `organization_id` (or a
    `default_assignee_id` of type `org_unit`) at an organization owned by a
    different tenant.
    """
    if organization_id is None:
        return True
    return (
        db(
            (db.organizations.id == organization_id)
            & (db.organizations.tenant_id == tenant_id)
        )
        .select()
        .first()
        is not None
    )


def _validate_assignee_ref(
    db, tenant_id: int, assignee_type: Any, assignee_id: Any
) -> tuple[bool, Optional[str]]:
    """Validate a (`default_assignee_type`, `default_assignee_id`) pair.

    Returns `(True, None)` if the pair is valid — including both unset, or
    a type set with no id. Returns `(False, "invalid_assignee_type")` if a
    type is present but not one of `_VALID_ASSIGNEE_TYPES`, or an id is
    present without a recognized type to resolve it against. Returns
    `(False, "invalid_assignee")` if a recognized type + id pair doesn't
    resolve to an identity/organization within `tenant_id` (cross-tenant
    IDOR guard, mirroring `issues/routes/issues.py::create_issue`).
    """
    if assignee_type is not None and assignee_type not in _VALID_ASSIGNEE_TYPES:
        return False, "invalid_assignee_type"

    if assignee_id is None:
        return True, None

    if assignee_type == "identity":
        if not identity_in_tenant(db, assignee_id, tenant_id):
            return False, "invalid_assignee"
        return True, None

    if assignee_type == "org_unit":
        if not _validate_organization_ref(db, tenant_id, assignee_id):
            return False, "invalid_assignee"
        return True, None

    # assignee_id given but no recognized type to validate it against.
    return False, "invalid_assignee_type"


_ERROR_RESPONSES = {
    "invalid_organization": lambda: ApiResponse.error(
        "organization_id not found in tenant", 400
    ),
    "invalid_assignee_type": lambda: ApiResponse.error(
        "default_assignee_type must be 'identity' or 'org_unit'", 400
    ),
    "invalid_assignee": lambda: ApiResponse.error(
        "default_assignee_id not found in tenant", 400
    ),
    "invalid_issue_type": lambda: ApiResponse.error("invalid issue_type", 400),
}


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
        now = datetime.now(timezone.utc)

        # Slug must be GLOBALLY unique (public URL /api/v1/intake/<slug> has
        # no tenant component). Reject collisions across ALL tenants.
        existing = db(db.hd_intake_forms.slug == slug).select().first()
        if existing:
            return None, "duplicate_slug"

        organization_id = data.get("organization_id")
        if not _validate_organization_ref(db, tenant_id, organization_id):
            return None, "invalid_organization"

        assignee_type = data.get("default_assignee_type")
        assignee_id = data.get("default_assignee_id")
        assignee_ok, assignee_error = _validate_assignee_ref(
            db, tenant_id, assignee_type, assignee_id
        )
        if not assignee_ok:
            return None, assignee_error

        issue_type = data.get("issue_type") or "support"
        if not isinstance(issue_type, str) or issue_type.lower() not in _VALID_ISSUE_TYPES:
            return None, "invalid_issue_type"

        metadata = data.get("metadata")

        # penguin-dal insert() does not apply SQLAlchemy Column(default=...),
        # so every NOT-NULL column is passed explicitly. village_id is minted
        # (and re-minted on collision) by _insert_form_with_unique_village_id.
        insert_data = {
            "tenant_id": tenant_id,
            "name": name,
            "slug": slug,
            "description": data.get("description"),
            "fields": json.dumps(fields),
            "issue_type": issue_type,
            "default_assignee_type": assignee_type,
            "default_assignee_id": assignee_id,
            "organization_id": organization_id,
            "is_public": data.get("is_public", False),
            "captcha_required": data.get("captcha_required", False),
            "is_active": data.get("is_active", True),
            "metadata": json.dumps(metadata) if metadata is not None else None,
            "created_at": now,
            "updated_at": now,
        }

        form_id = _insert_form_with_unique_village_id(
            db, tenant_id, redis_client, insert_data
        )
        db.commit()

        return db(db.hd_intake_forms.id == form_id).select().first(), None

    form_row, error = await run_in_threadpool(create)

    if error == "duplicate_slug":
        return ApiResponse.conflict("Form slug must be globally unique")

    if error in _ERROR_RESPONSES:
        return _ERROR_RESPONSES[error]()

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
            return None, "not_found"

        # Cross-tenant IDOR guards: validate against the caller's tenant_id,
        # resolving against the row's existing value whenever a field isn't
        # part of this partial update (e.g. changing default_assignee_id
        # without resending default_assignee_type still gets validated
        # against the type already on the row).
        if "organization_id" in data:
            if not _validate_organization_ref(db, tenant_id, data["organization_id"]):
                return None, "invalid_organization"

        if "default_assignee_type" in data or "default_assignee_id" in data:
            assignee_type = data.get(
                "default_assignee_type", form_row.default_assignee_type
            )
            assignee_id = data.get("default_assignee_id", form_row.default_assignee_id)
            assignee_ok, assignee_error = _validate_assignee_ref(
                db, tenant_id, assignee_type, assignee_id
            )
            if not assignee_ok:
                return None, assignee_error

        if "issue_type" in data:
            issue_type = data["issue_type"] or "support"
            if (
                not isinstance(issue_type, str)
                or issue_type.lower() not in _VALID_ISSUE_TYPES
            ):
                return None, "invalid_issue_type"

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

        return db(db.hd_intake_forms.id == form_id).select().first(), None

    form_row, error = await run_in_threadpool(update)

    if error == "not_found":
        return ApiResponse.not_found("Form")

    if error in _ERROR_RESPONSES:
        return _ERROR_RESPONSES[error]()

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
        400: CAPTCHA verification failed, CAPTCHA already used (replay),
             field validation failed, or the form has no resolvable
             "email" field/value for the contact
        404: Form not found, inactive, or not public

    The response deliberately omits internal ids and the tenant — only the
    issue's public village_id reference is returned to an unauthenticated
    caller.
    """
    db = current_app.db
    data = await request.get_json() or {}

    # A body like `42`, `[1, 2]`, or `"x"` is valid JSON but not an object —
    # request.get_json() happily returns it, and the .get() calls below
    # would raise AttributeError (-> unhandled 500) on anything but a dict.
    if not isinstance(data, dict):
        return ApiResponse.error("Invalid request body", 400)

    form_row = await run_in_threadpool(lambda: _fetch_public_form(db, slug))

    if not form_row:
        return ApiResponse.not_found("Form")

    redis_client = getattr(current_app, "redis_client", None)

    if form_row.captcha_required:
        altcha_payload = data.get("altcha")
        if not verify_solution(altcha_payload):
            return ApiResponse.error("CAPTCHA verification failed", 400)

        # Single-use enforcement: verify_solution alone accepts a solution
        # for CHALLENGE_TTL_SECONDS after issuance, so without this a
        # captured, already-solved challenge could be replayed against this
        # endpoint repeatedly within that window. `SET NX` atomically claims
        # the challenge id the first time it's redeemed; a falsy result
        # means another request already consumed it. Best-effort — skipped
        # (never a crash) when Redis is unavailable, since expiry alone
        # still bounds the exposure in that case.
        if redis_client is not None:
            challenge = extract_challenge(altcha_payload)
            if challenge:
                consumed_now = await run_in_threadpool(
                    lambda: redis_client.set(
                        f"altcha:used:{challenge}", "1", nx=True, ex=300
                    )
                )
                if not consumed_now:
                    return ApiResponse.error("captcha already used", 400)

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
    except ContactResolutionError as exc:
        logger.error(f"Intake form contact resolution failed for slug={slug}: {exc}")
        return ApiResponse.conflict("Unable to resolve contact; please retry")
    except ValueError as exc:
        logger.error(f"Intake form submit failed for slug={slug}: {exc}")
        return ApiResponse.error("Unable to create support issue", 400)

    return jsonify({"status": "created", "reference": village_id}), 201
