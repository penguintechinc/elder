"""Webhook & Notification System API endpoints for Elder v1.2.0 (Phase 9)."""

# flake8: noqa: E501

import base64
import binascii
import hmac
import json
import logging
import secrets
from datetime import UTC, datetime
from typing import Any, Optional

from quart import Blueprint, current_app, g, jsonify, request
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text
from werkzeug.security import check_password_hash, generate_password_hash

from apps.api.auth.decorators import admin_required, login_required, require_scope
from apps.api.common.identity import identity_in_tenant
from apps.api.common.licensing.enforce import check_limit
from apps.api.logging_config import log_error_and_respond
from apps.api.services.webhooks import WebhookService
from apps.api.services.webhooks.inbound_actions import (
    INBOUND_WEBHOOK_SCOPES,
    execute_inbound_action,
    normalize_stored_scopes,
    resolve_inbound_action,
    validate_inbound_scopes,
)
from apps.api.services.webhooks.service import _UNSET
from apps.api.utils.tenant_scoping import get_current_tenant_id, get_tenant_scoped
from shared.webhooks.delivery import DeliveryShedError

logger = logging.getLogger(__name__)

bp = Blueprint("webhooks", __name__)


def _tenant_id() -> int | None:
    """Tenant id from validated JWT claims (populated by before_request).

    Duplicated locally rather than imported cross-module — matches the
    existing per-module convention (see intake_forms.py's _get_tenant_id,
    issues/routes/common.py's _tenant_id) rather than introducing a
    cross-module import for an 8-line helper.
    """
    claims = getattr(g, "claims", {}) or {}
    raw = claims.get("tenant", "")
    if not raw:
        return None
    try:
        return int(raw)
    except (ValueError, TypeError):
        return None


def _shed_response(exc: DeliveryShedError) -> Any:
    """Answer an explicit send refused by a pending-delivery cap with 429.

    ``Retry-After`` tells a well-behaved client when to try again; the body
    names only the bounded reason, never queue depths or other tenants.
    """
    response = jsonify(
        {
            "error": "Webhook delivery queue is full; retry later",
            "reason": exc.reason,
            "retry_after": exc.retry_after,
        }
    )
    response.status_code = 429
    response.headers["Retry-After"] = str(exc.retry_after)
    return response


def get_webhook_service():
    """Get WebhookService instance with current database."""
    return WebhookService(current_app.db)


def _org_unit_in_tenant(db: Any, org_unit_id: int | None, tenant_id: int) -> bool:
    """Return True if org_unit_id is unset or belongs to tenant_id.

    Org-unit counterpart to identity_in_tenant: guards a webhook's
    filter_assignee_id (filter_assignee_type="org_unit") against cross-tenant
    IDOR the same way issues/routes/issues.py::_org_unit_in_tenant guards an
    issue's assignee_id. A None id is treated as valid.
    """
    if org_unit_id is None:
        return True
    return (
        db(
            (db.organizations.id == org_unit_id)
            & (db.organizations.tenant_id == tenant_id)
        )
        .select()
        .first()
        is not None
    )


def _validate_filter_assignee_ref(
    db: Any,
    tenant_id: int,
    filter_assignee_type: str | None,
    filter_assignee_id: int | None,
) -> str | None:
    """Validate an EFFECTIVE (filter_assignee_type, filter_assignee_id) pair
    — i.e. the values that will actually be persisted after the request is
    applied — against tenant_id.

    Returns None if the pair is valid (including both unset). Returns an
    error message string if:
      - exactly one of the pair is set ("must be set together" — mirrors
        WebhookService.create_webhook's own `(type is None) != (id is None)`
        check. WebhookService.update_webhook does NOT enforce this pairing
        invariant on its own — see service.py's update_webhook, which only
        allow-lists the type value when one is explicitly given. The route
        is therefore the only place this is enforced on the update path,
        and callers MUST pass effective post-patch values, not raw
        request-body values, or a PUT that sets only one half of the pair
        while the other half already has a stored value defeats this check),
      - or a recognized type (identity/org_unit) is set but the id doesn't
        resolve to a row within tenant_id — the same cross-tenant IDOR guard
        applied to issues.assignee_id
        (issues/routes/issues.py::_resolve_assignee_type).

    An unrecognized filter_assignee_type with both fields set is left to
    WebhookService's own allow-list check (enforced on both create and
    update) rather than duplicated here.
    """
    if (filter_assignee_type is None) != (filter_assignee_id is None):
        return "filter_assignee_type and filter_assignee_id must be set together"

    if filter_assignee_id is None:
        return None

    if filter_assignee_type == "identity":
        if not identity_in_tenant(db, filter_assignee_id, tenant_id):
            return "filter_assignee_id not found in tenant"
        return None

    if filter_assignee_type == "org_unit":
        if not _org_unit_in_tenant(db, filter_assignee_id, tenant_id):
            return "filter_assignee_id not found in tenant"
        return None

    # Unrecognized filter_assignee_type — WebhookService's own allow-list
    # check rejects this (both create_webhook and update_webhook validate it
    # whenever a non-None type is passed through).
    return None


# ===========================
# Webhook Endpoints
# ===========================


@bp.route("", methods=["GET"])
@login_required
@require_scope("webhooks_alerting:read")
def list_webhooks():
    """
    List all webhooks for the caller's tenant.

    Query params:
        - enabled: Filter by active status

    Returns:
        200: List of webhooks
        403: Tenant not found
    """
    try:
        tenant_id = _tenant_id()
        if not tenant_id:
            return jsonify({"error": "Tenant not found"}), 403

        service = get_webhook_service()

        enabled = request.args.get("enabled")
        enabled_bool = None
        if enabled is not None:
            enabled_bool = enabled.lower() == "true"

        webhooks = service.list_webhooks(tenant_id=tenant_id, enabled=enabled_bool)

        return jsonify({"webhooks": webhooks, "count": len(webhooks)}), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("", methods=["POST"])
@login_required
@require_scope("webhooks_alerting:admin")
@admin_required
async def create_webhook():
    """
    Create a new webhook for the caller's tenant.

    Request body:
        {
            "name": "Support bot assignments",
            "url": "https://hooks.example.com/...",
            "events": ["issue.assigned"],
            "secret": "shared-secret-for-hmac",
            "organization_id": 1,
            "headers": {"X-Custom-Header": "value"},
            "filter_issue_type": "support",
            "filter_assignee_type": "identity",
            "filter_assignee_id": 42,
            "metadata": {"team": "support"}
        }

    Returns:
        201: Webhook created
        400: Invalid request
        403: Tenant not found
    """
    try:
        tenant_id = _tenant_id()
        if not tenant_id:
            return jsonify({"error": "Tenant not found"}), 403

        blocked = await check_limit("object", tenant_id)
        if blocked is not None:
            return blocked

        data = await request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        required = ["name", "url", "events"]
        missing = [f for f in required if f not in data]
        if missing:
            return (
                jsonify({"error": f"Missing required fields: {', '.join(missing)}"}),
                400,
            )

        redis_client = getattr(current_app, "redis_client", None)

        service = get_webhook_service()

        # Cross-tenant IDOR guard: organization_id is optional (a webhook
        # need not belong to an org), so an unset value stays valid per
        # _org_unit_in_tenant's normal semantics — only a *set* org id that
        # resolves outside the caller's tenant is rejected.
        organization_id = data.get("organization_id")
        if not _org_unit_in_tenant(service.db, organization_id, tenant_id):
            return jsonify({"error": "organization_id not found in tenant"}), 400

        filter_assignee_type = data.get("filter_assignee_type")
        filter_assignee_id = data.get("filter_assignee_id")
        assignee_error = _validate_filter_assignee_ref(
            service.db, tenant_id, filter_assignee_type, filter_assignee_id
        )
        if assignee_error:
            return jsonify({"error": assignee_error}), 400

        webhook = service.create_webhook(
            tenant_id=tenant_id,
            name=data["name"],
            url=data["url"],
            events=data["events"],
            redis_client=redis_client,
            organization_id=organization_id,
            secret=data.get("secret"),
            headers=data.get("headers"),
            filter_issue_type=data.get("filter_issue_type"),
            filter_assignee_type=filter_assignee_type,
            filter_assignee_id=filter_assignee_id,
            metadata=data.get("metadata"),
            is_active=data.get("is_active", True),
        )

        return jsonify(webhook), 201

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 400)


@bp.route("/<int:webhook_id>", methods=["GET"])
@login_required
@require_scope("webhooks_alerting:read")
def get_webhook(webhook_id):
    """
    Get webhook details (must belong to the caller's tenant).

    Returns:
        200: Webhook details
        403: Tenant not found
        404: Webhook not found
    """
    try:
        tenant_id = _tenant_id()
        if not tenant_id:
            return jsonify({"error": "Tenant not found"}), 403

        service = get_webhook_service()
        webhook = service.get_webhook(webhook_id, tenant_id)
        return jsonify(webhook), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/<int:webhook_id>", methods=["PUT"])
@admin_required
@require_scope("webhooks_alerting:admin")
async def update_webhook(webhook_id):
    """
    Update webhook configuration (must belong to the caller's tenant).

    Request body (all optional):
        {
            "name": "Updated name",
            "url": "https://new-url.com",
            "events": ["issue.assigned"],
            "secret": "new-secret",
            "headers": {"X-New-Header": "value"},
            "is_active": false,
            "filter_issue_type": "support",
            "filter_assignee_type": "org_unit",
            "filter_assignee_id": 7,
            "metadata": {"team": "support"}
        }

    Returns:
        200: Webhook updated
        403: Tenant not found
        404: Webhook not found
    """
    try:
        tenant_id = _tenant_id()
        if not tenant_id:
            return jsonify({"error": "Tenant not found"}), 403

        data = await request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        service = get_webhook_service()

        # Cross-tenant IDOR + pairing guard: only re-validate when this
        # update actually touches the assignee filter — falls back to the
        # existing row's value (via dict.get's default, which only applies
        # when the key is ABSENT, so an explicit `null` in the request body
        # is honored rather than silently replaced by the stored value) for
        # whichever half of the pair isn't part of this partial update
        # (mirrors helpdesk/routes/intake_forms.py::update_form). The
        # resulting (eff_type, eff_id) is the EFFECTIVE pair that will be
        # persisted, which _validate_filter_assignee_ref must see — passing
        # raw per-field request values here (instead of resolving against
        # `current`) would let a PUT that sets only `filter_assignee_id`
        # persist it against whatever `filter_assignee_type` the row
        # already had (including None), skipping the in-tenant check
        # entirely. WebhookService.update_webhook does NOT enforce this
        # pairing invariant itself (only create_webhook does), so the route
        # is the sole enforcement point on this path. get_webhook raises
        # "... not found" for a missing/cross-tenant webhook_id, which the
        # except block below already maps to 404.
        if "filter_assignee_type" in data or "filter_assignee_id" in data:
            current = service.get_webhook(webhook_id, tenant_id)
            eff_type = data.get("filter_assignee_type", current["filter_assignee_type"])
            eff_id = data.get("filter_assignee_id", current["filter_assignee_id"])
            assignee_error = _validate_filter_assignee_ref(
                service.db, tenant_id, eff_type, eff_id
            )
            if assignee_error:
                return jsonify({"error": assignee_error}), 400

        webhook = service.update_webhook(
            webhook_id=webhook_id,
            tenant_id=tenant_id,
            name=data.get("name"),
            url=data.get("url"),
            events=data.get("events"),
            secret=data.get("secret"),
            headers=data.get("headers"),
            is_active=data.get("is_active"),
            filter_issue_type=data.get("filter_issue_type", _UNSET),
            filter_assignee_type=data.get("filter_assignee_type", _UNSET),
            filter_assignee_id=data.get("filter_assignee_id", _UNSET),
            metadata=data.get("metadata"),
        )

        return jsonify(webhook), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 400)


@bp.route("/<int:webhook_id>", methods=["DELETE"])
@admin_required
@require_scope("webhooks_alerting:admin")
def delete_webhook(webhook_id):
    """
    Delete a webhook (must belong to the caller's tenant).

    Returns:
        200: Webhook deleted
        403: Tenant not found
        404: Webhook not found
    """
    try:
        tenant_id = _tenant_id()
        if not tenant_id:
            return jsonify({"error": "Tenant not found"}), 403

        service = get_webhook_service()
        result = service.delete_webhook(webhook_id, tenant_id)
        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/<int:webhook_id>/test", methods=["POST"])
@login_required
@require_scope("webhooks_alerting:write")
async def test_webhook(webhook_id):
    """
    Send a test event to webhook (must belong to the caller's tenant).

    Returns:
        200: Test sent successfully
        400: Test failed
        403: Tenant not found
        404: Webhook not found
        429: Delivery queue full (Retry-After header set)
    """
    try:
        tenant_id = _tenant_id()
        if not tenant_id:
            return jsonify({"error": "Tenant not found"}), 403

        service = get_webhook_service()
        result = await service.test_webhook(webhook_id, tenant_id)

        status_code = 200 if result.get("success") else 400
        return jsonify(result), status_code

    except DeliveryShedError as e:
        return _shed_response(e)

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/<int:webhook_id>/deliveries", methods=["GET"])
@login_required
@require_scope("webhooks_alerting:read")
def get_webhook_deliveries(webhook_id):
    """
    Get webhook delivery history (must belong to the caller's tenant).

    Query params:
        - limit: Number of deliveries (default: 50)
        - status: Filter by delivery status ("success" or "failed")

    Returns:
        200: Delivery history
        403: Tenant not found
        404: Webhook not found
    """
    try:
        tenant_id = _tenant_id()
        if not tenant_id:
            return jsonify({"error": "Tenant not found"}), 403

        service = get_webhook_service()

        limit = request.args.get("limit", 50, type=int)
        status = request.args.get("status")

        deliveries = service.get_webhook_deliveries(
            webhook_id=webhook_id, tenant_id=tenant_id, limit=limit, status=status
        )

        return jsonify({"deliveries": deliveries, "count": len(deliveries)}), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/<int:webhook_id>/deliveries/<int:delivery_id>/redeliver", methods=["POST"])
@admin_required
@require_scope("webhooks_alerting:admin")
async def redeliver_webhook(webhook_id, delivery_id):
    """
    Retry a failed webhook delivery (must belong to the caller's tenant).

    Returns:
        200: Redelivery initiated
        403: Tenant not found
        404: Webhook or delivery not found
        429: Delivery queue full (Retry-After header set)
    """
    try:
        tenant_id = _tenant_id()
        if not tenant_id:
            return jsonify({"error": "Tenant not found"}), 403

        service = get_webhook_service()
        result = await service.redeliver_webhook(webhook_id, tenant_id, delivery_id)

        status_code = 200 if result.get("success") else 400
        return jsonify(result), status_code

    except DeliveryShedError as e:
        return _shed_response(e)

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


# ===========================
# Notification Rule Endpoints
# ===========================


@bp.route("/notification-rules", methods=["GET"])
@login_required
@require_scope("webhooks_alerting:read")
def list_notification_rules():
    """
    List all notification rules scoped to caller's tenant.

    Query params:
        - organization_id: Filter by organization
        - channel: Filter by channel type

    Returns:
        200: List of notification rules
        403: Tenant not found
    """
    try:
        tenant_id = _tenant_id()
        if not tenant_id:
            return jsonify({"error": "Tenant not found"}), 403

        service = get_webhook_service()
        organization_id = request.args.get("organization_id", type=int)
        channel = request.args.get("channel")

        rules = service.list_notification_rules(
            tenant_id=tenant_id, organization_id=organization_id, channel=channel
        )

        return jsonify({"rules": rules, "count": len(rules)}), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/notification-rules", methods=["POST"])
@login_required
@require_scope("webhooks_alerting:admin")
@admin_required
async def create_notification_rule():
    """
    Create a new notification rule.

    Request body:
        {
            "name": "Alert on critical issues",
            "channel": "email",
            "events": ["issue.created"],
            "config": {
                "recipients": ["team@company.com"],
                "priority_filter": "critical"
            },
            "organization_id": 1,
            "description": "Send email alerts for critical issues"
        }

    Returns:
        201: Rule created
        400: Invalid request
        403: Tenant not found
        404: organization_id not found in tenant
    """
    try:
        tenant_id = _tenant_id()
        if not tenant_id:
            return jsonify({"error": "Tenant not found"}), 403

        blocked = await check_limit("object", tenant_id)
        if blocked is not None:
            return blocked

        data = await request.get_json(silent=True)

        if not data:
            return jsonify({"error": "Request body required"}), 400

        required = ["name", "channel", "events", "config", "organization_id"]
        missing = [f for f in required if f not in data]
        if missing:
            return (
                jsonify({"error": f"Missing required fields: {', '.join(missing)}"}),
                400,
            )

        organization_id = data["organization_id"]
        if (
            not isinstance(organization_id, int)
            or isinstance(organization_id, bool)
            or organization_id <= 0
        ):
            return jsonify({"error": "organization_id must be a positive integer"}), 400

        service = get_webhook_service()
        if not _org_unit_in_tenant(service.db, organization_id, tenant_id):
            return jsonify({"error": "organization_id not found in tenant"}), 404

        redis_client = getattr(current_app, "redis_client", None)
        rule = service.create_notification_rule(
            name=data["name"],
            channel=data["channel"],
            events=data["events"],
            config=data["config"],
            organization_id=organization_id,
            description=data.get("description"),
            tenant_id=tenant_id,
            redis_client=redis_client,
        )

        return jsonify(rule), 201

    except ValueError as e:
        return log_error_and_respond(
            logger, e, "Invalid notification rule request", 400
        )
    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/notification-rules/<int:rule_id>", methods=["GET"])
@login_required
@require_scope("webhooks_alerting:read")
def get_notification_rule(rule_id):
    """
    Get notification rule details, scoped to caller's tenant.

    Returns:
        200: Rule details
        403: Tenant not found
        404: Rule not found or cross-tenant
    """
    try:
        tenant_id = _tenant_id()
        if not tenant_id:
            return jsonify({"error": "Tenant not found"}), 403

        service = get_webhook_service()
        rule = service.get_notification_rule(rule_id, tenant_id=tenant_id)
        return jsonify(rule), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/notification-rules/<int:rule_id>", methods=["PUT"])
@admin_required
@require_scope("webhooks_alerting:admin")
async def update_notification_rule(rule_id):
    """
    Update notification rule.

    Request body (all optional):
        {
            "name": "Updated name",
            "events": ["issue.created", "issue.updated"],
            "config": {"recipients": ["new@company.com"]},
            "description": "Updated description",
            "enabled": false
        }

    Returns:
        200: Rule updated
        400: Invalid request
        403: Tenant not found
        404: Rule not found
    """
    try:
        tenant_id = _tenant_id()
        if not tenant_id:
            return jsonify({"error": "Tenant not found"}), 403

        data = await request.get_json(silent=True)

        if not data:
            return jsonify({"error": "Request body required"}), 400

        service = get_webhook_service()
        rule = service.update_notification_rule(
            rule_id=rule_id,
            name=data.get("name"),
            events=data.get("events"),
            config=data.get("config"),
            description=data.get("description"),
            enabled=data.get("enabled"),
            tenant_id=tenant_id,
        )

        return jsonify(rule), 200

    except ValueError as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(
            logger, e, "Invalid notification rule request", 400
        )
    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/notification-rules/<int:rule_id>", methods=["DELETE"])
@admin_required
@require_scope("webhooks_alerting:admin")
def delete_notification_rule(rule_id):
    """
    Delete notification rule.

    Returns:
        200: Rule deleted
        403: Tenant not found
        404: Rule not found
    """
    try:
        tenant_id = _tenant_id()
        if not tenant_id:
            return jsonify({"error": "Tenant not found"}), 403

        service = get_webhook_service()
        result = service.delete_notification_rule(rule_id, tenant_id=tenant_id)
        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/notification-rules/<int:rule_id>/test", methods=["POST"])
@login_required
@require_scope("webhooks_alerting:write")
async def test_notification_rule(rule_id):
    """
    Test a notification rule.

    Returns:
        200: Test notification sent
        400: Test failed
        403: Tenant not found
        404: Rule not found
        429: Delivery queue full (Retry-After header set)
    """
    try:
        tenant_id = _tenant_id()
        if not tenant_id:
            return jsonify({"error": "Tenant not found"}), 403

        service = get_webhook_service()
        result = await service.test_notification_rule(rule_id, tenant_id=tenant_id)

        status_code = 200 if result.get("success") else 400
        return jsonify(result), status_code

    except DeliveryShedError as e:
        return _shed_response(e)

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


# ===========================
# Alert Configuration Endpoints
# ===========================


@bp.route("/alert-configurations", methods=["GET"])
@login_required
@require_scope("webhooks_alerting:read")
def list_alert_configurations():
    """List alert configurations scoped to the caller's tenant."""
    try:
        tenant_id = _tenant_id()
        if not tenant_id:
            return jsonify({"error": "Tenant not found"}), 403

        organization_id = None
        if "organization_id" in request.args:
            organization_id = request.args.get("organization_id", type=int)
            if organization_id is None or organization_id <= 0:
                return (
                    jsonify({"error": "organization_id must be a positive integer"}),
                    400,
                )

        destination_type = request.args.get("destination_type")
        service = get_webhook_service()
        configs = service.list_alert_configurations(
            tenant_id=tenant_id,
            organization_id=organization_id,
            destination_type=destination_type,
        )
        return jsonify({"alert_configurations": configs, "count": len(configs)}), 200

    except ValueError as e:
        return log_error_and_respond(
            logger, e, "Invalid alert configuration query", 400
        )
    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/alert-configurations", methods=["POST"])
@login_required
@require_scope("webhooks_alerting:admin")
@admin_required
async def create_alert_configuration():
    """Create a new alert configuration with a universal village_id."""
    try:
        tenant_id = _tenant_id()
        if not tenant_id:
            return jsonify({"error": "Tenant not found"}), 403

        blocked = await check_limit("object", tenant_id)
        if blocked is not None:
            return blocked

        data = await request.get_json(silent=True)
        if not data or not isinstance(data, dict):
            return jsonify({"error": "Request body required"}), 400

        required = ["name", "destination_type", "config", "organization_id"]
        missing = [f for f in required if f not in data]
        if missing:
            return (
                jsonify({"error": f"Missing required fields: {', '.join(missing)}"}),
                400,
            )

        organization_id = data["organization_id"]
        if (
            not isinstance(organization_id, int)
            or isinstance(organization_id, bool)
            or organization_id <= 0
        ):
            return jsonify({"error": "organization_id must be a positive integer"}), 400

        service = get_webhook_service()
        if not _org_unit_in_tenant(service.db, organization_id, tenant_id):
            return jsonify({"error": "organization_id not found in tenant"}), 404

        redis_client = getattr(current_app, "redis_client", None)
        created = service.create_alert_configuration(
            tenant_id=tenant_id,
            organization_id=organization_id,
            destination_type=data["destination_type"],
            name=data["name"],
            config=data["config"],
            enabled=data.get("enabled", 1),
            severity_filter=data.get("severity_filter"),
            redis_client=redis_client,
        )
        return jsonify(created), 201

    except ValueError as e:
        return log_error_and_respond(
            logger, e, "Invalid alert configuration request", 400
        )
    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/alert-configurations/<int:config_id>", methods=["GET"])
@login_required
@require_scope("webhooks_alerting:read")
def get_alert_configuration(config_id):
    """Get a single alert configuration scoped to the caller's tenant."""
    try:
        tenant_id = _tenant_id()
        if not tenant_id:
            return jsonify({"error": "Tenant not found"}), 403

        service = get_webhook_service()
        config = service.get_alert_configuration(config_id, tenant_id=tenant_id)
        return jsonify(config), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/alert-configurations/<int:config_id>", methods=["PUT"])
@admin_required
@require_scope("webhooks_alerting:admin")
async def update_alert_configuration(config_id):
    """Update an alert configuration scoped to the caller's tenant."""
    try:
        tenant_id = _tenant_id()
        if not tenant_id:
            return jsonify({"error": "Tenant not found"}), 403

        data = await request.get_json(silent=True)
        if not data or not isinstance(data, dict):
            return jsonify({"error": "Request body required"}), 400

        severity_filter = (
            data["severity_filter"] if "severity_filter" in data else _UNSET
        )
        service = get_webhook_service()
        updated = service.update_alert_configuration(
            config_id=config_id,
            tenant_id=tenant_id,
            name=data.get("name"),
            enabled=data.get("enabled"),
            config=data.get("config"),
            severity_filter=severity_filter,
        )
        return jsonify(updated), 200

    except ValueError as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(
            logger, e, "Invalid alert configuration request", 400
        )
    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/alert-configurations/<int:config_id>", methods=["DELETE"])
@admin_required
@require_scope("webhooks_alerting:admin")
def delete_alert_configuration(config_id):
    """Delete an alert configuration scoped to the caller's tenant."""
    try:
        tenant_id = _tenant_id()
        if not tenant_id:
            return jsonify({"error": "Tenant not found"}), 403

        service = get_webhook_service()
        result = service.delete_alert_configuration(config_id, tenant_id=tenant_id)
        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


# ===========================
# Event Broadcasting Endpoint
# ===========================


@bp.route("/broadcast", methods=["POST"])
@admin_required
@require_scope("webhooks_alerting:admin")
async def broadcast_event():
    """
    Broadcast an event to all applicable webhooks and notification rules
    belonging to the caller's tenant.

    Request body:
        {
            "event_type": "entity.created",
            "payload": {...},
            "organization_id": 1
        }

    Returns:
        200: Event accepted; deliveries queued in the background
        400: Invalid request
        403: Tenant not found
        404: organization_id not found in tenant
        429: Delivery queue full (Retry-After header set)
    """
    try:
        tenant_id = _tenant_id()
        if not tenant_id:
            return jsonify({"error": "Tenant not found"}), 403

        data = await request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        required = ["event_type", "payload", "organization_id"]
        missing = [f for f in required if f not in data]
        if missing:
            return (
                jsonify({"error": f"Missing required fields: {', '.join(missing)}"}),
                400,
            )

        service = get_webhook_service()

        # Cross-tenant IDOR guard: organization_id is caller-supplied, so it
        # must be proven to belong to the caller's own tenant before it's
        # used to select which webhooks receive this payload. A `None`
        # organization_id is treated as invalid here (unlike
        # _org_unit_in_tenant's normal "unset filter" semantics elsewhere in
        # this file) because organization_id is a REQUIRED field on this
        # endpoint — `_org_unit_in_tenant(db, None, tenant_id)` returning
        # True would otherwise let an explicit JSON `null` bypass the check.
        organization_id = data["organization_id"]
        if organization_id is None or not _org_unit_in_tenant(
            service.db, organization_id, tenant_id
        ):
            return jsonify({"error": "organization_id not found in tenant"}), 404

        result = await service.broadcast_event(
            event_type=data["event_type"],
            payload=data["payload"],
            organization_id=organization_id,
            tenant_id=tenant_id,
        )

        return jsonify(result), 200

    except DeliveryShedError as e:
        return _shed_response(e)

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 400)


# ===========================
# Inbound (Receiving) Webhooks
# ===========================

_INBOUND_WEBHOOKS_SQLITE = """
CREATE TABLE IF NOT EXISTS inbound_webhooks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    village_id VARCHAR(32) UNIQUE,
    organization_id INTEGER,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    slug VARCHAR(64) NOT NULL UNIQUE,
    auth_type VARCHAR(32) NOT NULL DEFAULT 'bearer',
    bearer_token_hash VARCHAR(255),
    bearer_token_preview VARCHAR(32),
    basic_username VARCHAR(255),
    basic_password_hash VARCHAR(255),
    is_active BOOLEAN NOT NULL DEFAULT 1,
    stream_id INTEGER,
    scopes JSON,
    metadata JSON,
    last_received_at DATETIME,
    receive_count INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME,
    updated_at DATETIME
);
"""

_INBOUND_EVENTS_SQLITE = """
CREATE TABLE IF NOT EXISTS inbound_webhook_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inbound_webhook_id INTEGER NOT NULL,
    tenant_id INTEGER,
    source_ip VARCHAR(64),
    headers JSON,
    payload JSON,
    action VARCHAR(64),
    action_result JSON,
    status VARCHAR(32) NOT NULL DEFAULT 'received',
    received_at DATETIME NOT NULL
);
"""

_INBOUND_WEBHOOKS_PG = """
CREATE TABLE IF NOT EXISTS inbound_webhooks (
    id SERIAL PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id),
    village_id VARCHAR(32) UNIQUE,
    organization_id INTEGER REFERENCES organizations(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    slug VARCHAR(64) NOT NULL UNIQUE,
    auth_type VARCHAR(32) NOT NULL DEFAULT 'bearer',
    bearer_token_hash VARCHAR(255),
    bearer_token_preview VARCHAR(32),
    basic_username VARCHAR(255),
    basic_password_hash VARCHAR(255),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    stream_id INTEGER,
    scopes JSONB,
    metadata JSONB,
    last_received_at TIMESTAMPTZ,
    receive_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ
);
"""

_INBOUND_EVENTS_PG = """
CREATE TABLE IF NOT EXISTS inbound_webhook_events (
    id SERIAL PRIMARY KEY,
    inbound_webhook_id INTEGER NOT NULL REFERENCES inbound_webhooks(id) ON DELETE CASCADE,
    tenant_id INTEGER,
    source_ip VARCHAR(64),
    headers JSONB,
    payload JSONB,
    action VARCHAR(64),
    action_result JSONB,
    status VARCHAR(32) NOT NULL DEFAULT 'received',
    received_at TIMESTAMPTZ NOT NULL
);
"""


def _ensure_inbound_tables(db: Any) -> None:
    """Create or migrate inbound_webhooks and inbound_webhook_events (Postgres & SQLite)."""
    try:
        engine = db.engine
    except Exception:
        engine = None
    if engine is None:
        return

    inspector = sa_inspect(engine)
    has_webhooks = inspector.has_table("inbound_webhooks")
    has_events = inspector.has_table("inbound_webhook_events")
    is_sqlite = getattr(getattr(engine, "dialect", None), "name", "") == "sqlite"
    json_col_type = "JSON" if is_sqlite else "JSONB"
    altered = False

    with engine.begin() as conn:
        if not has_webhooks:
            conn.execute(
                text(_INBOUND_WEBHOOKS_SQLITE if is_sqlite else _INBOUND_WEBHOOKS_PG)
            )
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_inbound_webhooks_slug "
                    "ON inbound_webhooks (slug);"
                )
            )
        else:
            wh_cols = {c["name"] for c in inspector.get_columns("inbound_webhooks")}
            if "village_id" not in wh_cols:
                conn.execute(
                    text(
                        "ALTER TABLE inbound_webhooks ADD COLUMN village_id VARCHAR(32)"
                    )
                )
                conn.execute(
                    text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS ix_inbound_webhooks_village_id "
                        "ON inbound_webhooks (village_id);"
                    )
                )
                altered = True
            if "scopes" not in wh_cols:
                conn.execute(
                    # nosemgrep: python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text -- DDL with constant column type chosen by dialect; no request input
                    text(
                        f"ALTER TABLE inbound_webhooks ADD COLUMN scopes {json_col_type}"
                    )
                )
                altered = True

        if not has_events:
            conn.execute(
                text(_INBOUND_EVENTS_SQLITE if is_sqlite else _INBOUND_EVENTS_PG)
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_inbound_webhook_events_inbound_webhook_id "
                    "ON inbound_webhook_events (inbound_webhook_id);"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_inbound_webhook_events_tenant_id "
                    "ON inbound_webhook_events (tenant_id);"
                )
            )
        else:
            ev_cols = {
                c["name"] for c in inspector.get_columns("inbound_webhook_events")
            }
            if "action" not in ev_cols:
                conn.execute(
                    text(
                        "ALTER TABLE inbound_webhook_events ADD COLUMN action VARCHAR(64)"
                    )
                )
                altered = True
            if "action_result" not in ev_cols:
                conn.execute(
                    # nosemgrep: python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text -- DDL with constant column type chosen by dialect; no request input
                    text(
                        f"ALTER TABLE inbound_webhook_events ADD COLUMN action_result {json_col_type}"
                    )
                )
                altered = True

    metadata = getattr(db, "_metadata", None)
    if metadata is not None:
        to_reflect = [
            tbl
            for tbl in ("inbound_webhooks", "inbound_webhook_events")
            if altered or tbl not in metadata.tables
        ]
        if to_reflect:
            metadata.reflect(bind=engine, only=to_reflect, extend_existing=True)


def _iso_dt(val: Any) -> str | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)


def _parse_json_col(val: Any) -> Any:
    if isinstance(val, str):
        try:
            return json.loads(val)
        except ValueError:
            return val
    return val


def _serialize_inbound_webhook(row: Any) -> dict[str, Any]:
    """Serialize an inbound_webhooks row without ever exposing credential hashes."""
    slug = getattr(row, "slug", "")
    raw_meta = getattr(row, "webhook_metadata", None)
    if raw_meta is None:
        raw_meta = getattr(row, "metadata", None)
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "organization_id": getattr(row, "organization_id", None),
        "village_id": getattr(row, "village_id", None),
        "name": row.name,
        "description": getattr(row, "description", None),
        "slug": slug,
        "receive_path": f"/api/v1/webhooks/receive/{slug}",
        "auth_type": getattr(row, "auth_type", "bearer"),
        "bearer_token_preview": getattr(row, "bearer_token_preview", None),
        "basic_username": getattr(row, "basic_username", None),
        "is_active": bool(getattr(row, "is_active", True)),
        "stream_id": getattr(row, "stream_id", None),
        "scopes": normalize_stored_scopes(getattr(row, "scopes", None)),
        "metadata": _parse_json_col(raw_meta),
        "last_received_at": _iso_dt(getattr(row, "last_received_at", None)),
        "receive_count": int(getattr(row, "receive_count", 0) or 0),
        "created_at": _iso_dt(getattr(row, "created_at", None)),
        "updated_at": _iso_dt(getattr(row, "updated_at", None)),
    }


def _serialize_inbound_event(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "inbound_webhook_id": row.inbound_webhook_id,
        "tenant_id": row.tenant_id,
        "source_ip": getattr(row, "source_ip", None),
        "headers": _parse_json_col(getattr(row, "headers", None)),
        "payload": _parse_json_col(getattr(row, "payload", None)),
        "action": getattr(row, "action", None),
        "action_result": _parse_json_col(getattr(row, "action_result", None)),
        "status": getattr(row, "status", "received"),
        "received_at": _iso_dt(getattr(row, "received_at", None)),
    }


def _stream_in_tenant(db: Any, stream_id: int | None, tenant_id: int) -> bool:
    """Return True if stream_id is None or belongs to tenant_id."""
    if stream_id is None:
        return True
    if isinstance(stream_id, bool) or not isinstance(stream_id, int) or stream_id <= 0:
        return False
    engine = getattr(db, "engine", None)
    if engine is not None and not sa_inspect(engine).has_table("stream_playbooks"):
        return True
    return (
        db(
            (db.stream_playbooks.id == stream_id)
            & (db.stream_playbooks.tenant_id == tenant_id)
        )
        .select(db.stream_playbooks.id)
        .first()
        is not None
    )


def _build_inbound_credentials(
    auth_type: str,
    *,
    bearer_token: str | None = None,
    basic_username: str | None = None,
    basic_password: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Generate/hash required inbound webhook credentials.

    Returns (db_fields, one_time_credentials).
    """
    if auth_type == "bearer":
        raw_token = (bearer_token or "").strip() or ("wh_" + secrets.token_urlsafe(32))
        preview = (
            f"{raw_token[:6]}...{raw_token[-4:]}"
            if len(raw_token) > 10
            else f"{raw_token[:3]}..."
        )
        db_fields = {
            "bearer_token_hash": generate_password_hash(raw_token),
            "bearer_token_preview": preview[:32],
            "basic_username": None,
            "basic_password_hash": None,
        }
        creds = {
            "auth_type": "bearer",
            "bearer_token": raw_token,
        }
        return db_fields, creds

    username = (basic_username or "").strip() or ("wh_" + secrets.token_hex(6))
    raw_password = (basic_password or "").strip() or secrets.token_urlsafe(24)
    db_fields = {
        "bearer_token_hash": None,
        "bearer_token_preview": None,
        "basic_username": username,
        "basic_password_hash": generate_password_hash(raw_password),
    }
    creds = {
        "auth_type": "basic",
        "username": username,
        "password": raw_password,
    }
    return db_fields, creds


def _mint_inbound_village_id(tenant_id: int) -> str:
    from apps.api.utils.village_id import mint_village_id

    redis_client = getattr(current_app, "redis_client", None)
    db = getattr(current_app, "db", None)
    return mint_village_id(tenant_id, redis_client=redis_client, db=db)


@bp.route("/inbound/scopes", methods=["GET"])
@login_required
@require_scope("webhooks:read")
def list_inbound_webhook_scopes():
    """Return the catalog of available OAuth2-like scopes for inbound webhooks."""
    return jsonify({"scopes": INBOUND_WEBHOOK_SCOPES}), 200


@bp.route("/inbound", methods=["GET"])
@login_required
@require_scope("webhooks:read")
def list_inbound_webhooks():
    """List all inbound webhooks for the caller's tenant."""
    try:
        tenant_id = get_current_tenant_id()
        if not tenant_id:
            return jsonify({"error": "Tenant not found"}), 403

        db = current_app.db
        _ensure_inbound_tables(db)
        query = db.inbound_webhooks.tenant_id == tenant_id
        if request.args.get("stream_id") is not None:
            stream_id = request.args.get("stream_id", type=int)
            if stream_id is None or stream_id <= 0:
                return jsonify({"error": "stream_id must be a positive integer"}), 400
            query &= db.inbound_webhooks.stream_id == stream_id
        rows = db(query).select(orderby=~db.inbound_webhooks.id)
        return (
            jsonify(
                {
                    "inbound_webhooks": [_serialize_inbound_webhook(r) for r in rows],
                    "count": len(rows),
                }
            ),
            200,
        )
    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to list inbound webhooks", 500)


@bp.route("/inbound", methods=["POST"])
@admin_required
async def create_inbound_webhook():
    """Create a new inbound webhook with required Bearer/Basic auth and permission scopes."""
    try:
        tenant_id = get_current_tenant_id()
        if not tenant_id:
            return jsonify({"error": "Tenant not found"}), 403

        data = await request.get_json(silent=True) or {}
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name is required"}), 400

        auth_type = (data.get("auth_type") or "bearer").strip().lower()
        if auth_type not in ("bearer", "basic"):
            return jsonify({"error": "auth_type must be 'bearer' or 'basic'"}), 400

        stream_id = data.get("stream_id")
        scopes, scopes_err = validate_inbound_scopes(
            data.get("scopes"), has_stream_id=bool(stream_id)
        )
        if scopes_err:
            return jsonify({"error": scopes_err}), 400

        db = current_app.db
        _ensure_inbound_tables(db)

        organization_id = data.get("organization_id")
        if organization_id is not None and not _org_unit_in_tenant(
            db, organization_id, tenant_id
        ):
            return jsonify({"error": "organization_id not found in tenant"}), 404

        if not _stream_in_tenant(db, stream_id, tenant_id):
            return jsonify({"error": "stream_id not found in tenant"}), 404

        slug = secrets.token_urlsafe(18)
        db_cred_fields, credentials = _build_inbound_credentials(
            auth_type,
            bearer_token=data.get("bearer_token"),
            basic_username=data.get("basic_username"),
            basic_password=data.get("basic_password"),
        )

        now = datetime.now(UTC)
        record_id = db.inbound_webhooks.insert(
            tenant_id=tenant_id,
            village_id=_mint_inbound_village_id(tenant_id),
            organization_id=organization_id,
            name=name,
            description=data.get("description"),
            slug=slug,
            auth_type=auth_type,
            is_active=bool(data.get("is_active", True)),
            stream_id=stream_id,
            scopes=scopes,
            metadata=data.get("metadata"),
            receive_count=0,
            created_at=now,
            updated_at=now,
            **db_cred_fields,
        )
        db.commit()

        created = get_tenant_scoped(db, db.inbound_webhooks, record_id, tenant_id)
        return (
            jsonify(
                {
                    "webhook": _serialize_inbound_webhook(created),
                    "credentials": credentials,
                }
            ),
            201,
        )
    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to create inbound webhook", 400)


@bp.route("/inbound/<int:webhook_id>/rotate-credentials", methods=["POST"])
@admin_required
def rotate_inbound_webhook_credentials(webhook_id: int):
    """Regenerate credentials for an inbound webhook and return them once."""
    try:
        tenant_id = get_current_tenant_id()
        if not tenant_id:
            return jsonify({"error": "Tenant not found"}), 403

        db = current_app.db
        _ensure_inbound_tables(db)
        hook = get_tenant_scoped(db, db.inbound_webhooks, webhook_id, tenant_id)
        if not hook:
            return jsonify({"error": "Inbound webhook not found"}), 404

        auth_type = (getattr(hook, "auth_type", None) or "bearer").lower()
        if auth_type not in ("bearer", "basic"):
            auth_type = "bearer"

        db_cred_fields, credentials = _build_inbound_credentials(auth_type)
        db(
            (db.inbound_webhooks.id == webhook_id)
            & (db.inbound_webhooks.tenant_id == tenant_id)
        ).update(
            updated_at=datetime.now(UTC),
            **db_cred_fields,
        )
        db.commit()

        updated = get_tenant_scoped(db, db.inbound_webhooks, webhook_id, tenant_id)
        return (
            jsonify(
                {
                    "webhook": _serialize_inbound_webhook(updated),
                    "credentials": credentials,
                }
            ),
            200,
        )
    except Exception as e:
        return log_error_and_respond(
            logger, e, "Failed to rotate inbound webhook credentials", 500
        )


@bp.route("/inbound/<int:webhook_id>", methods=["PUT"])
@admin_required
async def update_inbound_webhook(webhook_id: int):
    """Update an inbound webhook's configuration or authentication type."""
    try:
        tenant_id = get_current_tenant_id()
        if not tenant_id:
            return jsonify({"error": "Tenant not found"}), 403

        db = current_app.db
        _ensure_inbound_tables(db)
        hook = get_tenant_scoped(db, db.inbound_webhooks, webhook_id, tenant_id)
        if not hook:
            return jsonify({"error": "Inbound webhook not found"}), 404

        data = await request.get_json(silent=True) or {}
        if not data:
            return jsonify({"error": "Request body required"}), 400

        updates: dict[str, Any] = {"updated_at": datetime.now(UTC)}
        if "name" in data:
            name = (data.get("name") or "").strip()
            if not name:
                return jsonify({"error": "name cannot be empty"}), 400
            updates["name"] = name
        if "description" in data:
            updates["description"] = data.get("description")
        if "is_active" in data:
            updates["is_active"] = bool(data.get("is_active"))
        if "organization_id" in data:
            org_id = data.get("organization_id")
            if org_id is not None and not _org_unit_in_tenant(db, org_id, tenant_id):
                return jsonify({"error": "organization_id not found in tenant"}), 404
            updates["organization_id"] = org_id
        if "stream_id" in data:
            next_stream_id = data.get("stream_id")
            if not _stream_in_tenant(db, next_stream_id, tenant_id):
                return jsonify({"error": "stream_id not found in tenant"}), 404
            updates["stream_id"] = next_stream_id
        if "scopes" in data:
            effective_stream = updates.get(
                "stream_id", getattr(hook, "stream_id", None)
            )
            scopes, scopes_err = validate_inbound_scopes(
                data.get("scopes"), has_stream_id=bool(effective_stream)
            )
            if scopes_err:
                return jsonify({"error": scopes_err}), 400
            updates["scopes"] = scopes
        if "metadata" in data:
            updates["metadata"] = data.get("metadata")

        current_auth_type = (getattr(hook, "auth_type", None) or "bearer").lower()
        new_auth_type = (data.get("auth_type") or current_auth_type).strip().lower()
        if new_auth_type not in ("bearer", "basic"):
            return jsonify({"error": "auth_type must be 'bearer' or 'basic'"}), 400

        has_new_creds = any(
            k in data and data.get(k)
            for k in ("bearer_token", "basic_username", "basic_password")
        )
        credentials = None
        if new_auth_type != current_auth_type or has_new_creds:
            updates["auth_type"] = new_auth_type
            db_cred_fields, credentials = _build_inbound_credentials(
                new_auth_type,
                bearer_token=data.get("bearer_token"),
                basic_username=data.get("basic_username"),
                basic_password=data.get("basic_password"),
            )
            updates.update(db_cred_fields)

        db(
            (db.inbound_webhooks.id == webhook_id)
            & (db.inbound_webhooks.tenant_id == tenant_id)
        ).update(**updates)
        db.commit()

        updated = get_tenant_scoped(db, db.inbound_webhooks, webhook_id, tenant_id)
        resp_body: dict[str, Any] = {"webhook": _serialize_inbound_webhook(updated)}
        if credentials is not None:
            resp_body["credentials"] = credentials
        return jsonify(resp_body), 200
    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to update inbound webhook", 400)


@bp.route("/inbound/<int:webhook_id>", methods=["DELETE"])
@admin_required
def delete_inbound_webhook(webhook_id: int):
    """Delete an inbound webhook."""
    try:
        tenant_id = get_current_tenant_id()
        if not tenant_id:
            return jsonify({"error": "Tenant not found"}), 403

        db = current_app.db
        _ensure_inbound_tables(db)
        hook = get_tenant_scoped(db, db.inbound_webhooks, webhook_id, tenant_id)
        if not hook:
            return jsonify({"error": "Inbound webhook not found"}), 404

        db(
            (db.inbound_webhooks.id == webhook_id)
            & (db.inbound_webhooks.tenant_id == tenant_id)
        ).delete()
        db.commit()
        return jsonify({"deleted": True, "id": webhook_id}), 200
    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to delete inbound webhook", 500)


@bp.route("/inbound/<int:webhook_id>/events", methods=["GET"])
@login_required
@require_scope("webhooks:read")
def list_inbound_webhook_events(webhook_id: int):
    """List recent received events for an inbound webhook."""
    try:
        tenant_id = get_current_tenant_id()
        if not tenant_id:
            return jsonify({"error": "Tenant not found"}), 403

        db = current_app.db
        _ensure_inbound_tables(db)
        hook = get_tenant_scoped(db, db.inbound_webhooks, webhook_id, tenant_id)
        if not hook:
            return jsonify({"error": "Inbound webhook not found"}), 404

        rows = db(
            (db.inbound_webhook_events.inbound_webhook_id == webhook_id)
            & (db.inbound_webhook_events.tenant_id == tenant_id)
        ).select(orderby=~db.inbound_webhook_events.id, limitby=(0, 50))
        return (
            jsonify(
                {
                    "events": [_serialize_inbound_event(r) for r in rows],
                    "count": len(rows),
                }
            ),
            200,
        )
    except Exception as e:
        return log_error_and_respond(
            logger, e, "Failed to list inbound webhook events", 500
        )


def _verify_inbound_auth(hook: Any, auth_header: str) -> tuple[bool, str]:
    """Verify required Bearer or Basic auth against an inbound webhook row."""
    auth_type = (getattr(hook, "auth_type", None) or "bearer").lower()
    if auth_type == "bearer":
        challenge = 'Bearer realm="Elder Webhook"'
        if not auth_header or not auth_header.lower().startswith("bearer "):
            return False, challenge
        token = auth_header[7:].strip()
        token_hash = getattr(hook, "bearer_token_hash", None) or ""
        if not token or not token_hash:
            return False, challenge
        return check_password_hash(token_hash, token), challenge

    if auth_type == "basic":
        challenge = 'Basic realm="Elder Webhook"'
        if not auth_header or not auth_header.lower().startswith("basic "):
            return False, challenge
        encoded = auth_header[6:].strip()
        try:
            decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return False, challenge
        if ":" not in decoded:
            return False, challenge
        username, password = decoded.split(":", 1)
        expected_user = getattr(hook, "basic_username", None) or ""
        password_hash = getattr(hook, "basic_password_hash", None) or ""
        if not expected_user or not password_hash:
            return False, challenge
        if not hmac.compare_digest(username, expected_user):
            return False, challenge
        return check_password_hash(password_hash, password), challenge

    return False, 'Bearer realm="Elder Webhook"'


def _resolve_source_ip() -> str | None:
    cf_ip = (request.headers.get("CF-Connecting-IP") or "").strip()
    if cf_ip:
        return cf_ip[:64]
    xff = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    if xff:
        return xff[:64]
    return request.remote_addr


@bp.route("/receive/<slug>", methods=["POST"])
async def receive_inbound_webhook(slug: str):
    """Public inbound webhook receiver — requires Bearer/Basic auth & enforces granted scopes."""
    try:
        db = current_app.db
        _ensure_inbound_tables(db)

        hook = (
            db(
                (db.inbound_webhooks.slug == slug)
                & (db.inbound_webhooks.is_active == True)
            )
            .select()
            .first()
        )
        if not hook:
            return jsonify({"error": "Webhook not found"}), 404

        auth_header = request.headers.get("Authorization", "")
        ok, challenge = _verify_inbound_auth(hook, auth_header)
        if not ok:
            return (
                jsonify({"error": "Authentication required"}),
                401,
                {"WWW-Authenticate": challenge},
            )

        payload = await request.get_json(silent=True)
        if payload is None:
            raw_body = (await request.get_data(as_text=True)) or ""
            payload = {"raw": raw_body[:65536]} if raw_body else {}

        redacted_headers: dict[str, str] = {}
        for key, value in request.headers.items():
            if key.lower() in ("authorization", "cookie", "x-api-key"):
                redacted_headers[key] = "[REDACTED]"
            else:
                redacted_headers[key] = value

        now = datetime.now(UTC)
        source_ip = _resolve_source_ip()
        granted_scopes = normalize_stored_scopes(getattr(hook, "scopes", None))
        required_scope, action_spec, parse_err = resolve_inbound_action(hook, payload)

        if parse_err or action_spec is None:
            err_result = {"error": parse_err or "Invalid webhook action"}
            event_id = db.inbound_webhook_events.insert(
                inbound_webhook_id=hook.id,
                tenant_id=hook.tenant_id,
                source_ip=source_ip,
                headers=redacted_headers,
                payload=payload,
                action=None,
                action_result=err_result,
                status="invalid",
                received_at=now,
            )
            db(db.inbound_webhooks.id == hook.id).update(
                receive_count=(int(getattr(hook, "receive_count", 0) or 0) + 1),
                last_received_at=now,
            )
            db.commit()
            return (
                jsonify({"error": err_result["error"], "event_id": event_id}),
                400,
            )

        if required_scope not in granted_scopes:
            err_result = {"error": f"Webhook lacks required scope: {required_scope}"}
            event_id = db.inbound_webhook_events.insert(
                inbound_webhook_id=hook.id,
                tenant_id=hook.tenant_id,
                source_ip=source_ip,
                headers=redacted_headers,
                payload=payload,
                action=required_scope,
                action_result=err_result,
                status="forbidden",
                received_at=now,
            )
            db(db.inbound_webhooks.id == hook.id).update(
                receive_count=(int(getattr(hook, "receive_count", 0) or 0) + 1),
                last_received_at=now,
            )
            db.commit()
            return (
                jsonify(
                    {
                        "error": err_result["error"],
                        "required_scope": required_scope,
                        "event_id": event_id,
                    }
                ),
                403,
            )

        http_status, event_status, action_result = execute_inbound_action(
            db, hook, required_scope, action_spec, source_ip
        )

        event_id = db.inbound_webhook_events.insert(
            inbound_webhook_id=hook.id,
            tenant_id=hook.tenant_id,
            source_ip=source_ip,
            headers=redacted_headers,
            payload=payload,
            action=required_scope,
            action_result=action_result,
            status=event_status,
            received_at=now,
        )
        db(db.inbound_webhooks.id == hook.id).update(
            receive_count=(int(getattr(hook, "receive_count", 0) or 0) + 1),
            last_received_at=now,
        )
        db.commit()

        response_body: dict[str, Any] = {
            "status": event_status,
            "event_id": event_id,
            "action": required_scope,
            "result": action_result,
        }
        if "error" in action_result:
            response_body["error"] = action_result["error"]
        return jsonify(response_body), http_status
    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to receive webhook", 500)
