"""Helpdesk ticket CRUD and workflow endpoints using penguin-dal."""

# flake8: noqa: E501

import logging
from datetime import datetime, timezone

from quart import Blueprint, current_app, g, jsonify, request

from apps.api.auth.decorators import login_required
from apps.api.logging_config import log_error_and_respond
from apps.api.modules.helpdesk.services.sla import apply_sla_policy
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.pydal_helpers import PaginationParams, commit_db

logger = logging.getLogger(__name__)

bp = Blueprint("helpdesk_tickets", __name__)


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
async def list_tickets():
    """
    List tickets with optional filtering and pagination.

    Query Parameters:
        - page: Page number (default: 1)
        - per_page: Items per page (default: 20, max: 100)
        - status: Filter by status (new, open, pending, on_hold, resolved, closed)
        - priority: Filter by priority (low, medium, high, urgent, critical)
        - assignee_id: Filter by assignee identity_id
        - requester_id: Filter by requester identity_id
        - hd_team_id: Filter by team ID

    Returns:
        200: Paginated list of tickets
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    # Extract pagination params
    pagination = PaginationParams.from_request()

    # Build query
    query = db.hd_tickets.tenant_id == tenant_id

    # Apply filters
    if request.args.get("status"):
        status_filter = request.args.get("status")
        query &= db.hd_tickets.status == status_filter

    if request.args.get("priority"):
        priority_filter = request.args.get("priority")
        query &= db.hd_tickets.priority == priority_filter

    if request.args.get("assignee_id"):
        assignee_id = request.args.get("assignee_id", type=int)
        query &= db.hd_tickets.assignee_identity_id == assignee_id

    if request.args.get("requester_id"):
        requester_id = request.args.get("requester_id", type=int)
        query &= db.hd_tickets.requester_identity_id == requester_id

    if request.args.get("hd_team_id"):
        team_id = request.args.get("hd_team_id", type=int)
        query &= db.hd_tickets.hd_team_id == team_id

    def get_tickets():
        total = db(query).count()
        rows = db(query).select(
            orderby=~db.hd_tickets.created_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )
        return total, rows

    total, rows = await run_in_threadpool(get_tickets)

    tickets = [
        {
            "id": r.id,
            "village_id": r.village_id,
            "subject": r.subject,
            "status": r.status,
            "priority": r.priority,
            "channel": r.channel,
            "requester_id": r.requester_identity_id,
            "assignee_id": r.assignee_identity_id,
            "team_id": r.hd_team_id,
            "category": r.category,
            "tags": r.tags,
            "sla_breach_at": r.sla_breach_at.isoformat() if r.sla_breach_at else None,
            "first_response_at": r.first_response_at.isoformat() if r.first_response_at else None,
            "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
            "closed_at": r.closed_at.isoformat() if r.closed_at else None,
            "created_at": r.created_at.isoformat(),
            "updated_at": r.updated_at.isoformat(),
        }
        for r in rows
    ]

    return (
        jsonify(
            {
                "items": tickets,
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
async def create_ticket():
    """
    Create a new ticket.

    Request body:
        {
            "subject": "string (required)",
            "priority": "low|medium|high|urgent|critical",
            "channel": "web|email|api",
            "requester_id": "int (required, identity_id)",
            "assignee_id": "int (optional)",
            "team_id": "int (optional)",
            "category": "string (optional)",
            "tags": ["array of tags (optional)"]
        }

    Returns:
        201: Created ticket
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    data = await request.get_json() or {}

    # Validate required fields
    subject = data.get("subject", "").strip()
    if not subject:
        return ApiResponse.validation_error("subject", "is required")

    requester_id = data.get("requester_id")
    if not requester_id:
        return ApiResponse.validation_error("requester_id", "is required")

    priority = data.get("priority", "medium")
    channel = data.get("channel", "web")
    assignee_id = data.get("assignee_id")
    team_id = data.get("team_id")
    category = data.get("category")
    tags = data.get("tags", [])

    def create():
        from shared.utils.village_id import generate_village_id

        now = datetime.now(timezone.utc)

        # Generate village_id
        redis_client = current_app.redis_client
        village_id = generate_village_id(tenant_id, redis_client)

        # Insert ticket
        ticket_id = db.hd_tickets.insert(
            tenant_id=tenant_id,
            village_id=village_id,
            subject=subject,
            status="new",
            priority=priority,
            channel=channel,
            requester_identity_id=requester_id,
            assignee_identity_id=assignee_id,
            hd_team_id=team_id,
            category=category,
            tags=tags,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        # Fetch the ticket to apply SLA policy
        ticket_row = db(db.hd_tickets.id == ticket_id).select().first()
        apply_sla_policy(db, tenant_id, ticket_row)
        db.commit()

        # Return created ticket
        return db(db.hd_tickets.id == ticket_id).select().first()

    ticket_row = await run_in_threadpool(create)

    return (
        jsonify(
            {
                "id": ticket_row.id,
                "village_id": ticket_row.village_id,
                "subject": ticket_row.subject,
                "status": ticket_row.status,
                "priority": ticket_row.priority,
                "channel": ticket_row.channel,
                "requester_id": ticket_row.requester_identity_id,
                "assignee_id": ticket_row.assignee_identity_id,
                "team_id": ticket_row.hd_team_id,
                "category": ticket_row.category,
                "tags": ticket_row.tags,
                "sla_breach_at": ticket_row.sla_breach_at.isoformat() if ticket_row.sla_breach_at else None,
                "first_response_at": ticket_row.first_response_at.isoformat() if ticket_row.first_response_at else None,
                "resolved_at": ticket_row.resolved_at.isoformat() if ticket_row.resolved_at else None,
                "closed_at": ticket_row.closed_at.isoformat() if ticket_row.closed_at else None,
                "created_at": ticket_row.created_at.isoformat(),
                "updated_at": ticket_row.updated_at.isoformat(),
            }
        ),
        201,
    )


@bp.route("/<int:ticket_id>", methods=["GET"])
@login_required
async def get_ticket(ticket_id):
    """
    Get a single ticket by ID.

    Path parameters:
        ticket_id: Ticket ID

    Returns:
        200: Ticket details
        404: Ticket not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    def fetch():
        return db((db.hd_tickets.id == ticket_id) & (db.hd_tickets.tenant_id == tenant_id)).select().first()

    ticket_row = await run_in_threadpool(fetch)

    if not ticket_row:
        return ApiResponse.not_found("Ticket")

    return jsonify(
        {
            "id": ticket_row.id,
            "village_id": ticket_row.village_id,
            "subject": ticket_row.subject,
            "status": ticket_row.status,
            "priority": ticket_row.priority,
            "channel": ticket_row.channel,
            "requester_id": ticket_row.requester_identity_id,
            "assignee_id": ticket_row.assignee_identity_id,
            "team_id": ticket_row.hd_team_id,
            "category": ticket_row.category,
            "tags": ticket_row.tags,
            "sla_breach_at": ticket_row.sla_breach_at.isoformat() if ticket_row.sla_breach_at else None,
            "first_response_at": ticket_row.first_response_at.isoformat() if ticket_row.first_response_at else None,
            "resolved_at": ticket_row.resolved_at.isoformat() if ticket_row.resolved_at else None,
            "closed_at": ticket_row.closed_at.isoformat() if ticket_row.closed_at else None,
            "created_at": ticket_row.created_at.isoformat(),
            "updated_at": ticket_row.updated_at.isoformat(),
        }
    )


@bp.route("/<int:ticket_id>", methods=["PATCH"])
@login_required
async def update_ticket(ticket_id):
    """
    Update a ticket.

    Path parameters:
        ticket_id: Ticket ID

    Request body:
        {
            "subject": "string (optional)",
            "priority": "low|medium|high|urgent|critical",
            "status": "new|open|pending|on_hold|resolved|closed",
            "assignee_id": "int (optional)",
            "category": "string (optional)",
            "tags": ["array (optional)"]
        }

    Returns:
        200: Updated ticket
        404: Ticket not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    data = await request.get_json() or {}

    def update():
        ticket_row = db((db.hd_tickets.id == ticket_id) & (db.hd_tickets.tenant_id == tenant_id)).select().first()

        if not ticket_row:
            return None

        now = datetime.now(timezone.utc)
        updates = {"updated_at": now}

        # Only update provided fields
        if "subject" in data:
            updates["subject"] = data["subject"]
        if "priority" in data:
            updates["priority"] = data["priority"]
        if "status" in data:
            updates["status"] = data["status"]
        if "assignee_id" in data:
            updates["assignee_identity_id"] = data["assignee_id"]
        if "category" in data:
            updates["category"] = data["category"]
        if "tags" in data:
            updates["tags"] = data["tags"]

        db(db.hd_tickets.id == ticket_id).update(**updates)
        db.commit()

        return db(db.hd_tickets.id == ticket_id).select().first()

    ticket_row = await run_in_threadpool(update)

    if not ticket_row:
        return ApiResponse.not_found("Ticket")

    return jsonify(
        {
            "id": ticket_row.id,
            "village_id": ticket_row.village_id,
            "subject": ticket_row.subject,
            "status": ticket_row.status,
            "priority": ticket_row.priority,
            "channel": ticket_row.channel,
            "requester_id": ticket_row.requester_identity_id,
            "assignee_id": ticket_row.assignee_identity_id,
            "team_id": ticket_row.hd_team_id,
            "category": ticket_row.category,
            "tags": ticket_row.tags,
            "sla_breach_at": ticket_row.sla_breach_at.isoformat() if ticket_row.sla_breach_at else None,
            "first_response_at": ticket_row.first_response_at.isoformat() if ticket_row.first_response_at else None,
            "resolved_at": ticket_row.resolved_at.isoformat() if ticket_row.resolved_at else None,
            "closed_at": ticket_row.closed_at.isoformat() if ticket_row.closed_at else None,
            "created_at": ticket_row.created_at.isoformat(),
            "updated_at": ticket_row.updated_at.isoformat(),
        }
    )


@bp.route("/<int:ticket_id>", methods=["DELETE"])
@login_required
async def delete_ticket(ticket_id):
    """
    Delete a ticket.

    Path parameters:
        ticket_id: Ticket ID

    Returns:
        204: Ticket deleted
        404: Ticket not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    def delete():
        ticket_row = db((db.hd_tickets.id == ticket_id) & (db.hd_tickets.tenant_id == tenant_id)).select().first()

        if not ticket_row:
            return False

        db(db.hd_tickets.id == ticket_id).delete()
        db.commit()
        return True

    deleted = await run_in_threadpool(delete)

    if not deleted:
        return ApiResponse.not_found("Ticket")

    return "", 204


@bp.route("/<int:ticket_id>/assign", methods=["POST"])
@login_required
async def assign_ticket(ticket_id):
    """
    Assign a ticket to a user.

    Path parameters:
        ticket_id: Ticket ID

    Request body:
        {
            "assignee_id": "int (identity_id)"
        }

    Returns:
        200: Updated ticket
        404: Ticket not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    data = await request.get_json() or {}
    assignee_id = data.get("assignee_id")

    if not assignee_id:
        return ApiResponse.validation_error("assignee_id", "is required")

    def assign():
        ticket_row = db((db.hd_tickets.id == ticket_id) & (db.hd_tickets.tenant_id == tenant_id)).select().first()

        if not ticket_row:
            return None

        now = datetime.now(timezone.utc)
        db(db.hd_tickets.id == ticket_id).update(
            assignee_identity_id=assignee_id,
            updated_at=now,
        )
        db.commit()

        return db(db.hd_tickets.id == ticket_id).select().first()

    ticket_row = await run_in_threadpool(assign)

    if not ticket_row:
        return ApiResponse.not_found("Ticket")

    return jsonify(
        {
            "id": ticket_row.id,
            "village_id": ticket_row.village_id,
            "subject": ticket_row.subject,
            "status": ticket_row.status,
            "priority": ticket_row.priority,
            "channel": ticket_row.channel,
            "requester_id": ticket_row.requester_identity_id,
            "assignee_id": ticket_row.assignee_identity_id,
            "team_id": ticket_row.hd_team_id,
            "category": ticket_row.category,
            "tags": ticket_row.tags,
            "sla_breach_at": ticket_row.sla_breach_at.isoformat() if ticket_row.sla_breach_at else None,
            "first_response_at": ticket_row.first_response_at.isoformat() if ticket_row.first_response_at else None,
            "resolved_at": ticket_row.resolved_at.isoformat() if ticket_row.resolved_at else None,
            "closed_at": ticket_row.closed_at.isoformat() if ticket_row.closed_at else None,
            "created_at": ticket_row.created_at.isoformat(),
            "updated_at": ticket_row.updated_at.isoformat(),
        }
    )


@bp.route("/<int:ticket_id>/merge", methods=["POST"])
@login_required
async def merge_tickets(ticket_id):
    """
    Merge two tickets (keep primary, mark secondary as merged).

    Path parameters:
        ticket_id: Primary ticket ID

    Request body:
        {
            "merge_from_id": "secondary ticket id"
        }

    Returns:
        200: Merge result
        404: Ticket not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    data = await request.get_json() or {}
    merge_from_id = data.get("merge_from_id")

    if not merge_from_id:
        return ApiResponse.validation_error("merge_from_id", "is required")

    def merge():
        # Fetch primary ticket
        primary = db((db.hd_tickets.id == ticket_id) & (db.hd_tickets.tenant_id == tenant_id)).select().first()

        if not primary:
            return (None, None, "primary_not_found")

        # Fetch secondary ticket
        secondary = db(
            (db.hd_tickets.id == merge_from_id) & (db.hd_tickets.tenant_id == tenant_id)
        ).select().first()

        if not secondary:
            return (None, None, "secondary_not_found")

        # Move messages from secondary to primary
        messages = db(db.hd_ticket_messages.hd_ticket_id == secondary.id).select()
        for msg in messages:
            db(db.hd_ticket_messages.id == msg.id).update(hd_ticket_id=primary.id)

        # Mark secondary as closed
        now = datetime.now(timezone.utc)
        db(db.hd_tickets.id == secondary.id).update(
            status="closed",
            closed_at=now,
            updated_at=now,
        )
        db.commit()

        return (primary.id, secondary.id, None)

    primary_id, secondary_id, error = await run_in_threadpool(merge)

    if error == "primary_not_found":
        return ApiResponse.not_found("Primary ticket")
    elif error == "secondary_not_found":
        return ApiResponse.not_found("Secondary ticket")
    elif error:
        return ApiResponse.error(error, 400)

    return jsonify({"primary_id": primary_id, "merged_id": secondary_id})
