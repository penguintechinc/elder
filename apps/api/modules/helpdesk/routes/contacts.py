"""Helpdesk CRM contacts endpoints using penguin-dal."""

# flake8: noqa: E501

import logging
from datetime import datetime, timezone

from quart import Blueprint, current_app, g, jsonify, request

from apps.api.auth.decorators import login_required
from apps.api.modules.helpdesk.common import identity_in_tenant
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.pydal_helpers import PaginationParams

logger = logging.getLogger(__name__)

bp = Blueprint("helpdesk_contacts", __name__)


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


@bp.route("", methods=["GET"])
@login_required
async def list_contacts():
    """
    List contacts with optional filtering and pagination.

    Query Parameters:
        - page: Page number (default: 1)
        - per_page: Items per page (default: 20, max: 100)
        - name: Filter by first/last name (substring match)
        - email: Filter by email (substring match)
        - company_id: Filter by company ID (exact match)

    Returns:
        200: Paginated list of contacts
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    # Extract pagination params
    pagination = PaginationParams.from_request()

    # Build query
    query = db.hd_contacts.tenant_id == tenant_id

    # Apply filters
    if request.args.get("name"):
        name_filter = request.args.get("name").strip()
        # Filter by first_name OR last_name
        query &= (db.hd_contacts.first_name.ilike(f"%{name_filter}%")) | (
            db.hd_contacts.last_name.ilike(f"%{name_filter}%")
        )

    if request.args.get("email"):
        email_filter = request.args.get("email").strip()
        query &= db.hd_contacts.email.ilike(f"%{email_filter}%")

    if request.args.get("company_id"):
        company_id = request.args.get("company_id", type=int)
        query &= db.hd_contacts.hd_company_id == company_id

    def get_contacts():
        total = db(query).count()
        rows = db(query).select(
            orderby=~db.hd_contacts.created_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )
        return total, rows

    total, rows = await run_in_threadpool(get_contacts)

    contacts = [
        {
            "id": r.id,
            "village_id": r.village_id,
            "first_name": r.first_name,
            "last_name": r.last_name,
            "email": r.email,
            "phone": r.phone,
            "job_title": r.job_title,
            "company_id": r.hd_company_id,
            "identity_id": r.identity_id,
            "notes": r.notes,
            "created_at": r.created_at.isoformat(),
            "updated_at": r.updated_at.isoformat(),
        }
        for r in rows
    ]

    return (
        jsonify(
            {
                "items": contacts,
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
async def create_contact():
    """
    Create a new contact.

    Request body:
        {
            "email": "string (required)",
            "first_name": "string (optional)",
            "last_name": "string (optional)",
            "phone": "string (optional)",
            "job_title": "string (optional)",
            "company_id": "int (optional)",
            "identity_id": "int (optional)",
            "notes": "string (optional)"
        }

    Returns:
        201: Created contact
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    data = await request.get_json() or {}

    # Validate required fields
    email = data.get("email", "").strip()
    if not email:
        return ApiResponse.validation_error("email", "is required")

    first_name = data.get("first_name", "").strip() or None
    last_name = data.get("last_name", "").strip() or None
    phone = data.get("phone", "").strip() or None
    job_title = data.get("job_title", "").strip() or None
    company_id = data.get("company_id")
    identity_id = data.get("identity_id")
    notes = data.get("notes", "").strip() or None

    # Capture redis_client outside threadpool context
    redis_client = current_app.redis_client

    def create():
        from shared.utils.village_id import generate_village_id

        # Cross-tenant IDOR guard: linked identity/company must be in this tenant
        if not identity_in_tenant(db, identity_id, tenant_id):
            return "identity_not_in_tenant"
        if (
            company_id is not None
            and not db(
                (db.hd_companies.id == company_id)
                & (db.hd_companies.tenant_id == tenant_id)
            )
            .select()
            .first()
        ):
            return "company_not_in_tenant"

        now = datetime.now(timezone.utc)

        # Generate village_id
        village_id = generate_village_id(tenant_id, redis_client)

        # Insert contact
        contact_id = db.hd_contacts.insert(
            tenant_id=tenant_id,
            village_id=village_id,
            email=email,
            first_name=first_name,
            last_name=last_name,
            phone=phone,
            job_title=job_title,
            hd_company_id=company_id,
            identity_id=identity_id,
            notes=notes,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        # Return created contact
        return db(db.hd_contacts.id == contact_id).select().first()

    contact_row = await run_in_threadpool(create)

    if contact_row == "identity_not_in_tenant":
        return ApiResponse.error("identity_id not found in tenant", 400)
    if contact_row == "company_not_in_tenant":
        return ApiResponse.error("company_id not found in tenant", 400)

    return (
        jsonify(
            {
                "id": contact_row.id,
                "village_id": contact_row.village_id,
                "email": contact_row.email,
                "first_name": contact_row.first_name,
                "last_name": contact_row.last_name,
                "phone": contact_row.phone,
                "job_title": contact_row.job_title,
                "company_id": contact_row.hd_company_id,
                "identity_id": contact_row.identity_id,
                "notes": contact_row.notes,
                "created_at": contact_row.created_at.isoformat(),
                "updated_at": contact_row.updated_at.isoformat(),
            }
        ),
        201,
    )


@bp.route("/<int:contact_id>", methods=["GET"])
@login_required
async def get_contact(contact_id):
    """
    Get a single contact by ID.

    Path parameters:
        contact_id: Contact ID

    Returns:
        200: Contact details
        404: Contact not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    def fetch():
        return (
            db(
                (db.hd_contacts.id == contact_id)
                & (db.hd_contacts.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

    contact_row = await run_in_threadpool(fetch)

    if not contact_row:
        return ApiResponse.not_found("Contact")

    return jsonify(
        {
            "id": contact_row.id,
            "village_id": contact_row.village_id,
            "email": contact_row.email,
            "first_name": contact_row.first_name,
            "last_name": contact_row.last_name,
            "phone": contact_row.phone,
            "job_title": contact_row.job_title,
            "company_id": contact_row.hd_company_id,
            "identity_id": contact_row.identity_id,
            "notes": contact_row.notes,
            "created_at": contact_row.created_at.isoformat(),
            "updated_at": contact_row.updated_at.isoformat(),
        }
    )


@bp.route("/<int:contact_id>", methods=["PATCH"])
@login_required
async def update_contact(contact_id):
    """
    Update a contact.

    Path parameters:
        contact_id: Contact ID

    Request body:
        {
            "email": "string (optional)",
            "first_name": "string (optional)",
            "last_name": "string (optional)",
            "phone": "string (optional)",
            "job_title": "string (optional)",
            "company_id": "int (optional)",
            "identity_id": "int (optional)",
            "notes": "string (optional)"
        }

    Returns:
        200: Updated contact
        404: Contact not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    data = await request.get_json() or {}

    def update():
        contact_row = (
            db(
                (db.hd_contacts.id == contact_id)
                & (db.hd_contacts.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not contact_row:
            return None

        # Cross-tenant IDOR guard: linked identity/company must be in this tenant
        if "identity_id" in data and not identity_in_tenant(
            db, data["identity_id"], tenant_id
        ):
            return "identity_not_in_tenant"
        if (
            "company_id" in data
            and data["company_id"] is not None
            and not db(
                (db.hd_companies.id == data["company_id"])
                & (db.hd_companies.tenant_id == tenant_id)
            )
            .select()
            .first()
        ):
            return "company_not_in_tenant"

        now = datetime.now(timezone.utc)
        updates = {"updated_at": now}

        # Only update provided fields
        if "email" in data:
            email = data["email"].strip()
            if email:
                updates["email"] = email
        if "first_name" in data:
            updates["first_name"] = data["first_name"].strip() or None
        if "last_name" in data:
            updates["last_name"] = data["last_name"].strip() or None
        if "phone" in data:
            updates["phone"] = data["phone"].strip() or None
        if "job_title" in data:
            updates["job_title"] = data["job_title"].strip() or None
        if "company_id" in data:
            updates["hd_company_id"] = data["company_id"]
        if "identity_id" in data:
            updates["identity_id"] = data["identity_id"]
        if "notes" in data:
            updates["notes"] = data["notes"].strip() or None

        db(db.hd_contacts.id == contact_id).update(**updates)
        db.commit()

        return db(db.hd_contacts.id == contact_id).select().first()

    contact_row = await run_in_threadpool(update)

    if contact_row == "identity_not_in_tenant":
        return ApiResponse.error("identity_id not found in tenant", 400)
    if contact_row == "company_not_in_tenant":
        return ApiResponse.error("company_id not found in tenant", 400)

    if not contact_row:
        return ApiResponse.not_found("Contact")

    return jsonify(
        {
            "id": contact_row.id,
            "village_id": contact_row.village_id,
            "email": contact_row.email,
            "first_name": contact_row.first_name,
            "last_name": contact_row.last_name,
            "phone": contact_row.phone,
            "job_title": contact_row.job_title,
            "company_id": contact_row.hd_company_id,
            "identity_id": contact_row.identity_id,
            "notes": contact_row.notes,
            "created_at": contact_row.created_at.isoformat(),
            "updated_at": contact_row.updated_at.isoformat(),
        }
    )


@bp.route("/<int:contact_id>", methods=["DELETE"])
@login_required
async def delete_contact(contact_id):
    """
    Delete a contact.

    Path parameters:
        contact_id: Contact ID

    Returns:
        204: Contact deleted
        404: Contact not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    def delete():
        contact_row = (
            db(
                (db.hd_contacts.id == contact_id)
                & (db.hd_contacts.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not contact_row:
            return False

        db(db.hd_contacts.id == contact_id).delete()
        db.commit()
        return True

    deleted = await run_in_threadpool(delete)

    if not deleted:
        return ApiResponse.not_found("Contact")

    return ApiResponse.no_content()


@bp.route("/<int:contact_id>/timeline", methods=["GET"])
@login_required
async def get_contact_timeline(contact_id):
    """
    Get contact timeline (linked tickets).

    Path parameters:
        contact_id: Contact ID

    Returns:
        200: Contact timeline with linked tickets
        404: Contact not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    def fetch_timeline():
        contact_row = (
            db(
                (db.hd_contacts.id == contact_id)
                & (db.hd_contacts.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not contact_row:
            return None, []

        timeline = []

        # If contact has an identity_id, fetch tickets where requester matches
        if contact_row.identity_id:
            ticket_query = (
                db.hd_tickets.requester_identity_id == contact_row.identity_id
            ) & (db.hd_tickets.tenant_id == tenant_id)
            tickets = db(ticket_query).select(
                orderby=~db.hd_tickets.created_at,
                limitby=(0, 50),
            )

            for ticket in tickets:
                timeline.append(
                    {
                        "type": "ticket",
                        "id": ticket.id,
                        "village_id": ticket.village_id,
                        "subject": ticket.subject,
                        "status": ticket.status,
                        "priority": ticket.priority,
                        "created_at": ticket.created_at.isoformat(),
                    }
                )

        return contact_row, timeline

    contact_row, timeline = await run_in_threadpool(fetch_timeline)

    if not contact_row:
        return ApiResponse.not_found("Contact")

    return jsonify(
        {
            "contact_id": contact_id,
            "timeline": timeline,
            "count": len(timeline),
        }
    )
