"""Webhook & Notification System API endpoints for Elder v1.2.0 (Phase 9)."""

# flake8: noqa: E501


import logging

from quart import Blueprint, current_app, jsonify, request

from apps.api.auth.decorators import admin_required, login_required
from apps.api.logging_config import log_error_and_respond
from apps.api.services.webhooks import WebhookService

logger = logging.getLogger(__name__)

bp = Blueprint("webhooks", __name__)


def get_webhook_service():
    """Get WebhookService instance with current database."""
    return WebhookService(current_app.db)


# ===========================
# Webhook Endpoints
# ===========================


@bp.route("", methods=["GET"])
@login_required
def list_webhooks():
    """
    List all webhooks.

    Query params:
        - organization_id: Filter by organization
        - enabled: Filter by enabled status

    Returns:
        200: List of webhooks
    """
    try:
        service = get_webhook_service()

        organization_id = request.args.get("organization_id", type=int)
        enabled = request.args.get("enabled")

        # Convert enabled string to boolean
        enabled_bool = None
        if enabled is not None:
            enabled_bool = enabled.lower() == "true"

        webhooks = service.list_webhooks(
            organization_id=organization_id, enabled=enabled_bool
        )

        return jsonify({"webhooks": webhooks, "count": len(webhooks)}), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("", methods=["POST"])
@login_required
@admin_required
async def create_webhook():
    """
    Create a new webhook.

    Request body:
        {
            "name": "Slack notifications",
            "url": "https://hooks.slack.com/...",
            "events": ["entity.created", "entity.updated", "issue.created"],
            "secret": "shared-secret-for-hmac",
            "organization_id": 1,
            "description": "Send notifications to Slack",
            "headers": {"X-Custom-Header": "value"}
        }

    Returns:
        201: Webhook created
        400: Invalid request
    """
    try:
        data = await request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        required = ["name", "url", "events", "organization_id"]
        missing = [f for f in required if f not in data]
        if missing:
            return (
                jsonify({"error": f'Missing required fields: {", ".join(missing)}'}),
                400,
            )

        service = get_webhook_service()
        webhook = service.create_webhook(
            name=data["name"],
            url=data["url"],
            events=data["events"],
            organization_id=data["organization_id"],
            secret=data.get("secret"),
            description=data.get("description"),
            headers=data.get("headers"),
        )

        return jsonify(webhook), 201

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 400)


@bp.route("/<int:webhook_id>", methods=["GET"])
@login_required
def get_webhook(webhook_id):
    """
    Get webhook details.

    Returns:
        200: Webhook details
        404: Webhook not found
    """
    try:
        service = get_webhook_service()
        webhook = service.get_webhook(webhook_id)
        return jsonify(webhook), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/<int:webhook_id>", methods=["PUT"])
@admin_required
async def update_webhook(webhook_id):
    """
    Update webhook configuration.

    Request body (all optional):
        {
            "name": "Updated name",
            "url": "https://new-url.com",
            "events": ["entity.created"],
            "secret": "new-secret",
            "description": "Updated description",
            "headers": {"X-New-Header": "value"},
            "enabled": false
        }

    Returns:
        200: Webhook updated
        404: Webhook not found
    """
    try:
        data = await request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        service = get_webhook_service()
        webhook = service.update_webhook(
            webhook_id=webhook_id,
            name=data.get("name"),
            url=data.get("url"),
            events=data.get("events"),
            secret=data.get("secret"),
            description=data.get("description"),
            headers=data.get("headers"),
            enabled=data.get("enabled"),
        )

        return jsonify(webhook), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 400)


@bp.route("/<int:webhook_id>", methods=["DELETE"])
@admin_required
def delete_webhook(webhook_id):
    """
    Delete webhook.

    Returns:
        200: Webhook deleted
        404: Webhook not found
    """
    try:
        service = get_webhook_service()
        result = service.delete_webhook(webhook_id)
        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/<int:webhook_id>/test", methods=["POST"])
@login_required
def test_webhook(webhook_id):
    """
    Send a test event to webhook.

    Returns:
        200: Test sent successfully
        400: Test failed
        404: Webhook not found
    """
    try:
        service = get_webhook_service()
        result = service.test_webhook(webhook_id)

        status_code = 200 if result.get("success") else 400
        return jsonify(result), status_code

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/<int:webhook_id>/deliveries", methods=["GET"])
@login_required
def get_webhook_deliveries(webhook_id):
    """
    Get webhook delivery history.

    Query params:
        - limit: Number of deliveries (default: 50)
        - success: Filter by success status

    Returns:
        200: Delivery history
        404: Webhook not found
    """
    try:
        service = get_webhook_service()

        limit = request.args.get("limit", 50, type=int)
        success = request.args.get("success")

        # Convert success string to boolean
        success_bool = None
        if success is not None:
            success_bool = success.lower() == "true"

        deliveries = service.get_webhook_deliveries(
            webhook_id=webhook_id, limit=limit, success=success_bool
        )

        return jsonify({"deliveries": deliveries, "count": len(deliveries)}), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/<int:webhook_id>/deliveries/<int:delivery_id>/redeliver", methods=["POST"])
@admin_required
def redeliver_webhook(webhook_id, delivery_id):
    """
    Retry a failed webhook delivery.

    Returns:
        200: Redelivery initiated
        404: Webhook or delivery not found
    """
    try:
        service = get_webhook_service()
        result = service.redeliver_webhook(webhook_id, delivery_id)

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
async def broadcast_event():
    """
    Broadcast an event to all applicable webhooks and notification rules.

    Request body:
        {
            "event_type": "entity.created",
            "payload": {...},
            "organization_id": 1
        }

    Returns:
        200: Event broadcasted
        400: Invalid request
    """
    try:
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
        result = service.broadcast_event(
            event_type=data["event_type"],
            payload=data["payload"],
            organization_id=data["organization_id"],
        )

        return jsonify(result), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 400)
