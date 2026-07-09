"""Keys Management API endpoints for Elder v1.2.0 (Phase 3)."""

# flake8: noqa: E501


import logging

from quart import Blueprint, current_app, jsonify, request

from apps.api.auth.decorators import login_required, require_scope
from apps.api.logging_config import log_error_and_respond
from apps.api.services.keys import KeysService

logger = logging.getLogger(__name__)

bp = Blueprint("keys", __name__)


def get_keys_service():
    """Get KeysService instance with current database."""
    return KeysService(current_app.db)


# Key Management Endpoints


@bp.route("", methods=["GET"])
@login_required
@require_scope("secrets:read")
def list_keys():
    """
    List all keys accessible by current user/organization.

    Query params:
        - provider_id: Filter by key provider
        - key_type: Filter by key type (symmetric, asymmetric, hmac)
        - enabled_only: Only return enabled keys (default: false)

    Returns:
        200: List of keys
    """
    try:
        service = get_keys_service()

        provider_id = request.args.get("provider_id", type=int)
        key_type = request.args.get("key_type")
        enabled_only = request.args.get("enabled_only", "false").lower() == "true"

        keys = service.list_keys(
            provider_id=provider_id, key_type=key_type, enabled_only=enabled_only
        )

        return jsonify({"keys": keys, "count": len(keys)}), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/<int:key_id>", methods=["GET"])
@login_required
@require_scope("secrets:read")
def get_key(key_id):
    """
    Get a specific key details.

    Returns:
        200: Key details
        404: Key not found
    """
    try:
        service = get_keys_service()
        key = service.get_key(key_id)
        return jsonify(key), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("", methods=["POST"])
@login_required
@require_scope("secrets:write")
async def create_key():
    """
    Create a new encryption key.

    Request body:
        {
            "provider_id": 1,
            "key_name": "encryption-key-prod",
            "key_type": "symmetric",  // symmetric, asymmetric, hmac
            "key_spec": "SYMMETRIC_DEFAULT",  // optional, provider-specific
            "description": "Production encryption key",
            "tags": {"env": "prod", "team": "security"}  // optional
        }

    Returns:
        201: Key created
        400: Invalid request
        404: Provider not found
    """
    try:
        data = await request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        required = ["provider_id", "key_name"]
        missing = [field for field in required if field not in data]
        if missing:
            return (
                jsonify({"error": f'Missing required fields: {", ".join(missing)}'}),
                400,
            )

        service = get_keys_service()

        key = service.create_key(
            provider_id=data["provider_id"],
            key_name=data["key_name"],
            key_type=data.get("key_type", "symmetric"),
            key_spec=data.get("key_spec"),
            description=data.get("description"),
            tags=data.get("tags"),
        )

        return jsonify(key), 201

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 400)


@bp.route("/<int:key_id>/enable", methods=["POST"])
@login_required
@require_scope("secrets:write")
def enable_key(key_id):
    """
    Enable a disabled key.

    Returns:
        200: Key enabled
        404: Key not found
    """
    try:
        service = get_keys_service()
        key = service.enable_key(key_id)
        return jsonify(key), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/<int:key_id>/disable", methods=["POST"])
@login_required
@require_scope("secrets:write")
def disable_key(key_id):
    """
    Disable a key.

    Returns:
        200: Key disabled
        404: Key not found
    """
    try:
        service = get_keys_service()
        key = service.disable_key(key_id)
        return jsonify(key), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/<int:key_id>/rotate", methods=["POST"])
@login_required
@require_scope("secrets:admin")
def rotate_key(key_id):
    """
    Rotate a key (enable automatic rotation or rotate immediately).

    Returns:
        200: Key rotation initiated
        404: Key not found
    """
    try:
        service = get_keys_service()
        result = service.rotate_key(key_id)
        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/<int:key_id>", methods=["DELETE"])
@login_required
@require_scope("secrets:write")
def delete_key(key_id):
    """
    Schedule key deletion.

    Query params:
        - pending_days: Days before deletion (default: 30)

    Returns:
        200: Key deletion scheduled
        404: Key not found
    """
    try:
        pending_days = request.args.get("pending_days", 30, type=int)

        service = get_keys_service()
        result = service.delete_key(key_id, pending_days)
        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


# Cryptographic Operations


@bp.route("/<int:key_id>/encrypt", methods=["POST"])
@login_required
@require_scope("secrets:write")
async def encrypt_data(key_id):
    """
    Encrypt data using this key.

    Request body:
        {
            "plaintext": "data to encrypt",
            "context": {"key": "value"}  // optional encryption context
        }

    Returns:
        200: Encrypted ciphertext
        404: Key not found
    """
    try:
        data = await request.get_json()

        if not data or "plaintext" not in data:
            return jsonify({"error": "plaintext field required"}), 400

        service = get_keys_service()
        result = service.encrypt(
            key_id=key_id, plaintext=data["plaintext"], context=data.get("context")
        )

        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/<int:key_id>/decrypt", methods=["POST"])
@login_required
@require_scope("secrets:write")
async def decrypt_data(key_id):
    """
    Decrypt data using this key.

    Request body:
        {
            "ciphertext": "encrypted data",
            "context": {"key": "value"}  // optional, must match encryption context
        }

    Returns:
        200: Decrypted plaintext
        404: Key not found
    """
    try:
        data = await request.get_json()

        if not data or "ciphertext" not in data:
            return jsonify({"error": "ciphertext field required"}), 400

        service = get_keys_service()
        result = service.decrypt(
            key_id=key_id, ciphertext=data["ciphertext"], context=data.get("context")
        )

        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/<int:key_id>/generate-data-key", methods=["POST"])
@login_required
@require_scope("secrets:write")
async def generate_data_key(key_id):
    """
    Generate a data encryption key.

    Request body:
        {
            "key_spec": "AES_256",  // AES_256 or AES_128
            "context": {"key": "value"}  // optional encryption context
        }

    Returns:
        200: Data key (plaintext and encrypted)
        404: Key not found
    """
    try:
        data = await request.get_json() or {}

        service = get_keys_service()
        result = service.generate_data_key(
            key_id=key_id,
            key_spec=data.get("key_spec", "AES_256"),
            context=data.get("context"),
        )

        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/<int:key_id>/sign", methods=["POST"])
@login_required
@require_scope("secrets:write")
async def sign_data(key_id):
    """
    Sign data using this key (asymmetric keys only).

    Request body:
        {
            "message": "data to sign",
            "signing_algorithm": "RSASSA_PSS_SHA_256"  // optional
        }

    Returns:
        200: Signature
        404: Key not found
        400: Key not asymmetric
    """
    try:
        data = await request.get_json()

        if not data or "message" not in data:
            return jsonify({"error": "message field required"}), 400

        service = get_keys_service()
        result = service.sign(
            key_id=key_id,
            message=data["message"],
            signing_algorithm=data.get("signing_algorithm", "RSASSA_PSS_SHA_256"),
        )

        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        if "must be asymmetric" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 400)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/<int:key_id>/verify", methods=["POST"])
@login_required
@require_scope("secrets:write")
async def verify_signature(key_id):
    """
    Verify a message signature (asymmetric keys only).

    Request body:
        {
            "message": "original message",
            "signature": "base64-encoded signature",
            "signing_algorithm": "RSASSA_PSS_SHA_256"
        }

    Returns:
        200: Verification result
        404: Key not found
        400: Invalid request or key not asymmetric
    """
    try:
        data = await request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        required = ["message", "signature", "signing_algorithm"]
        missing = [field for field in required if field not in data]
        if missing:
            return (
                jsonify({"error": f'Missing required fields: {", ".join(missing)}'}),
                400,
            )

        service = get_keys_service()
        result = service.verify(
            key_id=key_id,
            message=data["message"],
            signature=data["signature"],
            signing_algorithm=data["signing_algorithm"],
        )

        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        if "must be asymmetric" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 400)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/<int:key_id>/access-log", methods=["GET"])
@login_required
@require_scope("secrets:read")
def get_key_access_log(key_id):
    """
    Get access log for a key.

    Query params:
        - limit: Maximum number of records (default: 100)
        - offset: Pagination offset (default: 0)

    Returns:
        200: Access log entries
        404: Key not found
    """
    try:
        limit = request.args.get("limit", 100, type=int)
        offset = request.args.get("offset", 0, type=int)

        service = get_keys_service()
        logs = service.get_access_log(key_id, limit=limit, offset=offset)

        return (
            jsonify(
                {
                    "key_id": key_id,
                    "logs": logs,
                    "count": len(logs),
                    "limit": limit,
                    "offset": offset,
                }
            ),
            200,
        )

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


# Key Provider endpoints


@bp.route("/providers", methods=["GET"])
@login_required
@require_scope("secrets:read")
def list_key_providers():
    """
    List all key providers.

    Query params:
        - enabled_only: Only return enabled providers (default: false)

    Returns:
        200: List of key providers
    """
    try:
        enabled_only = request.args.get("enabled_only", "false").lower() == "true"

        service = get_keys_service()
        providers = service.list_providers(enabled_only=enabled_only)

        return jsonify({"providers": providers, "count": len(providers)}), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/providers", methods=["POST"])
@login_required
@require_scope("secrets:admin")
async def create_key_provider():
    """
    Register a new key provider.

    Request body:
        {
            "name": "AWS KMS - Production",
            "provider_type": "aws_kms",  // aws_kms, gcp_kms, infisical
            "config": {
                "region": "us-east-1",
                "access_key_id": "...",
                "secret_access_key": "..."
            },
            "description": "Production AWS KMS provider"  // optional
        }

    Returns:
        201: Provider created
        400: Invalid request
    """
    try:
        data = await request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        required = ["name", "provider_type", "config"]
        missing = [field for field in required if field not in data]
        if missing:
            return (
                jsonify({"error": f'Missing required fields: {", ".join(missing)}'}),
                400,
            )

        service = get_keys_service()

        provider = service.create_provider(
            name=data["name"],
            provider_type=data["provider_type"],
            config=data["config"],
            description=data.get("description"),
        )

        return jsonify(provider), 201

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 400)


@bp.route("/providers/<int:provider_id>", methods=["GET"])
@login_required
@require_scope("secrets:read")
def get_key_provider(provider_id):
    """
    Get key provider details.

    Returns:
        200: Provider details
        404: Provider not found
    """
    try:
        service = get_keys_service()
        provider = service.get_provider(provider_id)
        return jsonify(provider), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/providers/<int:provider_id>", methods=["PUT"])
@login_required
@require_scope("secrets:admin")
async def update_key_provider(provider_id):
    """
    Update key provider configuration.

    Request body:
        {
            "name": "New name",  // optional
            "config": {...},     // optional
            "description": "...",  // optional
            "enabled": true      // optional
        }

    Returns:
        200: Provider updated
        404: Provider not found
    """
    try:
        data = await request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        service = get_keys_service()

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
@login_required
@require_scope("secrets:admin")
def delete_key_provider(provider_id):
    """
    Delete key provider.

    Returns:
        200: Provider deleted
        404: Provider not found
        400: Provider has registered keys
    """
    try:
        service = get_keys_service()
        result = service.delete_provider(provider_id)
        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        if "cannot delete" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 400)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/providers/<int:provider_id>/test", methods=["POST"])
@login_required
@require_scope("secrets:admin")
def test_key_provider(provider_id):
    """
    Test provider connectivity.

    Returns:
        200: Test result
        404: Provider not found
    """
    try:
        service = get_keys_service()
        result = service.test_provider(provider_id)
        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)
