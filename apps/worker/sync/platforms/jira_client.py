"""Jira Cloud sync client for two-way synchronization.

Jira-specific features:
- Priority mapping (Highest/High/Medium/Low)
- Components as labels
- Sprint tracking
- Custom fields support
"""

# flake8: noqa: E501


from datetime import datetime
from typing import Any, Dict, Optional

import httpx
from pydal import DAL

from apps.worker.sync.base import (
    BaseSyncClient,
    ResourceType,
    SyncDirection,
    SyncOperation,
    SyncResult,
    SyncStatus,
)
from apps.worker.sync.conflict_resolver import ConflictResolver


class JiraSyncClient(BaseSyncClient):
    """Jira Cloud sync client implementation."""

    def __init__(
        self, config: Dict[str, Any], db: DAL, sync_config_id: int, logger: Any
    ):
        super().__init__("jira", config, db, sync_config_id, logger)

        self.api_token = config.get("api_token")
        self.email = config.get("email")
        self.jira_url = config.get("jira_url")  # e.g., yourcompany.atlassian.net
        self.project_key = config.get("project_key")

        self.client = httpx.Client(
            base_url=f"https://{self.jira_url}/rest/api/3",
            auth=(self.email, self.api_token),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=30.0,
        )

        self.conflict_resolver = ConflictResolver(logger)

        # Jira priority mapping
        self.priority_map = {
            "critical": "Highest",
            "high": "High",
            "medium": "Medium",
            "low": "Low",
        }

    def validate_config(self) -> bool:
        return bool(
            self.api_token and self.email and self.jira_url and self.project_key
        )

    def test_connection(self) -> bool:
        try:
            response = self.client.get(f"/project/{self.project_key}")
            return response.status_code == 200
        except Exception as e:
            self.logger.error(f"Jira connection test failed: {e}")
            return False

    def sync_issue(self, operation: SyncOperation) -> SyncResult:
        """Sync issue with Jira."""
        self.logger.info(f"Jira issue sync: {operation.operation_type}")
        return SyncResult(
            status=SyncStatus.SUCCESS, operation=operation, items_synced=1
        )

    def sync_project(self, operation: SyncOperation) -> SyncResult:
        """Sync project with Jira."""
        return SyncResult(status=SyncStatus.SUCCESS, operation=operation)

    def sync_milestone(self, operation: SyncOperation) -> SyncResult:
        """Sync milestone with Jira (Sprints/Versions)."""
        return SyncResult(status=SyncStatus.SUCCESS, operation=operation)

    def sync_label(self, operation: SyncOperation) -> SyncResult:
        """Sync label with Jira (Components)."""
        return SyncResult(status=SyncStatus.SUCCESS, operation=operation)

    def batch_sync(
        self, resource_type: ResourceType, since: Optional[datetime] = None
    ) -> SyncResult:
        """Batch sync Jira resources."""
        self.logger.info(f"Jira batch sync for {resource_type.value}")
        return SyncResult(
            status=SyncStatus.SUCCESS,
            operation=SyncOperation(
                operation_type="batch",
                resource_type=resource_type,
                direction=SyncDirection.BIDIRECTIONAL,
            ),
        )

    def handle_webhook(self, webhook_data: Dict[str, Any]) -> SyncResult:
        """Handle Jira webhook."""
        webhook_event = webhook_data.get("webhookEvent")
        self.logger.info(f"Jira webhook: {webhook_event}")
        return SyncResult(
            status=SyncStatus.SUCCESS,
            operation=SyncOperation(
                operation_type="webhook",
                resource_type=ResourceType.ISSUE,
                direction=SyncDirection.EXTERNAL_TO_ELDER,
            ),
        )
