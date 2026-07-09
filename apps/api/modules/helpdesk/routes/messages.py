"""Helpdesk ticket message endpoints using penguin-dal."""

# flake8: noqa: E501

import logging
from datetime import datetime, timezone

from quart import Blueprint, current_app, g, jsonify, request

from apps.api.auth.decorators import login_required
from apps.api.logging_config import log_error_and_respond
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.pydal_helpers import PaginationParams

logger = logging.getLogger(__name__)

bp = Blueprint("helpdesk_messages", __name__)


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


@bp.route("/<int:ticket_id>/messages", methods=["GET"])
@login_required
async def list_messages(ticket_id):
    """
    List messages for a ticket with pagination.

    Path parameters:
        ticket_id: Ticket ID

    Query Parameters:
        - page: Page number (default: 1)
        - per_page: Items per page (default: 20, max: 100)

    Returns:
        200: Paginated message list
        404: Ticket not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    # Extract pagination params
    pagination = PaginationParams.from_request()

    def fetch():
        # Verify ticket exists and belongs to tenant
        ticket = db((db.hd_tickets.id == ticket_id) & (db.hd_tickets.tenant_id == tenant_id)).select().first()

        if not ticket:
            return (None, None)

        # Get messages
        total = db(db.hd_ticket_messages.hd_ticket_id == ticket.id).count()
        rows = db(db.hd_ticket_messages.hd_ticket_id == ticket.id).select(
            orderby=~db.hd_ticket_messages.created_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )

        return (total, rows)

    total, rows = await run_in_threadpool(fetch)

    if total is None:
        return ApiResponse.not_found("Ticket")

    messages = [
        {
            "id": r.id,
            "ticket_id": r.hd_ticket_id,
            "sender_id": r.sender_identity_id,
            "message_type": r.message_type,
            "body_text": r.body_text,
            "body_html": r.body_html,
            "is_internal": r.is_internal,
            "email_message_id": r.email_message_id,
            "created_at": r.created_at.isoformat(),
            "updated_at": r.updated_at.isoformat(),
        }
        for r in rows
    ]

    return jsonify(
        {
            "items": messages,
            "pagination": {
                "page": pagination.page,
                "per_page": pagination.per_page,
                "total": total,
                "pages": (total + pagination.per_page - 1) // pagination.per_page,
            },
        }
    )


@bp.route("/<int:ticket_id>/messages", methods=["POST"])
@login_required
async def add_message(ticket_id):
    """
    Add a message to a ticket.

    Path parameters:
        ticket_id: Ticket ID

    Request body:
        {
            "sender_id": "int (identity_id, required)",
            "message_type": "reply|note|system",
            "body_text": "string (required)",
            "body_html": "string (optional)",
            "is_internal": "bool (default: false)",
            "email_message_id": "string (optional, RFC 2822 Message-ID)"
        }

    Returns:
        201: Created message
        404: Ticket not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    data = await request.get_json() or {}

    # Validate required fields
    sender_id = data.get("sender_id")
    if not sender_id:
        return ApiResponse.validation_error("sender_id", "is required")

    body_text = data.get("body_text", "").strip()
    if not body_text:
        return ApiResponse.validation_error("body_text", "is required")

    message_type = data.get("message_type", "reply")
    body_html = data.get("body_html")
    is_internal = data.get("is_internal", False)
    email_message_id = data.get("email_message_id")

    def create():
        # Verify ticket exists and belongs to tenant
        ticket = db((db.hd_tickets.id == ticket_id) & (db.hd_tickets.tenant_id == tenant_id)).select().first()

        if not ticket:
            return None

        now = datetime.now(timezone.utc)

        # Insert message
        message_id = db.hd_ticket_messages.insert(
            hd_ticket_id=ticket.id,
            sender_identity_id=sender_id,
            message_type=message_type,
            body_text=body_text,
            body_html=body_html,
            is_internal=is_internal,
            email_message_id=email_message_id,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        return db(db.hd_ticket_messages.id == message_id).select().first()

    message_row = await run_in_threadpool(create)

    if not message_row:
        return ApiResponse.not_found("Ticket")

    return (
        jsonify(
            {
                "id": message_row.id,
                "ticket_id": message_row.hd_ticket_id,
                "sender_id": message_row.sender_identity_id,
                "message_type": message_row.message_type,
                "body_text": message_row.body_text,
                "body_html": message_row.body_html,
                "is_internal": message_row.is_internal,
                "email_message_id": message_row.email_message_id,
                "created_at": message_row.created_at.isoformat(),
                "updated_at": message_row.updated_at.isoformat(),
            }
        ),
        201,
    )
