"""Webhook & Notification System API endpoints for Elder v1.2.0 (Phase 9)."""

# flake8: noqa: E501


import logging
from typing import Any, Optional

from quart import Blueprint, current_app, g, jsonify, request

from apps.api.auth.decorators import admin_required, login_required, require_scope
from apps.api.logging_config import log_error_and_respond
from apps.api.modules.helpdesk.common import identity_in_tenant
from apps.api.services.webhooks import WebhookService

logger = logging.getLogger(__name__)

bp = Blueprint("webhooks", __name__)


def _tenant_id() -> Optional[int]:
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


def get_webhook_service():
    """Get WebhookService instance with current database."""
    return WebhookService(current_app.db)


def _org_unit_in_tenant(db: Any, org_unit_id: Optional[int], tenant_id: int) -> bool:
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
    filter_assignee_type: Optional[str],
    filter_assignee_id: Optional[int],
) -> Optional[str]:
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
        (issues/routes/issues.py::_resolve_assignee_type) and
        hd_intake_forms.default_assignee_id
        (helpdesk/routes/intake_forms.py::_validate_assignee_ref).

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

        data = await request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        required = ["name", "url", "events"]
        missing = [f for f in required if f not in data]
        if missing:
            return (
                jsonify({"error": f'Missing required fields: {", ".join(missing)}'}),
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
            filter_issue_type=data.get("filter_issue_type"),
            filter_assignee_type=data.get("filter_assignee_type"),
            filter_assignee_id=data.get("filter_assignee_id"),
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
def test_webhook(webhook_id):
    """
    Send a test event to webhook (must belong to the caller's tenant).

    Returns:
        200: Test sent successfully
        400: Test failed
        403: Tenant not found
        404: Webhook not found
    """
    try:
        tenant_id = _tenant_id()
        if not tenant_id:
            return jsonify({"error": "Tenant not found"}), 403

        service = get_webhook_service()
        result = service.test_webhook(webhook_id, tenant_id)

        status_code = 200 if result.get("success") else 400
        return jsonify(result), status_code

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
def redeliver_webhook(webhook_id, delivery_id):
    """
    Retry a failed webhook delivery (must belong to the caller's tenant).

    Returns:
        200: Redelivery initiated
        403: Tenant not found
        404: Webhook or delivery not found
    """
    try:
        tenant_id = _tenant_id()
        if not tenant_id:
            return jsonify({"error": "Tenant not found"}), 403

        service = get_webhook_service()
        result = service.redeliver_webhook(webhook_id, tenant_id, delivery_id)

        status_code = 200 if result.get("success") else 400
        return jsonify(result), status_code

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
    List all notification rules.

    Query params:
        - organization_id: Filter by organization
        - channel: Filter by channel type

    Returns:
        200: List of notification rules
    """
    try:
        service = get_webhook_service()

        organization_id = request.args.get("organization_id", type=int)
        channel = request.args.get("channel")

        rules = service.list_notification_rules(
            organization_id=organization_id, channel=channel
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
    """
    try:
        data = await request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        required = ["name", "channel", "events", "config", "organization_id"]
        missing = [f for f in required if f not in data]
        if missing:
            return (
                jsonify({"error": f'Missing required fields: {", ".join(missing)}'}),
                400,
            )

        service = get_webhook_service()
        rule = service.create_notification_rule(
            name=data["name"],
            channel=data["channel"],
            events=data["events"],
            config=data["config"],
            organization_id=data["organization_id"],
            description=data.get("description"),
        )

        return jsonify(rule), 201

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 400)


@bp.route("/notification-rules/<int:rule_id>", methods=["GET"])
@login_required
@require_scope("webhooks_alerting:read")
def get_notification_rule(rule_id):
    """
    Get notification rule details.

    Returns:
        200: Rule details
        404: Rule not found
    """
    try:
        service = get_webhook_service()
        rule = service.get_notification_rule(rule_id)
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
        404: Rule not found
    """
    try:
        data = await request.get_json()

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
        )

        return jsonify(rule), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 400)


@bp.route("/notification-rules/<int:rule_id>", methods=["DELETE"])
@admin_required
@require_scope("webhooks_alerting:admin")
def delete_notification_rule(rule_id):
    """
    Delete notification rule.

    Returns:
        200: Rule deleted
        404: Rule not found
    """
    try:
        service = get_webhook_service()
        result = service.delete_notification_rule(rule_id)
        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/notification-rules/<int:rule_id>/test", methods=["POST"])
@login_required
@require_scope("webhooks_alerting:write")
def test_notification_rule(rule_id):
    """
    Test a notification rule.

    Returns:
        200: Test notification sent
        400: Test failed
        404: Rule not found
    """
    try:
        service = get_webhook_service()
        result = service.test_notification_rule(rule_id)

        status_code = 200 if result.get("success") else 400
        return jsonify(result), status_code

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
        200: Event broadcasted
        400: Invalid request
        403: Tenant not found
        404: organization_id not found in tenant
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
                jsonify({"error": f'Missing required fields: {", ".join(missing)}'}),
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

        result = service.broadcast_event(
            event_type=data["event_type"],
            payload=data["payload"],
            organization_id=organization_id,
            tenant_id=tenant_id,
        )

        return jsonify(result), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 400)
