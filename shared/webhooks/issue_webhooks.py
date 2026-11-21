"""Webhook notifications for issue creation events."""

# flake8: noqa: E501

from typing import Any, Dict, List, Optional

import requests
import structlog

logger = structlog.get_logger()


async def send_issue_created_webhooks(
    db,
    issue_id: int,
    issue_title: str,
    issue_type: str,
    is_incident: int,
    organization_id: int | None,
    web_url_base: str = "http://localhost:3000",
) -> list[dict[str, Any]]:
    """
    Send webhook notifications for a newly created issue to all configured webhooks for the organization.

    Args:
        db: Database connection
        issue_id: Issue ID
        issue_title: Issue title
        issue_type: Issue type (operations, code, config, security, etc.)
        is_incident: Whether issue is marked as incident (0 or 1)
        organization_id: Organization ID (optional)
        web_url_base: Base URL for web interface

    Returns:
        List of webhook delivery results
    """
    if not organization_id:
        logger.info(
            "issue_webhooks_skipped", issue_id=issue_id, reason="no_organization"
        )
        return []

    # Get all webhook configurations for this organization.
    # NOTE: destination_type is a SQLAlchemy Enum(AlertDestinationType) column
    # with no values_callable, so it persists the member NAME — the pg enum
    # `alertdestinationtype` accepts EMAIL/WEBHOOK/PAGERDUTY/SLACK (upper), not
    # the lowercase `.value`. Comparing to "webhook" raised
    # InvalidTextRepresentation and silently killed this fire-and-forget task,
    # so issue-created webhooks never dispatched. Match the stored name.
    webhook_configs = db(
        (db.alert_configurations.organization_id == organization_id)
        & (db.alert_configurations.destination_type == "WEBHOOK")
        & (db.alert_configurations.enabled == 1)
    ).select()

    if not webhook_configs:
        logger.info(
            "issue_webhooks_skipped",
            issue_id=issue_id,
            organization_id=organization_id,
            reason="no_webhooks",
        )
        return []

    # Prepare webhook payload
    payload = {
        "event": "issue.created",
        "issue": {
            "id": issue_id,
            "title": issue_title,
            "type": issue_type,
            "is_incident": bool(is_incident),
            "url": f"{web_url_base}/issues/{issue_id}",
        },
        "organization_id": organization_id,
    }

    results = []

    # Send to each webhook
    for config in webhook_configs:
        webhook_url = config.config.get("url") if config.config else None
        if not webhook_url:
            logger.warning(
                "webhook_config_missing_url",
                config_id=config.id,
                organization_id=organization_id,
            )
            continue

        try:
            # Get additional webhook config
            headers = config.config.get("headers", {})
            timeout = config.config.get("timeout", 10)

            response = requests.post(
                webhook_url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "Elder-Issue-Webhook/1.0",
                    **headers,
                },
                timeout=timeout,
            )

            success = response.status_code in range(200, 300)

            logger.info(
                "issue_webhook_sent",
                issue_id=issue_id,
                config_id=config.id,
                webhook_url=webhook_url,
                status_code=response.status_code,
                success=success,
            )

            results.append(
                {
                    "config_id": config.id,
                    "webhook_url": webhook_url,
                    "status_code": response.status_code,
                    "success": success,
                    "response": response.text[:500] if not success else None,
                }
            )

        except requests.exceptions.RequestException as e:
            logger.error(
                "issue_webhook_failed",
                issue_id=issue_id,
                config_id=config.id,
                webhook_url=webhook_url,
                error=str(e),
            )

            results.append(
                {
                    "config_id": config.id,
                    "webhook_url": webhook_url,
                    "success": False,
                    "error": str(e),
                }
            )

    return results
