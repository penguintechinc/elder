"""IAM Management API endpoints for Elder v1.2.0 (Phase 4)."""

# flake8: noqa: E501


import logging

from quart import Blueprint, current_app, jsonify, request

from apps.api.auth.decorators import admin_required, login_required
from apps.api.logging_config import log_error_and_respond
from apps.api.services.iam import IAMService

logger = logging.getLogger(__name__)

bp = Blueprint("iam", __name__)


def get_iam_service():
    """Get IAMService instance with current database."""
    return IAMService(current_app.db)


# Provider Management Endpoints


@bp.route("/providers", methods=["GET"])
@login_required
def list_providers():
    """
    List all IAM providers.

    Query params:
        - enabled_only: Only return enabled providers (default: false)

    Returns:
        200: List of providers
    """
    try:
        service = get_iam_service()
        enabled_only = request.args.get("enabled_only", "false").lower() == "true"

        providers = service.list_providers(enabled_only=enabled_only)

        return jsonify({"providers": providers, "count": len(providers)}), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/providers/<int:provider_id>", methods=["GET"])
@login_required
def get_provider(provider_id):
    """
    Get a specific provider details.

    Returns:
        200: Provider details
        404: Provider not found
    """
    try:
        service = get_iam_service()
        provider = service.get_provider(provider_id)
        return jsonify(provider), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/providers", methods=["POST"])
@login_required
@admin_required
def create_provider():
    """
    Create a new IAM provider.

    Request body:
        {
            "name": "AWS Production IAM",
            "provider_type": "aws_iam",  // aws_iam, gcp_iam, kubernetes
            "description": "Production AWS IAM integration",
            "config": {
                "region": "us-east-1",
                "access_key_id": "AKIA...",
                "secret_access_key": "..."
            }
        }

    Returns:
        201: Provider created
        400: Invalid request
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        required = ["name", "provider_type", "config"]
        missing = [f for f in required if f not in data]
        if missing:
            return (
                jsonify({"error": f'Missing required fields: {", ".join(missing)}'}),
                400,
            )

        service = get_iam_service()
        provider = service.create_provider(
            name=data["name"],
            provider_type=data["provider_type"],
            config=data["config"],
            description=data.get("description"),
        )

        return jsonify(provider), 201

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 400)


@bp.route("/providers/<int:provider_id>", methods=["PUT"])
@admin_required
def update_provider(provider_id):
    """
    Update provider configuration.

    Request body (all optional):
        {
            "name": "Updated Name",
            "config": {...},
            "description": "Updated description",
            "enabled": true
        }

    Returns:
        200: Provider updated
        404: Provider not found
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        service = get_iam_service()
        provider = service.update_provider(
            provider_id=provider_id,
            name=data.get("name"),
            config=data.get("config"),
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
def delete_provider(provider_id):
    """
    Delete an IAM provider.

    Returns:
        200: Provider deleted
        404: Provider not found
        409: Provider has resources
    """
    try:
        service = get_iam_service()
        result = service.delete_provider(provider_id)
        return jsonify(result), 200

    except Exception as e:
        error_msg = str(e).lower()
        if "not found" in error_msg:
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        if "cannot delete" in error_msg:
            return log_error_and_respond(logger, e, "Failed to process request", 409)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/providers/<int:provider_id>/test", methods=["POST"])
@login_required
def test_provider(provider_id):
    """
    Test provider connectivity.

    Returns:
        200: Test result
        404: Provider not found
    """
    try:
        service = get_iam_service()
        result = service.test_provider(provider_id)
        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/providers/<int:provider_id>/sync", methods=["POST"])
@admin_required
def sync_provider(provider_id):
    """
    Sync resources from provider to database.

    Returns:
        200: Sync result
        404: Provider not found
    """
    try:
        service = get_iam_service()
        result = service.sync_provider(provider_id)
        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


# User Management Endpoints


@bp.route("/providers/<int:provider_id>/users", methods=["GET"])
@login_required
def list_users(provider_id):
    """
    List users from provider.

    Query params:
        - limit: Maximum number of results
        - next_token: Pagination token

    Returns:
        200: List of users
        404: Provider not found
    """
    try:
        service = get_iam_service()

        kwargs = {}
        if request.args.get("limit"):
            kwargs["limit"] = int(request.args.get("limit"))
        if request.args.get("next_token"):
            kwargs["next_token"] = request.args.get("next_token")

        users = service.list_users(provider_id, **kwargs)
        return jsonify(users), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/providers/<int:provider_id>/users/<user_id>", methods=["GET"])
@login_required
def get_user(provider_id, user_id):
    """
    Get user details.

    Returns:
        200: User details
        404: Provider or user not found
    """
    try:
        service = get_iam_service()
        user = service.get_user(provider_id, user_id)
        return jsonify(user), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/providers/<int:provider_id>/users", methods=["POST"])
@login_required
@admin_required
def create_user(provider_id):
    """
    Create a new user.

    Request body:
        {
            "username": "john.doe",
            "display_name": "John Doe",
            "tags": {"department": "engineering"}
        }

    Returns:
        201: User created
        400: Invalid request
        404: Provider not found
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        if "username" not in data:
            return jsonify({"error": "Missing required field: username"}), 400

        service = get_iam_service()
        user = service.create_user(provider_id, **data)

        return jsonify(user), 201

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 400)


@bp.route("/providers/<int:provider_id>/users/<user_id>", methods=["PUT"])
@admin_required
def update_user(provider_id, user_id):
    """
    Update user.

    Request body:
        {
            "display_name": "John Doe Updated",
            "tags": {"department": "platform"}
        }

    Returns:
        200: User updated
        404: Provider or user not found
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        service = get_iam_service()
        user = service.update_user(provider_id, user_id, **data)

        return jsonify(user), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 400)


@bp.route("/providers/<int:provider_id>/users/<user_id>", methods=["DELETE"])
@admin_required
def delete_user(provider_id, user_id):
    """
    Delete user.

    Returns:
        200: User deleted
        404: Provider or user not found
    """
    try:
        service = get_iam_service()
        result = service.delete_user(provider_id, user_id)
        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


# Role Management Endpoints


@bp.route("/providers/<int:provider_id>/roles", methods=["GET"])
@login_required
def list_roles(provider_id):
    """
    List roles from provider.

    Query params:
        - limit: Maximum number of results
        - next_token: Pagination token

    Returns:
        200: List of roles
        404: Provider not found
    """
    try:
        service = get_iam_service()

        kwargs = {}
        if request.args.get("limit"):
            kwargs["limit"] = int(request.args.get("limit"))
        if request.args.get("next_token"):
            kwargs["next_token"] = request.args.get("next_token")

        roles = service.list_roles(provider_id, **kwargs)
        return jsonify(roles), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/providers/<int:provider_id>/roles/<role_id>", methods=["GET"])
@login_required
def get_role(provider_id, role_id):
    """
    Get role details.

    Returns:
        200: Role details
        404: Provider or role not found
    """
    try:
        service = get_iam_service()
        role = service.get_role(provider_id, role_id)
        return jsonify(role), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/providers/<int:provider_id>/roles", methods=["POST"])
@login_required
@admin_required
def create_role(provider_id):
    """
    Create a new role.

    Request body:
        {
            "role_name": "MyCustomRole",
            "description": "Custom role for app access",
            "trust_policy": {...},  // AWS: assume role policy, GCP: permissions, K8s: rules
            "tags": {"env": "prod"}
        }

    Returns:
        201: Role created
        400: Invalid request
        404: Provider not found
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        if "role_name" not in data:
            return jsonify({"error": "Missing required field: role_name"}), 400

        service = get_iam_service()
        role = service.create_role(provider_id, **data)

        return jsonify(role), 201

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 400)


@bp.route("/providers/<int:provider_id>/roles/<role_id>", methods=["PUT"])
@admin_required
def update_role(provider_id, role_id):
    """
    Update role.

    Request body:
        {
            "description": "Updated description",
            "tags": {"env": "staging"}
        }

    Returns:
        200: Role updated
        404: Provider or role not found
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        service = get_iam_service()
        role = service.update_role(provider_id, role_id, **data)

        return jsonify(role), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 400)


@bp.route("/providers/<int:provider_id>/roles/<role_id>", methods=["DELETE"])
@admin_required
def delete_role(provider_id, role_id):
    """
    Delete role.

    Returns:
        200: Role deleted
        404: Provider or role not found
    """
    try:
        service = get_iam_service()
        result = service.delete_role(provider_id, role_id)
        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


# Policy Management Endpoints


@bp.route("/providers/<int:provider_id>/policies", methods=["GET"])
@login_required
def list_policies(provider_id):
    """
    List policies from provider.

    Query params:
        - limit: Maximum number of results
        - next_token: Pagination token

    Returns:
        200: List of policies
        404: Provider not found
    """
    try:
        service = get_iam_service()

        kwargs = {}
        if request.args.get("limit"):
            kwargs["limit"] = int(request.args.get("limit"))
        if request.args.get("next_token"):
            kwargs["next_token"] = request.args.get("next_token")

        policies = service.list_policies(provider_id, **kwargs)
        return jsonify(policies), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/providers/<int:provider_id>/policies/<policy_id>", methods=["GET"])
@login_required
def get_policy(provider_id, policy_id):
    """
    Get policy details.

    Returns:
        200: Policy details
        404: Provider or policy not found
    """
    try:
        service = get_iam_service()
        policy = service.get_policy(provider_id, policy_id)
        return jsonify(policy), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/providers/<int:provider_id>/policies", methods=["POST"])
@login_required
@admin_required
def create_policy(provider_id):
    """
    Create a new policy.

    Request body:
        {
            "policy_name": "MyCustomPolicy",
            "policy_document": {...},  // Provider-specific policy document
            "description": "Custom policy for resource access",
            "tags": {"env": "prod"}
        }

    Returns:
        201: Policy created
        400: Invalid request
        404: Provider not found
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        required = ["policy_name", "policy_document"]
        missing = [f for f in required if f not in data]
        if missing:
            return (
                jsonify({"error": f'Missing required fields: {", ".join(missing)}'}),
                400,
            )

        service = get_iam_service()
        policy = service.create_policy(provider_id, **data)

        return jsonify(policy), 201

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 400)


@bp.route("/providers/<int:provider_id>/policies/<policy_id>", methods=["DELETE"])
@admin_required
def delete_policy(provider_id, policy_id):
    """
    Delete policy.

    Returns:
        200: Policy deleted
        404: Provider or policy not found
    """
    try:
        service = get_iam_service()
        result = service.delete_policy(provider_id, policy_id)
        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


# Policy Attachment Endpoints


@bp.route(
    "/providers/<int:provider_id>/users/<user_id>/policies/<policy_id>",
    methods=["POST"],
)
@admin_required
def attach_policy_to_user(provider_id, user_id, policy_id):
    """
    Attach policy to user.

    Returns:
        200: Policy attached
        404: Provider, user, or policy not found
    """
    try:
        service = get_iam_service()
        result = service.attach_policy_to_user(provider_id, user_id, policy_id)
        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 400)


@bp.route(
    "/providers/<int:provider_id>/users/<user_id>/policies/<policy_id>",
    methods=["DELETE"],
)
@admin_required
def detach_policy_from_user(provider_id, user_id, policy_id):
    """
    Detach policy from user.

    Returns:
        200: Policy detached
        404: Provider, user, or policy not found
    """
    try:
        service = get_iam_service()
        result = service.detach_policy_from_user(provider_id, user_id, policy_id)
        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 400)


@bp.route(
    "/providers/<int:provider_id>/roles/<role_id>/policies/<policy_id>",
    methods=["POST"],
)
@admin_required
def attach_policy_to_role(provider_id, role_id, policy_id):
    """
    Attach policy to role.

    Returns:
        200: Policy attached
        404: Provider, role, or policy not found
    """
    try:
        service = get_iam_service()
        result = service.attach_policy_to_role(provider_id, role_id, policy_id)
        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 400)


@bp.route(
    "/providers/<int:provider_id>/roles/<role_id>/policies/<policy_id>",
    methods=["DELETE"],
)
@admin_required
def detach_policy_from_role(provider_id, role_id, policy_id):
    """
    Detach policy from role.

    Returns:
        200: Policy detached
        404: Provider, role, or policy not found
    """
    try:
        service = get_iam_service()
        result = service.detach_policy_from_role(provider_id, role_id, policy_id)
        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 400)


@bp.route("/providers/<int:provider_id>/users/<user_id>/policies", methods=["GET"])
@login_required
def list_user_policies(provider_id, user_id):
    """
    List policies attached to user.

    Returns:
        200: List of policies
        404: Provider or user not found
    """
    try:
        service = get_iam_service()
        policies = service.list_user_policies(provider_id, user_id)
        return jsonify(policies), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/providers/<int:provider_id>/roles/<role_id>/policies", methods=["GET"])
@login_required
def list_role_policies(provider_id, role_id):
    """
    List policies attached to role.

    Returns:
        200: List of policies
        404: Provider or role not found
    """
    try:
        service = get_iam_service()
        policies = service.list_role_policies(provider_id, role_id)
        return jsonify(policies), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


# Access Key Management Endpoints


@bp.route("/providers/<int:provider_id>/users/<user_id>/access-keys", methods=["POST"])
@login_required
@admin_required
def create_access_key(provider_id, user_id):
    """
    Create access key for user.

    Returns:
        201: Access key created
        404: Provider or user not found
    """
    try:
        service = get_iam_service()
        key = service.create_access_key(provider_id, user_id)
        return jsonify(key), 201

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 400)


@bp.route("/providers/<int:provider_id>/users/<user_id>/access-keys", methods=["GET"])
@login_required
def list_access_keys(provider_id, user_id):
    """
    List access keys for user.

    Returns:
        200: List of access keys
        404: Provider or user not found
    """
    try:
        service = get_iam_service()
        keys = service.list_access_keys(provider_id, user_id)
        return jsonify(keys), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route(
    "/providers/<int:provider_id>/users/<user_id>/access-keys/<key_id>",
    methods=["DELETE"],
)
@admin_required
def delete_access_key(provider_id, user_id, key_id):
    """
    Delete access key.

    Returns:
        200: Access key deleted
        404: Provider, user, or key not found
    """
    try:
        service = get_iam_service()
        result = service.delete_access_key(provider_id, user_id, key_id)
        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


# Group Management Endpoints


@bp.route("/providers/<int:provider_id>/groups", methods=["GET"])
@login_required
def list_groups(provider_id):
    """
    List groups from provider.

    Query params:
        - limit: Maximum number of results
        - next_token: Pagination token

    Returns:
        200: List of groups
        404: Provider not found
    """
    try:
        service = get_iam_service()

        kwargs = {}
        if request.args.get("limit"):
            kwargs["limit"] = int(request.args.get("limit"))
        if request.args.get("next_token"):
            kwargs["next_token"] = request.args.get("next_token")

        groups = service.list_groups(provider_id, **kwargs)
        return jsonify(groups), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/providers/<int:provider_id>/groups", methods=["POST"])
@login_required
@admin_required
def create_group(provider_id):
    """
    Create a new group.

    Request body:
        {
            "group_name": "Developers",
            "description": "Developer group"
        }

    Returns:
        201: Group created
        400: Invalid request
        404: Provider not found
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        if "group_name" not in data:
            return jsonify({"error": "Missing required field: group_name"}), 400

        service = get_iam_service()
        group = service.create_group(provider_id, **data)

        return jsonify(group), 201

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 400)


@bp.route("/providers/<int:provider_id>/groups/<group_id>", methods=["DELETE"])
@admin_required
def delete_group(provider_id, group_id):
    """
    Delete group.

    Returns:
        200: Group deleted
        404: Provider or group not found
    """
    try:
        service = get_iam_service()
        result = service.delete_group(provider_id, group_id)
        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route(
    "/providers/<int:provider_id>/groups/<group_id>/users/<user_id>", methods=["POST"]
)
@admin_required
def add_user_to_group(provider_id, group_id, user_id):
    """
    Add user to group.

    Returns:
        200: User added to group
        404: Provider, group, or user not found
    """
    try:
        service = get_iam_service()
        result = service.add_user_to_group(provider_id, user_id, group_id)
        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 400)


@bp.route(
    "/providers/<int:provider_id>/groups/<group_id>/users/<user_id>", methods=["DELETE"]
)
@admin_required
def remove_user_from_group(provider_id, group_id, user_id):
    """
    Remove user from group.

    Returns:
        200: User removed from group
        404: Provider, group, or user not found
    """
    try:
        service = get_iam_service()
        result = service.remove_user_from_group(provider_id, user_id, group_id)
        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 400)
