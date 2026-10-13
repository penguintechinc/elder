"""Helpdesk SLA policy CRUD endpoints using penguin-dal."""

# flake8: noqa: E501

import logging

from quart import Blueprint, current_app, g, request

from apps.api.auth.decorators import login_required, require_scope
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.pydal_helpers import PaginationParams

logger = logging.getLogger(__name__)

bp = Blueprint("helpdesk_sla_policies", __name__)


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
@require_scope("helpdesk:read")
async def list_sla_policies():
    """
    List SLA policies with pagination.

    Query Parameters:
        - page: Page number (default: 1)
        - per_page: Items per page (default: 20, max: 100)

    Returns:
        200: Paginated list of SLA policies
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    # Extract pagination params
    pagination = PaginationParams.from_request()

    # Build query
    query = db.hd_sla_policies.tenant_id == tenant_id

    def get_policies():
        total = db(query).count()
        rows = db(query).select(
            orderby=db.hd_sla_policies.priority,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )
        return total, rows

    total, rows = await run_in_threadpool(get_policies)

    policies = [
        {
            "id": r.id,
            "name": r.name,
            "priority": r.priority,
            "first_response_hours": r.first_response_hours,
            "resolution_hours": r.resolution_hours,
            "business_hours_only": r.business_hours_only,
            "is_active": r.is_active,
        }
        for r in rows
    ]

    return (
        {
            "items": policies,
            "pagination": {
                "page": pagination.page,
                "per_page": pagination.per_page,
                "total": total,
                "pages": (total + pagination.per_page - 1) // pagination.per_page,
            },
        },
        200,
    )


@bp.route("", methods=["POST"])
@login_required
@require_scope("helpdesk:admin")
async def create_sla_policy():
    """
    Create a new SLA policy.

    Request body:
        {
            "name": "High Priority SLA",
            "priority": "high",
            "first_response_hours": 1,
            "resolution_hours": 4,
            "business_hours_only": false,
            "is_active": true
        }

    Returns:
        201: Created SLA policy
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    data = await request.get_json() or {}

    # Validate required fields
    name = data.get("name", "").strip()
    priority = data.get("priority", "").strip()
    first_response_hours = data.get("first_response_hours")
    resolution_hours = data.get("resolution_hours")

    if not name:
        return ApiResponse.validation_error("name", "is required")

    if not priority:
        return ApiResponse.validation_error("priority", "is required")

    if priority not in ["low", "medium", "high", "urgent", "critical"]:
        return ApiResponse.error("Invalid priority value", 400)

    if not isinstance(first_response_hours, int) or not isinstance(
        resolution_hours, int
    ):
        return ApiResponse.error(
            "first_response_hours and resolution_hours must be integers", 400
        )

    if first_response_hours <= 0 or resolution_hours <= 0:
        return ApiResponse.error("Hours must be positive integers", 400)

    def create():
        policy_id = db.hd_sla_policies.insert(
            tenant_id=tenant_id,
            name=name,
            priority=priority,
            first_response_hours=first_response_hours,
            resolution_hours=resolution_hours,
            business_hours_only=data.get("business_hours_only", True),
            is_active=data.get("is_active", True),
        )
        db.commit()
        return db(db.hd_sla_policies.id == policy_id).select().first()

    policy_row = await run_in_threadpool(create)

    return (
        {
            "id": policy_row.id,
            "name": policy_row.name,
            "priority": policy_row.priority,
            "first_response_hours": policy_row.first_response_hours,
            "resolution_hours": policy_row.resolution_hours,
            "business_hours_only": policy_row.business_hours_only,
            "is_active": policy_row.is_active,
        },
        201,
    )


@bp.route("/<int:policy_id>", methods=["PUT"])
@login_required
@require_scope("helpdesk:admin")
async def update_sla_policy(policy_id: int):
    """
    Update SLA policy properties.

    Path parameters:
        policy_id: Policy ID

    Request body:
        {
            "name": "Updated Name",
            "first_response_hours": 2,
            "resolution_hours": 8,
            "business_hours_only": true,
            "is_active": true
        }

    Returns:
        200: Updated SLA policy
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    data = await request.get_json() or {}

    def update():
        # Fetch policy
        query = (db.hd_sla_policies.id == policy_id) & (
            db.hd_sla_policies.tenant_id == tenant_id
        )
        policy_row = db(query).select().first()

        if not policy_row:
            return None

        # Update fields
        update_data = {}
        if "name" in data:
            name = data["name"].strip()
            if name:
                update_data["name"] = name

        if "first_response_hours" in data:
            hours = data["first_response_hours"]
            if isinstance(hours, int) and hours > 0:
                update_data["first_response_hours"] = hours
            else:
                return "invalid_hours"

        if "resolution_hours" in data:
            hours = data["resolution_hours"]
            if isinstance(hours, int) and hours > 0:
                update_data["resolution_hours"] = hours
            else:
                return "invalid_hours"

        if "business_hours_only" in data:
            update_data["business_hours_only"] = data["business_hours_only"]

        if "is_active" in data:
            update_data["is_active"] = data["is_active"]

        if update_data:
            db(query).update(**update_data)
            db.commit()

        # Return updated row
        return db(query).select().first()

    result = await run_in_threadpool(update)

    if result is None:
        return ApiResponse.not_found("SLA policy", policy_id)

    if result == "invalid_hours":
        return ApiResponse.error("Hours must be positive integers", 400)

    return (
        {
            "id": result.id,
            "name": result.name,
            "priority": result.priority,
            "first_response_hours": result.first_response_hours,
            "resolution_hours": result.resolution_hours,
            "business_hours_only": result.business_hours_only,
            "is_active": result.is_active,
        },
        200,
    )


@bp.route("/<int:policy_id>", methods=["DELETE"])
@login_required
@require_scope("helpdesk:admin")
async def delete_sla_policy(policy_id: int):
    """
    Delete an SLA policy.

    Path parameters:
        policy_id: Policy ID

    Returns:
        204: No content
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    def delete():
        # Fetch policy
        query = (db.hd_sla_policies.id == policy_id) & (
            db.hd_sla_policies.tenant_id == tenant_id
        )
        policy_row = db(query).select().first()

        if not policy_row:
            return None

        # Delete policy
        db(query).delete()
        db.commit()
        return True

    result = await run_in_threadpool(delete)

    if result is None:
        return ApiResponse.not_found("SLA policy", policy_id)

    return ApiResponse.no_content()
