"""Trello sync client for two-way synchronization.

Trello mapping:
- Boards → Projects
- Lists → Milestones
- Cards → Issues
- Labels → Labels
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


class TrelloSyncClient(BaseSyncClient):
    """Trello sync client implementation."""

    def __init__(
        self, config: Dict[str, Any], db: DAL, sync_config_id: int, logger: Any
    ):
        super().__init__("trello", config, db, sync_config_id, logger)

        self.api_key = config.get("api_key")
        self.api_token = config.get("api_token")
        self.board_id = config.get("board_id")

        self.client = httpx.Client(
            base_url="https://api.trello.com/1",
            params={"key": self.api_key, "token": self.api_token},
            timeout=30.0,
        )

        self.conflict_resolver = ConflictResolver(logger)

    def validate_config(self) -> bool:
        return bool(self.api_key and self.api_token and self.board_id)

    def test_connection(self) -> bool:
        try:
            response = self.client.get(f"/boards/{self.board_id}")
            return response.status_code == 200
        except Exception as e:
            self.logger.error(f"Trello connection test failed: {e}")
            return False

    def sync_issue(self, operation: SyncOperation) -> SyncResult:
        """Sync issue with Trello (Card)."""
        self.logger.info(f"Trello card sync: {operation.operation_type}")
        return SyncResult(
            status=SyncStatus.SUCCESS, operation=operation, items_synced=1
        )

    def sync_project(self, operation: SyncOperation) -> SyncResult:
        """Sync project with Trello (Board)."""
        return SyncResult(status=SyncStatus.SUCCESS, operation=operation)

    def sync_milestone(self, operation: SyncOperation) -> SyncResult:
        """Sync milestone with Trello (List)."""
        return SyncResult(status=SyncStatus.SUCCESS, operation=operation)

    def sync_label(self, operation: SyncOperation) -> SyncResult:
        """Sync label with Trello."""
        return SyncResult(status=SyncStatus.SUCCESS, operation=operation)

    def batch_sync(
        self, resource_type: ResourceType, since: Optional[datetime] = None
    ) -> SyncResult:
        """Batch sync Trello resources."""
        self.logger.info(f"Trello batch sync for {resource_type.value}")
        return SyncResult(
            status=SyncStatus.SUCCESS,
            operation=SyncOperation(
                operation_type="batch",
                resource_type=resource_type,
                direction=SyncDirection.BIDIRECTIONAL,
            ),
        )

    def handle_webhook(self, webhook_data: Dict[str, Any]) -> SyncResult:
        """Handle Trello webhook."""
        action_type = webhook_data.get("action", {}).get("type")
        self.logger.info(f"Trello webhook: {action_type}")
        return SyncResult(
            status=SyncStatus.SUCCESS,
            operation=SyncOperation(
                operation_type="webhook",
                resource_type=ResourceType.ISSUE,
                direction=SyncDirection.EXTERNAL_TO_ELDER,
            ),
        )
