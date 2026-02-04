"""On-call rotation webhook handlers for alert integrations."""

# flake8: noqa: E501


import datetime

from flask import Blueprint, current_app, request

from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.validation_helpers import validate_json_body
from apps.api.utils.async_utils import run_in_threadpool

bp = Blueprint("on_call_webhooks", __name__)


def _get_current_oncall_for_rotation(db, rotation_id: int) -> dict:
    """Get the current on-call person for a rotation."""
    now = datetime.datetime.now(datetime.timezone.utc)

    shift = (
        db(
            (db.on_call_shifts.rotation_id == rotation_id)
            & (db.on_call_shifts.shift_start <= now)
            & (db.on_call_shifts.shift_end > now)
        )
        .select()
        .first()
    )

    if not shift:
        return None

    identity = db.identities[shift.identity_id]
    if not identity:
        return None

    return {
        "shift_id": shift.id,
        "identity_id": identity.id,
        "identity_name": identity.username,
        "identity_email": identity.email,
        "shift_start": shift.shift_start,
        "shift_end": shift.shift_end,
        "is_override": shift.is_override,
    }


@bp.route("/alertmanager/webhook", methods=["POST"])
async def handle_alertmanager_webhook():
    """
    Receive Prometheus AlertManager alerts and notify on-call rotations.

    This webhook is called by AlertManager for each alert firing. It:
    1. Parses alert details from AlertManager format
    2. Finds matching on-call rotations
    3. Gets current on-call person
    4. Sends notifications to configured channels
    5. Records notification in on_call_notifications table

    Request Body (AlertManager format):
        - alerts: Array of alert objects
        - groupLabels: Alert group labels (service, team, etc.)
        - commonLabels: Common labels for all alerts
        - commonAnnotations: Common annotations

    Returns:
        202: Accepted for processing
        400: Invalid request

    Example:
        POST /api/v1/on-call/alertmanager/webhook
        {
            "alerts": [
                {
                    "status": "firing",
                    "labels": {
                        "alertname": "HighErrorRate",
                        "service": "api",
                        "severity": "critical"
                    },
                    "annotations": {
                        "summary": "High error rate detected"
                    }
                }
            ],
            "groupLabels": {
                "service": "api"
            },
            "commonLabels": {
                "severity": "critical"
            }
        }
    """
    db = current_app.db

    data = request.get_json()
    if error := validate_json_body(data):
        return error

    alerts = data.get("alerts", [])
    if not alerts:
        return ApiResponse.error("No alerts in request", 400)

    async def process_alerts():
        for alert in alerts:
            if alert.get("status") != "firing":
                continue

            labels = alert.get("labels", {})
            annotations = alert.get("annotations", {})

            # Try to find rotation by service label
            service_name = labels.get("service")
            if not service_name:
                continue

            # Find service by name
            def get_service():
                service = db(db.services.name == service_name).select().first()
                return service

            service = await run_in_threadpool(get_service)
            if not service:
                continue

            # Get active rotations for this service
            def get_rotations():
                rotations = db(
                    (db.on_call_rotations.service_id == service.id)
                    & (db.on_call_rotations.is_active is True)
                ).select()
                return rotations

            rotations = await run_in_threadpool(get_rotations)

            # For each rotation, notify current on-call
            for rotation in rotations:
                current = _get_current_oncall_for_rotation(db, rotation.id)
                if not current:
                    continue

                # Record notification
                subject = f"Alert: {labels.get('alertname', 'Unknown')}"
                message = annotations.get("summary", "No summary")

                def record_notification():
                    notification_data = {
                        "rotation_id": rotation.id,
                        "identity_id": current["identity_id"],
                        "notification_type": "alert",
                        "channel": "webhook",
                        "subject": subject,
                        "message": message,
                        "metadata": {
                            "alert_labels": labels,
                            "alert_annotations": annotations,
                        },
                        "status": "pending",
                    }

                    notification_id = db.on_call_notifications.insert(
                        **notification_data
                    )
                    db.commit()

                    return notification_id

                await run_in_threadpool(record_notification)

    await process_alerts()

    return (
        ApiResponse.success({"status": "accepted", "alerts_processed": len(alerts)}),
        202,
    )
