"""GitHub sync client for two-way synchronization.

Implements two-way synchronization between Elder and GitHub:
- Issues ↔ GitHub Issues (REST API 2022-11-28)
- Projects ↔ GitHub Projects v2 (GraphQL API v4)
- Milestones ↔ GitHub Milestones (REST API 2022-11-28)
- Labels ↔ GitHub Labels (REST API 2022-11-28)
"""

# flake8: noqa: E501

from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import quote

import httpx
from penguin_dal import DAL

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

_GITHUB_API_VERSION = "2022-11-28"
_DEFAULT_LABEL_COLOR = "#808080"

_PROJECTS_ORG_QUERY = """
query($owner: String!, $cursor: String) {
  organization(login: $owner) {
    id
    projectsV2(first: 50, after: $cursor) {
      pageInfo {
        hasNextPage
        endCursor
      }
      nodes {
        id
        title
        shortDescription
        readme
        closed
        number
        url
        updatedAt
      }
    }
  }
}
"""

_PROJECTS_USER_QUERY = """
query($owner: String!, $cursor: String) {
  user(login: $owner) {
    id
    projectsV2(first: 50, after: $cursor) {
      pageInfo {
        hasNextPage
        endCursor
      }
      nodes {
        id
        title
        shortDescription
        readme
        closed
        number
        url
        updatedAt
      }
    }
  }
}
"""

_OWNER_ID_ORG_QUERY = """
query($owner: String!) {
  organization(login: $owner) {
    id
  }
}
"""

_OWNER_ID_USER_QUERY = """
query($owner: String!) {
  user(login: $owner) {
    id
  }
}
"""

_CREATE_PROJECT_V2_MUTATION = """
mutation($ownerId: ID!, $title: String!) {
  createProjectV2(input: { ownerId: $ownerId, title: $title }) {
    projectV2 {
      id
      title
      shortDescription
      closed
      number
      url
      updatedAt
    }
  }
}
"""

_UPDATE_PROJECT_V2_MUTATION = """
mutation($projectId: ID!, $title: String, $shortDescription: String, $closed: Boolean) {
  updateProjectV2(
    input: {
      projectId: $projectId
      title: $title
      shortDescription: $shortDescription
      closed: $closed
    }
  ) {
    projectV2 {
      id
      title
      shortDescription
      closed
      number
      url
      updatedAt
    }
  }
}
"""


def _parse_datetime(value: Any) -> datetime | None:
    """Parse an ISO8601 string or datetime/date into a UTC-aware datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return (
            value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        )
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            return (
                parsed.replace(tzinfo=UTC)
                if parsed.tzinfo is None
                else parsed.astimezone(UTC)
            )
        except ValueError:
            return None
    return None


def _parse_date(value: Any) -> date | None:
    """Parse an ISO date/datetime string into a ``datetime.date``."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        dt = _parse_datetime(value)
        if dt:
            return dt.date()
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def _format_github_due_on(value: Any) -> str | None:
    """Format a date/datetime/ISO string to GitHub milestone ``YYYY-MM-DDTHH:MM:SSZ``."""
    if value is None or value == "":
        return None
    dt = _parse_datetime(value)
    if dt is not None:
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return None


def _normalize_color_with_hash(color: str | None) -> str:
    """Return a 7-character ``#rrggbb`` hex color for Elder ``issue_labels``."""
    cleaned = (color or "").strip().lstrip("#")
    if len(cleaned) == 6 and all(c in "0123456789abcdefABCDEF" for c in cleaned):
        return f"#{cleaned.lower()}"
    if len(cleaned) == 3 and all(c in "0123456789abcdefABCDEF" for c in cleaned):
        expanded = "".join(c * 2 for c in cleaned)
        return f"#{expanded.lower()}"
    return _DEFAULT_LABEL_COLOR


def _normalize_color_no_hash(color: str | None) -> str:
    """Return a 6-character hex color without ``#`` for GitHub REST labels API."""
    return _normalize_color_with_hash(color).lstrip("#")


def _compute_graphql_url(base_url: str, explicit_graphql_url: str | None) -> str:
    """Compute the GitHub GraphQL v4 endpoint URL for cloud or GHES."""
    if explicit_graphql_url and explicit_graphql_url.strip():
        return explicit_graphql_url.strip().rstrip("/")
    normalized = base_url.rstrip("/")
    if normalized == "https://api.github.com":
        return "https://api.github.com/graphql"
    if normalized.endswith("/api/v3"):
        return f"{normalized[: -len('/api/v3')]}/api/graphql"
    return f"{normalized}/graphql"


class GitHubSyncClient(BaseSyncClient):
    """GitHub-specific sync client implementation.

    Handles two-way synchronization with GitHub using REST API (2022-11-28)
    and Projects v2 GraphQL API v4.
    """

    def __init__(
        self,
        config: dict[str, Any],
        db: DAL,
        sync_config_id: int,
        logger: Any,
    ):
        """Initialize GitHub sync client.

        Args:
            config: GitHub configuration with ``api_token``, ``owner`` (or
                ``org_name``), ``repo_name``, and optional ``base_url``/``graphql_url``
            db: penguin-dal database instance
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

        self.api_token = (config.get("api_token") or "").strip() or None
        owner = (config.get("owner") or config.get("org_name") or "").strip() or None
        self.owner = owner
        self.org_name = (config.get("org_name") or owner or "").strip() or None
        self.repo_name = (config.get("repo_name") or "").strip() or None
        self.base_url = (
            (config.get("base_url") or "https://api.github.com").strip().rstrip("/")
        )
        self.graphql_url = _compute_graphql_url(
            self.base_url, config.get("graphql_url")
        )

        self.client = httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_token or ''}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": _GITHUB_API_VERSION,
            },
            timeout=30.0,
        )

        self.conflict_resolver = ConflictResolver(logger)

    def _repo_path(self, suffix: str = "") -> str:
        """Build repository API path, requiring explicit ``owner`` and ``repo_name``."""
        if not self.owner or not self.repo_name:
            raise ValueError("GitHub owner and repo_name must be configured")
        return f"/repos/{quote(self.owner, safe='')}/{quote(self.repo_name, safe='')}{suffix}"

    def validate_config(self) -> bool:
        """Validate GitHub configuration.

        Requires ``api_token``, ``owner`` (or ``org_name``), and ``repo_name``.
        """
        if not self.api_token:
            self.logger.error("GitHub API token is required")
            return False
        if not self.owner:
            self.logger.error("GitHub owner or org_name is required")
            return False
        if not self.repo_name:
            self.logger.error("GitHub repository name is required")
            return False
        return True

    def test_connection_detail(self) -> dict[str, Any]:
        """Test connection to GitHub repository and return structured details."""
        if not self.validate_config():
            return {
                "connected": False,
                "success": False,
                "platform": "github",
                "error": "Missing required GitHub configuration (api_token, owner, repo_name)",
            }

        try:
            response = self.client.get(self._repo_path())
            if response.status_code == 200:
                repo_data = response.json()
                return {
                    "connected": True,
                    "success": True,
                    "platform": "github",
                    "status_code": 200,
                    "repository": {
                        "full_name": repo_data.get("full_name"),
                        "private": repo_data.get("private"),
                        "default_branch": repo_data.get("default_branch"),
                        "html_url": repo_data.get("html_url"),
                        "open_issues_count": repo_data.get("open_issues_count"),
                    },
                }

            error_msg = f"GitHub API returned HTTP {response.status_code}"
            try:
                body = response.json()
                if isinstance(body, dict) and body.get("message"):
                    error_msg = f"{error_msg}: {body['message']}"
            except Exception:
                pass

            return {
                "connected": False,
                "success": False,
                "platform": "github",
                "status_code": response.status_code,
                "error": error_msg,
            }
        except Exception as e:
            self.logger.error(f"GitHub connection test failed: {e}")
            return {
                "connected": False,
                "success": False,
                "platform": "github",
                "error": str(e),
            }

    def test_connection(self) -> bool:
        """Test connection to GitHub REST API."""
        return bool(self.test_connection_detail().get("connected"))

    def _get_sync_config_row(self) -> Any:
        """Fetch this client's ``sync_configs`` row if available."""
        return self.db(self.db.sync_configs.id == self.sync_config_id).select().first()

    def _resolve_org_and_tenant(self) -> tuple[int | None, int | None, str | None]:
        """Resolve Elder ``organization_id`` and its owning ``tenant_id``."""
        org_id = self.config.get("elder_organization_id")
        if not org_id:
            config_row = self._get_sync_config_row()
            if config_row and isinstance(
                getattr(config_row, "config_json", None), dict
            ):
                org_id = config_row.config_json.get("elder_organization_id")

        if not org_id:
            return None, None, "No Elder organization mapped for GitHub repository"

        try:
            org_id_int = int(org_id)
        except (TypeError, ValueError):
            return None, None, f"Invalid elder_organization_id: {org_id!r}"

        org_row = self.db(self.db.organizations.id == org_id_int).select().first()
        if not org_row:
            return None, None, f"Elder organization {org_id_int} not found"

        tenant_id = getattr(org_row, "tenant_id", None)
        return org_id_int, tenant_id, None

    def _should_allow_create(self, operation: SyncOperation) -> bool:
        """Determine whether creating new Elder records is permitted."""
        if operation.metadata.get("allow_create"):
            return True
        config_row = self._get_sync_config_row()
        if config_row is not None:
            return bool(getattr(config_row, "two_way_create", False))
        return bool(self.config.get("two_way_create", False))

    def _upsert_mapping(
        self,
        resource_type: ResourceType,
        elder_id: int,
        external_id: str,
        *,
        elder_updated_at: datetime | None = None,
        external_updated_at: datetime | None = None,
        sync_method: str = "batch",
        existing: SyncMapping | None = None,
    ) -> SyncMapping:
        """Create or update a ``sync_mappings`` record idempotently."""
        mapping = (
            existing
            or self.get_mapping(resource_type, elder_id=elder_id)
            or self.get_mapping(resource_type, external_id=external_id)
        )

        if mapping:
            mapping.elder_updated_at = elder_updated_at
            mapping.external_updated_at = external_updated_at
            if mapping.external_id != external_id:
                now = datetime.now(UTC)
                self.db(
                    (self.db.sync_mappings.elder_type == resource_type.value)
                    & (self.db.sync_mappings.elder_id == mapping.elder_id)
                    & (self.db.sync_mappings.external_platform == self.platform_name)
                    & (self.db.sync_mappings.sync_config_id == self.sync_config_id)
                ).update(
                    external_id=external_id,
                    sync_status="synced",
                    last_synced_at=now,
                    elder_updated_at=elder_updated_at,
                    external_updated_at=external_updated_at,
                    updated_at=now,
                )
                self.db.commit()
                mapping.external_id = external_id
            else:
                self.update_mapping(mapping)
            return mapping

        new_mapping = SyncMapping(
            elder_type=resource_type.value,
            elder_id=elder_id,
            external_platform=self.platform_name,
            external_id=external_id,
            sync_config_id=self.sync_config_id,
            elder_updated_at=elder_updated_at,
            external_updated_at=external_updated_at,
        )
        self.create_mapping(new_mapping, sync_method=sync_method)
        return new_mapping

    # ------------------------------------------------------------------
    # Issues
    # ------------------------------------------------------------------

    def sync_issue(self, operation: SyncOperation) -> SyncResult:
        """Sync a single issue with GitHub."""
        self.logger.info(
            f"Syncing GitHub issue: {operation.operation_type}",
            extra={"correlation_id": operation.correlation_id},
        )

        try:
            if operation.direction == SyncDirection.ELDER_TO_EXTERNAL:
                return self._sync_issue_to_github(operation)
            if operation.direction == SyncDirection.EXTERNAL_TO_ELDER:
                return self._sync_issue_from_github(operation)
            return self._sync_issue_bidirectional(operation)
        except Exception as e:
            self.logger.error(
                f"GitHub issue sync failed: {e}",
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
        """Push an Elder issue to GitHub REST API."""
        elder_issue = operation.elder_data or {}
        mapping = operation.mapping
        if mapping is None and elder_issue.get("id") is not None:
            mapping = self.get_mapping(ResourceType.ISSUE, elder_id=elder_issue["id"])

        status_upper = str(elder_issue.get("status") or "OPEN").upper()
        state = "open" if status_upper in {"OPEN", "IN_PROGRESS"} else "closed"

        github_data: dict[str, Any] = {
            "title": elder_issue.get("title") or "",
            "body": elder_issue.get("description") or "",
            "state": state,
        }

        if elder_issue.get("labels"):
            labels_list: list[str] = []
            for lbl in elder_issue["labels"]:
                if isinstance(lbl, dict):
                    if lbl.get("name"):
                        labels_list.append(str(lbl["name"]))
                elif lbl:
                    labels_list.append(str(lbl))
            if labels_list:
                github_data["labels"] = labels_list

        # Official GitHub REST API uses `assignees` array (not deprecated `assignee`)
        assignees_raw = elder_issue.get("assignees")
        if assignees_raw is None and elder_issue.get("assignee"):
            assignees_raw = [elder_issue["assignee"]]
        if assignees_raw:
            assignees = [str(a) for a in assignees_raw if a]
            if assignees:
                github_data["assignees"] = assignees

        if elder_issue.get("milestone_id"):
            ms_mapping = self.get_mapping(
                ResourceType.MILESTONE, elder_id=elder_issue["milestone_id"]
            )
            if ms_mapping:
                github_data["milestone"] = int(ms_mapping.external_id)

        if operation.operation_type == "create" and mapping is None:
            response = self.client.post(
                self._repo_path("/issues"),
                json=github_data,
            )
            if response.status_code == 201:
                gh_issue = response.json()
                new_mapping = self._upsert_mapping(
                    ResourceType.ISSUE,
                    elder_id=elder_issue["id"],
                    external_id=str(gh_issue["number"]),
                    elder_updated_at=_parse_datetime(elder_issue.get("updated_at")),
                    external_updated_at=_parse_datetime(gh_issue.get("updated_at")),
                    sync_method="manual",
                )
                return SyncResult(
                    status=SyncStatus.SUCCESS,
                    operation=operation,
                    items_synced=1,
                    created_mappings=[new_mapping],
                )
            return SyncResult(
                status=SyncStatus.FAILED,
                operation=operation,
                items_failed=1,
                errors=[f"GitHub API error: {response.status_code} - {response.text}"],
            )

        if not mapping:
            return SyncResult(
                status=SyncStatus.FAILED,
                operation=operation,
                items_failed=1,
                errors=["No mapping found for issue update"],
            )

        response = self.client.patch(
            self._repo_path(f"/issues/{mapping.external_id}"),
            json=github_data,
        )
        if response.status_code == 200:
            gh_issue = response.json()
            updated = self._upsert_mapping(
                ResourceType.ISSUE,
                elder_id=mapping.elder_id,
                external_id=mapping.external_id,
                elder_updated_at=_parse_datetime(elder_issue.get("updated_at")),
                external_updated_at=_parse_datetime(gh_issue.get("updated_at")),
                existing=mapping,
            )
            return SyncResult(
                status=SyncStatus.SUCCESS,
                operation=operation,
                items_synced=1,
                updated_mappings=[updated],
            )

        return SyncResult(
            status=SyncStatus.FAILED,
            operation=operation,
            items_failed=1,
            errors=[f"GitHub API error: {response.status_code} - {response.text}"],
        )

    def _sync_issue_labels(self, issue_id: int, labels: list[Any]) -> None:
        """Upsert GitHub labels into ``db.issue_labels`` and link via ``db.issue_label_assignments``."""
        if not labels:
            return
        now = datetime.now(UTC)
        for item in labels:
            if isinstance(item, dict):
                raw_name = (item.get("name") or "").strip()
                raw_color = item.get("color")
                desc = item.get("description")
            else:
                raw_name = str(item).strip()
                raw_color = None
                desc = None

            if not raw_name:
                continue

            name = raw_name[:50]
            color = _normalize_color_with_hash(raw_color)

            label_row = self.db(self.db.issue_labels.name == name).select().first()
            if label_row:
                label_id = label_row.id
            else:
                label_id = self.db.issue_labels.insert(
                    name=name,
                    color=color,
                    description=desc,
                    created_at=now,
                    updated_at=now,
                )

            existing_assign = (
                self.db(
                    (self.db.issue_label_assignments.issue_id == issue_id)
                    & (self.db.issue_label_assignments.label_id == label_id)
                )
                .select()
                .first()
            )
            if not existing_assign:
                self.db.issue_label_assignments.insert(
                    issue_id=issue_id,
                    label_id=label_id,
                )

        self.db.commit()

    def _sync_issue_milestone(
        self,
        issue_id: int,
        gh_milestone: dict[str, Any] | None,
        org_id: int,
        tenant_id: int | None,
    ) -> None:
        """Upsert a GitHub milestone into ``db.milestones`` and link via ``db.issue_milestone_links``."""
        if not isinstance(gh_milestone, dict) or gh_milestone.get("number") is None:
            return

        milestone_id = self._upsert_elder_milestone(
            gh_milestone, org_id=org_id, tenant_id=tenant_id
        )
        if not milestone_id:
            return

        existing_link = (
            self.db(
                (self.db.issue_milestone_links.issue_id == issue_id)
                & (self.db.issue_milestone_links.milestone_id == milestone_id)
            )
            .select()
            .first()
        )
        if not existing_link:
            self.db.issue_milestone_links.insert(
                issue_id=issue_id,
                milestone_id=milestone_id,
                created_at=datetime.now(UTC),
            )
            self.db.commit()

    def _sync_issue_from_github(self, operation: SyncOperation) -> SyncResult:
        """Pull/upsert a GitHub issue into Elder ``db.issues``."""
        gh_issue = operation.external_data or {}
        if gh_issue.get("number") is None:
            return SyncResult(
                status=SyncStatus.FAILED,
                operation=operation,
                items_failed=1,
                errors=["GitHub issue payload missing 'number'"],
            )

        ext_id = str(gh_issue["number"])
        mapping = operation.mapping or self.get_mapping(
            ResourceType.ISSUE, external_id=ext_id
        )

        org_id, tenant_id, org_err = self._resolve_org_and_tenant()
        if org_err or org_id is None:
            return SyncResult(
                status=SyncStatus.FAILED,
                operation=operation,
                items_failed=1,
                errors=[org_err or "No Elder organization mapped"],
            )

        now = datetime.now(UTC)
        status_upper = "OPEN" if gh_issue.get("state", "open") == "open" else "CLOSED"
        gh_labels = gh_issue.get("labels") or []
        priority_upper = self._map_github_labels_to_priority(gh_labels)
        issue_type_upper = self._map_github_labels_to_issue_type(gh_labels)
        ext_updated = _parse_datetime(gh_issue.get("updated_at"))
        closed_at = (
            _parse_datetime(gh_issue.get("closed_at")) or now
            if status_upper == "CLOSED"
            else None
        )

        elder_fields = {
            "title": (gh_issue.get("title") or "")[:255],
            "description": gh_issue.get("body") or "",
            "status": status_upper,
            "priority": priority_upper,
            "issue_type": issue_type_upper,
            "closed_at": closed_at,
            "updated_at": now,
        }

        is_create = operation.operation_type == "create" and mapping is None
        if is_create:
            if not self._should_allow_create(operation):
                self.logger.info(
                    "Two-way creation disabled, skipping GitHub issue creation in Elder"
                )
                return SyncResult(
                    status=SyncStatus.SUCCESS,
                    operation=operation,
                    metadata={"skipped": True, "reason": "two_way_create_disabled"},
                )

            issue_id = self.db.issues.insert(
                title=elder_fields["title"],
                description=elder_fields["description"],
                status=elder_fields["status"],
                priority=elder_fields["priority"],
                issue_type=elder_fields["issue_type"],
                resource_type="organization",
                resource_id=org_id,
                organization_id=org_id,
                tenant_id=tenant_id,
                is_incident=0,
                reporter_id=None,
                closed_at=closed_at,
                created_at=now,
                updated_at=now,
            )
            self.db.commit()

            self._sync_issue_labels(issue_id, gh_labels)
            self._sync_issue_milestone(
                issue_id, gh_issue.get("milestone"), org_id=org_id, tenant_id=tenant_id
            )

            created_mapping = self._upsert_mapping(
                ResourceType.ISSUE,
                elder_id=issue_id,
                external_id=ext_id,
                elder_updated_at=now,
                external_updated_at=ext_updated,
                sync_method=operation.metadata.get("sync_method", "webhook"),
            )
            return SyncResult(
                status=SyncStatus.SUCCESS,
                operation=operation,
                items_synced=1,
                created_mappings=[created_mapping],
            )

        if not mapping:
            if not self._should_allow_create(operation):
                return SyncResult(
                    status=SyncStatus.FAILED,
                    operation=operation,
                    items_failed=1,
                    errors=["No mapping found for GitHub issue"],
                )
            operation.operation_type = "create"
            return self._sync_issue_from_github(operation)

        self.db(self.db.issues.id == mapping.elder_id).update(**elder_fields)
        self.db.commit()

        self._sync_issue_labels(mapping.elder_id, gh_labels)
        self._sync_issue_milestone(
            mapping.elder_id,
            gh_issue.get("milestone"),
            org_id=org_id,
            tenant_id=tenant_id,
        )

        updated_mapping = self._upsert_mapping(
            ResourceType.ISSUE,
            elder_id=mapping.elder_id,
            external_id=ext_id,
            elder_updated_at=now,
            external_updated_at=ext_updated,
            existing=mapping,
        )
        return SyncResult(
            status=SyncStatus.SUCCESS,
            operation=operation,
            items_synced=1,
            updated_mappings=[updated_mapping],
        )

    def _sync_issue_bidirectional(self, operation: SyncOperation) -> SyncResult:
        """Handle bidirectional issue sync with conflict detection."""
        conflict = self.conflict_resolver.detect_conflict(
            elder_data=operation.elder_data or {},
            external_data=operation.external_data or {},
            mapping=operation.mapping,
        )

        if conflict:
            resolved_conflict = self.conflict_resolver.resolve_conflict(conflict)
            if not resolved_conflict.resolved:
                mapping_row = (
                    self.db(
                        (self.db.sync_mappings.elder_type == ResourceType.ISSUE.value)
                        & (
                            self.db.sync_mappings.external_platform
                            == self.platform_name
                        )
                        & (self.db.sync_mappings.sync_config_id == self.sync_config_id)
                        & (
                            self.db.sync_mappings.elder_id
                            == (operation.mapping.elder_id if operation.mapping else 0)
                        )
                    )
                    .select()
                    .first()
                )
                mapping_id = mapping_row.id if mapping_row else 0
                if mapping_id:
                    self.record_conflict(resolved_conflict, mapping_id)

                return SyncResult(
                    status=SyncStatus.CONFLICT,
                    operation=operation,
                    conflicts=[
                        self.conflict_resolver.get_conflict_summary(resolved_conflict)
                    ],
                )

            if resolved_conflict.resolution_data == operation.elder_data:
                operation.direction = SyncDirection.ELDER_TO_EXTERNAL
                return self._sync_issue_to_github(operation)

        operation.direction = SyncDirection.EXTERNAL_TO_ELDER
        return self._sync_issue_from_github(operation)

    # ------------------------------------------------------------------
    # Milestones
    # ------------------------------------------------------------------

    def _upsert_elder_milestone(
        self,
        gh_ms: dict[str, Any],
        *,
        org_id: int,
        tenant_id: int | None,
        existing_mapping: SyncMapping | None = None,
        sync_method: str = "batch",
    ) -> int:
        """Insert or update an Elder ``db.milestones`` row and its mapping."""
        ext_id = str(gh_ms["number"])
        mapping = existing_mapping or self.get_mapping(
            ResourceType.MILESTONE, external_id=ext_id
        )
        now = datetime.now(UTC)
        status = "closed" if gh_ms.get("state") == "closed" else "open"
        due_date = _parse_date(gh_ms.get("due_on"))
        closed_at = (
            _parse_datetime(gh_ms.get("closed_at")) or now
            if status == "closed"
            else None
        )
        fields = {
            "title": (gh_ms.get("title") or "")[:255],
            "description": gh_ms.get("description") or "",
            "status": status,
            "due_date": due_date,
            "closed_at": closed_at,
            "updated_at": now,
        }

        if mapping:
            self.db(self.db.milestones.id == mapping.elder_id).update(**fields)
            self.db.commit()
            self._upsert_mapping(
                ResourceType.MILESTONE,
                elder_id=mapping.elder_id,
                external_id=ext_id,
                elder_updated_at=now,
                external_updated_at=_parse_datetime(gh_ms.get("updated_at")),
                existing=mapping,
            )
            return mapping.elder_id

        milestone_id = self.db.milestones.insert(
            title=fields["title"],
            description=fields["description"],
            status=fields["status"],
            organization_id=org_id,
            tenant_id=tenant_id,
            due_date=due_date,
            closed_at=closed_at,
            created_at=now,
            updated_at=now,
        )
        self.db.commit()
        self._upsert_mapping(
            ResourceType.MILESTONE,
            elder_id=milestone_id,
            external_id=ext_id,
            elder_updated_at=now,
            external_updated_at=_parse_datetime(gh_ms.get("updated_at")),
            sync_method=sync_method,
        )
        return milestone_id

    def sync_milestone(self, operation: SyncOperation) -> SyncResult:
        """Sync a single milestone with GitHub Milestones REST API."""
        try:
            if operation.direction == SyncDirection.ELDER_TO_EXTERNAL:
                elder_ms = operation.elder_data or {}
                mapping = operation.mapping
                if mapping is None and elder_ms.get("id") is not None:
                    mapping = self.get_mapping(
                        ResourceType.MILESTONE, elder_id=elder_ms["id"]
                    )

                payload: dict[str, Any] = {
                    "title": elder_ms.get("title") or "",
                    "state": (
                        "closed"
                        if str(elder_ms.get("status") or "open").lower() == "closed"
                        else "open"
                    ),
                    "description": elder_ms.get("description") or "",
                }
                due_on = _format_github_due_on(elder_ms.get("due_date"))
                if due_on:
                    payload["due_on"] = due_on

                if operation.operation_type == "create" and mapping is None:
                    resp = self.client.post(
                        self._repo_path("/milestones"), json=payload
                    )
                    if resp.status_code == 201:
                        gh_ms = resp.json()
                        new_map = self._upsert_mapping(
                            ResourceType.MILESTONE,
                            elder_id=elder_ms["id"],
                            external_id=str(gh_ms["number"]),
                            elder_updated_at=_parse_datetime(
                                elder_ms.get("updated_at")
                            ),
                            external_updated_at=_parse_datetime(
                                gh_ms.get("updated_at")
                            ),
                            sync_method="manual",
                        )
                        return SyncResult(
                            status=SyncStatus.SUCCESS,
                            operation=operation,
                            items_synced=1,
                            created_mappings=[new_map],
                        )
                    return SyncResult(
                        status=SyncStatus.FAILED,
                        operation=operation,
                        items_failed=1,
                        errors=[f"GitHub API error: {resp.status_code} - {resp.text}"],
                    )

                if not mapping:
                    return SyncResult(
                        status=SyncStatus.FAILED,
                        operation=operation,
                        items_failed=1,
                        errors=["No mapping found for milestone update"],
                    )

                resp = self.client.patch(
                    self._repo_path(f"/milestones/{mapping.external_id}"),
                    json=payload,
                )
                if resp.status_code == 200:
                    gh_ms = resp.json()
                    upd_map = self._upsert_mapping(
                        ResourceType.MILESTONE,
                        elder_id=mapping.elder_id,
                        external_id=mapping.external_id,
                        elder_updated_at=_parse_datetime(elder_ms.get("updated_at")),
                        external_updated_at=_parse_datetime(gh_ms.get("updated_at")),
                        existing=mapping,
                    )
                    return SyncResult(
                        status=SyncStatus.SUCCESS,
                        operation=operation,
                        items_synced=1,
                        updated_mappings=[upd_map],
                    )
                return SyncResult(
                    status=SyncStatus.FAILED,
                    operation=operation,
                    items_failed=1,
                    errors=[f"GitHub API error: {resp.status_code} - {resp.text}"],
                )

            # EXTERNAL_TO_ELDER / BIDIRECTIONAL pull
            gh_ms = operation.external_data or {}
            if gh_ms.get("number") is None:
                return SyncResult(
                    status=SyncStatus.FAILED,
                    operation=operation,
                    items_failed=1,
                    errors=["GitHub milestone payload missing 'number'"],
                )
            mapping = operation.mapping or self.get_mapping(
                ResourceType.MILESTONE, external_id=str(gh_ms["number"])
            )
            if mapping is None and not self._should_allow_create(operation):
                return SyncResult(
                    status=SyncStatus.SUCCESS,
                    operation=operation,
                    metadata={"skipped": True, "reason": "two_way_create_disabled"},
                )

            org_id, tenant_id, org_err = self._resolve_org_and_tenant()
            if org_err or org_id is None:
                return SyncResult(
                    status=SyncStatus.FAILED,
                    operation=operation,
                    items_failed=1,
                    errors=[org_err or "No Elder organization mapped"],
                )

            self._upsert_elder_milestone(
                gh_ms,
                org_id=org_id,
                tenant_id=tenant_id,
                existing_mapping=mapping,
                sync_method=operation.metadata.get("sync_method", "batch"),
            )
            return SyncResult(
                status=SyncStatus.SUCCESS,
                operation=operation,
                items_synced=1,
            )
        except Exception as e:
            self.logger.error(f"GitHub milestone sync failed: {e}", exc_info=True)
            return SyncResult(
                status=SyncStatus.FAILED,
                operation=operation,
                items_failed=1,
                errors=[str(e)],
            )

    # ------------------------------------------------------------------
    # Labels
    # ------------------------------------------------------------------

    def _upsert_elder_label(
        self,
        gh_label: dict[str, Any],
        *,
        existing_mapping: SyncMapping | None = None,
        sync_method: str = "batch",
    ) -> int:
        """Insert or update an Elder ``db.issue_labels`` row and its mapping."""
        raw_name = (gh_label.get("name") or "").strip()
        ext_id = raw_name
        name = raw_name[:50]
        color = _normalize_color_with_hash(gh_label.get("color"))
        desc = gh_label.get("description")
        now = datetime.now(UTC)

        mapping = existing_mapping or self.get_mapping(
            ResourceType.LABEL, external_id=ext_id
        )
        if mapping:
            self.db(self.db.issue_labels.id == mapping.elder_id).update(
                name=name,
                color=color,
                description=desc,
                updated_at=now,
            )
            self.db.commit()
            self._upsert_mapping(
                ResourceType.LABEL,
                elder_id=mapping.elder_id,
                external_id=ext_id,
                elder_updated_at=now,
                existing=mapping,
            )
            return mapping.elder_id

        existing_row = self.db(self.db.issue_labels.name == name).select().first()
        if existing_row:
            self.db(self.db.issue_labels.id == existing_row.id).update(
                color=color,
                description=desc,
                updated_at=now,
            )
            self.db.commit()
            label_id = existing_row.id
        else:
            label_id = self.db.issue_labels.insert(
                name=name,
                color=color,
                description=desc,
                created_at=now,
                updated_at=now,
            )
            self.db.commit()

        self._upsert_mapping(
            ResourceType.LABEL,
            elder_id=label_id,
            external_id=ext_id,
            elder_updated_at=now,
            sync_method=sync_method,
        )
        return label_id

    def sync_label(self, operation: SyncOperation) -> SyncResult:
        """Sync a single label with GitHub Labels REST API."""
        try:
            if operation.direction == SyncDirection.ELDER_TO_EXTERNAL:
                elder_lbl = operation.elder_data or {}
                mapping = operation.mapping
                if mapping is None and elder_lbl.get("id") is not None:
                    mapping = self.get_mapping(
                        ResourceType.LABEL, elder_id=elder_lbl["id"]
                    )

                name = (elder_lbl.get("name") or "").strip()
                color = _normalize_color_no_hash(elder_lbl.get("color"))
                description = (elder_lbl.get("description") or "")[:100]

                if operation.operation_type == "create" and mapping is None:
                    payload = {
                        "name": name,
                        "color": color,
                        "description": description,
                    }
                    resp = self.client.post(self._repo_path("/labels"), json=payload)
                    if resp.status_code == 201:
                        gh_lbl = resp.json()
                        new_map = self._upsert_mapping(
                            ResourceType.LABEL,
                            elder_id=elder_lbl["id"],
                            external_id=gh_lbl["name"],
                            elder_updated_at=_parse_datetime(
                                elder_lbl.get("updated_at")
                            ),
                            sync_method="manual",
                        )
                        return SyncResult(
                            status=SyncStatus.SUCCESS,
                            operation=operation,
                            items_synced=1,
                            created_mappings=[new_map],
                        )

                    if resp.status_code == 422 and "already_exists" in resp.text:
                        encoded = quote(name, safe="")
                        self.client.get(self._repo_path(f"/labels/{encoded}"))
                        patch_resp = self.client.patch(
                            self._repo_path(f"/labels/{encoded}"),
                            json={
                                "new_name": name,
                                "color": color,
                                "description": description,
                            },
                        )
                        if patch_resp.status_code == 200:
                            gh_lbl = patch_resp.json()
                            new_map = self._upsert_mapping(
                                ResourceType.LABEL,
                                elder_id=elder_lbl["id"],
                                external_id=gh_lbl.get("name") or name,
                                elder_updated_at=_parse_datetime(
                                    elder_lbl.get("updated_at")
                                ),
                                sync_method="manual",
                            )
                            return SyncResult(
                                status=SyncStatus.SUCCESS,
                                operation=operation,
                                items_synced=1,
                                created_mappings=[new_map],
                            )
                        return SyncResult(
                            status=SyncStatus.FAILED,
                            operation=operation,
                            items_failed=1,
                            errors=[
                                f"GitHub API error: {patch_resp.status_code} - {patch_resp.text}"
                            ],
                        )

                    return SyncResult(
                        status=SyncStatus.FAILED,
                        operation=operation,
                        items_failed=1,
                        errors=[f"GitHub API error: {resp.status_code} - {resp.text}"],
                    )

                if not mapping:
                    return SyncResult(
                        status=SyncStatus.FAILED,
                        operation=operation,
                        items_failed=1,
                        errors=["No mapping found for label update"],
                    )

                encoded_ext = quote(mapping.external_id, safe="")
                resp = self.client.patch(
                    self._repo_path(f"/labels/{encoded_ext}"),
                    json={
                        "new_name": name,
                        "color": color,
                        "description": description,
                    },
                )
                if resp.status_code == 200:
                    gh_lbl = resp.json()
                    upd_map = self._upsert_mapping(
                        ResourceType.LABEL,
                        elder_id=mapping.elder_id,
                        external_id=gh_lbl["name"],
                        elder_updated_at=_parse_datetime(elder_lbl.get("updated_at")),
                        existing=mapping,
                    )
                    return SyncResult(
                        status=SyncStatus.SUCCESS,
                        operation=operation,
                        items_synced=1,
                        updated_mappings=[upd_map],
                    )
                return SyncResult(
                    status=SyncStatus.FAILED,
                    operation=operation,
                    items_failed=1,
                    errors=[f"GitHub API error: {resp.status_code} - {resp.text}"],
                )

            # EXTERNAL_TO_ELDER pull
            gh_lbl = operation.external_data or {}
            if not gh_lbl.get("name"):
                return SyncResult(
                    status=SyncStatus.FAILED,
                    operation=operation,
                    items_failed=1,
                    errors=["GitHub label payload missing 'name'"],
                )
            lookup_ext = operation.metadata.get("previous_name") or gh_lbl["name"]
            mapping = operation.mapping or self.get_mapping(
                ResourceType.LABEL, external_id=str(lookup_ext)
            )
            if mapping is None and not self._should_allow_create(operation):
                return SyncResult(
                    status=SyncStatus.SUCCESS,
                    operation=operation,
                    metadata={"skipped": True, "reason": "two_way_create_disabled"},
                )

            self._upsert_elder_label(
                gh_lbl,
                existing_mapping=mapping,
                sync_method=operation.metadata.get("sync_method", "batch"),
            )
            return SyncResult(
                status=SyncStatus.SUCCESS,
                operation=operation,
                items_synced=1,
            )
        except Exception as e:
            self.logger.error(f"GitHub label sync failed: {e}", exc_info=True)
            return SyncResult(
                status=SyncStatus.FAILED,
                operation=operation,
                items_failed=1,
                errors=[str(e)],
            )

    # ------------------------------------------------------------------
    # Projects (GitHub Projects v2 GraphQL API v4)
    # ------------------------------------------------------------------

    def _graphql(
        self, query: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Execute a GraphQL query or mutation against ``self.graphql_url``."""
        resp = self.client.post(
            self.graphql_url,
            json={"query": query, "variables": variables or {}},
        )
        if resp.status_code != 200:
            raise RuntimeError(f"GitHub GraphQL HTTP {resp.status_code}: {resp.text}")
        payload = resp.json()
        return payload

    def _fetch_graphql_owner_id(self) -> str:
        """Resolve GitHub GraphQL node ID for ``self.owner`` (org first, then user)."""
        org_res = self._graphql(_OWNER_ID_ORG_QUERY, {"owner": self.owner})
        org_node = (org_res.get("data") or {}).get("organization")
        if org_node and org_node.get("id"):
            return str(org_node["id"])

        user_res = self._graphql(_OWNER_ID_USER_QUERY, {"owner": self.owner})
        user_node = (user_res.get("data") or {}).get("user")
        if user_node and user_node.get("id"):
            return str(user_node["id"])

        errors = org_res.get("errors") or user_res.get("errors") or []
        raise RuntimeError(
            f"Unable to resolve GitHub owner ID for {self.owner}: {errors}"
        )

    def _upsert_elder_project(
        self,
        node: dict[str, Any],
        *,
        org_id: int,
        tenant_id: int | None,
        existing_mapping: SyncMapping | None = None,
        sync_method: str = "batch",
    ) -> int:
        """Insert or update an Elder ``db.projects`` row from a Projects v2 node."""
        ext_id = str(node["id"])
        mapping = existing_mapping or self.get_mapping(
            ResourceType.PROJECT, external_id=ext_id
        )
        now = datetime.now(UTC)
        status = "archived" if node.get("closed") else "active"
        fields = {
            "name": (node.get("title") or "")[:255],
            "description": node.get("shortDescription") or node.get("readme") or "",
            "status": status,
            "updated_at": now,
        }
        ext_updated = _parse_datetime(node.get("updatedAt"))

        if mapping:
            self.db(self.db.projects.id == mapping.elder_id).update(**fields)
            self.db.commit()
            self._upsert_mapping(
                ResourceType.PROJECT,
                elder_id=mapping.elder_id,
                external_id=ext_id,
                elder_updated_at=now,
                external_updated_at=ext_updated,
                existing=mapping,
            )
            return mapping.elder_id

        project_id = self.db.projects.insert(
            name=fields["name"],
            description=fields["description"],
            status=fields["status"],
            organization_id=org_id,
            tenant_id=tenant_id,
            created_at=now,
            updated_at=now,
        )
        self.db.commit()
        self._upsert_mapping(
            ResourceType.PROJECT,
            elder_id=project_id,
            external_id=ext_id,
            elder_updated_at=now,
            external_updated_at=ext_updated,
            sync_method=sync_method,
        )
        return project_id

    def sync_project(self, operation: SyncOperation) -> SyncResult:
        """Sync a single project with GitHub Projects v2 GraphQL API."""
        try:
            if operation.direction == SyncDirection.ELDER_TO_EXTERNAL:
                elder_proj = operation.elder_data or {}
                mapping = operation.mapping
                if mapping is None and elder_proj.get("id") is not None:
                    mapping = self.get_mapping(
                        ResourceType.PROJECT, elder_id=elder_proj["id"]
                    )

                title = elder_proj.get("name") or elder_proj.get("title") or ""
                short_desc = elder_proj.get("description") or ""
                closed = str(elder_proj.get("status") or "active").lower() in {
                    "archived",
                    "completed",
                    "closed",
                }

                if operation.operation_type == "create" and mapping is None:
                    owner_node_id = self._fetch_graphql_owner_id()
                    created_res = self._graphql(
                        _CREATE_PROJECT_V2_MUTATION,
                        {"ownerId": owner_node_id, "title": title},
                    )
                    if created_res.get("errors"):
                        return SyncResult(
                            status=SyncStatus.FAILED,
                            operation=operation,
                            items_failed=1,
                            errors=[str(created_res["errors"])],
                        )
                    proj_node = (
                        (created_res.get("data") or {})
                        .get("createProjectV2", {})
                        .get("projectV2")
                    )
                    if not proj_node:
                        return SyncResult(
                            status=SyncStatus.FAILED,
                            operation=operation,
                            items_failed=1,
                            errors=[
                                "GitHub createProjectV2 returned no projectV2 node"
                            ],
                        )

                    if short_desc or closed:
                        upd_res = self._graphql(
                            _UPDATE_PROJECT_V2_MUTATION,
                            {
                                "projectId": proj_node["id"],
                                "title": title,
                                "shortDescription": short_desc,
                                "closed": closed,
                            },
                        )
                        updated_node = (
                            (upd_res.get("data") or {})
                            .get("updateProjectV2", {})
                            .get("projectV2")
                        )
                        if updated_node:
                            proj_node = updated_node

                    new_map = self._upsert_mapping(
                        ResourceType.PROJECT,
                        elder_id=elder_proj["id"],
                        external_id=str(proj_node["id"]),
                        elder_updated_at=_parse_datetime(elder_proj.get("updated_at")),
                        external_updated_at=_parse_datetime(proj_node.get("updatedAt")),
                        sync_method="manual",
                    )
                    return SyncResult(
                        status=SyncStatus.SUCCESS,
                        operation=operation,
                        items_synced=1,
                        created_mappings=[new_map],
                    )

                if not mapping:
                    return SyncResult(
                        status=SyncStatus.FAILED,
                        operation=operation,
                        items_failed=1,
                        errors=["No mapping found for project update"],
                    )

                upd_res = self._graphql(
                    _UPDATE_PROJECT_V2_MUTATION,
                    {
                        "projectId": mapping.external_id,
                        "title": title,
                        "shortDescription": short_desc,
                        "closed": closed,
                    },
                )
                if upd_res.get("errors"):
                    return SyncResult(
                        status=SyncStatus.FAILED,
                        operation=operation,
                        items_failed=1,
                        errors=[str(upd_res["errors"])],
                    )
                proj_node = (upd_res.get("data") or {}).get("updateProjectV2", {}).get(
                    "projectV2"
                ) or {}
                upd_map = self._upsert_mapping(
                    ResourceType.PROJECT,
                    elder_id=mapping.elder_id,
                    external_id=mapping.external_id,
                    elder_updated_at=_parse_datetime(elder_proj.get("updated_at")),
                    external_updated_at=_parse_datetime(proj_node.get("updatedAt")),
                    existing=mapping,
                )
                return SyncResult(
                    status=SyncStatus.SUCCESS,
                    operation=operation,
                    items_synced=1,
                    updated_mappings=[upd_map],
                )

            # EXTERNAL_TO_ELDER pull
            node = operation.external_data or {}
            if not node.get("id"):
                return SyncResult(
                    status=SyncStatus.FAILED,
                    operation=operation,
                    items_failed=1,
                    errors=["GitHub projectV2 payload missing 'id'"],
                )

            org_id, tenant_id, org_err = self._resolve_org_and_tenant()
            if org_err or org_id is None:
                return SyncResult(
                    status=SyncStatus.FAILED,
                    operation=operation,
                    items_failed=1,
                    errors=[org_err or "No Elder organization mapped"],
                )

            self._upsert_elder_project(
                node,
                org_id=org_id,
                tenant_id=tenant_id,
                existing_mapping=operation.mapping,
                sync_method=operation.metadata.get("sync_method", "batch"),
            )
            return SyncResult(
                status=SyncStatus.SUCCESS,
                operation=operation,
                items_synced=1,
            )
        except Exception as e:
            self.logger.error(f"GitHub project sync failed: {e}", exc_info=True)
            return SyncResult(
                status=SyncStatus.FAILED,
                operation=operation,
                items_failed=1,
                errors=[str(e)],
            )

    # ------------------------------------------------------------------
    # Batch sync
    # ------------------------------------------------------------------

    def batch_sync(
        self,
        resource_type: ResourceType,
        since: datetime | None = None,
        direction: str = "pull",
    ) -> SyncResult:
        """Perform batch synchronization for GitHub resources.

        Supports ``ISSUE``, ``MILESTONE``, ``PROJECT``, and ``LABEL`` across
        ``"pull"``, ``"push"``, and ``"bidirectional"`` directions.
        """
        norm_dir = (direction or "pull").lower()
        if norm_dir == SyncDirection.EXTERNAL_TO_ELDER.value:
            norm_dir = "pull"
        elif norm_dir == SyncDirection.ELDER_TO_EXTERNAL.value:
            norm_dir = "push"

        self.logger.info(
            f"Starting GitHub batch sync for {resource_type.value} ({norm_dir})",
            extra={"since": since.isoformat() if since else None},
        )

        if norm_dir not in {"pull", "push", "bidirectional"}:
            return SyncResult(
                status=SyncStatus.FAILED,
                operation=SyncOperation(
                    operation_type="batch",
                    resource_type=resource_type,
                    direction=SyncDirection.BIDIRECTIONAL,
                ),
                items_failed=1,
                errors=[f"Unsupported sync direction: {direction}"],
            )

        handlers = {
            ResourceType.ISSUE: (self._batch_pull_issues, self._batch_push_issues),
            ResourceType.MILESTONE: (
                self._batch_pull_milestones,
                self._batch_push_milestones,
            ),
            ResourceType.PROJECT: (
                self._batch_pull_projects,
                self._batch_push_projects,
            ),
            ResourceType.LABEL: (self._batch_pull_labels, self._batch_push_labels),
        }
        pair = handlers.get(resource_type)
        if not pair:
            return SyncResult(
                status=SyncStatus.FAILED,
                operation=SyncOperation(
                    operation_type="batch",
                    resource_type=resource_type,
                    direction=SyncDirection.BIDIRECTIONAL,
                ),
                items_failed=1,
                errors=[f"Unsupported resource type: {resource_type.value}"],
            )

        pull_fn, push_fn = pair
        if norm_dir == "pull":
            return pull_fn(since)
        if norm_dir == "push":
            return push_fn()

        pull_res = pull_fn(since)
        push_res = push_fn()
        total_synced = pull_res.items_synced + push_res.items_synced
        total_failed = pull_res.items_failed + push_res.items_failed
        all_errors = list(pull_res.errors) + list(push_res.errors)
        status = (
            SyncStatus.SUCCESS
            if total_failed == 0 and not all_errors
            else (SyncStatus.PARTIAL_SUCCESS if total_synced > 0 else SyncStatus.FAILED)
        )
        return SyncResult(
            status=status,
            operation=SyncOperation(
                operation_type="batch",
                resource_type=resource_type,
                direction=SyncDirection.BIDIRECTIONAL,
            ),
            items_synced=total_synced,
            items_failed=total_failed,
            errors=all_errors,
            conflicts=list(pull_res.conflicts) + list(push_res.conflicts),
        )

    def _aggregate_result(
        self,
        resource_type: ResourceType,
        direction: SyncDirection,
        total_synced: int,
        total_failed: int,
        errors: list[str],
    ) -> SyncResult:
        """Build a consistent batch SyncResult."""
        if total_failed == 0 and not errors:
            status = SyncStatus.SUCCESS
        elif total_synced > 0:
            status = SyncStatus.PARTIAL_SUCCESS
        else:
            status = SyncStatus.FAILED

        return SyncResult(
            status=status,
            operation=SyncOperation(
                operation_type="batch",
                resource_type=resource_type,
                direction=direction,
            ),
            items_synced=total_synced,
            items_failed=total_failed,
            errors=errors,
        )

    def _batch_pull_issues(self, since: datetime | None = None) -> SyncResult:
        """Pull issues from GitHub REST API with pagination."""
        total_synced = 0
        total_failed = 0
        errors: list[str] = []

        params: dict[str, Any] = {"state": "all", "per_page": 100}
        if since:
            params["since"] = since.isoformat()

        page = 1
        while True:
            params["page"] = page
            try:
                response = self.client.get(self._repo_path("/issues"), params=params)
                if response.status_code != 200:
                    errors.append(f"GitHub API error: {response.status_code}")
                    total_failed += 1
                    break

                issues = response.json()
                if not issues:
                    break

                for issue in issues:
                    if "pull_request" in issue:
                        continue
                    mapping = self.get_mapping(
                        ResourceType.ISSUE, external_id=str(issue["number"])
                    )
                    op = SyncOperation(
                        operation_type="update" if mapping else "create",
                        resource_type=ResourceType.ISSUE,
                        direction=SyncDirection.EXTERNAL_TO_ELDER,
                        external_data=issue,
                        mapping=mapping,
                        metadata={"allow_create": True, "sync_method": "batch"},
                    )
                    res = self._sync_issue_from_github(op)
                    if res.is_success:
                        total_synced += 1
                    else:
                        total_failed += 1
                        errors.extend(res.errors)

                if len(issues) < 100:
                    break
                page += 1
            except Exception as e:
                errors.append(f"Batch issue pull error: {e}")
                total_failed += 1
                self.logger.error(f"Batch issue pull error: {e}", exc_info=True)
                break

        return self._aggregate_result(
            ResourceType.ISSUE,
            SyncDirection.EXTERNAL_TO_ELDER,
            total_synced,
            total_failed,
            errors,
        )

    def _batch_push_issues(self) -> SyncResult:
        """Push Elder issues for the configured organization to GitHub."""
        org_id, _, err = self._resolve_org_and_tenant()
        if err or org_id is None:
            return self._aggregate_result(
                ResourceType.ISSUE,
                SyncDirection.ELDER_TO_EXTERNAL,
                0,
                1,
                [err or "No Elder organization mapped"],
            )

        rows = self.db(
            (self.db.issues.organization_id == org_id)
            | (
                (self.db.issues.resource_type == "organization")
                & (self.db.issues.resource_id == org_id)
            )
        ).select()

        total_synced = 0
        total_failed = 0
        errors: list[str] = []

        for row in rows:
            mapping = self.get_mapping(ResourceType.ISSUE, elder_id=row.id)
            # Collect attached labels
            label_rows = self.db(
                (self.db.issue_label_assignments.issue_id == row.id)
                & (self.db.issue_label_assignments.label_id == self.db.issue_labels.id)
            ).select(self.db.issue_labels.name)
            label_names = [lr.name for lr in label_rows]

            # Collect first linked milestone if any
            ms_link = (
                self.db(self.db.issue_milestone_links.issue_id == row.id)
                .select()
                .first()
            )
            elder_payload = {
                "id": row.id,
                "title": row.title,
                "description": row.description,
                "status": row.status,
                "priority": row.priority,
                "updated_at": row.updated_at,
                "labels": label_names,
                "milestone_id": ms_link.milestone_id if ms_link else None,
            }
            op = SyncOperation(
                operation_type="update" if mapping else "create",
                resource_type=ResourceType.ISSUE,
                direction=SyncDirection.ELDER_TO_EXTERNAL,
                elder_data=elder_payload,
                mapping=mapping,
            )
            res = self._sync_issue_to_github(op)
            if res.is_success:
                total_synced += 1
            else:
                total_failed += 1
                errors.extend(res.errors)

        return self._aggregate_result(
            ResourceType.ISSUE,
            SyncDirection.ELDER_TO_EXTERNAL,
            total_synced,
            total_failed,
            errors,
        )

    def _batch_pull_milestones(self, since: datetime | None = None) -> SyncResult:
        """Pull milestones from GitHub REST API with pagination."""
        total_synced = 0
        total_failed = 0
        errors: list[str] = []
        page = 1

        while True:
            try:
                response = self.client.get(
                    self._repo_path("/milestones"),
                    params={"state": "all", "per_page": 100, "page": page},
                )
                if response.status_code != 200:
                    errors.append(f"GitHub API error: {response.status_code}")
                    total_failed += 1
                    break

                milestones = response.json()
                if not milestones:
                    break

                for ms in milestones:
                    mapping = self.get_mapping(
                        ResourceType.MILESTONE, external_id=str(ms["number"])
                    )
                    op = SyncOperation(
                        operation_type="update" if mapping else "create",
                        resource_type=ResourceType.MILESTONE,
                        direction=SyncDirection.EXTERNAL_TO_ELDER,
                        external_data=ms,
                        mapping=mapping,
                        metadata={"allow_create": True, "sync_method": "batch"},
                    )
                    res = self.sync_milestone(op)
                    if res.is_success:
                        total_synced += 1
                    else:
                        total_failed += 1
                        errors.extend(res.errors)

                if len(milestones) < 100:
                    break
                page += 1
            except Exception as e:
                errors.append(f"Batch milestone pull error: {e}")
                total_failed += 1
                break

        return self._aggregate_result(
            ResourceType.MILESTONE,
            SyncDirection.EXTERNAL_TO_ELDER,
            total_synced,
            total_failed,
            errors,
        )

    def _batch_push_milestones(self) -> SyncResult:
        """Push Elder milestones for the configured organization to GitHub."""
        org_id, _, err = self._resolve_org_and_tenant()
        if err or org_id is None:
            return self._aggregate_result(
                ResourceType.MILESTONE,
                SyncDirection.ELDER_TO_EXTERNAL,
                0,
                1,
                [err or "No Elder organization mapped"],
            )

        rows = self.db(self.db.milestones.organization_id == org_id).select()
        total_synced = 0
        total_failed = 0
        errors: list[str] = []

        for row in rows:
            mapping = self.get_mapping(ResourceType.MILESTONE, elder_id=row.id)
            op = SyncOperation(
                operation_type="update" if mapping else "create",
                resource_type=ResourceType.MILESTONE,
                direction=SyncDirection.ELDER_TO_EXTERNAL,
                elder_data={
                    "id": row.id,
                    "title": row.title,
                    "description": row.description,
                    "status": row.status,
                    "due_date": row.due_date,
                    "updated_at": row.updated_at,
                },
                mapping=mapping,
            )
            res = self.sync_milestone(op)
            if res.is_success:
                total_synced += 1
            else:
                total_failed += 1
                errors.extend(res.errors)

        return self._aggregate_result(
            ResourceType.MILESTONE,
            SyncDirection.ELDER_TO_EXTERNAL,
            total_synced,
            total_failed,
            errors,
        )

    def _batch_pull_labels(self, since: datetime | None = None) -> SyncResult:
        """Pull labels from GitHub REST API with pagination."""
        total_synced = 0
        total_failed = 0
        errors: list[str] = []
        page = 1

        while True:
            try:
                response = self.client.get(
                    self._repo_path("/labels"),
                    params={"per_page": 100, "page": page},
                )
                if response.status_code != 200:
                    errors.append(f"GitHub API error: {response.status_code}")
                    total_failed += 1
                    break

                labels = response.json()
                if not labels:
                    break

                for lbl in labels:
                    mapping = self.get_mapping(
                        ResourceType.LABEL, external_id=str(lbl["name"])
                    )
                    op = SyncOperation(
                        operation_type="update" if mapping else "create",
                        resource_type=ResourceType.LABEL,
                        direction=SyncDirection.EXTERNAL_TO_ELDER,
                        external_data=lbl,
                        mapping=mapping,
                        metadata={"allow_create": True, "sync_method": "batch"},
                    )
                    res = self.sync_label(op)
                    if res.is_success:
                        total_synced += 1
                    else:
                        total_failed += 1
                        errors.extend(res.errors)

                if len(labels) < 100:
                    break
                page += 1
            except Exception as e:
                errors.append(f"Batch label pull error: {e}")
                total_failed += 1
                break

        return self._aggregate_result(
            ResourceType.LABEL,
            SyncDirection.EXTERNAL_TO_ELDER,
            total_synced,
            total_failed,
            errors,
        )

    def _batch_push_labels(self) -> SyncResult:
        """Push Elder ``issue_labels`` to GitHub repository labels."""
        rows = self.db(self.db.issue_labels.id > 0).select()
        total_synced = 0
        total_failed = 0
        errors: list[str] = []

        for row in rows:
            mapping = self.get_mapping(ResourceType.LABEL, elder_id=row.id)
            op = SyncOperation(
                operation_type="update" if mapping else "create",
                resource_type=ResourceType.LABEL,
                direction=SyncDirection.ELDER_TO_EXTERNAL,
                elder_data={
                    "id": row.id,
                    "name": row.name,
                    "color": row.color,
                    "description": row.description,
                    "updated_at": row.updated_at,
                },
                mapping=mapping,
            )
            res = self.sync_label(op)
            if res.is_success:
                total_synced += 1
            else:
                total_failed += 1
                errors.extend(res.errors)

        return self._aggregate_result(
            ResourceType.LABEL,
            SyncDirection.ELDER_TO_EXTERNAL,
            total_synced,
            total_failed,
            errors,
        )

    def _batch_pull_projects(self, since: datetime | None = None) -> SyncResult:
        """Pull GitHub Projects v2 via GraphQL API v4 (org with user fallback)."""
        total_synced = 0
        total_failed = 0
        errors: list[str] = []

        cursor: str | None = None
        use_user_fallback = False

        while True:
            try:
                query = (
                    _PROJECTS_USER_QUERY if use_user_fallback else _PROJECTS_ORG_QUERY
                )
                payload = self._graphql(query, {"owner": self.owner, "cursor": cursor})
                data = payload.get("data") or {}
                owner_obj = (
                    data.get("user") if use_user_fallback else data.get("organization")
                )

                if not owner_obj and not use_user_fallback:
                    use_user_fallback = True
                    cursor = None
                    continue

                if payload.get("errors") and not owner_obj:
                    errors.append(str(payload["errors"]))
                    total_failed += 1
                    break

                projects_conn = (owner_obj or {}).get("projectsV2") or {}
                nodes = projects_conn.get("nodes") or []

                for node in nodes:
                    if not node or not node.get("id"):
                        continue
                    mapping = self.get_mapping(
                        ResourceType.PROJECT, external_id=str(node["id"])
                    )
                    op = SyncOperation(
                        operation_type="update" if mapping else "create",
                        resource_type=ResourceType.PROJECT,
                        direction=SyncDirection.EXTERNAL_TO_ELDER,
                        external_data=node,
                        mapping=mapping,
                        metadata={"allow_create": True, "sync_method": "batch"},
                    )
                    res = self.sync_project(op)
                    if res.is_success:
                        total_synced += 1
                    else:
                        total_failed += 1
                        errors.extend(res.errors)

                page_info = projects_conn.get("pageInfo") or {}
                if not page_info.get("hasNextPage") or not page_info.get("endCursor"):
                    break
                cursor = page_info["endCursor"]
            except Exception as e:
                errors.append(f"Batch project pull error: {e}")
                total_failed += 1
                break

        return self._aggregate_result(
            ResourceType.PROJECT,
            SyncDirection.EXTERNAL_TO_ELDER,
            total_synced,
            total_failed,
            errors,
        )

    def _batch_push_projects(self) -> SyncResult:
        """Push Elder projects for the configured organization to GitHub Projects v2."""
        org_id, _, err = self._resolve_org_and_tenant()
        if err or org_id is None:
            return self._aggregate_result(
                ResourceType.PROJECT,
                SyncDirection.ELDER_TO_EXTERNAL,
                0,
                1,
                [err or "No Elder organization mapped"],
            )

        rows = self.db(self.db.projects.organization_id == org_id).select()
        total_synced = 0
        total_failed = 0
        errors: list[str] = []

        for row in rows:
            mapping = self.get_mapping(ResourceType.PROJECT, elder_id=row.id)
            op = SyncOperation(
                operation_type="update" if mapping else "create",
                resource_type=ResourceType.PROJECT,
                direction=SyncDirection.ELDER_TO_EXTERNAL,
                elder_data={
                    "id": row.id,
                    "name": row.name,
                    "description": row.description,
                    "status": row.status,
                    "updated_at": row.updated_at,
                },
                mapping=mapping,
            )
            res = self.sync_project(op)
            if res.is_success:
                total_synced += 1
            else:
                total_failed += 1
                errors.extend(res.errors)

        return self._aggregate_result(
            ResourceType.PROJECT,
            SyncDirection.ELDER_TO_EXTERNAL,
            total_synced,
            total_failed,
            errors,
        )

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------

    def handle_webhook(self, webhook_data: dict[str, Any]) -> SyncResult:
        """Handle GitHub webhook event for issues, milestones, and labels."""
        action = (webhook_data.get("action") or "").lower()
        event_hint = (webhook_data.get("_event") or "").lower()

        if event_hint == "ping" or (
            "zen" in webhook_data and "hook_id" in webhook_data
        ):
            return SyncResult(
                status=SyncStatus.SUCCESS,
                operation=SyncOperation(
                    operation_type="webhook",
                    resource_type=ResourceType.ISSUE,
                    direction=SyncDirection.EXTERNAL_TO_ELDER,
                ),
                metadata={"event": "ping", "zen": webhook_data.get("zen")},
            )

        if "issue" in webhook_data:
            issue = webhook_data["issue"]
            mapping = self.get_mapping(
                ResourceType.ISSUE, external_id=str(issue["number"])
            )
            if action == "deleted":
                return SyncResult(
                    status=SyncStatus.SUCCESS,
                    operation=SyncOperation(
                        operation_type="delete",
                        resource_type=ResourceType.ISSUE,
                        direction=SyncDirection.EXTERNAL_TO_ELDER,
                        mapping=mapping,
                    ),
                    metadata={"deleted": True, "external_id": str(issue["number"])},
                )
            if action == "opened" and mapping is None:
                op_type = "create"
            elif action in {
                "opened",
                "edited",
                "closed",
                "reopened",
                "labeled",
                "unlabeled",
                "assigned",
                "unassigned",
                "milestoned",
                "demilestoned",
            }:
                op_type = "update"
            else:
                op_type = "update"

            return self._sync_issue_from_github(
                SyncOperation(
                    operation_type=op_type,
                    resource_type=ResourceType.ISSUE,
                    direction=SyncDirection.EXTERNAL_TO_ELDER,
                    external_data=issue,
                    mapping=mapping,
                    metadata={"sync_method": "webhook"},
                )
            )

        if "milestone" in webhook_data:
            ms = webhook_data["milestone"]
            mapping = self.get_mapping(
                ResourceType.MILESTONE, external_id=str(ms["number"])
            )
            if action == "deleted":
                return SyncResult(
                    status=SyncStatus.SUCCESS,
                    operation=SyncOperation(
                        operation_type="delete",
                        resource_type=ResourceType.MILESTONE,
                        direction=SyncDirection.EXTERNAL_TO_ELDER,
                        mapping=mapping,
                    ),
                    metadata={"deleted": True, "external_id": str(ms["number"])},
                )
            op_type = (
                "create" if (action == "created" and mapping is None) else "update"
            )
            return self.sync_milestone(
                SyncOperation(
                    operation_type=op_type,
                    resource_type=ResourceType.MILESTONE,
                    direction=SyncDirection.EXTERNAL_TO_ELDER,
                    external_data=ms,
                    mapping=mapping,
                    metadata={"sync_method": "webhook"},
                )
            )

        if "label" in webhook_data:
            lbl = webhook_data["label"]
            prev_name = (webhook_data.get("changes") or {}).get("name", {}).get("from")
            mapping = (
                self.get_mapping(ResourceType.LABEL, external_id=str(prev_name))
                if prev_name
                else None
            ) or self.get_mapping(ResourceType.LABEL, external_id=str(lbl["name"]))
            if action == "deleted":
                return SyncResult(
                    status=SyncStatus.SUCCESS,
                    operation=SyncOperation(
                        operation_type="delete",
                        resource_type=ResourceType.LABEL,
                        direction=SyncDirection.EXTERNAL_TO_ELDER,
                        mapping=mapping,
                    ),
                    metadata={"deleted": True, "external_id": str(lbl["name"])},
                )
            op_type = (
                "create" if (action == "created" and mapping is None) else "update"
            )
            return self.sync_label(
                SyncOperation(
                    operation_type=op_type,
                    resource_type=ResourceType.LABEL,
                    direction=SyncDirection.EXTERNAL_TO_ELDER,
                    external_data=lbl,
                    mapping=mapping,
                    metadata={
                        "sync_method": "webhook",
                        "previous_name": prev_name,
                    },
                )
            )

        return SyncResult(
            status=SyncStatus.SUCCESS,
            operation=SyncOperation(
                operation_type="webhook",
                resource_type=ResourceType.ISSUE,
                direction=SyncDirection.EXTERNAL_TO_ELDER,
            ),
            metadata={"ignored": True, "reason": "no_supported_resource_data"},
        )

    def _map_github_labels_to_priority(self, labels: list[Any]) -> str:
        """Map GitHub labels to Elder UPPERCASE ``IssuePriority`` enum name."""
        tokens: set[str] = set()
        for label in labels:
            raw = (
                (label.get("name", "") if isinstance(label, dict) else str(label))
                .strip()
                .lower()
            )
            if raw:
                tokens.add(raw)
                for prefix in ("priority:", "priority/", "sev:", "severity:"):
                    if raw.startswith(prefix):
                        tokens.add(raw[len(prefix) :].strip())

        if any(p in tokens for p in ("critical", "urgent", "p0", "sev0")):
            return "CRITICAL"
        if any(p in tokens for p in ("high", "important", "p1", "sev1")):
            return "HIGH"
        if any(p in tokens for p in ("low", "minor", "p3", "p4")):
            return "LOW"
        return "MEDIUM"

    def _map_github_labels_to_issue_type(self, labels: list[Any]) -> str:
        """Map GitHub labels to Elder UPPERCASE ``IssueType`` enum name."""
        label_names = [
            (label.get("name", "") if isinstance(label, dict) else str(label)).lower()
            for label in labels
        ]

        if any(t in label_names for t in ["security", "cve", "vulnerability"]):
            return "SECURITY"
        if any(t in label_names for t in ["bug", "defect"]):
            return "BUG"
        if any(t in label_names for t in ["enhancement", "feature", "feature-request"]):
            return "FEATURE"
        if any(t in label_names for t in ["operations", "ops", "incident"]):
            return "OPERATIONS"
        if any(t in label_names for t in ["config", "configuration"]):
            return "CONFIG"
        if any(t in label_names for t in ["support", "question", "helpdesk"]):
            return "SUPPORT"
        return "OTHER"
