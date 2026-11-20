"""Jira Cloud sync client for two-way synchronization.

Implements two-way synchronization against the official Jira Cloud REST API v3:
- Issues ↔ Jira Issues (POST /rest/api/3/search/jql, ADF descriptions, transitions)
- Projects ↔ Jira Projects
- Milestones ↔ Jira Versions
- Labels ↔ Jira Labels (default) or Jira Components (configurable label_mode)
"""

# flake8: noqa: E501

import re
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

_DEFAULT_LABEL_COLOR = "#808080"


def normalize_jira_host(raw_url: str | None) -> str:
    """Normalize a Jira Cloud domain or URL to bare ``host[:port]``."""
    raw = (raw_url or "").strip()
    if raw.startswith("https://"):
        raw = raw[len("https://") :]
    elif raw.startswith("http://"):
        raw = raw[len("http://") :]
    raw = raw.strip("/")
    return raw.split("/")[0] if raw else ""


def text_to_adf(text: str | None) -> dict[str, Any]:
    """Convert plain text into an Atlassian Document Format (ADF) v1 document."""
    if not text:
        return {"type": "doc", "version": 1, "content": []}

    paragraphs = []
    for line in str(text).split("\n"):
        if line:
            paragraphs.append(
                {"type": "paragraph", "content": [{"type": "text", "text": line}]}
            )
        else:
            paragraphs.append({"type": "paragraph", "content": []})

    return {"type": "doc", "version": 1, "content": paragraphs}


def _extract_adf_node_text(node: Any) -> str:
    """Recursively extract plain text from a single ADF AST node."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""

    node_type = node.get("type", "")
    if node_type == "text":
        return str(node.get("text", ""))
    if node_type == "hardBreak":
        return "\n"
    if node_type in {"mention", "emoji"}:
        return str((node.get("attrs") or {}).get("text", ""))
    if node_type == "inlineCard":
        return str((node.get("attrs") or {}).get("url", ""))

    children = node.get("content") or []
    child_texts = [_extract_adf_node_text(child) for child in children]

    if node_type in {"doc", "bulletList", "orderedList", "table", "blockquote"}:
        return "\n".join(part for part in child_texts if part is not None)
    if node_type in {"paragraph", "heading", "listItem", "codeBlock"}:
        return "".join(child_texts)
    if node_type == "tableRow":
        return " | ".join(part for part in child_texts if part)
    return "".join(child_texts)


def adf_to_text(adf: Any) -> str:
    """Extract plain text from an Atlassian Document Format (ADF) payload."""
    if adf is None:
        return ""
    if isinstance(adf, str):
        return adf
    if isinstance(adf, dict):
        return _extract_adf_node_text(adf).strip("\n") if adf.get("content") else ""
    return str(adf)


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
        s = value.strip()
        # Jira timestamps often look like 2026-11-20T10:15:00.000+0000
        if len(s) >= 5 and (s[-5] in ("+", "-")) and s[-4:].isdigit():
            s = f"{s[:-2]}:{s[-2:]}"
        try:
            parsed = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return (
                parsed.replace(tzinfo=UTC)
                if parsed.tzinfo is None
                else parsed.astimezone(UTC)
            )
        except ValueError:
            return None
    return None


def _parse_date(value: Any) -> date | None:
    """Parse a date/datetime/ISO string into a ``datetime.date``."""
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


def _format_iso_date(value: Any) -> str | None:
    """Format a date/datetime/ISO string into ``YYYY-MM-DD`` for Jira Cloud."""
    d = _parse_date(value)
    return d.isoformat() if d else None


class JiraSyncClient(BaseSyncClient):
    """Jira Cloud REST API v3 sync client implementation."""

    def __init__(
        self, config: dict[str, Any], db: DAL, sync_config_id: int, logger: Any
    ):
        super().__init__("jira", config, db, sync_config_id, logger)

        self.api_token = (config.get("api_token") or "").strip() or None
        self.email = (config.get("email") or "").strip() or None
        self.jira_url = normalize_jira_host(config.get("jira_url"))
        self.project_key = (config.get("project_key") or "").strip() or None
        self.label_mode = (config.get("label_mode") or "labels").strip().lower()
        if self.label_mode not in {"labels", "components"}:
            self.label_mode = "labels"
        self.sync_all_projects = bool(config.get("sync_all_projects", False))
        self.project_lead_account_id = (
            config.get("project_lead_account_id") or ""
        ).strip() or None

        self.base_url = f"https://{self.jira_url}/rest/api/3" if self.jira_url else ""

        self.client = httpx.Client(
            base_url=self.base_url or "https://localhost/rest/api/3",
            auth=(self.email or "", self.api_token or ""),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=30.0,
        )

        self.conflict_resolver = ConflictResolver(logger)

        self.priority_map = {
            "CRITICAL": "Highest",
            "URGENT": "Highest",
            "HIGH": "High",
            "MEDIUM": "Medium",
            "LOW": "Low",
        }

    def validate_config(self) -> bool:
        """Validate required Jira Cloud credentials and project key."""
        return bool(
            self.api_token and self.email and self.jira_url and self.project_key
        )

    def test_connection_detail(self) -> dict[str, Any]:
        """Test connectivity against ``GET /rest/api/3/project/{project_key}``."""
        if not self.validate_config():
            return {
                "connected": False,
                "success": False,
                "platform": "jira",
                "error": "Missing required Jira configuration (jira_url, email, api_token, project_key)",
            }

        try:
            response = self.client.get(
                f"/project/{quote(self.project_key or '', safe='')}"
            )
            if response.status_code == 200:
                proj = response.json()
                return {
                    "connected": True,
                    "success": True,
                    "platform": "jira",
                    "status_code": 200,
                    "project": {
                        "id": proj.get("id"),
                        "key": proj.get("key"),
                        "name": proj.get("name"),
                        "projectTypeKey": proj.get("projectTypeKey"),
                    },
                }

            error_msg = f"Jira API returned HTTP {response.status_code}"
            try:
                body = response.json()
                if isinstance(body, dict):
                    msgs = body.get("errorMessages") or []
                    if msgs:
                        error_msg = f"{error_msg}: {'; '.join(msgs)}"
            except Exception:
                pass
            return {
                "connected": False,
                "success": False,
                "platform": "jira",
                "status_code": response.status_code,
                "error": error_msg,
            }
        except Exception as e:
            self.logger.error(f"Jira connection test failed: {e}")
            return {
                "connected": False,
                "success": False,
                "platform": "jira",
                "error": str(e),
            }

    def test_connection(self) -> bool:
        """Test connection to Jira Cloud REST API v3."""
        return bool(self.test_connection_detail().get("connected"))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_sync_config_row(self) -> Any:
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
            return None, None, "No Elder organization mapped for Jira project"

        try:
            org_id_int = int(org_id)
        except (TypeError, ValueError):
            return None, None, f"Invalid elder_organization_id: {org_id!r}"

        org_row = self.db(self.db.organizations.id == org_id_int).select().first()
        if not org_row:
            return None, None, f"Elder organization {org_id_int} not found"

        return org_id_int, getattr(org_row, "tenant_id", None), None

    def _should_allow_create(self, operation: SyncOperation) -> bool:
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
        mapping = (
            existing
            or self.get_mapping(resource_type, elder_id=elder_id)
            or self.get_mapping(resource_type, external_id=external_id)
        )
        if mapping:
            mapping.elder_updated_at = elder_updated_at
            mapping.external_updated_at = external_updated_at
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

    def _map_jira_status(self, status_obj: dict[str, Any] | None) -> str:
        """Map Jira statusCategory/status name to Elder UPPERCASE IssueStatus."""
        if not isinstance(status_obj, dict):
            return "OPEN"
        cat_key = ((status_obj.get("statusCategory") or {}).get("key") or "").lower()
        if cat_key == "done":
            return "CLOSED"
        if cat_key == "indeterminate":
            return "IN_PROGRESS"
        if cat_key == "new":
            return "OPEN"

        name = (status_obj.get("name") or "").lower()
        if any(tok in name for tok in ("done", "closed", "resolved", "complete")):
            return "CLOSED"
        if any(tok in name for tok in ("progress", "review", "doing")):
            return "IN_PROGRESS"
        return "OPEN"

    def _map_jira_priority(self, priority_obj: dict[str, Any] | None) -> str:
        """Map Jira priority to Elder UPPERCASE IssuePriority."""
        if not isinstance(priority_obj, dict):
            return "MEDIUM"
        name = (priority_obj.get("name") or "").lower()
        if name in {"highest", "blocker", "critical"}:
            return "CRITICAL"
        if name == "high":
            return "HIGH"
        if name in {"low", "lowest", "minor", "trivial"}:
            return "LOW"
        return "MEDIUM"

    def _map_jira_issue_type(self, issuetype_obj: dict[str, Any] | None) -> str:
        """Map Jira issuetype to Elder UPPERCASE IssueType."""
        if not isinstance(issuetype_obj, dict):
            return "OTHER"
        name = (issuetype_obj.get("name") or "").lower()
        if "bug" in name or "defect" in name:
            return "BUG"
        if any(tok in name for tok in ("story", "feature", "epic", "improvement")):
            return "FEATURE"
        if "security" in name or "vuln" in name:
            return "SECURITY"
        if any(tok in name for tok in ("incident", "problem", "operations", "task")):
            return "OPERATIONS"
        if "support" in name or "service" in name:
            return "SUPPORT"
        if "config" in name:
            return "CONFIG"
        if "architecture" in name:
            return "ARCHITECTURE"
        if "process" in name:
            return "PROCESS"
        if "approval" in name:
            return "APPROVAL"
        if "code" in name:
            return "CODE"
        return "OTHER"

    def _map_elder_type_to_jira(self, issue_type: str | None) -> str:
        """Map Elder IssueType to Jira issuetype name."""
        upper = (issue_type or "OTHER").upper()
        mapping = {
            "BUG": "Bug",
            "SECURITY": "Bug",
            "FEATURE": "Story",
            "OPERATIONS": "Task",
            "SUPPORT": "Task",
        }
        return mapping.get(upper, "Task")

    def _sync_issue_labels(self, issue_id: int, labels: list[Any]) -> None:
        """Upsert Jira label strings into ``db.issue_labels`` and link to issue."""
        if not labels:
            return
        now = datetime.now(UTC)
        for lbl in labels:
            raw_name = (
                lbl.get("name", "") if isinstance(lbl, dict) else str(lbl)
            ).strip()
            if not raw_name:
                continue
            name = raw_name[:50]
            label_row = self.db(self.db.issue_labels.name == name).select().first()
            if label_row:
                label_id = label_row.id
            else:
                label_id = self.db.issue_labels.insert(
                    name=name,
                    color=_DEFAULT_LABEL_COLOR,
                    description=None,
                    created_at=now,
                    updated_at=now,
                )

            existing = (
                self.db(
                    (self.db.issue_label_assignments.issue_id == issue_id)
                    & (self.db.issue_label_assignments.label_id == label_id)
                )
                .select()
                .first()
            )
            if not existing:
                self.db.issue_label_assignments.insert(
                    issue_id=issue_id,
                    label_id=label_id,
                )
        self.db.commit()

    def _sync_issue_versions(
        self,
        issue_id: int,
        versions: list[dict[str, Any]],
        org_id: int,
        tenant_id: int | None,
    ) -> None:
        """Upsert Jira fixVersions into ``db.milestones`` and link via ``db.issue_milestone_links``."""
        if not versions:
            return
        for ver in versions:
            if not isinstance(ver, dict) or ver.get("id") is None:
                continue
            milestone_id = self._upsert_elder_milestone(
                ver, org_id=org_id, tenant_id=tenant_id
            )
            existing = (
                self.db(
                    (self.db.issue_milestone_links.issue_id == issue_id)
                    & (self.db.issue_milestone_links.milestone_id == milestone_id)
                )
                .select()
                .first()
            )
            if not existing:
                self.db.issue_milestone_links.insert(
                    issue_id=issue_id,
                    milestone_id=milestone_id,
                    created_at=datetime.now(UTC),
                )
                self.db.commit()

    def _transition_jira_issue(self, issue_key: str, elder_status: str) -> None:
        """Transition a Jira issue's workflow status to match Elder's status."""
        target_upper = elder_status.upper()
        if target_upper in {"CLOSED", "RESOLVED"}:
            target_cat = "done"
        elif target_upper == "IN_PROGRESS":
            target_cat = "indeterminate"
        else:
            target_cat = "new"

        resp = self.client.get(f"/issue/{quote(issue_key, safe='')}/transitions")
        if resp.status_code != 200:
            self.logger.warning(
                f"Failed to fetch Jira transitions for {issue_key}: HTTP {resp.status_code}"
            )
            return

        transitions = (resp.json() or {}).get("transitions") or []
        chosen_id = None
        for t in transitions:
            to_cat = (
                ((t.get("to") or {}).get("statusCategory") or {}).get("key", "").lower()
            )
            if to_cat == target_cat:
                chosen_id = t.get("id")
                break

        if not chosen_id:
            fallback_names = {
                "done": {"done", "closed", "resolved", "complete", "completed"},
                "indeterminate": {"in progress", "in review", "active", "started"},
                "new": {"to do", "open", "backlog", "new", "reopen"},
            }.get(target_cat, set())
            for t in transitions:
                to_name = ((t.get("to") or {}).get("name") or "").strip().lower()
                t_name = (t.get("name") or "").strip().lower()
                if to_name in fallback_names or t_name in fallback_names:
                    chosen_id = t.get("id")
                    break

        if not chosen_id:
            return

        post_resp = self.client.post(
            f"/issue/{quote(issue_key, safe='')}/transitions",
            json={"transition": {"id": str(chosen_id)}},
        )
        if post_resp.status_code not in (200, 204):
            self.logger.warning(
                f"Failed to apply Jira transition {chosen_id} to {issue_key}: HTTP {post_resp.status_code}"
            )

    # ------------------------------------------------------------------
    # Issues
    # ------------------------------------------------------------------

    def sync_issue(self, operation: SyncOperation) -> SyncResult:
        """Sync a single issue with Jira Cloud REST API v3."""
        self.logger.info(f"Jira issue sync: {operation.operation_type}")
        try:
            if operation.direction == SyncDirection.ELDER_TO_EXTERNAL:
                return self._sync_issue_to_jira(operation)
            if operation.direction == SyncDirection.EXTERNAL_TO_ELDER:
                return self._sync_issue_from_jira(operation)
            return self._sync_issue_bidirectional(operation)
        except Exception as e:
            self.logger.error(f"Jira issue sync failed: {e}", exc_info=True)
            return SyncResult(
                status=SyncStatus.FAILED,
                operation=operation,
                items_failed=1,
                errors=[str(e)],
            )

    def _sync_issue_to_jira(self, operation: SyncOperation) -> SyncResult:
        """Push an Elder issue to Jira Cloud REST API v3."""
        elder_issue = operation.elder_data or {}
        mapping = operation.mapping
        if mapping is None and elder_issue.get("id") is not None:
            mapping = self.get_mapping(ResourceType.ISSUE, elder_id=elder_issue["id"])

        priority_upper = str(elder_issue.get("priority") or "MEDIUM").upper()
        jira_priority = self.priority_map.get(priority_upper, "Medium")
        jira_type = self._map_elder_type_to_jira(elder_issue.get("issue_type"))

        fields: dict[str, Any] = {
            "summary": elder_issue.get("title") or "",
            "description": text_to_adf(elder_issue.get("description") or ""),
            "priority": {"name": jira_priority},
        }
        if elder_issue.get("labels") is not None:
            fields["labels"] = [
                lbl["name"] if isinstance(lbl, dict) else str(lbl)
                for lbl in elder_issue["labels"]
                if (lbl.get("name") if isinstance(lbl, dict) else lbl)
            ]
        if elder_issue.get("milestone_id"):
            ms_map = self.get_mapping(
                ResourceType.MILESTONE, elder_id=elder_issue["milestone_id"]
            )
            if ms_map:
                fields["fixVersions"] = [{"id": str(ms_map.external_id)}]

        if operation.operation_type == "create" and mapping is None:
            fields["project"] = {"key": self.project_key}
            fields["issuetype"] = {"name": jira_type}
            resp = self.client.post("/issue", json={"fields": fields})
            if resp.status_code in (200, 201):
                created = resp.json()
                issue_key = str(created["key"])
                status_upper = str(elder_issue.get("status") or "OPEN").upper()
                if status_upper in {"IN_PROGRESS", "CLOSED", "RESOLVED"}:
                    self._transition_jira_issue(issue_key, status_upper)

                new_map = self._upsert_mapping(
                    ResourceType.ISSUE,
                    elder_id=elder_issue["id"],
                    external_id=issue_key,
                    elder_updated_at=_parse_datetime(elder_issue.get("updated_at")),
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
                errors=[f"Jira API error: {resp.status_code} - {resp.text}"],
            )

        if not mapping:
            return SyncResult(
                status=SyncStatus.FAILED,
                operation=operation,
                items_failed=1,
                errors=["No mapping found for Jira issue update"],
            )

        resp = self.client.put(
            f"/issue/{quote(mapping.external_id, safe='')}",
            json={"fields": fields},
        )
        # Jira Cloud PUT /rest/api/3/issue/{key} returns 204 No Content on success
        if resp.status_code not in (200, 204):
            return SyncResult(
                status=SyncStatus.FAILED,
                operation=operation,
                items_failed=1,
                errors=[f"Jira API error: {resp.status_code} - {resp.text}"],
            )

        if elder_issue.get("status"):
            self._transition_jira_issue(mapping.external_id, str(elder_issue["status"]))

        upd_map = self._upsert_mapping(
            ResourceType.ISSUE,
            elder_id=mapping.elder_id,
            external_id=mapping.external_id,
            elder_updated_at=_parse_datetime(elder_issue.get("updated_at")),
            existing=mapping,
        )
        return SyncResult(
            status=SyncStatus.SUCCESS,
            operation=operation,
            items_synced=1,
            updated_mappings=[upd_map],
        )

    def _sync_issue_from_jira(self, operation: SyncOperation) -> SyncResult:
        """Pull/upsert a Jira issue into Elder ``db.issues``."""
        jira_issue = operation.external_data or {}
        issue_key = jira_issue.get("key")
        if not issue_key:
            return SyncResult(
                status=SyncStatus.FAILED,
                operation=operation,
                items_failed=1,
                errors=["Jira issue payload missing 'key'"],
            )

        fields = jira_issue.get("fields") or {}
        mapping = operation.mapping or self.get_mapping(
            ResourceType.ISSUE, external_id=str(issue_key)
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
        status_upper = self._map_jira_status(fields.get("status"))
        priority_upper = self._map_jira_priority(fields.get("priority"))
        issue_type_upper = self._map_jira_issue_type(fields.get("issuetype"))
        ext_updated = _parse_datetime(fields.get("updated"))
        closed_at = now if status_upper == "CLOSED" else None

        elder_fields = {
            "title": (fields.get("summary") or str(issue_key))[:255],
            "description": adf_to_text(fields.get("description")),
            "status": status_upper,
            "priority": priority_upper,
            "issue_type": issue_type_upper,
            "due_date": _parse_datetime(fields.get("duedate")),
            "closed_at": closed_at,
            "updated_at": now,
        }

        if operation.operation_type == "create" and mapping is None:
            if not self._should_allow_create(operation):
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
                due_date=elder_fields["due_date"],
                closed_at=closed_at,
                created_at=now,
                updated_at=now,
            )
            self.db.commit()

            self._sync_issue_labels(issue_id, fields.get("labels") or [])
            self._sync_issue_versions(
                issue_id,
                fields.get("fixVersions") or [],
                org_id=org_id,
                tenant_id=tenant_id,
            )

            created_map = self._upsert_mapping(
                ResourceType.ISSUE,
                elder_id=issue_id,
                external_id=str(issue_key),
                elder_updated_at=now,
                external_updated_at=ext_updated,
                sync_method=operation.metadata.get("sync_method", "webhook"),
            )
            return SyncResult(
                status=SyncStatus.SUCCESS,
                operation=operation,
                items_synced=1,
                created_mappings=[created_map],
            )

        if not mapping:
            if not self._should_allow_create(operation):
                return SyncResult(
                    status=SyncStatus.FAILED,
                    operation=operation,
                    items_failed=1,
                    errors=["No mapping found for Jira issue"],
                )
            operation.operation_type = "create"
            return self._sync_issue_from_jira(operation)

        self.db(self.db.issues.id == mapping.elder_id).update(**elder_fields)
        self.db.commit()

        self._sync_issue_labels(mapping.elder_id, fields.get("labels") or [])
        self._sync_issue_versions(
            mapping.elder_id,
            fields.get("fixVersions") or [],
            org_id=org_id,
            tenant_id=tenant_id,
        )

        upd_map = self._upsert_mapping(
            ResourceType.ISSUE,
            elder_id=mapping.elder_id,
            external_id=str(issue_key),
            elder_updated_at=now,
            external_updated_at=ext_updated,
            existing=mapping,
        )
        return SyncResult(
            status=SyncStatus.SUCCESS,
            operation=operation,
            items_synced=1,
            updated_mappings=[upd_map],
        )

    def _sync_issue_bidirectional(self, operation: SyncOperation) -> SyncResult:
        conflict = self.conflict_resolver.detect_conflict(
            elder_data=operation.elder_data or {},
            external_data=operation.external_data or {},
            mapping=operation.mapping,
        )
        if conflict:
            resolved = self.conflict_resolver.resolve_conflict(conflict)
            if not resolved.resolved:
                return SyncResult(
                    status=SyncStatus.CONFLICT,
                    operation=operation,
                    conflicts=[self.conflict_resolver.get_conflict_summary(resolved)],
                )
            if resolved.resolution_data == operation.elder_data:
                operation.direction = SyncDirection.ELDER_TO_EXTERNAL
                return self._sync_issue_to_jira(operation)

        operation.direction = SyncDirection.EXTERNAL_TO_ELDER
        return self._sync_issue_from_jira(operation)

    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------

    def _upsert_elder_project(
        self,
        proj: dict[str, Any],
        *,
        org_id: int,
        tenant_id: int | None,
        existing_mapping: SyncMapping | None = None,
        sync_method: str = "batch",
    ) -> int:
        ext_id = str(proj.get("key") or proj.get("id"))
        mapping = existing_mapping or self.get_mapping(
            ResourceType.PROJECT, external_id=ext_id
        )
        now = datetime.now(UTC)
        status = "archived" if proj.get("archived") else "active"
        fields = {
            "name": (proj.get("name") or ext_id)[:255],
            "description": adf_to_text(proj.get("description")),
            "status": status,
            "updated_at": now,
        }

        if mapping:
            self.db(self.db.projects.id == mapping.elder_id).update(**fields)
            self.db.commit()
            self._upsert_mapping(
                ResourceType.PROJECT,
                elder_id=mapping.elder_id,
                external_id=ext_id,
                elder_updated_at=now,
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
            sync_method=sync_method,
        )
        return project_id

    def sync_project(self, operation: SyncOperation) -> SyncResult:
        """Sync project with Jira Cloud Projects REST API."""
        try:
            if operation.direction == SyncDirection.ELDER_TO_EXTERNAL:
                elder_proj = operation.elder_data or {}
                mapping = operation.mapping
                if mapping is None and elder_proj.get("id") is not None:
                    mapping = self.get_mapping(
                        ResourceType.PROJECT, elder_id=elder_proj["id"]
                    )

                name = elder_proj.get("name") or ""
                description = elder_proj.get("description") or ""

                if operation.operation_type == "create" and mapping is None:
                    lead_account_id = (
                        elder_proj.get("lead_account_id")
                        or self.project_lead_account_id
                    )
                    if not lead_account_id:
                        return SyncResult(
                            status=SyncStatus.FAILED,
                            operation=operation,
                            items_failed=1,
                            errors=[
                                "Jira project creation requires 'project_lead_account_id' to be configured"
                            ],
                        )

                    raw_key = (
                        elder_proj.get("key")
                        or re.sub(r"[^A-Z0-9]", "", name.upper())[:10]
                    )
                    if not raw_key or not raw_key[0].isalpha():
                        raw_key = f"EL{raw_key}"[:10]

                    resp = self.client.post(
                        "/project",
                        json={
                            "key": raw_key,
                            "name": name,
                            "projectTypeKey": elder_proj.get(
                                "project_type_key", "software"
                            ),
                            "leadAccountId": lead_account_id,
                            "description": description,
                        },
                    )
                    if resp.status_code in (200, 201):
                        created = resp.json()
                        new_map = self._upsert_mapping(
                            ResourceType.PROJECT,
                            elder_id=elder_proj["id"],
                            external_id=str(created.get("key") or raw_key),
                            elder_updated_at=_parse_datetime(
                                elder_proj.get("updated_at")
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
                        errors=[f"Jira API error: {resp.status_code} - {resp.text}"],
                    )

                if not mapping:
                    return SyncResult(
                        status=SyncStatus.FAILED,
                        operation=operation,
                        items_failed=1,
                        errors=["No mapping found for Jira project update"],
                    )

                resp = self.client.put(
                    f"/project/{quote(mapping.external_id, safe='')}",
                    json={"name": name, "description": description},
                )
                if resp.status_code not in (200, 204):
                    return SyncResult(
                        status=SyncStatus.FAILED,
                        operation=operation,
                        items_failed=1,
                        errors=[f"Jira API error: {resp.status_code} - {resp.text}"],
                    )
                upd_map = self._upsert_mapping(
                    ResourceType.PROJECT,
                    elder_id=mapping.elder_id,
                    external_id=mapping.external_id,
                    elder_updated_at=_parse_datetime(elder_proj.get("updated_at")),
                    existing=mapping,
                )
                return SyncResult(
                    status=SyncStatus.SUCCESS,
                    operation=operation,
                    items_synced=1,
                    updated_mappings=[upd_map],
                )

            # EXTERNAL_TO_ELDER pull
            proj = operation.external_data or {}
            if not (proj.get("key") or proj.get("id")):
                return SyncResult(
                    status=SyncStatus.FAILED,
                    operation=operation,
                    items_failed=1,
                    errors=["Jira project payload missing 'key' or 'id'"],
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
                proj,
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
            self.logger.error(f"Jira project sync failed: {e}", exc_info=True)
            return SyncResult(
                status=SyncStatus.FAILED,
                operation=operation,
                items_failed=1,
                errors=[str(e)],
            )

    # ------------------------------------------------------------------
    # Milestones <-> Jira Versions
    # ------------------------------------------------------------------

    def _upsert_elder_milestone(
        self,
        version: dict[str, Any],
        *,
        org_id: int,
        tenant_id: int | None,
        existing_mapping: SyncMapping | None = None,
        sync_method: str = "batch",
    ) -> int:
        ext_id = str(version["id"])
        mapping = existing_mapping or self.get_mapping(
            ResourceType.MILESTONE, external_id=ext_id
        )
        now = datetime.now(UTC)
        is_closed = bool(version.get("released") or version.get("archived"))
        status = "closed" if is_closed else "open"
        due_date = _parse_date(version.get("releaseDate"))
        closed_at = now if is_closed else None
        fields = {
            "title": (version.get("name") or "")[:255],
            "description": version.get("description") or "",
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
            sync_method=sync_method,
        )
        return milestone_id

    def sync_milestone(self, operation: SyncOperation) -> SyncResult:
        """Sync milestone with Jira Versions (``/rest/api/3/version``)."""
        try:
            if operation.direction == SyncDirection.ELDER_TO_EXTERNAL:
                elder_ms = operation.elder_data or {}
                mapping = operation.mapping
                if mapping is None and elder_ms.get("id") is not None:
                    mapping = self.get_mapping(
                        ResourceType.MILESTONE, elder_id=elder_ms["id"]
                    )

                is_closed = str(elder_ms.get("status") or "open").lower() == "closed"
                payload: dict[str, Any] = {
                    "name": elder_ms.get("title") or "",
                    "description": elder_ms.get("description") or "",
                    "project": self.project_key,
                    "released": is_closed,
                }
                release_date = _format_iso_date(elder_ms.get("due_date"))
                if release_date:
                    payload["releaseDate"] = release_date

                if operation.operation_type == "create" and mapping is None:
                    resp = self.client.post("/version", json=payload)
                    if resp.status_code in (200, 201):
                        created = resp.json()
                        new_map = self._upsert_mapping(
                            ResourceType.MILESTONE,
                            elder_id=elder_ms["id"],
                            external_id=str(created["id"]),
                            elder_updated_at=_parse_datetime(
                                elder_ms.get("updated_at")
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
                        errors=[f"Jira API error: {resp.status_code} - {resp.text}"],
                    )

                if not mapping:
                    return SyncResult(
                        status=SyncStatus.FAILED,
                        operation=operation,
                        items_failed=1,
                        errors=["No mapping found for Jira version update"],
                    )

                resp = self.client.put(
                    f"/version/{quote(mapping.external_id, safe='')}",
                    json=payload,
                )
                if resp.status_code not in (200, 204):
                    return SyncResult(
                        status=SyncStatus.FAILED,
                        operation=operation,
                        items_failed=1,
                        errors=[f"Jira API error: {resp.status_code} - {resp.text}"],
                    )
                upd_map = self._upsert_mapping(
                    ResourceType.MILESTONE,
                    elder_id=mapping.elder_id,
                    external_id=mapping.external_id,
                    elder_updated_at=_parse_datetime(elder_ms.get("updated_at")),
                    existing=mapping,
                )
                return SyncResult(
                    status=SyncStatus.SUCCESS,
                    operation=operation,
                    items_synced=1,
                    updated_mappings=[upd_map],
                )

            # EXTERNAL_TO_ELDER pull
            ver = operation.external_data or {}
            if ver.get("id") is None:
                return SyncResult(
                    status=SyncStatus.FAILED,
                    operation=operation,
                    items_failed=1,
                    errors=["Jira version payload missing 'id'"],
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
                ver,
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
            self.logger.error(f"Jira milestone sync failed: {e}", exc_info=True)
            return SyncResult(
                status=SyncStatus.FAILED,
                operation=operation,
                items_failed=1,
                errors=[str(e)],
            )

    # ------------------------------------------------------------------
    # Labels <-> Jira Labels & Components
    # ------------------------------------------------------------------

    def _upsert_elder_label(
        self,
        name: str,
        *,
        external_id: str,
        description: str | None = None,
        existing_mapping: SyncMapping | None = None,
        sync_method: str = "batch",
    ) -> int:
        clean_name = name.strip()[:50]
        now = datetime.now(UTC)
        mapping = existing_mapping or self.get_mapping(
            ResourceType.LABEL, external_id=external_id
        )
        if mapping:
            self.db(self.db.issue_labels.id == mapping.elder_id).update(
                name=clean_name,
                description=description,
                updated_at=now,
            )
            self.db.commit()
            self._upsert_mapping(
                ResourceType.LABEL,
                elder_id=mapping.elder_id,
                external_id=external_id,
                elder_updated_at=now,
                existing=mapping,
            )
            return mapping.elder_id

        existing = self.db(self.db.issue_labels.name == clean_name).select().first()
        if existing:
            self.db(self.db.issue_labels.id == existing.id).update(
                description=description
                if description is not None
                else existing.description,
                updated_at=now,
            )
            self.db.commit()
            label_id = existing.id
        else:
            label_id = self.db.issue_labels.insert(
                name=clean_name,
                color=_DEFAULT_LABEL_COLOR,
                description=description,
                created_at=now,
                updated_at=now,
            )
            self.db.commit()

        self._upsert_mapping(
            ResourceType.LABEL,
            elder_id=label_id,
            external_id=external_id,
            elder_updated_at=now,
            sync_method=sync_method,
        )
        return label_id

    def sync_label(self, operation: SyncOperation) -> SyncResult:
        """Sync label with Jira Labels or Jira Project Components."""
        try:
            if operation.direction == SyncDirection.ELDER_TO_EXTERNAL:
                if self.label_mode == "labels":
                    return SyncResult(
                        status=SyncStatus.SUCCESS,
                        operation=operation,
                        items_synced=0,
                        metadata={
                            "label_mode": "labels",
                            "managed_on_issue": True,
                        },
                    )

                elder_lbl = operation.elder_data or {}
                mapping = operation.mapping
                if mapping is None and elder_lbl.get("id") is not None:
                    mapping = self.get_mapping(
                        ResourceType.LABEL, elder_id=elder_lbl["id"]
                    )

                payload = {
                    "name": elder_lbl.get("name") or "",
                    "description": elder_lbl.get("description") or "",
                    "project": self.project_key,
                }
                if operation.operation_type == "create" and mapping is None:
                    resp = self.client.post("/component", json=payload)
                    if resp.status_code in (200, 201):
                        created = resp.json()
                        new_map = self._upsert_mapping(
                            ResourceType.LABEL,
                            elder_id=elder_lbl["id"],
                            external_id=str(created["id"]),
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
                        errors=[f"Jira API error: {resp.status_code} - {resp.text}"],
                    )

                if not mapping:
                    return SyncResult(
                        status=SyncStatus.FAILED,
                        operation=operation,
                        items_failed=1,
                        errors=["No mapping found for Jira component update"],
                    )

                resp = self.client.put(
                    f"/component/{quote(mapping.external_id, safe='')}",
                    json={
                        "name": payload["name"],
                        "description": payload["description"],
                    },
                )
                if resp.status_code not in (200, 204):
                    return SyncResult(
                        status=SyncStatus.FAILED,
                        operation=operation,
                        items_failed=1,
                        errors=[f"Jira API error: {resp.status_code} - {resp.text}"],
                    )
                upd_map = self._upsert_mapping(
                    ResourceType.LABEL,
                    elder_id=mapping.elder_id,
                    external_id=mapping.external_id,
                    elder_updated_at=_parse_datetime(elder_lbl.get("updated_at")),
                    existing=mapping,
                )
                return SyncResult(
                    status=SyncStatus.SUCCESS,
                    operation=operation,
                    items_synced=1,
                    updated_mappings=[upd_map],
                )

            # EXTERNAL_TO_ELDER pull
            ext = operation.external_data or {}
            if isinstance(ext, str):
                name = ext
                ext_id = ext
                desc = None
            else:
                name = (ext.get("name") or ext.get("label") or "").strip()
                ext_id = str(ext.get("id") or name)
                desc = ext.get("description")

            if not name:
                return SyncResult(
                    status=SyncStatus.FAILED,
                    operation=operation,
                    items_failed=1,
                    errors=["Jira label/component payload missing 'name'"],
                )

            self._upsert_elder_label(
                name,
                external_id=ext_id,
                description=desc,
                existing_mapping=operation.mapping,
                sync_method=operation.metadata.get("sync_method", "batch"),
            )
            return SyncResult(
                status=SyncStatus.SUCCESS,
                operation=operation,
                items_synced=1,
            )
        except Exception as e:
            self.logger.error(f"Jira label sync failed: {e}", exc_info=True)
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
        """Perform batch synchronization for Jira Cloud resources."""
        norm_dir = (direction or "pull").lower()
        if norm_dir == SyncDirection.EXTERNAL_TO_ELDER.value:
            norm_dir = "pull"
        elif norm_dir == SyncDirection.ELDER_TO_EXTERNAL.value:
            norm_dir = "push"

        self.logger.info(f"Jira batch sync for {resource_type.value} ({norm_dir})")

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
        errors = list(pull_res.errors) + list(push_res.errors)
        return self._aggregate_result(
            resource_type,
            SyncDirection.BIDIRECTIONAL,
            total_synced,
            total_failed,
            errors,
        )

    def _aggregate_result(
        self,
        resource_type: ResourceType,
        direction: SyncDirection,
        total_synced: int,
        total_failed: int,
        errors: list[str],
    ) -> SyncResult:
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
        """Pull Jira issues via official ``POST /rest/api/3/search/jql``."""
        total_synced = 0
        total_failed = 0
        errors: list[str] = []

        jql = f'project = "{self.project_key}"'
        if since:
            jql = f'{jql} AND updated >= "{since.strftime("%Y-%m-%d %H:%M")}"'
        jql = f"{jql} ORDER BY updated DESC"

        next_page_token: str | None = None
        while True:
            try:
                body: dict[str, Any] = {
                    "jql": jql,
                    "maxResults": 100,
                    "fields": [
                        "summary",
                        "description",
                        "status",
                        "priority",
                        "issuetype",
                        "labels",
                        "fixVersions",
                        "assignee",
                        "updated",
                        "duedate",
                    ],
                }
                if next_page_token:
                    body["nextPageToken"] = next_page_token

                resp = self.client.post("/search/jql", json=body)
                if resp.status_code != 200:
                    errors.append(f"Jira API error: {resp.status_code} - {resp.text}")
                    total_failed += 1
                    break

                data = resp.json() or {}
                issues = data.get("issues") or []
                for issue in issues:
                    issue_key = issue.get("key")
                    mapping = (
                        self.get_mapping(ResourceType.ISSUE, external_id=str(issue_key))
                        if issue_key
                        else None
                    )
                    op = SyncOperation(
                        operation_type="update" if mapping else "create",
                        resource_type=ResourceType.ISSUE,
                        direction=SyncDirection.EXTERNAL_TO_ELDER,
                        external_data=issue,
                        mapping=mapping,
                        metadata={"allow_create": True, "sync_method": "batch"},
                    )
                    res = self._sync_issue_from_jira(op)
                    if res.is_success:
                        total_synced += 1
                    else:
                        total_failed += 1
                        errors.extend(res.errors)

                next_page_token = data.get("nextPageToken")
                if data.get("isLast", True) or not next_page_token:
                    break
            except Exception as e:
                errors.append(f"Jira batch issue pull error: {e}")
                total_failed += 1
                break

        return self._aggregate_result(
            ResourceType.ISSUE,
            SyncDirection.EXTERNAL_TO_ELDER,
            total_synced,
            total_failed,
            errors,
        )

    def _batch_push_issues(self) -> SyncResult:
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
            label_rows = self.db(
                (self.db.issue_label_assignments.issue_id == row.id)
                & (self.db.issue_label_assignments.label_id == self.db.issue_labels.id)
            ).select(self.db.issue_labels.name)
            label_names = [lr.name for lr in label_rows]

            ms_link = (
                self.db(self.db.issue_milestone_links.issue_id == row.id)
                .select()
                .first()
            )
            op = SyncOperation(
                operation_type="update" if mapping else "create",
                resource_type=ResourceType.ISSUE,
                direction=SyncDirection.ELDER_TO_EXTERNAL,
                elder_data={
                    "id": row.id,
                    "title": row.title,
                    "description": row.description,
                    "status": row.status,
                    "priority": row.priority,
                    "issue_type": row.issue_type,
                    "updated_at": row.updated_at,
                    "labels": label_names,
                    "milestone_id": ms_link.milestone_id if ms_link else None,
                },
                mapping=mapping,
            )
            res = self._sync_issue_to_jira(op)
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

    def _batch_pull_projects(self, since: datetime | None = None) -> SyncResult:
        total_synced = 0
        total_failed = 0
        errors: list[str] = []

        if self.sync_all_projects:
            start_at = 0
            while True:
                try:
                    resp = self.client.get(
                        "/project/search",
                        params={"startAt": start_at, "maxResults": 50},
                    )
                    if resp.status_code != 200:
                        errors.append(f"Jira API error: {resp.status_code}")
                        total_failed += 1
                        break
                    data = resp.json() or {}
                    values = data.get("values") or []
                    for proj in values:
                        ext_id = str(proj.get("key") or proj.get("id"))
                        mapping = self.get_mapping(
                            ResourceType.PROJECT, external_id=ext_id
                        )
                        op = SyncOperation(
                            operation_type="update" if mapping else "create",
                            resource_type=ResourceType.PROJECT,
                            direction=SyncDirection.EXTERNAL_TO_ELDER,
                            external_data=proj,
                            mapping=mapping,
                            metadata={"allow_create": True, "sync_method": "batch"},
                        )
                        res = self.sync_project(op)
                        if res.is_success:
                            total_synced += 1
                        else:
                            total_failed += 1
                            errors.extend(res.errors)
                    if data.get("isLast", True) or not values:
                        break
                    start_at += len(values)
                except Exception as e:
                    errors.append(f"Jira batch project pull error: {e}")
                    total_failed += 1
                    break
        else:
            try:
                resp = self.client.get(
                    f"/project/{quote(self.project_key or '', safe='')}"
                )
                if resp.status_code != 200:
                    errors.append(f"Jira API error: {resp.status_code}")
                    total_failed += 1
                else:
                    proj = resp.json()
                    ext_id = str(proj.get("key") or proj.get("id"))
                    mapping = self.get_mapping(ResourceType.PROJECT, external_id=ext_id)
                    op = SyncOperation(
                        operation_type="update" if mapping else "create",
                        resource_type=ResourceType.PROJECT,
                        direction=SyncDirection.EXTERNAL_TO_ELDER,
                        external_data=proj,
                        mapping=mapping,
                        metadata={"allow_create": True, "sync_method": "batch"},
                    )
                    res = self.sync_project(op)
                    if res.is_success:
                        total_synced += 1
                    else:
                        total_failed += 1
                        errors.extend(res.errors)
            except Exception as e:
                errors.append(f"Jira project pull error: {e}")
                total_failed += 1

        return self._aggregate_result(
            ResourceType.PROJECT,
            SyncDirection.EXTERNAL_TO_ELDER,
            total_synced,
            total_failed,
            errors,
        )

    def _batch_push_projects(self) -> SyncResult:
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

    def _batch_pull_milestones(self, since: datetime | None = None) -> SyncResult:
        total_synced = 0
        total_failed = 0
        errors: list[str] = []

        try:
            resp = self.client.get(
                f"/project/{quote(self.project_key or '', safe='')}/versions"
            )
            if resp.status_code != 200:
                errors.append(f"Jira API error: {resp.status_code}")
                total_failed += 1
            else:
                versions = resp.json() or []
                for ver in versions:
                    mapping = self.get_mapping(
                        ResourceType.MILESTONE, external_id=str(ver["id"])
                    )
                    op = SyncOperation(
                        operation_type="update" if mapping else "create",
                        resource_type=ResourceType.MILESTONE,
                        direction=SyncDirection.EXTERNAL_TO_ELDER,
                        external_data=ver,
                        mapping=mapping,
                        metadata={"allow_create": True, "sync_method": "batch"},
                    )
                    res = self.sync_milestone(op)
                    if res.is_success:
                        total_synced += 1
                    else:
                        total_failed += 1
                        errors.extend(res.errors)
        except Exception as e:
            errors.append(f"Jira batch version pull error: {e}")
            total_failed += 1

        return self._aggregate_result(
            ResourceType.MILESTONE,
            SyncDirection.EXTERNAL_TO_ELDER,
            total_synced,
            total_failed,
            errors,
        )

    def _batch_push_milestones(self) -> SyncResult:
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
        total_synced = 0
        total_failed = 0
        errors: list[str] = []

        if self.label_mode == "components":
            try:
                resp = self.client.get(
                    f"/project/{quote(self.project_key or '', safe='')}/components"
                )
                if resp.status_code != 200:
                    errors.append(f"Jira API error: {resp.status_code}")
                    total_failed += 1
                else:
                    comps = resp.json() or []
                    for comp in comps:
                        mapping = self.get_mapping(
                            ResourceType.LABEL, external_id=str(comp["id"])
                        )
                        op = SyncOperation(
                            operation_type="update" if mapping else "create",
                            resource_type=ResourceType.LABEL,
                            direction=SyncDirection.EXTERNAL_TO_ELDER,
                            external_data=comp,
                            mapping=mapping,
                            metadata={"allow_create": True, "sync_method": "batch"},
                        )
                        res = self.sync_label(op)
                        if res.is_success:
                            total_synced += 1
                        else:
                            total_failed += 1
                            errors.extend(res.errors)
            except Exception as e:
                errors.append(f"Jira batch component pull error: {e}")
                total_failed += 1
        else:
            start_at = 0
            while True:
                try:
                    resp = self.client.get(
                        "/label", params={"startAt": start_at, "maxResults": 1000}
                    )
                    if resp.status_code != 200:
                        errors.append(f"Jira API error: {resp.status_code}")
                        total_failed += 1
                        break
                    data = resp.json() or {}
                    values = data.get("values") or []
                    for lbl in values:
                        mapping = self.get_mapping(
                            ResourceType.LABEL, external_id=str(lbl)
                        )
                        op = SyncOperation(
                            operation_type="update" if mapping else "create",
                            resource_type=ResourceType.LABEL,
                            direction=SyncDirection.EXTERNAL_TO_ELDER,
                            external_data={"name": str(lbl), "id": str(lbl)},
                            mapping=mapping,
                            metadata={"allow_create": True, "sync_method": "batch"},
                        )
                        res = self.sync_label(op)
                        if res.is_success:
                            total_synced += 1
                        else:
                            total_failed += 1
                            errors.extend(res.errors)
                    if data.get("isLast", True) or not values:
                        break
                    start_at += len(values)
                except Exception as e:
                    errors.append(f"Jira batch label pull error: {e}")
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
        if self.label_mode == "labels":
            return SyncResult(
                status=SyncStatus.SUCCESS,
                operation=SyncOperation(
                    operation_type="batch",
                    resource_type=ResourceType.LABEL,
                    direction=SyncDirection.ELDER_TO_EXTERNAL,
                ),
                items_synced=0,
                metadata={"label_mode": "labels", "managed_on_issue": True},
            )

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

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------

    def handle_webhook(self, webhook_data: dict[str, Any]) -> SyncResult:
        """Handle Jira Cloud webhook events (issues, versions, projects)."""
        webhook_event = (webhook_data.get("webhookEvent") or "").lower()
        self.logger.info(f"Jira webhook: {webhook_event}")

        if webhook_event.startswith("jira:issue_") and "issue" in webhook_data:
            issue = webhook_data["issue"]
            issue_key = issue.get("key")
            mapping = (
                self.get_mapping(ResourceType.ISSUE, external_id=str(issue_key))
                if issue_key
                else None
            )
            action = webhook_event.replace("jira:issue_", "")
            if action == "deleted":
                return SyncResult(
                    status=SyncStatus.SUCCESS,
                    operation=SyncOperation(
                        operation_type="delete",
                        resource_type=ResourceType.ISSUE,
                        direction=SyncDirection.EXTERNAL_TO_ELDER,
                        mapping=mapping,
                    ),
                    metadata={"deleted": True, "external_id": str(issue_key)},
                )
            op_type = (
                "create" if (action == "created" and mapping is None) else "update"
            )
            return self._sync_issue_from_jira(
                SyncOperation(
                    operation_type=op_type,
                    resource_type=ResourceType.ISSUE,
                    direction=SyncDirection.EXTERNAL_TO_ELDER,
                    external_data=issue,
                    mapping=mapping,
                    metadata={"sync_method": "webhook"},
                )
            )

        if webhook_event.startswith("jira:version_") and "version" in webhook_data:
            ver = webhook_data["version"]
            mapping = (
                self.get_mapping(ResourceType.MILESTONE, external_id=str(ver["id"]))
                if ver.get("id") is not None
                else None
            )
            action = webhook_event.replace("jira:version_", "")
            if action == "deleted":
                return SyncResult(
                    status=SyncStatus.SUCCESS,
                    operation=SyncOperation(
                        operation_type="delete",
                        resource_type=ResourceType.MILESTONE,
                        direction=SyncDirection.EXTERNAL_TO_ELDER,
                        mapping=mapping,
                    ),
                    metadata={"deleted": True, "external_id": str(ver.get("id"))},
                )
            op_type = (
                "create" if (action == "created" and mapping is None) else "update"
            )
            return self.sync_milestone(
                SyncOperation(
                    operation_type=op_type,
                    resource_type=ResourceType.MILESTONE,
                    direction=SyncDirection.EXTERNAL_TO_ELDER,
                    external_data=ver,
                    mapping=mapping,
                    metadata={"sync_method": "webhook"},
                )
            )

        if webhook_event.startswith("project_") and "project" in webhook_data:
            proj = webhook_data["project"]
            ext_id = str(proj.get("key") or proj.get("id"))
            mapping = self.get_mapping(ResourceType.PROJECT, external_id=ext_id)
            action = webhook_event.replace("project_", "")
            if action == "deleted":
                return SyncResult(
                    status=SyncStatus.SUCCESS,
                    operation=SyncOperation(
                        operation_type="delete",
                        resource_type=ResourceType.PROJECT,
                        direction=SyncDirection.EXTERNAL_TO_ELDER,
                        mapping=mapping,
                    ),
                    metadata={"deleted": True, "external_id": ext_id},
                )
            op_type = (
                "create" if (action == "created" and mapping is None) else "update"
            )
            return self.sync_project(
                SyncOperation(
                    operation_type=op_type,
                    resource_type=ResourceType.PROJECT,
                    direction=SyncDirection.EXTERNAL_TO_ELDER,
                    external_data=proj,
                    mapping=mapping,
                    metadata={"sync_method": "webhook"},
                )
            )

        return SyncResult(
            status=SyncStatus.SUCCESS,
            operation=SyncOperation(
                operation_type="webhook",
                resource_type=ResourceType.ISSUE,
                direction=SyncDirection.EXTERNAL_TO_ELDER,
            ),
            metadata={"ignored": True, "webhook_event": webhook_event},
        )
