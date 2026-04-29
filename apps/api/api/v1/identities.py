"""Identity and User management API endpoints using PyDAL with async/await."""

# flake8: noqa: E501


import asyncio
from dataclasses import asdict
from datetime import datetime, timezone

from flask import Blueprint, current_app, g, jsonify, request
from penguin_libs.pydantic.flask_integration import validated_request
from werkzeug.security import generate_password_hash

from apps.api.auth import login_required, permission_required
from apps.api.models.dataclasses import (
    IdentityDTO,
    PaginatedResponse,
    from_pydal_row,
    from_pydal_rows,
)
from apps.api.models.pydantic.identity import (
    CreateIdentityGroupRequest,
    CreateIdentityRequest,
    IdentityGroupDTO,
    UpdateIdentityGroupRequest,
    UpdateIdentityRequest,
)
from apps.api.utils.async_utils import run_in_threadpool

bp = Blueprint("identities", __name__)


def _identity_row_to_dto(row) -> IdentityDTO:
    """Map a PyDAL identity row (DB column names) to IdentityDTO (API field names)."""
    d = row.as_dict()
    return IdentityDTO(
        id=d["id"],
        username=d["username"],
        email=d.get("email"),
        type=d.get("identity_type", "human"),
        tenant_id=d.get("tenant_id"),
        provider=d.get("auth_provider"),
        external_id=d.get("auth_provider_id"),
        name=d.get("full_name"),
        display_name=d.get("full_name"),
        is_active=d.get("is_active", True),
        last_seen_at=d.get("last_login_at"),
        created_at=d["created_at"],
        updated_at=d["updated_at"],
    )


@bp.route("", methods=["GET"])
@login_required
async def list_identities():
    """
    List all identities with pagination.

    Query Parameters:
        - page: Page number (default: 1)
        - per_page: Items per page (default: 50, max: 1000)
        - search: Search in username, email, or full name (case-insensitive)
        - identity_type: Filter by type (human/service_account)
        - is_active: Filter by active status
        - organization_id: Filter by organization

    Returns:
        200: List of identities
    """
    db = current_app.db

    # Get pagination params
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 50, type=int), 1000)

    # Build query
    query = db.identities.id > 0

    # Apply filters
    # Search filter — DB columns: username, email, full_name
    search = request.args.get("search") or request.args.get("name")
    if search:
        search_pattern = f"%{search}%"
        query &= (
            (db.identities.username.ilike(search_pattern))
            | (db.identities.email.ilike(search_pattern))
            | (db.identities.full_name.ilike(search_pattern))
        )

    identity_type = request.args.get("identity_type")
    if identity_type:
        query &= db.identities.identity_type == identity_type

    is_active = request.args.get("is_active")
    if is_active is not None:
        query &= db.identities.is_active == (is_active.lower() == "true")

    auth_provider = request.args.get("auth_provider")
    if auth_provider:
        query &= db.identities.auth_provider == auth_provider

    auth_provider_id = request.args.get("auth_provider_id")
    if auth_provider_id:
        query &= db.identities.auth_provider_id == auth_provider_id

    # Calculate pagination
    offset = (page - 1) * per_page

    # Execute database queries in a single thread pool task to avoid cursor issues
    def get_identities():
        total = db(query).count()
        # Select actual DB columns (identity_type, full_name, auth_provider, auth_provider_id)
        rows = db(query).select(
            db.identities.id,
            db.identities.identity_type,
            db.identities.username,
            db.identities.email,
            db.identities.full_name,
            db.identities.auth_provider,
            db.identities.auth_provider_id,
            db.identities.tenant_id,
            db.identities.is_active,
            db.identities.last_login_at,
            db.identities.created_at,
            db.identities.updated_at,
            orderby=db.identities.username,
            limitby=(offset, offset + per_page),
        )
        return total, rows

    total, rows = await run_in_threadpool(get_identities)

    # Calculate total pages
    pages = (total + per_page - 1) // per_page if total > 0 else 0

    items = [_identity_row_to_dto(row) for row in rows]

    # Create paginated response
    response = PaginatedResponse(
        items=[asdict(item) for item in items],
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )

    return jsonify(asdict(response)), 200


@bp.route("", methods=["POST"])
@login_required
@permission_required("manage_users")
@validated_request(body_model=CreateIdentityRequest)
async def create_identity(body: CreateIdentityRequest):
    """
    Create a new identity/user.

    Request Body:
        {
            "username": "string",
            "identity_type": "human" or "service_account",
            "auth_provider": "local", "ldap", "saml", etc.,
            "email": "string" (optional),
            "full_name": "string" (optional),
            "is_active": true/false (default: true)
        }

    Returns:
        201: Created identity
        400: Validation error
    """
    db = current_app.db

    # Log the request for debugging
    current_app.logger.info(
        f"Creating identity: username={body.username}, type={body.identity_type}, auth={body.auth_provider}"
    )

    # Create identity
    def create():
        # Check if username exists
        existing = db(db.identities.username == body.username).select().first()
        if existing:
            return None, "Username already exists", 400

        # Prepare insert data — use actual DB column names
        insert_data = {
            "username": body.username,
            "identity_type": body.identity_type,
            "auth_provider": body.auth_provider,
            "email": body.email,
            "full_name": body.full_name,
            "auth_provider_id": body.auth_provider_id,
            "is_active": body.is_active,
        }

        # Create identity
        now = datetime.now(timezone.utc)
        identity_id = db.identities.insert(
            created_at=now, updated_at=now, **insert_data
        )
        db.commit()

        return db.identities[identity_id], None, None

    identity, error, status = await run_in_threadpool(create)

    if error:
        return jsonify({"error": error}), status

    identity_dto = _identity_row_to_dto(identity)
    return jsonify(asdict(identity_dto)), 201


@bp.route("/<int:id>", methods=["GET"])
@login_required
@permission_required("view_users")
async def get_identity(id: int):
    """
    Get identity by ID.

    Path Parameters:
        - id: Identity ID

    Returns:
        200: Identity details
        404: Identity not found
    """
    db = current_app.db

    identity = await run_in_threadpool(lambda: db.identities[id])

    if not identity:
        return jsonify({"error": "Identity not found"}), 404

    identity_dto = _identity_row_to_dto(identity)
    return jsonify(asdict(identity_dto)), 200


@bp.route("/<int:id>", methods=["PATCH", "PUT"])
@login_required
@permission_required("manage_users")
@validated_request(body_model=UpdateIdentityRequest)
async def update_identity(id: int, body: UpdateIdentityRequest):
    """
    Update identity.

    Path Parameters:
        - id: Identity ID

    Request Body:
        {
            "email": "string" (optional),
            "full_name": "string" (optional),
            "password": "string" (optional),
            "is_active": true/false (optional),
            "mfa_enabled": true/false (optional),
            "portal_role": "admin" | "editor" | "viewer" (optional),
            "organization_id": int (optional)
        }

    Returns:
        200: Updated identity
        400: Validation error
        404: Identity not found
    """
    db = current_app.db

    # Check if identity exists
    existing = await run_in_threadpool(lambda: db.identities[id])
    if not existing:
        return jsonify({"error": "Identity not found"}), 404

    # Update identity
    def update():
        update_fields = {}

        if body.email is not None:
            update_fields["email"] = body.email
        if body.full_name is not None:
            update_fields["full_name"] = body.full_name
        if body.password is not None:
            # Note: identities table doesn't have password_hash - passwords are managed separately
            pass
        if body.is_active is not None:
            update_fields["is_active"] = body.is_active

        db(db.identities.id == id).update(**update_fields)
        db.commit()
        return db.identities[id]

    identity = await run_in_threadpool(update)

    identity_dto = _identity_row_to_dto(identity)
    return jsonify(asdict(identity_dto)), 200


@bp.route("/<int:id>", methods=["DELETE"])
@login_required
@permission_required("manage_users")
async def delete_identity(id: int):
    """
    Delete identity.

    Path Parameters:
        - id: Identity ID

    Returns:
        204: Identity deleted
        404: Identity not found
        400: Cannot delete own account or superuser
    """
    db = current_app.db

    # Check if identity exists
    def check_and_delete():
        identity = db.identities[id]
        if not identity:
            return None, "Identity not found", 404

        # Prevent deleting own account
        if identity.id == g.current_user.id:
            return None, "Cannot delete your own account", 400

        # Delete identity
        db(db.identities.id == id).delete()
        db.commit()
        return True, None, None

    result, error, status = await run_in_threadpool(check_and_delete)

    if error:
        return jsonify({"error": error}), status

    return "", 204


# Identity Groups endpoints


@bp.route("/groups", methods=["GET"])
@login_required
@permission_required("view_users")
async def list_groups():
    """
    List all identity groups.

    Query Parameters:
        - page: Page number
        - per_page: Items per page

    Returns:
        200: List of groups
    """
    db = current_app.db

    # Get pagination params
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 50, type=int), 1000)

    # Build query
    query = db.identity_groups.id > 0

    # Calculate pagination
    offset = (page - 1) * per_page

    # Use asyncio TaskGroup for concurrent queries (Python 3.12)
    async with asyncio.TaskGroup() as tg:
        count_task = tg.create_task(run_in_threadpool(lambda: db(query).count()))
        rows_task = tg.create_task(
            run_in_threadpool(
                lambda: db(query).select(
                    orderby=db.identity_groups.name, limitby=(offset, offset + per_page)
                )
            )
        )

    total = count_task.result()
    rows = rows_task.result()

    # Calculate total pages
    pages = (total + per_page - 1) // per_page if total > 0 else 0

    # Convert PyDAL rows to DTOs
    items = from_pydal_rows(rows, IdentityGroupDTO)

    # Create paginated response
    response = PaginatedResponse(
        items=[asdict(item) for item in items],
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )

    return jsonify(asdict(response)), 200


@bp.route("/groups", methods=["POST"])
@login_required
@permission_required("manage_users")
@validated_request(body_model=CreateIdentityGroupRequest)
async def create_group(body: CreateIdentityGroupRequest):
    """
    Create a new identity group.

    Request Body:
        {
            "name": "string",
            "description": "string" (optional),
            "ldap_dn": "string" (optional),
            "saml_group": "string" (optional),
            "is_active": true/false (default: true)
        }

    Returns:
        201: Created group
    """
    db = current_app.db

    # Create group
    def create():
        # Check if group name exists
        existing = db(db.identity_groups.name == body.name).select().first()
        if existing:
            return None, "Group name already exists", 400

        # Create group
        group_id = db.identity_groups.insert(
            name=body.name,
            description=body.description,
            is_active=body.is_active,
        )
        db.commit()

        return db.identity_groups[group_id], None, None

    group, error, status = await run_in_threadpool(create)

    if error:
        return jsonify({"error": error}), status

    group_dto = from_pydal_row(group, IdentityGroupDTO)
    return jsonify(asdict(group_dto)), 201


@bp.route("/groups/<int:id>", methods=["GET"])
@login_required
@permission_required("view_users")
async def get_group(id: int):
    """Get identity group by ID."""
    db = current_app.db

    group = await run_in_threadpool(lambda: db.identity_groups[id])

    if not group:
        return jsonify({"error": "Group not found"}), 404

    group_dto = from_pydal_row(group, IdentityGroupDTO)
    return jsonify(asdict(group_dto)), 200


@bp.route("/groups/<int:id>", methods=["PATCH", "PUT"])
@login_required
@permission_required("manage_users")
@validated_request(body_model=UpdateIdentityGroupRequest)
async def update_group(id: int, body: UpdateIdentityGroupRequest):
    """
    Update identity group.

    Path Parameters:
        - id: Group ID

    Request Body:
        {
            "name": "string" (optional),
            "description": "string" (optional),
            "ldap_dn": "string" (optional),
            "saml_group": "string" (optional),
            "is_active": true/false (optional)
        }

    Returns:
        200: Updated group
        404: Group not found
    """
    db = current_app.db

    # Check if group exists
    existing = await run_in_threadpool(lambda: db.identity_groups[id])
    if not existing:
        return jsonify({"error": "Group not found"}), 404

    # Update group
    def update():
        update_fields = {}

        if body.name is not None:
            update_fields["name"] = body.name
        if body.description is not None:
            update_fields["description"] = body.description
        if body.ldap_dn is not None:
            update_fields["ldap_dn"] = body.ldap_dn
        if body.saml_group is not None:
            update_fields["saml_group"] = body.saml_group
        if body.is_active is not None:
            update_fields["is_active"] = body.is_active

        db(db.identity_groups.id == id).update(**update_fields)
        db.commit()
        return db.identity_groups[id]

    group = await run_in_threadpool(update)

    group_dto = from_pydal_row(group, IdentityGroupDTO)
    return jsonify(asdict(group_dto)), 200


@bp.route("/groups/<int:id>", methods=["DELETE"])
@login_required
@permission_required("manage_users")
async def delete_group(id: int):
    """Delete identity group."""
    db = current_app.db

    # Check if group exists
    existing = await run_in_threadpool(lambda: db.identity_groups[id])
    if not existing:
        return jsonify({"error": "Group not found"}), 404

    # Delete group
    await run_in_threadpool(
        lambda: (db(db.identity_groups.id == id).delete(), db.commit())
    )

    return "", 204


@bp.route("/groups/<int:group_id>/members/<int:identity_id>", methods=["POST"])
@login_required
@permission_required("manage_users")
async def add_group_member(group_id: int, identity_id: int):
    """
    Add identity to group.

    Path Parameters:
        - group_id: Group ID
        - identity_id: Identity ID

    Returns:
        200: Member added
        400: Already a member
    """
    db = current_app.db

    # Check and add membership
    def add_member():
        # Verify group exists
        group = db.identity_groups[group_id]
        if not group:
            return None, "Group not found", 404

        # Verify identity exists
        identity = db.identities[identity_id]
        if not identity:
            return None, "Identity not found", 404

        # Check if already a member
        existing = (
            db(
                (db.identity_group_members.group_id == group_id)
                & (db.identity_group_members.identity_id == identity_id)
            )
            .select()
            .first()
        )

        if existing:
            return None, "Identity is already a member of this group", 400

        # Add membership
        db.identity_group_members.insert(
            group_id=group_id,
            identity_id=identity_id,
        )
        db.commit()

        return True, None, None

    result, error, status = await run_in_threadpool(add_member)

    if error:
        return jsonify({"error": error}), status

    return jsonify({"message": "Member added successfully"}), 200


@bp.route("/groups/<int:group_id>/members/<int:identity_id>", methods=["DELETE"])
@login_required
@permission_required("manage_users")
async def remove_group_member(group_id: int, identity_id: int):
    """
    Remove identity from group.

    Returns:
        204: Member removed
        404: Not a member
    """
    db = current_app.db

    # Check and remove membership
    def remove_member():
        membership = (
            db(
                (db.identity_group_members.group_id == group_id)
                & (db.identity_group_members.identity_id == identity_id)
            )
            .select()
            .first()
        )

        if not membership:
            return None, "Identity is not a member of this group", 404

        # Delete membership
        db(
            (db.identity_group_members.group_id == group_id)
            & (db.identity_group_members.identity_id == identity_id)
        ).delete()
        db.commit()

        return True, None, None

    result, error, status = await run_in_threadpool(remove_member)

    if error:
        return jsonify({"error": error}), status

    return "", 204
