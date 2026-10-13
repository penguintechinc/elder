"""Alertmanager client for sending incident alerts from Elder issues."""

# flake8: noqa: E501

import logging
import os
from datetime import UTC, datetime, timezone
from typing import Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


class AlertmanagerClient:
    """
    Client for sending alerts to Prometheus Alertmanager.

    Sends alerts when issues are marked as incidents, allowing integration
    with email, webhooks, PagerDuty, and other notification channels.
    """

    def __init__(self, alertmanager_url: str | None = None):
        """
        Initialize Alertmanager client.

        Args:
            alertmanager_url: URL to Alertmanager API (default: from env or localhost:9093)
        """
        self.alertmanager_url = alertmanager_url or os.getenv(
            "ALERTMANAGER_URL", "http://alertmanager:9093"
        )
        self.api_url = f"{self.alertmanager_url}/api/v2/alerts"
        self.timeout = httpx.Timeout(10.0, connect=5.0)

    async def send_alert(
        self,
        alertname: str,
        labels: dict[str, str],
        annotations: dict[str, str],
        starts_at: datetime | None = None,
        ends_at: datetime | None = None,
        generator_url: str | None = None,
    ) -> bool:
        """
        Send an alert to Alertmanager.

        Args:
            alertname: Name of the alert (e.g., 'ElderIncidentIssue')
            labels: Alert labels (severity, component, etc.)
            annotations: Alert annotations (summary, description, etc.)
            starts_at: When the alert started (default: now)
            ends_at: When the alert ended (None = ongoing)
            generator_url: URL to the source of the alert

        Returns:
            True if alert was sent successfully, False otherwise
        """
        if starts_at is None:
            starts_at = datetime.now(UTC)

        # Build alert payload
        alert = {
            "labels": {
                "alertname": alertname,
                **labels,
            },
            "annotations": annotations,
            "startsAt": starts_at.isoformat(),
            "generatorURL": generator_url or "",
        }

        if ends_at:
            alert["endsAt"] = ends_at.isoformat()

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(self.api_url, json=[alert])
                response.raise_for_status()
                logger.info(f"Alert sent to Alertmanager: {alertname}")
                return True

        except httpx.HTTPError as e:
            logger.error(f"Failed to send alert to Alertmanager: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error sending alert: {e}")
            return False

    async def send_incident_alert(
        self,
        issue_id: int,
        issue_title: str,
        priority: str,
        organization_name: str,
        organization_id: int,
        description: str | None = None,
        assigned_to: str | None = None,
        entities: list[str] | None = None,
        web_url: str | None = None,
    ) -> bool:
        """
        Send an incident alert for an Elder issue.

        Args:
            issue_id: ID of the issue
            issue_title: Title of the issue
            priority: Priority level (low, medium, high, critical)
            organization_name: Name of the organization
            organization_id: ID of the organization
            description: Issue description
            assigned_to: Username of assigned person
            entities: List of affected entity names
            web_url: URL to view the issue in web UI

        Returns:
            True if alert was sent successfully
        """
        # Map priority to severity
        severity_map = {
            "low": "warning",
            "medium": "warning",
            "high": "high",
            "critical": "critical",
        }
        severity = severity_map.get(priority.lower(), "warning")

        # Build labels
        labels = {
            "severity": severity,
            "component": "incident",
            "issue_id": str(issue_id),
            "issue_title": issue_title,
            "priority": priority,
            "organization": organization_name,
            "organization_id": str(organization_id),
        }

        if assigned_to:
            labels["assigned_to"] = assigned_to

        # Build annotations
        annotations = {
            "summary": f"Incident: {issue_title}",
            "description": description or "No description provided",
        }

        if entities:
            annotations["entities"] = ", ".join(entities)

        if web_url:
            annotations["url"] = web_url

        return await self.send_alert(
            alertname="ElderIncidentIssue",
            labels=labels,
            annotations=annotations,
            generator_url=web_url,
        )

    async def resolve_incident_alert(
        self,
        issue_id: int,
        issue_title: str,
        organization_name: str,
        organization_id: int,
    ) -> bool:
        """
        Resolve (end) an incident alert.

        Args:
            issue_id: ID of the issue
            issue_title: Title of the issue
            organization_name: Name of the organization
            organization_id: ID of the organization

        Returns:
            True if alert was resolved successfully
        """
        labels = {
            "severity": "info",
            "component": "incident",
            "issue_id": str(issue_id),
            "issue_title": issue_title,
            "organization": organization_name,
            "organization_id": str(organization_id),
        }

        annotations = {
            "summary": f"Incident Resolved: {issue_title}",
            "description": "This incident has been resolved",
        }

        return await self.send_alert(
            alertname="ElderIncidentIssue",
            labels=labels,
            annotations=annotations,
            ends_at=datetime.now(UTC),
        )


# Singleton instance
_alertmanager_client: AlertmanagerClient | None = None


def get_alertmanager_client() -> AlertmanagerClient:
    """Get or create the Alertmanager client singleton."""
    global _alertmanager_client
    if _alertmanager_client is None:
        _alertmanager_client = AlertmanagerClient()
    return _alertmanager_client


async def send_incident_alert(
    issue_id: int,
    issue_title: str,
    priority: str,
    organization_name: str,
    organization_id: int,
    **kwargs,
) -> bool:
    """
    Convenience function to send an incident alert.

    See AlertmanagerClient.send_incident_alert for parameters.
    """
    client = get_alertmanager_client()
    return await client.send_incident_alert(
        issue_id=issue_id,
        issue_title=issue_title,
        priority=priority,
        organization_name=organization_name,
        organization_id=organization_id,
        **kwargs,
    )
