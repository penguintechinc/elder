"""REST API endpoints for built-in secrets management."""

# flake8: noqa: E501

import logging

from quart import Blueprint, current_app, jsonify, request
from quart_schema import validate_response

from apps.api.auth.decorators import login_required, require_scope
from apps.api.logging_config import log_error_and_respond
from apps.api.models.pydantic.secrets import (
    DeleteSecretResponse,
    SecretConnectionTestResponse,
    SecretMetadataListResponse,
    SecretMetadataResponse,
    SecretValueResponse,
)
from apps.api.services.secrets import BuiltinSecretsClient
from apps.api.services.secrets.tenancy import org_in_tenant, tenant_org_ids
from apps.api.utils.tenant_scoping import get_current_tenant_id

logger = logging.getLogger(__name__)

bp = Blueprint("builtin_secrets", __name__, url_prefix="/api/v1/builtin-secrets")


def _scoped_org(organization_id):
    """Check a caller-supplied organization id against the verified tenant.

    Returns ``(org_id, None)`` when the organization belongs to the tenant in
    the caller's token, otherwise ``(None, error_response)``: 403 without a
    tenant, 400 when the id is missing, 404 for foreign or unknown orgs.
    """
    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return None, (jsonify({"error": "Tenant context required"}), 403)
    if not organization_id:
        return None, (jsonify({"error": "organization_id parameter required"}), 400)
    if not org_in_tenant(current_app.db, organization_id, tenant_id):
        return None, (jsonify({"error": "Organization not found"}), 404)
    return int(organization_id), None


@bp.route("", methods=["GET"])
@login_required
@require_scope("secrets:read")
@validate_response(SecretMetadataListResponse)
def list_secrets():
    """List built-in secrets for an organization."""
    try:
        prefix = request.args.get("prefix")
        organization_id, error = _scoped_org(
            request.args.get("organization_id", type=int)
        )
        if error:
            return error

        config = {"organization_id": organization_id}
        client = BuiltinSecretsClient(config)

        secrets = client.list_secrets(prefix=prefix)

        return SecretMetadataListResponse(
            secrets=[SecretMetadataResponse.model_validate(s) for s in secrets]
        ), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/<path:secret_path>", methods=["GET"])
@login_required
@require_scope("secrets:read")
@validate_response(SecretValueResponse)
def get_secret(secret_path):
    """Get a built-in secret by path."""
    try:
        organization_id, error = _scoped_org(
            request.args.get("organization_id", type=int)
        )
        if error:
            return error

        config = {"organization_id": organization_id}
        client = BuiltinSecretsClient(config)

        secret = client.get_secret(secret_path)

        # Return masked by default
        return SecretValueResponse.model_validate(secret), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("", methods=["POST"])
@login_required
@require_scope("secrets:write")
@validate_response(SecretMetadataResponse, status_code=201)
async def create_secret():
    """Create a new built-in secret."""
    try:
        data = await request.get_json()

        if not data or not isinstance(data, dict):
            return jsonify({"error": "Request body required"}), 400

        required_fields = ["name", "value", "organization_id"]
        missing = [f for f in required_fields if f not in data]
        if missing:
            return (
                jsonify({"error": f"Missing required fields: {', '.join(missing)}"}),
                400,
            )

        organization_id, error = _scoped_org(data["organization_id"])
        if error:
            return error

        config = {"organization_id": organization_id}
        client = BuiltinSecretsClient(config)

        metadata = {
            "description": data.get("description", ""),
            "secret_type": data.get("secret_type", "password"),
            "tags": data.get("tags", []),
            "expires_at": data.get("expires_at"),
        }

        secret_metadata = client.create_secret(
            path=data["name"],
            value=data["value"],
            metadata=metadata,
        )

        return SecretMetadataResponse.model_validate(secret_metadata), 201

    except Exception as e:
        logger.error(f"Create built-in secret error: {str(e)}")
        if "already exists" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 409)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/<path:secret_path>", methods=["PUT", "PATCH"])
@login_required
@require_scope("secrets:write")
@validate_response(SecretMetadataResponse)
async def update_secret(secret_path):
    """Update a built-in secret."""
    try:
        data = await request.get_json()

        if not data or not isinstance(data, dict):
            return jsonify({"error": "Request body required"}), 400

        organization_id, error = _scoped_org(
            request.args.get("organization_id", type=int)
        )
        if error:
            return error

        if "value" not in data:
            return jsonify({"error": "Missing required field: value"}), 400

        config = {"organization_id": organization_id}
        client = BuiltinSecretsClient(config)

        secret_metadata = client.update_secret(
            path=secret_path,
            value=data["value"],
        )

        return SecretMetadataResponse.model_validate(secret_metadata), 200

    except Exception as e:
        logger.error(f"Update built-in secret error: {str(e)}")
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/<path:secret_path>", methods=["DELETE"])
@login_required
@require_scope("secrets:write")
@validate_response(DeleteSecretResponse)
def delete_secret(secret_path):
    """Delete a built-in secret."""
    try:
        organization_id, error = _scoped_org(
            request.args.get("organization_id", type=int)
        )
        if error:
            return error

        config = {"organization_id": organization_id}
        client = BuiltinSecretsClient(config)

        result = client.delete_secret(path=secret_path, force=False)

        return DeleteSecretResponse(success=result, path=secret_path), 200

    except Exception as e:
        logger.error(f"Delete built-in secret error: {str(e)}")
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/test-connection", methods=["POST"])
@login_required
@require_scope("secrets:admin")
@validate_response(SecretConnectionTestResponse)
async def test_connection():
    """Test built-in secrets database connection."""
    try:
        data = await request.get_json() or {}
        if not isinstance(data, dict):
            return jsonify({"error": "JSON object body required"}), 400
        requested = data.get("organization_id")
        if requested is None:
            tenant_id = get_current_tenant_id()
            if not tenant_id:
                return jsonify({"error": "Tenant context required"}), 403
            owned = tenant_org_ids(current_app.db, tenant_id)
            if not owned:
                return jsonify({"error": "Organization not found"}), 404
            requested = owned[0]
        organization_id, error = _scoped_org(requested)
        if error:
            return error

        config = {"organization_id": organization_id}
        client = BuiltinSecretsClient(config)

        success = client.test_connection()

        return SecretConnectionTestResponse(success=success, provider="builtin"), 200

    except Exception as e:
        logger.error(f"Test built-in secrets connection error: {str(e)}")
        return jsonify({"error": str(e), "success": False}), 500
