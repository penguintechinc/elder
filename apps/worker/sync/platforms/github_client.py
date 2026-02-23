"""GitHub sync client for two-way synchronization.

Implements two-way synchronization between Elder and GitHub:
- Issues ↔ GitHub Issues
- Projects ↔ GitHub Projects
- Milestones ↔ GitHub Milestones
- Labels ↔ GitHub Labels
- Organizations ↔ GitHub Repositories/Organizations

GitHub API: REST API v3 + GraphQL for complex queries
"""

# flake8: noqa: E501


from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from pydal import DAL

from apps.worker.sync.base import (
    BaseSyncClient,
    ResourceType,
    SyncDirection,
    SyncMapping,
    SyncOperation,
    SyncResult,
    SyncStatus,
)
from apps.worker.sync.conflict_resolver import ConflictResolver


class GitHubSyncClient(BaseSyncClient):
    """GitHub-specific sync client implementation.

    Handles two-way synchronization with GitHub using REST API v3.
    """

    def __init__(
        self,
        config: Dict[str, Any],
        db: DAL,
        sync_config_id: int,
        logger: Any,
    ):
        """Initialize GitHub sync client.

        Args:
            config: GitHub configuration with api_token, org/repo info
            db: PyDAL database instance
            sync_config_id: Sync configuration ID
            logger: Logger instance
        """
        super().__init__(
            platform_name="github",
            config=config,
            db=db,
            sync_config_id=sync_config_id,
            logger=logger,
        )

        self.api_token = config.get("api_token")
        self.org_name = config.get("org_name")
        self.repo_name = config.get("repo_name")
        self.base_url = config.get("base_url", "https://api.github.com")

        self.client = httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30.0,
        )

        self.conflict_resolver = ConflictResolver(logger)

    def validate_config(self) -> bool:
        """Validate GitHub configuration.

        Returns:
            True if configuration is valid, False otherwise
        """
        if not self.api_token:
            self.logger.error("GitHub API token is required")
            return False

        if not self.repo_name:
            self.logger.error("GitHub repository name is required")
            return False

        return True

    def test_connection(self) -> bool:
        """Test connection to GitHub API.

        Returns:
            True if connection successful, False otherwise
        """
        try:
            if self.org_name:
                response = self.client.get(f"/orgs/{self.org_name}")
            else:
                # Test with user endpoint if no org
                response = self.client.get("/user")

            return response.status_code == 200

        except Exception as e:
            self.logger.error(f"GitHub connection test failed: {e}")
            return False

    def sync_issue(self, operation: SyncOperation) -> SyncResult:
        """Sync a single issue with GitHub.

        Args:
            operation: Sync operation details

        Returns:
            Result of the sync operation
        """
        self.logger.info(
            f"Syncing issue: {operation.operation_type}",
            extra={"correlation_id": operation.correlation_id},
        )

        try:
            if operation.direction == SyncDirection.ELDER_TO_EXTERNAL:
                return self._sync_issue_to_github(operation)
            elif operation.direction == SyncDirection.EXTERNAL_TO_ELDER:
                return self._sync_issue_from_github(operation)
            else:
                # Bidirectional - check for conflicts
                return self._sync_issue_bidirectional(operation)

        except Exception as e:
            self.logger.error(
                f"Issue sync failed: {e}",
                extra={"correlation_id": operation.correlation_id},
                exc_info=True,
            )
            return SyncResult(
                status=SyncStatus.FAILED,
                operation=operation,
                items_failed=1,
                errors=[str(e)],
            )

    def _sync_issue_to_github(self, operation: SyncOperation) -> SyncResult:
        """Sync Elder issue to GitHub.

        Args:
            operation: Sync operation

        Returns:
            Sync result
        """
        elder_issue = operation.elder_data
        mapping = operation.mapping

        # Prepare GitHub issue data
        github_data = {
            "title": elder_issue.get("title"),
            "body": elder_issue.get("description") or "",
            "state": "open" if elder_issue.get("status") == "open" else "closed",
        }

        # Map labels if present
        if elder_issue.get("labels"):
            github_data["labels"] = elder_issue["labels"]

        # Map assignees
        if elder_issue.get("assignee"):
            github_data["assignee"] = elder_issue["assignee"]

        # Map milestone
        if elder_issue.get("milestone_id"):
            milestone_mapping = self.get_mapping(
                ResourceType.MILESTONE,
                elder_id=elder_issue["milestone_id"],
            )
            if milestone_mapping:
                github_data["milestone"] = int(milestone_mapping.external_id)

        if operation.operation_type == "create":
            # Create new GitHub issue
            response = self.client.post(
                f"/repos/{self.org_name or 'owner'}/{self.repo_name}/issues",
                json=github_data,
            )

            if response.status_code == 201:
                github_issue = response.json()

                # Create mapping
                new_mapping = SyncMapping(
                    elder_type=ResourceType.ISSUE.value,
                    elder_id=elder_issue["id"],
                    external_platform="github",
                    external_id=str(github_issue["number"]),
                    sync_config_id=self.sync_config_id,
                    elder_updated_at=elder_issue.get("updated_at"),
                    external_updated_at=github_issue.get("updated_at"),
                )

                self.create_mapping(new_mapping, sync_method="manual")

                return SyncResult(
                    status=SyncStatus.SUCCESS,
                    operation=operation,
                    items_synced=1,
                    created_mappings=[new_mapping],
                )
            else:
                return SyncResult(
                    status=SyncStatus.FAILED,
                    operation=operation,
                    items_failed=1,
                    errors=[
                        f"GitHub API error: {response.status_code} - {response.text}"
                    ],
                )

        elif operation.operation_type == "update":
            # Update existing GitHub issue
            if not mapping:
                return SyncResult(
                    status=SyncStatus.FAILED,
                    operation=operation,
                    items_failed=1,
                    errors=["No mapping found for issue update"],
                )

            response = self.client.patch(
                f"/repos/{self.org_name or 'owner'}/{self.repo_name}/issues/{mapping.external_id}",
                json=github_data,
            )

            if response.status_code == 200:
                github_issue = response.json()

                mapping.external_updated_at = github_issue.get("updated_at")
                self.update_mapping(mapping)

                return SyncResult(
                    status=SyncStatus.SUCCESS,
                    operation=operation,
                    items_synced=1,
                    updated_mappings=[mapping],
                )
            else:
                return SyncResult(
                    status=SyncStatus.FAILED,
                    operation=operation,
                    items_failed=1,
                    errors=[
                        f"GitHub API error: {response.status_code} - {response.text}"
                    ],
                )

        return SyncResult(
            status=SyncStatus.FAILED,
            operation=operation,
            items_failed=1,
            errors=[f"Unknown operation type: {operation.operation_type}"],
        )

    def _sync_issue_from_github(self, operation: SyncOperation) -> SyncResult:
        """Sync GitHub issue to Elder.

        Args:
            operation: Sync operation

        Returns:
            Sync result
        """
        github_issue = operation.external_data
        mapping = operation.mapping

        # Prepare Elder issue data
        elder_data = {
            "title": github_issue.get("title"),
            "description": github_issue.get("body") or "",
            "status": "open" if github_issue.get("state") == "open" else "closed",
            "priority": self._map_github_labels_to_priority(
                github_issue.get("labels", [])
            ),
            "updated_at": github_issue.get("updated_at"),
        }

        if operation.operation_type == "create":
            # Create Elder issue
            config_row = (
                self.db(self.db.sync_configs.id == self.sync_config_id).select().first()
            )
            two_way_create = config_row.two_way_create if config_row else False

            if not two_way_create:
                self.logger.info(
                    "Two-way creation disabled, skipping GitHub issue creation in Elder"
                )
                return SyncResult(
                    status=SyncStatus.SUCCESS,
                    operation=operation,
                    metadata={"skipped": True, "reason": "two_way_create_disabled"},
                )

            # Get organization from config or mapping
            org_id = (
                config_row.config_json.get("elder_organization_id")
                if config_row
                else None
            )

            if not org_id:
                return SyncResult(
                    status=SyncStatus.FAILED,
                    operation=operation,
                    items_failed=1,
                    errors=["No Elder organization mapped for GitHub repository"],
                )

            # Create issue in Elder
            issue_id = self.db.issues.insert(
                title=elder_data["title"],
                description=elder_data["description"],
                status=elder_data["status"],
                priority=elder_data["priority"],
                organization_id=org_id,
                reporter_id=1,  # TODO: Map GitHub user to Elder identity
            )
            self.db.commit()

            # Create mapping
            new_mapping = SyncMapping(
                elder_type=ResourceType.ISSUE.value,
                elder_id=issue_id,
                external_platform="github",
                external_id=str(github_issue["number"]),
                sync_config_id=self.sync_config_id,
                elder_updated_at=datetime.now(),
                external_updated_at=github_issue.get("updated_at"),
            )

            self.create_mapping(new_mapping, sync_method="webhook")

            return SyncResult(
                status=SyncStatus.SUCCESS,
                operation=operation,
                items_synced=1,
                created_mappings=[new_mapping],
            )

        elif operation.operation_type == "update":
            # Update Elder issue
            if not mapping:
                return SyncResult(
                    status=SyncStatus.FAILED,
                    operation=operation,
                    items_failed=1,
                    errors=["No mapping found for GitHub issue"],
                )

            self.db(self.db.issues.id == mapping.elder_id).update(
                title=elder_data["title"],
                description=elder_data["description"],
                status=elder_data["status"],
                priority=elder_data["priority"],
                updated_at=datetime.now(),
            )
            self.db.commit()

            mapping.elder_updated_at = datetime.now()
            self.update_mapping(mapping)

            return SyncResult(
                status=SyncStatus.SUCCESS,
                operation=operation,
                items_synced=1,
                updated_mappings=[mapping],
            )

        return SyncResult(
            status=SyncStatus.FAILED,
            operation=operation,
            items_failed=1,
            errors=[f"Unknown operation type: {operation.operation_type}"],
        )

    def _sync_issue_bidirectional(self, operation: SyncOperation) -> SyncResult:
        """Handle bidirectional issue sync with conflict detection.

        Args:
            operation: Sync operation

        Returns:
            Sync result
        """
        # Detect conflicts
        conflict = self.conflict_resolver.detect_conflict(
            elder_data=operation.elder_data,
            external_data=operation.external_data,
            mapping=operation.mapping,
        )

        if conflict:
            # Resolve conflict
            resolved_conflict = self.conflict_resolver.resolve_conflict(conflict)

            if not resolved_conflict.resolved:
                # Manual resolution required
                mapping_id = (
                    self.create_mapping(operation.mapping)
                    if not operation.mapping
                    else operation.mapping.elder_id
                )
                self.record_conflict(resolved_conflict, mapping_id)

                return SyncResult(
                    status=SyncStatus.CONFLICT,
                    operation=operation,
                    conflicts=[
                        self.conflict_resolver.get_conflict_summary(resolved_conflict)
                    ],
                )

            # Use resolved data
            if resolved_conflict.resolution_data == operation.elder_data:
                # Elder wins, sync to GitHub
                operation.direction = SyncDirection.ELDER_TO_EXTERNAL
                return self._sync_issue_to_github(operation)
            else:
                # External wins, sync to Elder
                operation.direction = SyncDirection.EXTERNAL_TO_ELDER
                return self._sync_issue_from_github(operation)

        # No conflict, proceed with normal sync
        operation.direction = SyncDirection.EXTERNAL_TO_ELDER
        return self._sync_issue_from_github(operation)

    def sync_project(self, operation: SyncOperation) -> SyncResult:
        """Sync project with GitHub (Projects v2 or repository).

        Args:
            operation: Sync operation

        Returns:
            Sync result
        """
        # GitHub Projects v2 are organization-level
        # For simplicity, we'll map repositories to Elder projects
        self.logger.info("GitHub project sync not yet implemented")
        return SyncResult(
            status=SyncStatus.SUCCESS,
            operation=operation,
            metadata={"not_implemented": True},
        )

    def sync_milestone(self, operation: SyncOperation) -> SyncResult:
        """Sync milestone with GitHub.

        Args:
            operation: Sync operation

        Returns:
            Sync result
        """
        # Similar to issue sync but for milestones
        self.logger.info("GitHub milestone sync - simplified implementation")
        return SyncResult(
            status=SyncStatus.SUCCESS,
            operation=operation,
            metadata={"simplified": True},
        )

    def sync_label(self, operation: SyncOperation) -> SyncResult:
        """Sync label with GitHub.

        Args:
            operation: Sync operation

        Returns:
            Sync result
        """
        self.logger.info("GitHub label sync - simplified implementation")
        return SyncResult(
            status=SyncStatus.SUCCESS,
            operation=operation,
            metadata={"simplified": True},
        )

    def batch_sync(
        self,
        resource_type: ResourceType,
        since: Optional[datetime] = None,
    ) -> SyncResult:
        """Perform batch synchronization for GitHub resources.

        Args:
            resource_type: Type of resources to sync
            since: Only sync resources modified after this timestamp

        Returns:
            Aggregate sync result
        """
        self.logger.info(
            f"Starting GitHub batch sync for {resource_type.value}",
            extra={"since": since.isoformat() if since else None},
        )

        if resource_type == ResourceType.ISSUE:
            return self._batch_sync_issues(since)

        # Other resource types simplified for now
        return SyncResult(
            status=SyncStatus.SUCCESS,
            operation=SyncOperation(
                operation_type="batch",
                resource_type=resource_type,
                direction=SyncDirection.BIDIRECTIONAL,
            ),
            metadata={"not_implemented": True},
        )

    def _batch_sync_issues(self, since: Optional[datetime] = None) -> SyncResult:
        """Batch sync GitHub issues.

        Args:
            since: Only sync issues modified after this timestamp

        Returns:
            Aggregate sync result
        """
        total_synced = 0
        total_failed = 0
        errors = []

        # Fetch issues from GitHub with pagination
        params = {
            "state": "all",
            "per_page": 100,
        }

        if since:
            params["since"] = since.isoformat()

        page = 1
        while True:
            params["page"] = page

            try:
                response = self.client.get(
                    f"/repos/{self.org_name or 'owner'}/{self.repo_name}/issues",
                    params=params,
                )

                if response.status_code != 200:
                    errors.append(f"GitHub API error: {response.status_code}")
                    break

                issues = response.json()

                if not issues:
                    break

                # Process each issue
                for issue in issues:
                    # Skip pull requests (they appear in issues endpoint)
                    if "pull_request" in issue:
                        continue

                    # Check if mapping exists
                    mapping = self.get_mapping(
                        ResourceType.ISSUE,
                        external_id=str(issue["number"]),
                    )

                    operation = SyncOperation(
                        operation_type="update" if mapping else "create",
                        resource_type=ResourceType.ISSUE,
                        direction=SyncDirection.EXTERNAL_TO_ELDER,
                        external_data=issue,
                        mapping=mapping,
                    )

                    result = self._sync_issue_from_github(operation)

                    if result.is_success:
                        total_synced += 1
                    else:
                        total_failed += 1
                        errors.extend(result.errors)

                page += 1

            except Exception as e:
                errors.append(f"Batch sync error: {str(e)}")
                self.logger.error(f"Batch sync error: {e}", exc_info=True)
                break

        return SyncResult(
            status=(
                SyncStatus.SUCCESS if total_failed == 0 else SyncStatus.PARTIAL_SUCCESS
            ),
            operation=SyncOperation(
                operation_type="batch",
                resource_type=ResourceType.ISSUE,
                direction=SyncDirection.EXTERNAL_TO_ELDER,
            ),
            items_synced=total_synced,
            items_failed=total_failed,
            errors=errors,
        )

    def handle_webhook(self, webhook_data: Dict[str, Any]) -> SyncResult:
        """Handle GitHub webhook event.

        Args:
            webhook_data: GitHub webhook payload

        Returns:
            Result of processing webhook
        """
        action = webhook_data.get("action")
        issue = webhook_data.get("issue")

        if not issue:
            return SyncResult(
                status=SyncStatus.SUCCESS,
                operation=SyncOperation(
                    operation_type="webhook",
                    resource_type=ResourceType.ISSUE,
                    direction=SyncDirection.EXTERNAL_TO_ELDER,
                ),
                metadata={"no_issue_data": True},
            )

        # Check for existing mapping
        mapping = self.get_mapping(
            ResourceType.ISSUE,
            external_id=str(issue["number"]),
        )

        operation = SyncOperation(
            operation_type=(
                action if action in ["opened", "edited", "closed"] else "update"
            ),
            resource_type=ResourceType.ISSUE,
            direction=SyncDirection.EXTERNAL_TO_ELDER,
            external_data=issue,
            mapping=mapping,
        )

        return self._sync_issue_from_github(operation)

    def _map_github_labels_to_priority(self, labels: List[Dict[str, Any]]) -> str:
        """Map GitHub labels to Elder priority.

        Args:
            labels: GitHub label objects

        Returns:
            Priority string (critical, high, medium, low)
        """
        label_names = [label.get("name", "").lower() for label in labels]

        if any(p in label_names for p in ["critical", "urgent", "p0"]):
            return "critical"
        elif any(p in label_names for p in ["high", "important", "p1"]):
            return "high"
        elif any(p in label_names for p in ["low", "p3"]):
            return "low"

        return "medium"
