"""Streams webhook hooks — Public endpoints for triggering stream executions.

These endpoints are public (no auth required) and use token-based validation.
The token in the URL identifies and authorizes the webhook trigger.
"""

import hashlib
import hmac
import logging
import uuid
from datetime import UTC, datetime, timezone

from quart import Blueprint, current_app, request

from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool

logger = logging.getLogger(__name__)

bp = Blueprint("stream_hooks", __name__)


def verify_hmac_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify HMAC-SHA256 signature.

    Args:
        payload: Request body bytes
        signature: Signature from header (sha256=...)
        secret: HMAC secret

    Returns:
        True if signature is valid
    """
    if not signature or not signature.startswith("sha256="):
        return False

    expected_sig = signature[7:]  # Remove 'sha256=' prefix
    computed_sig = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()

    return hmac.compare_digest(expected_sig, computed_sig)


# ============================================================================
# Webhook Triggers
# ============================================================================


@bp.route("/<token>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def trigger_webhook(token: str):
    """Trigger a stream execution via webhook.

    This is a public endpoint - no authentication required.
    The token in the URL identifies and authorizes the webhook.

    Args:
        token: Webhook token (looked up in stream_webhooks table)

    Returns:
        202: Execution queued for processing
        404: Invalid webhook token
        405: HTTP method not allowed
        403: Webhook disabled or signature invalid
    """
    db = current_app.db

    # Capture all request context BEFORE threadpool
    method = request.method
    remote_addr = request.remote_addr or "unknown"
    request_body = await request.data  # bytes (async in Quart)
    signature_header = request.headers.get(
        "X-Hub-Signature-256"
    ) or request.headers.get("X-Signature-256")

    # Get input data from request BEFORE threadpool (async op)
    input_data = {}
    try:
        if request.is_json:
            input_data = (await request.get_json()) or {}
        elif await request.form:
            input_data = dict(await request.form)
        elif request.args:
            input_data = dict(request.args)
    except Exception as e:
        logger.warning(f"Error parsing request data: {e}")

    def trigger(token, method, remote_addr, request_body, signature_header, input_data):
        # Find webhook by token (public lookup, safe because token is cryptographically secure)
        # Uniform 404 for ALL of: unknown token, disabled webhook, missing or
        # disabled playbook. Distinct codes/messages here would let an
        # unauthenticated caller distinguish a real-but-disabled token from a
        # bogus one (a token-enumeration oracle) and learn playbook state.
        webhook = db(db.stream_webhooks.token == token).select().first()
        if not webhook or not webhook.is_enabled:
            return None, None, 404, "Not found"

        # Get playbook and tenant from webhook row
        playbook = db(db.stream_playbooks.id == webhook.playbook_id).select().first()
        if not playbook or not playbook.is_enabled:
            return None, None, 404, "Not found"

        tenant_id = playbook.tenant_id

        # Check allowed methods
        allowed_methods = webhook.allowed_methods or ["POST"]
        if method not in allowed_methods:
            return (
                None,
                None,
                405,
                (f"Method {method} not allowed. Allowed: {', '.join(allowed_methods)}"),
            )

        # Validate signature if required — FAIL CLOSED. If validation is enabled
        # but the secret is missing (misconfiguration) we reject rather than
        # skip the check and accept an unsigned request.
        if webhook.validate_signature:
            if not webhook.signature_secret or not verify_hmac_signature(
                request_body, signature_header, webhook.signature_secret
            ):
                return None, None, 401, "Invalid signature"

        # Add webhook metadata
        input_data["__webhook__"] = {
            "method": method,
            "remote_addr": remote_addr,
            "timestamp": datetime.now(UTC).isoformat(),
        }

        # Create execution record
        execution_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        db.stream_executions.insert(
            tenant_id=tenant_id,
            playbook_id=webhook.playbook_id,
            execution_id=execution_id,
            status="queued",
            trigger_type="webhook",
            triggered_by_identity_id=None,  # No user - triggered by webhook
            input_json=input_data,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        # Update webhook stats
        db(db.stream_webhooks.id == webhook.id).update(
            trigger_count=(webhook.trigger_count or 0) + 1,
            last_triggered_at=now,
            updated_at=now,
        )
        db.commit()

        logger.info(
            f"Webhook {webhook.token[:8]}... triggered playbook {webhook.playbook_id}, "
            f"execution {execution_id}"
        )

        return execution_id, webhook.id, 202, None

    execution_id, webhook_id, status_code, error_msg = await run_in_threadpool(
        trigger, token, method, remote_addr, request_body, signature_header, input_data
    )

    if status_code == 404:
        # Return 404 without revealing whether token or playbook is missing
        return ApiResponse.error("Not found", 404)
    elif status_code == 403:
        return ApiResponse.error(error_msg, 403)
    elif status_code == 405:
        return ApiResponse.error(error_msg, 405)
    elif status_code == 401:
        return ApiResponse.error(error_msg, 401)

    # Phase 4b-Streams-d: Enqueue job to Redis Streams job bus
    # (Enqueue failures do NOT fail the request — row exists for reconciliation)
    if execution_id and webhook_id:
        try:
            import redis.asyncio

            from apps.worker.config.settings import settings
            from shared.jobbus import JobBus

            if settings.redis_url:
                redis_client = redis.asyncio.from_url(settings.redis_url)
                jobbus = JobBus(redis_client)
                await jobbus.ensure_group("streams")

                # Get tenant_id from webhook playbook (set in trigger function)
                webhook_row = db(db.stream_webhooks.id == webhook_id).select().first()
                playbook_row = (
                    db(db.stream_playbooks.id == webhook_row.playbook_id)
                    .select()
                    .first()
                )
                tenant_id = playbook_row.tenant_id if playbook_row else None

                if tenant_id:
                    now = datetime.now(UTC)

                    await jobbus.enqueue(
                        "streams",
                        "execute_playbook",
                        {
                            "execution_id": execution_id,
                            "playbook_id": webhook_row.playbook_id,
                            "tenant_id": tenant_id,
                        },
                        enqueued_at=now.isoformat(),
                        tenant_id=tenant_id,
                        idempotency_key=execution_id,
                    )
                await redis_client.close()
                logger.info(
                    f"Webhook execution enqueued to job bus: execution_id={execution_id}"
                )
            else:
                logger.warning(
                    f"REDIS_URL not configured; webhook execution {execution_id} queued in DB but not enqueued to job bus"
                )
        except Exception as e:
            logger.warning(
                f"Failed to enqueue webhook execution {execution_id} to job bus; "
                f"worker will pick it up via reconcile: {e}"
            )

    # 202: Execution queued for processing (do NOT execute inline)
    return ApiResponse.success(
        data={
            "execution_id": execution_id,
            "status": "queued",
            "message": "Execution queued for processing",
        },
        status_code=202,
    )


@bp.route("/<token>/test", methods=["GET", "POST"])
async def test_webhook(token: str):
    """Test a webhook without triggering execution.

    Args:
        token: Webhook token

    Returns:
        200: Webhook info and validation result
        404: Invalid webhook token
    """
    db = current_app.db

    def test(token, method, content_type, request_body, signature_header):
        webhook = db(db.stream_webhooks.token == token).select().first()
        if not webhook:
            return None, None, 404

        playbook = db(db.stream_playbooks.id == webhook.playbook_id).select().first()

        # Validate signature if required — fail closed (missing secret or
        # missing/invalid signature => not valid).
        signature_valid = None
        if webhook.validate_signature:
            signature_valid = bool(
                webhook.signature_secret
                and signature_header
                and verify_hmac_signature(
                    request_body, signature_header, webhook.signature_secret
                )
            )

        return (
            {
                "webhook": {
                    "name": webhook.name,
                    "is_enabled": webhook.is_enabled,
                    "allowed_methods": webhook.allowed_methods or ["POST"],
                    "validate_signature": webhook.validate_signature,
                },
                "playbook": {
                    "id": playbook.id if playbook else None,
                    "name": playbook.name if playbook else None,
                    "is_enabled": playbook.is_enabled if playbook else None,
                },
                "request": {
                    "method": method,
                    "content_type": content_type,
                    "has_body": len(request_body) > 0,
                },
                "signature_valid": signature_valid,
            },
            None,
            200,
        )

    # Capture request context BEFORE threadpool
    method = request.method
    content_type = request.content_type or "none"
    request_body = await request.data  # bytes (async in Quart)
    signature_header = request.headers.get(
        "X-Hub-Signature-256"
    ) or request.headers.get("X-Signature-256")

    result, error, status_code = await run_in_threadpool(
        test, token, method, content_type, request_body, signature_header
    )

    if status_code == 404:
        return ApiResponse.error("Not found", 404)

    return ApiResponse.success(data=result)
