"""Webhook notifications for issue creation events."""

# flake8: noqa: E501

import asyncio
import json
from typing import Any

import structlog

from shared.webhooks import delivery as _delivery

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

    Callers fire this with ``asyncio.create_task`` after the issue commit, so
    it runs off the request path. Each delivery goes through the shared
    non-blocking engine (timeouts, retry/backoff, SSRF vetting) and the
    configs are delivered concurrently; nothing here raises.

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

    # Prepare webhook payload (serialized once; the same bytes go to every target)
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

    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    targets: list[tuple[Any, str]] = []
    for config in webhook_configs:
        webhook_url = config.config.get("url") if config.config else None
        if not webhook_url:
            logger.warning(
                "webhook_config_missing_url",
                config_id=config.id,
                organization_id=organization_id,
            )
            continue
        targets.append((config, webhook_url))

    return list(
        await asyncio.gather(
            *(
                _deliver_issue_created(config, webhook_url, body, issue_id)
                for config, webhook_url in targets
            )
        )
    )


async def _deliver_issue_created(
    config: Any, webhook_url: str, body: bytes, issue_id: int
) -> dict[str, Any]:
    """Deliver the issue.created body to one alert-configuration webhook.

    Returns a result dict that identifies the target by config id and bare
    hostname only: the URL can embed the receiver's credential, so it is
    never logged or echoed. Never raises (a bad config is a failed result).
    """
    try:
        custom_headers = config.config.get("headers") or {}
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Elder-Issue-Webhook/1.0",
            **{str(name): str(value) for name, value in dict(custom_headers).items()},
        }
        result = await _delivery.deliver(
            _delivery.DeliveryRequest(
                url=webhook_url, body=body, headers=headers, kind="issue_created"
            )
        )
    except Exception as exc:
        logger.error(
            "issue_webhook_failed",
            issue_id=issue_id,
            config_id=config.id,
            error=_delivery.scrub_error(exc),
        )
        return {
            "config_id": config.id,
            "target_host": _delivery.target_host(webhook_url),
            "success": False,
            "error": _delivery.scrub_error(exc),
        }

    logger.info(
        "issue_webhook_sent",
        issue_id=issue_id,
        config_id=config.id,
        target_host=_delivery.target_host(webhook_url),
        status_code=result.status_code,
        attempts=result.attempts,
        success=result.success,
    )
    return {
        "config_id": config.id,
        "target_host": _delivery.target_host(webhook_url),
        "status_code": result.status_code,
        "attempts": result.attempts,
        "success": result.success,
        "error": result.error,
        "response": result.response_excerpt[:500] if not result.success else None,
    }
