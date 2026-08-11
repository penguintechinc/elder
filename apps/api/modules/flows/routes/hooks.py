"""Flows webhook hooks — Handlers for GitHub and GitLab webhooks (public endpoints).

These endpoints are public (no auth required) and use signature-based validation.
Webhooks trigger automatic promotions for CI/CD pipeline orchestration.
SECURITY: Fail-closed validation — all signatures mandatory, no per-row feature flags.
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

bp = Blueprint("flows_hooks", __name__)


# ============================================================================
# Signature Verification Functions
# ============================================================================


def verify_github_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify GitHub webhook signature using HMAC-SHA256.

    GitHub sends the signature in the format: sha256=<hex_digest>

    Args:
        payload: Raw request body bytes
        signature: X-Hub-Signature-256 header value
        secret: Webhook secret from database

    Returns:
        True if signature is valid, False otherwise
    """
    if not signature or not signature.startswith("sha256="):
        return False

    expected_sig = signature[7:]  # Remove 'sha256=' prefix
    computed_sig = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()

    return hmac.compare_digest(expected_sig, computed_sig)


def verify_gitlab_token(token: str, secret: str) -> bool:
    """Verify GitLab webhook token.

    GitLab sends the token in the X-Gitlab-Token header.

    Args:
        token: X-Gitlab-Token header value
        secret: Webhook secret from database

    Returns:
        True if token matches secret, False otherwise
    """
    if not token or not secret:
        return False

    return hmac.compare_digest(token, secret)


# ============================================================================
# Webhook Triggers
# ============================================================================


@bp.route("/<webhook_id>", methods=["POST"])
async def trigger_webhook(webhook_id: str):
    """Trigger a pipeline promotion via webhook (public endpoint).

    This is a public endpoint - no authentication required.
    The webhook_id in the URL identifies the webhook.
    Uses uniform 404 for all mismatches (security best practice).
    Signature validation is MANDATORY (fail-closed).

    Args:
        webhook_id: Webhook identifier (looked up in iceflows_webhooks table)

    Returns:
        202: Promotion queued for processing
        404: Invalid webhook ID (also returned for disabled/missing pipeline)
        405: HTTP method not allowed (only POST)
        401: Signature validation failed
    """
    db = current_app.db

    # Capture all request context BEFORE threadpool
    method = request.method
    remote_addr = request.remote_addr or "unknown"
    request_body = await request.data  # bytes (async in Quart)
    signature_header = request.headers.get(
        "X-Hub-Signature-256"
    ) or request.headers.get("X-Signature-256")
    gitlab_token = request.headers.get("X-Gitlab-Token")

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

    def trigger(
        webhook_id,
        method,
        remote_addr,
        request_body,
        signature_header,
        gitlab_token,
        input_data,
    ):
        # Find webhook by ID
        # Uniform 404 for ALL of: unknown ID, disabled webhook, missing or disabled pipeline.
        webhook = db(db.iceflows_webhooks.webhook_id == webhook_id).select().first()
        if not webhook or not webhook.is_active:
            return None, None, 404, "Not found"

        # Get pipeline and tenant from webhook row
        pipeline = db(db.iceflows.id == webhook.flow_id).select().first()
        if not pipeline or not pipeline.is_enabled:
            return None, None, 404, "Not found"

        tenant_id = pipeline.tenant_id

        # Only POST allowed for git webhooks
        if method != "POST":
            return None, None, 405, "Method not allowed. Only POST accepted."

        # FAIL-CLOSED: Signature validation is MANDATORY (no per-row flag)
        # If no webhook_secret configured, reject (misconfiguration)
        if not webhook.webhook_secret:
            return None, None, 401, "Invalid signature"

        # Validate signature based on provider
        if webhook.provider == "gitlab":
            if not verify_gitlab_token(gitlab_token, webhook.webhook_secret):
                return None, None, 401, "Invalid signature"
        else:  # github (default provider)
            if not verify_github_signature(
                request_body, signature_header, webhook.webhook_secret
            ):
                return None, None, 401, "Invalid signature"

        # Add webhook metadata
        input_data["__webhook__"] = {
            "method": method,
            "remote_addr": remote_addr,
            "timestamp": datetime.now(UTC).isoformat(),
        }

        # Create promotion record
        promotion_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        # Get source and target stages
        source_stage = (
            db(
                (db.iceflows_stages.flow_id == webhook.flow_id)
                & (db.iceflows_stages.stage_order == webhook.trigger_stage_order)
            )
            .select()
            .first()
        )

        if not source_stage:
            return None, None, 404, "Not found"

        target_stage = (
            db(
                (db.iceflows_stages.flow_id == webhook.flow_id)
                & (db.iceflows_stages.stage_order == webhook.trigger_stage_order + 1)
            )
            .select()
            .first()
        )

        if not target_stage:
            return None, None, 404, "Not found"

        db.iceflows_promotions.insert(
            tenant_id=tenant_id,
            promotion_id=promotion_id,
            flow_id=webhook.flow_id,
            source_stage_id=source_stage.id,
            target_stage_id=target_stage.id,
            status="pending",
            requested_by_identity_id=None,  # No user - triggered by webhook
            commit_sha=input_data.get("commit_sha", ""),
            created_at=now,
            updated_at=now,
        )
        db.commit()

        # Update webhook stats
        db(db.iceflows_webhooks.id == webhook.id).update(
            last_triggered_at=now,
            trigger_count=(webhook.trigger_count or 0) + 1,
        )
        db.commit()

        return {"promotion_id": promotion_id}, tenant_id, 202, "Accepted"

    result = await run_in_threadpool(
        trigger,
        webhook_id,
        method,
        remote_addr,
        request_body,
        signature_header,
        gitlab_token,
        input_data,
    )

    if result[1] is None:
        # Error case: result[2] is the status code
        if result[2] == 404:
            return ApiResponse.error("Not found", 404)
        elif result[2] == 405:
            return ApiResponse.error(result[3], 405)
        elif result[2] == 401:
            return ApiResponse.error("Invalid signature", 401)

    # Success case: result[0] is data, result[1] is tenant_id, result[2] is status code
    return ApiResponse.success(result[0]), result[2]
