"""Helpdesk team CRUD endpoints using penguin-dal."""

# flake8: noqa: E501

import logging
from datetime import datetime, timezone
from uuid import uuid4

from quart import Blueprint, current_app, g, request

from apps.api.auth.decorators import login_required
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.pydal_helpers import PaginationParams

logger = logging.getLogger(__name__)

bp = Blueprint("helpdesk_teams", __name__)


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
async def list_teams():
    """
    List teams with pagination.

    Query Parameters:
        - page: Page number (default: 1)
        - per_page: Items per page (default: 20, max: 100)

    Returns:
        200: Paginated list of teams
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    # Extract pagination params
    pagination = PaginationParams.from_request()

    # Build query
    query = db.hd_teams.tenant_id == tenant_id

    def get_teams():
        total = db(query).count()
        rows = db(query).select(
            orderby=~db.hd_teams.created_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )
        return total, rows

    total, rows = await run_in_threadpool(get_teams)

    teams = [
        {
            "id": r.id,
            "name": r.name,
            "description": r.description,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]

    return (
        {
            "items": teams,
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
async def create_team():
    """
    Create a new team.

    Request body:
        {
            "name": "Team Name",
            "description": "Team description"
        }

    Returns:
        201: Created team
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    data = await request.get_json() or {}

    # Validate required fields
    name = data.get("name", "").strip()

    if not name:
        return ApiResponse.validation_error("name", "is required")

    description = data.get("description", "").strip() or None

    # Capture redis_client outside threadpool (may be mocked in tests)
    redis_client = getattr(current_app, "redis_client", None)

    def create():
        from shared.utils.village_id import generate_village_id

        now = datetime.now(timezone.utc)
        # Use redis_client if available, otherwise generate_village_id will handle it
        if redis_client:
            village_id = generate_village_id(tenant_id, redis_client)
        else:
            # Fallback for testing without redis
            village_id = f"team-{uuid4().hex[:12]}"

        team_id = db.hd_teams.insert(
            tenant_id=tenant_id,
            village_id=village_id,
            name=name,
            description=description,
            created_at=now,
            updated_at=now,
        )
        db.commit()
        return db(db.hd_teams.id == team_id).select().first()

    team_row = await run_in_threadpool(create)

    return (
        {
            "id": team_row.id,
            "name": team_row.name,
            "description": team_row.description,
            "created_at": team_row.created_at.isoformat(),
        },
        201,
    )


@bp.route("/<int:team_id>", methods=["GET"])
@login_required
async def get_team(team_id: int):
    """
    Get team detail with members.

    Path parameters:
        team_id: Team ID

    Returns:
        200: Team detail with members
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    def get():
        # Fetch team
        team_query = (db.hd_teams.id == team_id) & (db.hd_teams.tenant_id == tenant_id)
        team_row = db(team_query).select().first()

        if not team_row:
            return None

        # Fetch members
        members_query = db.hd_team_members.hd_team_id == team_id
        member_rows = db(members_query).select()

        member_data = [
            {
                "identity_id": m.identity_id,
                "role": m.role,
            }
            for m in member_rows
        ]

        return {
            "id": team_row.id,
            "name": team_row.name,
            "description": team_row.description,
            "members": member_data,
            "created_at": team_row.created_at.isoformat(),
        }

    result = await run_in_threadpool(get)

    if result is None:
        return ApiResponse.not_found("Team", team_id)

    return result, 200


@bp.route("/<int:team_id>", methods=["PUT"])
@login_required
async def update_team(team_id: int):
    """
    Update team properties.

    Path parameters:
        team_id: Team ID

    Request body:
        {
            "name": "New Team Name",
            "description": "New description"
        }

    Returns:
        200: Updated team
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    data = await request.get_json() or {}

    def update():
        # Fetch team
        team_query = (db.hd_teams.id == team_id) & (db.hd_teams.tenant_id == tenant_id)
        team_row = db(team_query).select().first()

        if not team_row:
            return None

        # Update fields
        update_data = {}
        if "name" in data:
            name = data["name"].strip()
            if name:
                update_data["name"] = name

        if "description" in data:
            description = data["description"].strip() or None
            update_data["description"] = description

        update_data["updated_at"] = datetime.now(timezone.utc)

        if update_data:
            db(team_query).update(**update_data)
            db.commit()

        # Return updated row
        return db(team_query).select().first()

    result = await run_in_threadpool(update)

    if result is None:
        return ApiResponse.not_found("Team", team_id)

    return (
        {
            "id": result.id,
            "name": result.name,
            "description": result.description,
            "created_at": result.created_at.isoformat(),
        },
        200,
    )


@bp.route("/<int:team_id>/members", methods=["POST"])
@login_required
async def add_team_member(team_id: int):
    """
    Add a member to a team.

    Path parameters:
        team_id: Team ID

    Request body:
        {
            "identity_id": "int",
            "role": "member"
        }

    Returns:
        201: Member addition response
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    data = await request.get_json() or {}

    # Validate required fields
    identity_id = data.get("identity_id")
    role = data.get("role", "member")

    if not identity_id:
        return ApiResponse.validation_error("identity_id", "is required")

    def add_member():
        # Fetch team
        team_query = (db.hd_teams.id == team_id) & (db.hd_teams.tenant_id == tenant_id)
        team_row = db(team_query).select().first()

        if not team_row:
            return "team_not_found"

        # Verify identity exists AND belongs to this tenant (cross-tenant IDOR guard)
        identity_row = (
            db(
                (db.identities.id == identity_id)
                & (db.identities.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not identity_row:
            return "identity_not_found"

        # Check if already a member
        existing_query = (db.hd_team_members.hd_team_id == team_id) & (
            db.hd_team_members.identity_id == identity_id
        )
        existing = db(existing_query).select().first()

        if existing:
            return "already_member"

        # Add member
        db.hd_team_members.insert(
            hd_team_id=team_id,
            identity_id=identity_id,
            role=role,
        )
        db.commit()
        return True

    result = await run_in_threadpool(add_member)

    if result == "team_not_found":
        return ApiResponse.not_found("Team", team_id)

    if result == "identity_not_found":
        return ApiResponse.not_found("Identity", identity_id)

    if result == "already_member":
        return ApiResponse.conflict("User is already a team member")

    return (
        {
            "team_id": team_id,
            "identity_id": identity_id,
            "role": role,
        },
        201,
    )


@bp.route("/<int:team_id>/members/<int:identity_id>", methods=["DELETE"])
@login_required
async def remove_team_member(team_id: int, identity_id: int):
    """
    Remove a member from a team.

    Path parameters:
        team_id: Team ID
        identity_id: Identity ID

    Returns:
        204: No content
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    def remove():
        # Fetch team
        team_query = (db.hd_teams.id == team_id) & (db.hd_teams.tenant_id == tenant_id)
        team_row = db(team_query).select().first()

        if not team_row:
            return "team_not_found"

        # Fetch member
        member_query = (db.hd_team_members.hd_team_id == team_id) & (
            db.hd_team_members.identity_id == identity_id
        )
        member_row = db(member_query).select().first()

        if not member_row:
            return "member_not_found"

        # Remove member
        db(member_query).delete()
        db.commit()
        return True

    result = await run_in_threadpool(remove)

    if result == "team_not_found":
        return ApiResponse.not_found("Team", team_id)

    if result == "member_not_found":
        return ApiResponse.not_found("Team member")

    return ApiResponse.no_content()
