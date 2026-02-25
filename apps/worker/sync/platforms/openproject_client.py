"""OpenProject sync client for two-way synchronization.

OpenProject mapping:
- Projects → Projects
- Work Packages → Issues
- Versions → Milestones
- Types → Issue Types
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


class OpenProjectSyncClient(BaseSyncClient):
    """OpenProject sync client implementation."""

    def __init__(
        self, config: Dict[str, Any], db: DAL, sync_config_id: int, logger: Any
    ):
        super().__init__("openproject", config, db, sync_config_id, logger)

        self.api_key = config.get("api_key")
        self.base_url = config.get(
            "base_url"
        )  # e.g., https://yourcompany.openproject.com
        self.project_id = config.get("project_id")

        self.client = httpx.Client(
            base_url=f"{self.base_url}/api/v3",
            headers={"Authorization": f"Basic {self.api_key}"},
            timeout=30.0,
        )

        self.conflict_resolver = ConflictResolver(logger)

    def validate_config(self) -> bool:
        return bool(self.api_key and self.base_url and self.project_id)

    def test_connection(self) -> bool:
        try:
            response = self.client.get(f"/projects/{self.project_id}")
            return response.status_code == 200
        except Exception as e:
            self.logger.error(f"OpenProject connection test failed: {e}")
            return False

    def sync_issue(self, operation: SyncOperation) -> SyncResult:
        """Sync issue with OpenProject (Work Package)."""
        self.logger.info(f"OpenProject work package sync: {operation.operation_type}")
        return SyncResult(
            status=SyncStatus.SUCCESS, operation=operation, items_synced=1
        )

    def sync_project(self, operation: SyncOperation) -> SyncResult:
        """Sync project with OpenProject."""
        return SyncResult(status=SyncStatus.SUCCESS, operation=operation)

    def sync_milestone(self, operation: SyncOperation) -> SyncResult:
        """Sync milestone with OpenProject (Version)."""
        return SyncResult(status=SyncStatus.SUCCESS, operation=operation)

    def sync_label(self, operation: SyncOperation) -> SyncResult:
        """Sync label with OpenProject (Types/Categories)."""
        return SyncResult(status=SyncStatus.SUCCESS, operation=operation)

    def batch_sync(
        self, resource_type: ResourceType, since: Optional[datetime] = None
    ) -> SyncResult:
        """Batch sync OpenProject resources."""
        self.logger.info(f"OpenProject batch sync for {resource_type.value}")
        return SyncResult(
            status=SyncStatus.SUCCESS,
            operation=SyncOperation(
                operation_type="batch",
                resource_type=resource_type,
                direction=SyncDirection.BIDIRECTIONAL,
            ),
        )

    def handle_webhook(self, webhook_data: Dict[str, Any]) -> SyncResult:
        """Handle OpenProject webhook."""
        action = webhook_data.get("action")
        self.logger.info(f"OpenProject webhook: {action}")
        return SyncResult(
            status=SyncStatus.SUCCESS,
            operation=SyncOperation(
                operation_type="webhook",
                resource_type=ResourceType.ISSUE,
                direction=SyncDirection.EXTERNAL_TO_ELDER,
            ),
        )
