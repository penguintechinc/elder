"""Google Workspace Integration API endpoints for Elder v1.2.0 (Phase 7)."""

# flake8: noqa: E501


import logging

from quart import Blueprint, current_app, jsonify, request

from apps.api.auth.decorators import admin_required, login_required, require_scope
from apps.api.logging_config import log_error_and_respond
from apps.api.services.google_workspace import GoogleWorkspaceService

logger = logging.getLogger(__name__)

bp = Blueprint("google_workspace", __name__)


def get_google_workspace_service():
    """Get GoogleWorkspaceService instance with current database."""
    return GoogleWorkspaceService(current_app.db)


# ===========================
# Provider Management Endpoints
# ===========================


@bp.route("/providers", methods=["GET"])
@login_required
@require_scope("discovery:read")
def list_providers():
    """
    List Google Workspace providers.

    Query params:
        - organization_id: Filter by organization

    Returns:
        200: List of providers
    """
    try:
        service = get_google_workspace_service()

        organization_id = request.args.get("organization_id", type=int)

        providers = service.list_providers(organization_id=organization_id)

        return jsonify({"providers": providers, "count": len(providers)}), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/providers", methods=["POST"])
@login_required
@require_scope("discovery:admin")
@admin_required
async def create_provider():
    """
    Create Google Workspace provider.

    Request body:
        {
            "name": "Company Workspace",
            "organization_id": 1,
            "customer_id": "C01234567",
            "admin_email": "admin@company.com",
            "service_account_json": {...},
            "description": "Main Google Workspace"
        }

    Returns:
        201: Provider created
        400: Invalid request
    """
    try:
        data = await request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        required = [
            "name",
            "organization_id",
            "customer_id",
            "admin_email",
            "service_account_json",
        ]
        missing = [f for f in required if f not in data]
        if missing:
            return (
                jsonify({"error": f'Missing required fields: {", ".join(missing)}'}),
                400,
            )

        service = get_google_workspace_service()

        provider = service.create_provider(
            name=data["name"],
            organization_id=data["organization_id"],
            customer_id=data["customer_id"],
            admin_email=data["admin_email"],
            service_account_json=data["service_account_json"],
            description=data.get("description"),
        )

        return jsonify(provider), 201

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 400)


@bp.route("/providers/<int:provider_id>", methods=["GET"])
@login_required
@require_scope("discovery:read")
def get_provider(provider_id):
    """
    Get provider details.

    Returns:
        200: Provider details
        404: Provider not found
    """
    try:
        service = get_google_workspace_service()
        provider = service.get_provider(provider_id)
        return jsonify(provider), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/providers/<int:provider_id>", methods=["PUT"])
@admin_required
@require_scope("discovery:admin")
async def update_provider(provider_id):
    """
    Update provider configuration.

    Request body (all optional):
        {
            "name": "Updated name",
            "customer_id": "C01234567",
            "admin_email": "newadmin@company.com",
            "service_account_json": {...},
            "description": "Updated description",
            "enabled": false
        }

    Returns:
        200: Provider updated
        404: Provider not found
    """
    try:
        data = await request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        service = get_google_workspace_service()

        provider = service.update_provider(
            provider_id=provider_id,
            name=data.get("name"),
            customer_id=data.get("customer_id"),
            admin_email=data.get("admin_email"),
            service_account_json=data.get("service_account_json"),
            description=data.get("description"),
            enabled=data.get("enabled"),
        )

        return jsonify(provider), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 400)


@bp.route("/providers/<int:provider_id>", methods=["DELETE"])
@admin_required
@require_scope("discovery:admin")
def delete_provider(provider_id):
    """
    Delete provider.

    Returns:
        200: Provider deleted
        404: Provider not found
    """
    try:
        service = get_google_workspace_service()
        result = service.delete_provider(provider_id)
        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/providers/<int:provider_id>/test", methods=["POST"])
@login_required
@require_scope("discovery:write")
def test_provider(provider_id):
    """
    Test provider connectivity.

    Returns:
        200: Test result
        404: Provider not found
    """
    try:
        service = get_google_workspace_service()
        result = service.test_provider(provider_id)

        status_code = 200 if result.get("success") else 400
        return jsonify(result), status_code

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


# ===========================
# User Management Endpoints
# ===========================


@bp.route("/providers/<int:provider_id>/users", methods=["GET"])
@login_required
@require_scope("discovery:read")
def list_users(provider_id):
    """
    List Google Workspace users.

    Query params:
        - domain: Filter by domain
        - limit: Maximum results (default: 100)

    Returns:
        200: List of users
        404: Provider not found
    """
    try:
        service = get_google_workspace_service()

        domain = request.args.get("domain")
        limit = request.args.get("limit", 100, type=int)

        result = service.list_users(provider_id=provider_id, domain=domain, limit=limit)

        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/providers/<int:provider_id>/users/<path:user_key>", methods=["GET"])
@login_required
@require_scope("discovery:read")
def get_user(provider_id, user_key):
    """
    Get user details.

    Returns:
        200: User details
        404: Provider or user not found
    """
    try:
        service = get_google_workspace_service()
        user = service.get_user(provider_id, user_key)
        return jsonify(user), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/providers/<int:provider_id>/users", methods=["POST"])
@login_required
@require_scope("discovery:admin")
@admin_required
async def create_user(provider_id):
    """
    Create Google Workspace user.

    Request body:
        {
            "primary_email": "user@company.com",
            "given_name": "John",
            "family_name": "Doe",
            "password": "SecurePassword123!",
            "org_unit_path": "/"
        }

    Returns:
        201: User created
        400: Invalid request
        404: Provider not found
    """
    try:
        data = await request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        required = ["primary_email", "given_name", "family_name", "password"]
        missing = [f for f in required if f not in data]
        if missing:
            return (
                jsonify({"error": f'Missing required fields: {", ".join(missing)}'}),
                400,
            )

        service = get_google_workspace_service()

        user = service.create_user(
            provider_id=provider_id,
            primary_email=data["primary_email"],
            given_name=data["given_name"],
            family_name=data["family_name"],
            password=data["password"],
            org_unit_path=data.get("org_unit_path", "/"),
        )

        return jsonify(user), 201

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 400)


@bp.route("/providers/<int:provider_id>/users/<path:user_key>", methods=["PUT"])
@admin_required
@require_scope("discovery:admin")
async def update_user(provider_id, user_key):
    """
    Update user details.

    Request body (all optional):
        {
            "given_name": "John",
            "family_name": "Doe",
            "suspended": false,
            "org_unit_path": "/Engineering"
        }

    Returns:
        200: User updated
        404: Provider or user not found
    """
    try:
        data = await request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        service = get_google_workspace_service()

        user = service.update_user(
            provider_id=provider_id,
            user_key=user_key,
            given_name=data.get("given_name"),
            family_name=data.get("family_name"),
            suspended=data.get("suspended"),
            org_unit_path=data.get("org_unit_path"),
        )

        return jsonify(user), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 400)


@bp.route("/providers/<int:provider_id>/users/<path:user_key>", methods=["DELETE"])
@admin_required
@require_scope("discovery:admin")
def delete_user(provider_id, user_key):
    """
    Delete Google Workspace user.

    Returns:
        200: User deleted
        404: Provider or user not found
    """
    try:
        service = get_google_workspace_service()
        result = service.delete_user(provider_id, user_key)
        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


# ===========================
# Group Management Endpoints
# ===========================


@bp.route("/providers/<int:provider_id>/groups", methods=["GET"])
@login_required
@require_scope("discovery:read")
def list_groups(provider_id):
    """
    List Google Workspace groups.

    Query params:
        - domain: Filter by domain
        - limit: Maximum results (default: 100)

    Returns:
        200: List of groups
        404: Provider not found
    """
    try:
        service = get_google_workspace_service()

        domain = request.args.get("domain")
        limit = request.args.get("limit", 100, type=int)

        result = service.list_groups(
            provider_id=provider_id, domain=domain, limit=limit
        )

        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/providers/<int:provider_id>/groups/<path:group_key>", methods=["GET"])
@login_required
@require_scope("discovery:read")
def get_group(provider_id, group_key):
    """
    Get group details.

    Returns:
        200: Group details
        404: Provider or group not found
    """
    try:
        service = get_google_workspace_service()
        group = service.get_group(provider_id, group_key)
        return jsonify(group), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/providers/<int:provider_id>/groups", methods=["POST"])
@login_required
@require_scope("discovery:admin")
@admin_required
async def create_group(provider_id):
    """
    Create Google Workspace group.

    Request body:
        {
            "email": "team@company.com",
            "name": "Engineering Team",
            "description": "Engineering department group"
        }

    Returns:
        201: Group created
        400: Invalid request
        404: Provider not found
    """
    try:
        data = await request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        required = ["email", "name"]
        missing = [f for f in required if f not in data]
        if missing:
            return (
                jsonify({"error": f'Missing required fields: {", ".join(missing)}'}),
                400,
            )

        service = get_google_workspace_service()

        group = service.create_group(
            provider_id=provider_id,
            email=data["email"],
            name=data["name"],
            description=data.get("description"),
        )

        return jsonify(group), 201

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 400)


@bp.route("/providers/<int:provider_id>/groups/<path:group_key>", methods=["DELETE"])
@admin_required
@require_scope("discovery:admin")
def delete_group(provider_id, group_key):
    """
    Delete Google Workspace group.

    Returns:
        200: Group deleted
        404: Provider or group not found
    """
    try:
        service = get_google_workspace_service()
        result = service.delete_group(provider_id, group_key)
        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route(
    "/providers/<int:provider_id>/groups/<path:group_key>/members", methods=["GET"]
)
@login_required
@require_scope("discovery:read")
def list_group_members(provider_id, group_key):
    """
    List group members.

    Query params:
        - limit: Maximum results (default: 100)

    Returns:
        200: List of members
        404: Provider or group not found
    """
    try:
        service = get_google_workspace_service()

        limit = request.args.get("limit", 100, type=int)

        result = service.list_group_members(
            provider_id=provider_id, group_key=group_key, limit=limit
        )

        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route(
    "/providers/<int:provider_id>/groups/<path:group_key>/members", methods=["POST"]
)
@admin_required
@require_scope("discovery:admin")
async def add_group_member(provider_id, group_key):
    """
    Add member to group.

    Request body:
        {
            "member_email": "user@company.com",
            "role": "MEMBER"
        }

    Returns:
        201: Member added
        400: Invalid request
        404: Provider or group not found
    """
    try:
        data = await request.get_json()

        if not data or "member_email" not in data:
            return jsonify({"error": "member_email is required"}), 400

        service = get_google_workspace_service()

        member = service.add_group_member(
            provider_id=provider_id,
            group_key=group_key,
            member_email=data["member_email"],
            role=data.get("role", "MEMBER"),
        )

        return jsonify(member), 201

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 400)


@bp.route(
    "/providers/<int:provider_id>/groups/<path:group_key>/members/<path:member_email>",
    methods=["DELETE"],
)
@admin_required
@require_scope("discovery:admin")
def remove_group_member(provider_id, group_key, member_email):
    """
    Remove member from group.

    Returns:
        200: Member removed
        404: Provider, group, or member not found
    """
    try:
        service = get_google_workspace_service()

        result = service.remove_group_member(
            provider_id=provider_id, group_key=group_key, member_email=member_email
        )

        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)
