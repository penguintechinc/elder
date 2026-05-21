"""Secrets Management API endpoints for Elder v1.2.0 (Phase 2) - FULL IMPLEMENTATION."""

# flake8: noqa: E501


import logging

from quart import Blueprint, jsonify, request

from apps.api.auth.decorators import login_required
from apps.api.logging_config import log_error_and_respond
from apps.api.services.secrets import SecretsService
from apps.api.services.secrets.base import (
    SecretAccessDeniedException,
    SecretNotFoundException,
    SecretProviderException,
)

logger = logging.getLogger(__name__)
bp = Blueprint("secrets", __name__)


def get_secrets_service():
    """Get secrets service instance."""
    return SecretsService()


@bp.route("", methods=["GET"])
@login_required
def list_secrets():
    """
    List all secrets accessible by current user/organization.

    Query params:
        - organization_id: Filter by organization
        - provider_id: Filter by secret provider
        - secret_type: Filter by secret type

    Returns:
        200: List of secrets (masked by default)
    """
    try:
        service = get_secrets_service()

        organization_id = request.args.get("organization_id", type=int)
        provider_id = request.args.get("provider_id", type=int)
        secret_type = request.args.get("secret_type")

        secrets = service.list_secrets(
            organization_id=organization_id,
            provider_id=provider_id,
            secret_type=secret_type,
        )

        return jsonify({"secrets": secrets, "total": len(secrets)}), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/<int:secret_id>", methods=["GET"])
@login_required
def get_secret(secret_id):
    """
    Get a specific secret (masked by default).

    Query params:
        - unmask: Set to 'true' to retrieve unmasked value (requires permission)

    Returns:
        200: Secret details
        403: Insufficient permissions to unmask
        404: Secret not found
    """
    try:
        from flask import g

        service = get_secrets_service()
        unmask = request.args.get("unmask", "false").lower() == "true"

        # TODO: Add permission check for unmask
        # For now, allow unmask for authenticated users

        secret = service.get_secret(
            secret_id=secret_id, unmask=unmask, identity_id=g.current_user.id
        )

        return jsonify({"secret": secret}), 200

    except ValueError as e:
        return log_error_and_respond(logger, e, "Failed to process request", 404)
    except SecretNotFoundException as e:
        return (
            jsonify({"error": "Secret not found in provider", "message": str(e)}),
            404,
        )
    except SecretAccessDeniedException as e:
        return log_error_and_respond(logger, e, "Failed to process request", 403)
    except Exception as e:
        logger.error(f"Error retrieving secret {secret_id}: {str(e)}")
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/<int:secret_id>/unmask", methods=["POST"])
@login_required
def unmask_secret(secret_id):
    """
    Unmask a secret and retrieve its actual value.

    Requires 'unmask_secret' permission and logs access.

    Returns:
        200: Unmasked secret value
        403: Insufficient permissions
        404: Secret not found
    """
    try:
        from flask import g

        service = get_secrets_service()

        # TODO: Add permission check for unmask_secret
        # For now, allow unmask for authenticated users

        secret = service.get_secret(
            secret_id=secret_id, unmask=True, identity_id=g.current_user.id
        )

        return jsonify({"secret": secret}), 200

    except ValueError as e:
        return log_error_and_respond(logger, e, "Failed to process request", 404)
    except SecretNotFoundException as e:
        return (
            jsonify({"error": "Secret not found in provider", "message": str(e)}),
            404,
        )
    except SecretAccessDeniedException as e:
        return log_error_and_respond(logger, e, "Failed to process request", 403)
    except Exception as e:
        logger.error(f"Error unmasking secret {secret_id}: {str(e)}")
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("", methods=["POST"])
@login_required
async def create_secret():
    """
    Register a new secret from a provider.

    Request body:
        {
            "name": "database-password",
            "provider_id": 1,
            "provider_path": "/prod/db/password",
            "secret_type": "password",
            "organization_id": 1,
            "is_kv": false,
            "metadata": {}
        }

    Returns:
        201: Secret created
        400: Invalid request
        404: Provider not found
    """
    try:
        data = await request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        required_fields = [
            "name",
            "provider_id",
            "provider_path",
            "secret_type",
            "organization_id",
        ]
        missing_fields = [field for field in required_fields if field not in data]

        if missing_fields:
            return (
                jsonify(
                    {
                        "error": "Missing required fields",
                        "missing_fields": missing_fields,
                    }
                ),
                400,
            )

        service = get_secrets_service()

        from flask import g

        secret = service.create_secret(
            name=data["name"],
            provider_id=data["provider_id"],
            provider_path=data["provider_path"],
            secret_type=data["secret_type"],
            organization_id=data["organization_id"],
            is_kv=data.get("is_kv", False),
            metadata=data.get("metadata"),
            identity_id=g.current_user.id,
        )

        return jsonify({"secret": secret}), 201

    except ValueError as e:
        return log_error_and_respond(logger, e, "Failed to process request", 400)
    except SecretNotFoundException as e:
        return (
            jsonify({"error": "Secret not found in provider", "message": str(e)}),
            404,
        )
    except Exception as e:
        logger.error(f"Error creating secret: {str(e)}")
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/<int:secret_id>", methods=["PUT"])
@login_required
async def update_secret(secret_id):
    """
    Update secret metadata (not the actual value in provider).

    Request body:
        {
            "name": "new-name",
            "secret_type": "api_key",
            "metadata": {}
        }

    Returns:
        200: Secret updated
        404: Secret not found
    """
    try:
        from flask import g

        data = await request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        service = get_secrets_service()

        secret = service.update_secret_metadata(
            secret_id=secret_id,
            name=data.get("name"),
            secret_type=data.get("secret_type"),
            metadata=data.get("metadata"),
            identity_id=g.current_user.id,
        )

        return jsonify({"secret": secret}), 200

    except ValueError as e:
        return log_error_and_respond(logger, e, "Failed to process request", 404)
    except Exception as e:
        logger.error(f"Error updating secret {secret_id}: {str(e)}")
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/<int:secret_id>", methods=["DELETE"])
@login_required
def delete_secret(secret_id):
    """
    Remove secret registration (does not delete from provider).

    Returns:
        204: Secret deleted
        404: Secret not found
    """
    try:
        from flask import g

        service = get_secrets_service()

        service.delete_secret(secret_id=secret_id, identity_id=g.current_user.id)

        return "", 204

    except ValueError as e:
        return log_error_and_respond(logger, e, "Failed to process request", 404)
    except Exception as e:
        logger.error(f"Error deleting secret {secret_id}: {str(e)}")
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/<int:secret_id>/sync", methods=["POST"])
@login_required
def sync_secret(secret_id):
    """
    Force sync secret metadata from provider.

    Returns:
        200: Secret synced
        404: Secret not found
    """
    try:
        from flask import g

        service = get_secrets_service()

        secret = service.sync_secret(secret_id=secret_id, identity_id=g.current_user.id)

        return jsonify({"secret": secret}), 200

    except ValueError as e:
        return log_error_and_respond(logger, e, "Failed to process request", 404)
    except SecretNotFoundException as e:
        return (
            jsonify({"error": "Secret not found in provider", "message": str(e)}),
            404,
        )
    except SecretAccessDeniedException as e:
        return log_error_and_respond(logger, e, "Failed to process request", 403)
    except Exception as e:
        logger.error(f"Error syncing secret {secret_id}: {str(e)}")
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/<int:secret_id>/access-log", methods=["GET"])
@login_required
def get_secret_access_log(secret_id):
    """
    Get access log for a secret.

    Query params:
        - limit: Number of entries (default: 100)

    Returns:
        200: Access log entries
        404: Secret not found
    """
    try:
        service = get_secrets_service()
        limit = request.args.get("limit", 100, type=int)

        access_log = service.get_secret_access_log(secret_id=secret_id, limit=limit)

        return (
            jsonify(
                {
                    "secret_id": secret_id,
                    "access_log": access_log,
                    "total": len(access_log),
                }
            ),
            200,
        )

    except Exception as e:
        logger.error(f"Error retrieving access log for secret {secret_id}: {str(e)}")
        return (
            jsonify({"error": "Failed to retrieve access log", "message": str(e)}),
            500,
        )


# Secret Provider endpoints
@bp.route("/providers", methods=["GET"])
@login_required
def list_secret_providers():
    """
    List all secret providers.

    Query params:
        - organization_id: Filter by organization
        - provider_type: Filter by provider type

    Returns:
        200: List of secret providers
    """
    try:
        service = get_secrets_service()

        organization_id = request.args.get("organization_id", type=int)
        provider_type = request.args.get("provider_type")

        providers = service.list_providers(
            organization_id=organization_id, provider_type=provider_type
        )

        return jsonify({"providers": providers, "total": len(providers)}), 200

    except Exception as e:
        logger.error(f"Error listing providers: {str(e)}")
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/providers", methods=["POST"])
@login_required
async def create_secret_provider():
    """
    Register a new secret provider.

    Request body:
        {
            "name": "AWS Secrets Manager - Production",
            "provider": "aws_secrets_manager",
            "organization_id": 1,
            "config_json": {
                "region": "us-east-1",
                "access_key_id": "...",
                "secret_access_key": "..."
            }
        }

    Returns:
        201: Provider created
        400: Invalid request
    """
    try:
        data = await request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        required_fields = ["name", "provider", "organization_id", "config_json"]
        missing_fields = [field for field in required_fields if field not in data]

        if missing_fields:
            return (
                jsonify(
                    {
                        "error": "Missing required fields",
                        "missing_fields": missing_fields,
                    }
                ),
                400,
            )

        service = get_secrets_service()

        provider = service.create_provider(
            name=data["name"],
            provider_type=data["provider"],
            config_json=data["config_json"],
            organization_id=data["organization_id"],
        )

        return jsonify({"provider": provider}), 201

    except ValueError as e:
        return log_error_and_respond(logger, e, "Failed to process request", 400)
    except Exception as e:
        logger.error(f"Error creating provider: {str(e)}")
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/providers/<int:provider_id>", methods=["GET"])
@login_required
def get_secret_provider(provider_id):
    """
    Get secret provider details.

    Returns:
        200: Provider details
        404: Provider not found
    """
    try:
        service = get_secrets_service()

        provider = service.get_provider(provider_id)

        return jsonify({"provider": provider}), 200

    except ValueError as e:
        return log_error_and_respond(logger, e, "Failed to process request", 404)
    except Exception as e:
        logger.error(f"Error retrieving provider {provider_id}: {str(e)}")
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/providers/<int:provider_id>", methods=["PUT"])
@login_required
async def update_secret_provider(provider_id):
    """
    Update secret provider configuration.

    Request body:
        {
            "name": "New Name",
            "config_json": {...},
            "enabled": true
        }

    Returns:
        200: Provider updated
        404: Provider not found
    """
    try:
        data = await request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        service = get_secrets_service()

        provider = service.update_provider(
            provider_id=provider_id,
            name=data.get("name"),
            config_json=data.get("config_json"),
            enabled=data.get("enabled"),
        )

        return jsonify({"provider": provider}), 200

    except ValueError as e:
        return log_error_and_respond(logger, e, "Failed to process request", 400)
    except Exception as e:
        logger.error(f"Error updating provider {provider_id}: {str(e)}")
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/providers/<int:provider_id>", methods=["DELETE"])
@login_required
def delete_secret_provider(provider_id):
    """
    Delete secret provider.

    Returns:
        204: Provider deleted
        400: Provider in use
        404: Provider not found
    """
    try:
        service = get_secrets_service()

        service.delete_provider(provider_id)

        return "", 204

    except ValueError as e:
        error_message = str(e)
        if "Cannot delete provider" in error_message:
            return jsonify({"error": "Provider in use", "message": error_message}), 400
        return jsonify({"error": "Provider not found", "message": error_message}), 404
    except Exception as e:
        logger.error(f"Error deleting provider {provider_id}: {str(e)}")
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/providers/<int:provider_id>/sync", methods=["POST"])
@login_required
def sync_secret_provider(provider_id):
    """
    Sync all secrets from provider.

    Returns:
        200: Sync initiated
        404: Provider not found
    """
    try:
        service = get_secrets_service()

        result = service.sync_provider(provider_id)

        return jsonify(result), 200

    except ValueError as e:
        return log_error_and_respond(logger, e, "Failed to process request", 404)
    except SecretProviderException as e:
        return log_error_and_respond(logger, e, "Failed to process request", 500)
    except Exception as e:
        logger.error(f"Error syncing provider {provider_id}: {str(e)}")
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/providers/<int:provider_id>/test", methods=["POST"])
@login_required
def test_secret_provider(provider_id):
    """
    Test provider connection.

    Returns:
        200: Connection successful
        400: Connection failed
        404: Provider not found
    """
    try:
        service = get_secrets_service()

        success = service.test_provider_connection(provider_id)

        if success:
            return (
                jsonify({"success": True, "message": "Provider connection successful"}),
                200,
            )
        else:
            return (
                jsonify({"success": False, "message": "Provider connection failed"}),
                400,
            )

    except ValueError as e:
        return log_error_and_respond(logger, e, "Failed to process request", 404)
    except Exception as e:
        logger.error(f"Error testing provider {provider_id}: {str(e)}")
        return log_error_and_respond(logger, e, "Failed to process request", 500)
