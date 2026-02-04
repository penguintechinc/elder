"""Resource Role management API endpoints using PyDAL with async/await."""

# flake8: noqa: E501


from dataclasses import asdict

from flask import Blueprint, current_app, g, jsonify, request
from apps.api.models.pydantic import CreateResourceRoleRequest, ResourceRoleResponse
from pydantic import ValidationError

from apps.api.auth.decorators import login_required
from apps.api.models.dataclasses import ResourceRoleDTO, from_pydal_rows
from apps.api.utils.async_utils import run_in_threadpool
from penguin_licensing import license_required

bp = Blueprint("resource_roles", __name__)


@bp.route("", methods=["GET"])
@login_required
@license_required("enterprise")
async def list_resource_roles():
    """
    List resource role assignments with optional filtering.

    Query Parameters:
        - identity_id: Filter by identity
        - group_id: Filter by group
        - resource_type: Filter by resource type (entity/organization)
        - resource_id: Filter by resource ID
        - role: Filter by role (maintainer/operator/viewer)

    Returns:
        200: List of resource roles
        403: License required

    Example:
        GET /api/v1/resource-roles?resource_type=entity&resource_id=42
        {
            "items": [
                {
                    "id": 1,
                    "identity_id": 5,
                    "resource_type": "entity",
                    "resource_id": 42,
                    "role": "maintainer",
                    "created_at": "2024-10-23T10:00:00Z"
                }
            ],
            "total": 1
        }
    """
    db = current_app.db

    # Build query
    def get_roles():
        query = db.resource_roles.id > 0

        # Apply filters from query params
        if request.args.get("identity_id"):
            try:
                identity_id = int(request.args.get("identity_id"))
                query &= db.resource_roles.identity_id == identity_id
            except (ValueError, TypeError):
                return None, "Invalid identity_id", 400

        if request.args.get("group_id"):
            try:
                group_id = int(request.args.get("group_id"))
                query &= db.resource_roles.group_id == group_id
            except (ValueError, TypeError):
                return None, "Invalid group_id", 400

        if request.args.get("resource_type"):
            resource_type = request.args.get("resource_type")
            if resource_type not in ("entity", "organization"):
                return None, "Invalid resource_type", 400
            query &= db.resource_roles.resource_type == resource_type

        if request.args.get("resource_id"):
            try:
                resource_id = int(request.args.get("resource_id"))
                query &= db.resource_roles.resource_id == resource_id
            except (ValueError, TypeError):
                return None, "Invalid resource_id", 400

        if request.args.get("role"):
            role = request.args.get("role")
            if role not in ("maintainer", "operator", "viewer"):
                return None, "Invalid role", 400
            query &= db.resource_roles.role == role

        roles = db(query).select()
        return roles, None, None

    result = await run_in_threadpool(get_roles)

    # Handle errors from query building
    if isinstance(result, tuple) and result[1] is not None:
        _, error, status = result
        return jsonify({"error": error}), status

    rows = result[0] if isinstance(result, tuple) else result

    # Convert to DTOs
    items = [ResourceRoleResponse.from_pydal_row(row) for row in rows]

    return (
        jsonify(
            {
                "items": [item.model_dump(exclude_none=True) for item in items],
                "total": len(items),
            }
        ),
        200,
    )


@bp.route("", methods=["POST"])
@login_required
@license_required("enterprise")
async def create_resource_role():
    """
    Grant a resource role to an identity or group.

    Requires maintainer role on the resource to grant roles.

    Request Body:
        {
            "identity_id": 5 (optional),
            "group_id": 3 (optional),
            "resource_type": "entity",
            "resource_id": 42,
            "role": "operator"
        }

    Returns:
        201: Resource role created
        400: Invalid request
        403: License required or insufficient permissions
        409: Role already exists

    Example:
        POST /api/v1/resource-roles
        {
            "identity_id": 5,
            "resource_type": "entity",
            "resource_id": 42,
            "role": "operator"
        }

        Response:
        {
            "id": 1,
            "identity_id": 5,
            "resource_type": "entity",
            "resource_id": 42,
            "role": "operator",
            "created_at": "2024-10-23T10:00:00Z"
        }
    """
    db = current_app.db

    # Validate request body
    try:
        req_data = CreateResourceRoleRequest(**request.get_json() or {})
    except ValidationError as e:
        errors = []
        for err in e.errors():
            errors.append(
                {"field": ".".join(str(x) for x in err["loc"]), "message": err["msg"]}
            )
        return jsonify({"error": "Validation failed", "details": errors}), 400

    # Must have either identity_id or group_id
    if not req_data.identity_id and not req_data.group_id:
        return jsonify({"error": "Either identity_id or group_id is required"}), 400

    # Check if current user has maintainer role on this resource
    def check_and_create():
        # Superusers can grant any role
        if not g.current_user.is_superuser:
            # Check if current user has maintainer role
            has_maintainer = (
                db(
                    (db.resource_roles.identity_id == g.current_user.id)
                    & (db.resource_roles.resource_type == req_data.resource_type)
                    & (db.resource_roles.resource_id == req_data.resource_id)
                    & (db.resource_roles.role == "maintainer")
                )
                .select()
                .first()
            )

            if not has_maintainer:
                return (
                    None,
                    "Insufficient permissions - only maintainers can grant resource roles",
                    403,
                )

        # Check if role already exists
        if req_data.identity_id:
            existing_role = (
                db(
                    (db.resource_roles.identity_id == req_data.identity_id)
                    & (db.resource_roles.resource_type == req_data.resource_type)
                    & (db.resource_roles.resource_id == req_data.resource_id)
                )
                .select()
                .first()
            )
        else:
            existing_role = (
                db(
                    (db.resource_roles.group_id == req_data.group_id)
                    & (db.resource_roles.resource_type == req_data.resource_type)
                    & (db.resource_roles.resource_id == req_data.resource_id)
                )
                .select()
                .first()
            )

        if existing_role:
            return (
                None,
                "Role already exists for this identity/group on this resource",
                409,
            )

        # Create resource role
        role_id = db.resource_roles.insert(
            identity_id=req_data.identity_id,
            group_id=req_data.group_id,
            resource_type=req_data.resource_type,
            resource_id=req_data.resource_id,
            role=req_data.role,
        )
        db.commit()

        return db.resource_roles[role_id], None, None

    result, error, status = await run_in_threadpool(check_and_create)

    if error:
        return jsonify({"error": error}), status

    role_dto = ResourceRoleResponse.from_pydal_row(result)
    return jsonify(role_dto.model_dump(exclude_none=True)), 201


@bp.route("/<int:id>", methods=["DELETE"])
@login_required
@license_required("enterprise")
async def revoke_resource_role(id: int):
    """
    Revoke a resource role.

    Requires maintainer role on the resource to revoke roles.

    Path Parameters:
        - id: Resource role ID

    Returns:
        204: Resource role revoked
        403: License required or insufficient permissions
        404: Resource role not found

    Example:
        DELETE /api/v1/resource-roles/1
    """
    db = current_app.db

    # Check and delete role
    def check_and_delete():
        role = db.resource_roles[id]
        if not role:
            return None, "Resource role not found", 404

        # Check if current user has maintainer role on this resource
        # Superusers can revoke any role
        if not g.current_user.is_superuser:
            has_maintainer = (
                db(
                    (db.resource_roles.identity_id == g.current_user.id)
                    & (db.resource_roles.resource_type == role.resource_type)
                    & (db.resource_roles.resource_id == role.resource_id)
                    & (db.resource_roles.role == "maintainer")
                )
                .select()
                .first()
            )

            if not has_maintainer:
                return (
                    None,
                    "Insufficient permissions - only maintainers can revoke resource roles",
                    403,
                )

        # Delete role
        db(db.resource_roles.id == id).delete()
        db.commit()

        return True, None, None

    result, error, status = await run_in_threadpool(check_and_delete)

    if error:
        return jsonify({"error": error}), status

    return "", 204


@bp.route("/entities/<int:id>/roles", methods=["GET"])
@login_required
@license_required("enterprise")
async def list_entity_roles(id: int):
    """
    List all resource roles for an entity.

    Path Parameters:
        - id: Entity ID

    Returns:
        200: List of resource roles for this entity
        403: License required
        404: Entity not found

    Example:
        GET /api/v1/resource-roles/entities/42/roles
        {
            "items": [
                {
                    "id": 1,
                    "identity_id": 5,
                    "role": "maintainer"
                }
            ],
            "total": 1
        }
    """
    db = current_app.db

    def get_entity_roles():
        # Verify entity exists
        entity = db.entities[id]
        if not entity:
            return None, "Entity not found", 404

        # Get all roles for this entity
        roles = db(
            (db.resource_roles.resource_type == "entity")
            & (db.resource_roles.resource_id == id)
        ).select()

        return roles, None, None

    result, error, status = await run_in_threadpool(get_entity_roles)

    if error:
        return jsonify({"error": error}), status

    # Convert to DTOs
    items = from_pydal_rows(result, ResourceRoleDTO)

    return (
        jsonify({"items": [asdict(item) for item in items], "total": len(items)}),
        200,
    )


@bp.route("/organizations/<int:id>/roles", methods=["GET"])
@login_required
@license_required("enterprise")
async def list_organization_roles(id: int):
    """
    List all resource roles for an organization.

    Path Parameters:
        - id: Organization ID

    Returns:
        200: List of resource roles for this organization
        403: License required
        404: Organization not found

    Example:
        GET /api/v1/resource-roles/organizations/1/roles
        {
            "items": [
                {
                    "id": 2,
                    "identity_id": 3,
                    "role": "operator"
                }
            ],
            "total": 1
        }
    """
    db = current_app.db

    def get_org_roles():
        # Verify organization exists
        org = db.organizations[id]
        if not org:
            return None, "Organization not found", 404

        # Get all roles for this organization
        roles = db(
            (db.resource_roles.resource_type == "organization")
            & (db.resource_roles.resource_id == id)
        ).select()

        return roles, None, None

    result, error, status = await run_in_threadpool(get_org_roles)

    if error:
        return jsonify({"error": error}), status

    # Convert to DTOs
    items = from_pydal_rows(result, ResourceRoleDTO)

    return (
        jsonify({"items": [asdict(item) for item in items], "total": len(items)}),
        200,
    )


@bp.route("/identities/<int:id>/resource-roles", methods=["GET"])
@login_required
@license_required("enterprise")
async def list_identity_resource_roles(id: int):
    """
    List all resource roles assigned to an identity.

    Path Parameters:
        - id: Identity ID

    Returns:
        200: List of resource roles for this identity
        403: License required
        404: Identity not found

    Example:
        GET /api/v1/resource-roles/identities/5/resource-roles
        {
            "items": [
                {
                    "id": 1,
                    "resource_type": "entity",
                    "resource_id": 42,
                    "role": "maintainer"
                },
                {
                    "id": 2,
                    "resource_type": "organization",
                    "resource_id": 1,
                    "role": "operator"
                }
            ],
            "total": 2
        }
    """
    db = current_app.db

    def get_identity_roles():
        # Verify identity exists
        identity = db.identities[id]
        if not identity:
            return None, "Identity not found", 404

        # Get all roles for this identity
        roles = db(db.resource_roles.identity_id == id).select()

        return roles, None, None

    result, error, status = await run_in_threadpool(get_identity_roles)

    if error:
        return jsonify({"error": error}), status

    # Convert to DTOs
    items = from_pydal_rows(result, ResourceRoleDTO)

    return (
        jsonify({"items": [asdict(item) for item in items], "total": len(items)}),
        200,
    )
