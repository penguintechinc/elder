"""Comprehensive unit tests for Jira Cloud & GitHub integrations.

Covers:
- BaseSyncClient history/conflict inserts, UTC timestamps, close()/context manager
- BatchSyncScheduler PyDAL boolean queries and sync_history inserts
- WebhookHandler case-insensitive headers, fail-closed require_secret, Jira
  HMAC-SHA256 X-Hub-Signature, GitHub label/milestone/issue/ping & Jira
  issue/version/project event parsing
- GitHubSyncClient REST (2022-11-28) & Projects v2 GraphQL v4 compliance
- JiraSyncClient REST v3 (/search/jql, ADF, 204 updates, transitions,
  versions, labels/components, projects) compliance
- /api/v1/sync/* Quart endpoints & webhook platform rate-limiter exemption
"""

import hashlib
import hmac
import importlib.util
import json
import logging
import sys
from datetime import UTC, datetime
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from penguin_dal import DAL, Field
from quart import Quart
from quart_schema import QuartSchema

# Stub apscheduler ONLY when absent in the test container (apps/worker installs it).
if importlib.util.find_spec("apscheduler") is None:
    _aps = ModuleType("apscheduler")
    _sched = ModuleType("apscheduler.schedulers")
    _async_sched = ModuleType("apscheduler.schedulers.asyncio")
    _triggers = ModuleType("apscheduler.triggers")
    _interval = ModuleType("apscheduler.triggers.interval")

    class _DummyScheduler:
        def __init__(self, *args, **kwargs):
            self.jobs = {}

        def start(self):
            return None

        def shutdown(self, wait=True):
            return None

        def add_job(
            self,
            func,
            trigger=None,
            id=None,
            args=None,
            replace_existing=True,
            **kwargs,
        ):
            self.jobs[id] = (func, args)

        def remove_job(self, job_id):
            self.jobs.pop(job_id, None)

        def get_job(self, job_id):
            return self.jobs.get(job_id)

    class _DummyIntervalTrigger:
        def __init__(self, seconds=300):
            self.seconds = seconds

    _async_sched.AsyncIOScheduler = _DummyScheduler  # type: ignore[attr-defined]
    _interval.IntervalTrigger = _DummyIntervalTrigger  # type: ignore[attr-defined]
    sys.modules["apscheduler"] = _aps
    sys.modules["apscheduler.schedulers"] = _sched
    sys.modules["apscheduler.schedulers.asyncio"] = _async_sched
    sys.modules["apscheduler.triggers"] = _triggers
    sys.modules["apscheduler.triggers.interval"] = _interval

from apps.api.modules.discovery.routes.sync import bp as sync_bp
from apps.api.utils.rate_limiter import is_platform_request
from apps.worker.sync.base import (
    BaseSyncClient,
    ResourceType,
    SyncDirection,
    SyncMapping,
    SyncOperation,
    SyncResult,
    SyncStatus,
)
from apps.worker.sync.batch_scheduler import BatchSyncScheduler
from apps.worker.sync.conflict_resolver import ConflictResolver
from apps.worker.sync.platforms.github_client import GitHubSyncClient
from apps.worker.sync.platforms.jira_client import (
    JiraSyncClient,
    adf_to_text,
    normalize_jira_host,
    text_to_adf,
)
from apps.worker.sync.webhook_handler import WebhookEvent, WebhookHandler


def _create_sync_test_db(db_path: str | None = None) -> DAL:
    """Create a SQLite PyDAL database matching Elder sync schemas."""
    uri = f"sqlite:///{db_path}" if db_path else "sqlite:memory:"
    db = DAL(uri, migrate=True)

    db.define_table(
        "organizations",
        Field("name", "string"),
        Field("tenant_id", "integer"),
        Field("created_at", "datetime"),
        Field("updated_at", "datetime"),
    )
    db.define_table(
        "sync_configs",
        Field("name", "string", unique=True),
        Field("platform", "string"),
        Field("enabled", "boolean"),
        Field("sync_interval", "integer"),
        Field("batch_fallback_enabled", "boolean"),
        Field("batch_size", "integer"),
        Field("two_way_create", "boolean"),
        Field("webhook_enabled", "boolean"),
        Field("webhook_secret", "string"),
        Field("last_sync_at", "datetime"),
        Field("last_batch_sync_at", "datetime"),
        Field("config_json", "json"),
        Field("created_at", "datetime"),
        Field("updated_at", "datetime"),
    )
    db.define_table(
        "sync_history",
        Field("sync_config_id", "integer"),
        Field("correlation_id", "string"),
        Field("sync_type", "string"),
        Field("items_synced", "integer"),
        Field("items_failed", "integer"),
        Field("started_at", "datetime"),
        Field("completed_at", "datetime"),
        Field("success", "boolean"),
        Field("error_message", "text"),
        Field("sync_metadata", "json"),
    )
    db.define_table(
        "sync_mappings",
        Field("elder_type", "string"),
        Field("elder_id", "integer"),
        Field("external_platform", "string"),
        Field("external_id", "string"),
        Field("sync_config_id", "integer"),
        Field("sync_status", "string"),
        Field("sync_method", "string"),
        Field("last_synced_at", "datetime"),
        Field("elder_updated_at", "datetime"),
        Field("external_updated_at", "datetime"),
        Field("created_at", "datetime"),
        Field("updated_at", "datetime"),
    )
    db.define_table(
        "sync_conflicts",
        Field("mapping_id", "integer"),
        Field("conflict_type", "string"),
        Field("elder_data", "json"),
        Field("external_data", "json"),
        Field("resolution_strategy", "string"),
        Field("resolved", "boolean"),
        Field("resolved_at", "datetime"),
        Field("resolved_by_id", "integer"),
        Field("created_at", "datetime"),
    )
    db.define_table(
        "projects",
        Field("name", "string"),
        Field("description", "text"),
        Field("status", "string"),
        Field("start_date", "datetime"),
        Field("target_date", "datetime"),
        Field("organization_id", "integer"),
        Field("tenant_id", "integer"),
        Field("created_at", "datetime"),
        Field("updated_at", "datetime"),
    )
    db.define_table(
        "milestones",
        Field("title", "string"),
        Field("description", "text"),
        Field("status", "string"),
        Field("due_date", "datetime"),
        Field("closed_at", "datetime"),
        Field("organization_id", "integer"),
        Field("tenant_id", "integer"),
        Field("created_at", "datetime"),
        Field("updated_at", "datetime"),
    )
    db.define_table(
        "issue_labels",
        Field("name", "string"),
        Field("color", "string"),
        Field("description", "text"),
        Field("created_at", "datetime"),
        Field("updated_at", "datetime"),
    )
    db.define_table(
        "issues",
        Field("title", "string"),
        Field("description", "text"),
        Field("status", "string"),
        Field("priority", "string"),
        Field("issue_type", "string"),
        Field("resource_type", "string"),
        Field("resource_id", "integer"),
        Field("organization_id", "integer"),
        Field("tenant_id", "integer"),
        Field("is_incident", "integer"),
        Field("reporter_id", "integer"),
        Field("due_date", "datetime"),
        Field("closed_at", "datetime"),
        Field("created_at", "datetime"),
        Field("updated_at", "datetime"),
    )
    db.define_table(
        "issue_label_assignments",
        Field("issue_id", "integer"),
        Field("label_id", "integer"),
    )
    db.define_table(
        "issue_milestone_links",
        Field("issue_id", "integer"),
        Field("milestone_id", "integer"),
        Field("created_at", "datetime"),
    )
    db.define_table(
        "issue_project_links",
        Field("issue_id", "integer"),
        Field("project_id", "integer"),
        Field("created_at", "datetime"),
    )

    now = datetime.now(UTC)
    db.organizations.insert(
        name="Acme Org", tenant_id=7, created_at=now, updated_at=now
    )
    db.commit()
    return db


def _mock_client_transport(client: Any, handler: Any) -> None:
    """Attach an ``httpx.MockTransport`` while keeping base_url/headers/auth."""
    old = client.client
    client.client = httpx.Client(
        base_url=old.base_url,
        headers=old.headers,
        auth=old.auth,
        transport=httpx.MockTransport(handler),
        timeout=30.0,
    )
    old.close()


class _DummySyncClient(BaseSyncClient):
    """Minimal concrete BaseSyncClient for base framework unit tests."""

    def __init__(self, db: DAL, sync_config_id: int, logger: Any):
        super().__init__("github", {}, db, sync_config_id, logger)
        self.client = MagicMock()

    def validate_config(self) -> bool:
        return True

    def test_connection(self) -> bool:
        return True

    def sync_issue(self, operation: SyncOperation) -> SyncResult:
        return SyncResult(
            status=SyncStatus.SUCCESS, operation=operation, items_synced=1
        )

    def sync_project(self, operation: SyncOperation) -> SyncResult:
        return SyncResult(
            status=SyncStatus.SUCCESS, operation=operation, items_synced=1
        )

    def sync_milestone(self, operation: SyncOperation) -> SyncResult:
        return SyncResult(
            status=SyncStatus.SUCCESS, operation=operation, items_synced=1
        )

    def sync_label(self, operation: SyncOperation) -> SyncResult:
        return SyncResult(
            status=SyncStatus.SUCCESS, operation=operation, items_synced=1
        )

    def batch_sync(
        self,
        resource_type: ResourceType,
        since: datetime | None = None,
        direction: str = "pull",
    ) -> SyncResult:
        return SyncResult(
            status=SyncStatus.SUCCESS,
            operation=SyncOperation(
                "batch", resource_type, SyncDirection.EXTERNAL_TO_ELDER
            ),
            items_synced=2,
        )

    def handle_webhook(self, webhook_data: dict) -> SyncResult:
        return SyncResult(
            status=SyncStatus.SUCCESS,
            operation=SyncOperation(
                "webhook", ResourceType.ISSUE, SyncDirection.EXTERNAL_TO_ELDER
            ),
            items_synced=1,
            metadata={"handled": True, "webhook_data": webhook_data},
        )


@pytest.fixture
def sync_db(tmp_path) -> DAL:
    db = _create_sync_test_db(str(tmp_path / "sync_test.db"))
    yield db
    db.close()


@pytest.fixture
def test_logger() -> logging.Logger:
    return logging.getLogger("test.jira_github_sync")


class TestBaseSyncAndScheduler:
    def test_record_history_and_conflict_match_postgres_columns(
        self, sync_db, test_logger
    ):
        now = datetime.now(UTC)
        cfg_id = sync_db.sync_configs.insert(
            name="gh-main",
            platform="github",
            enabled=True,
            sync_interval=300,
            batch_fallback_enabled=True,
            batch_size=100,
            two_way_create=True,
            webhook_enabled=True,
            webhook_secret="sec",
            config_json={},
            created_at=now,
            updated_at=now,
        )
        mapping_id = sync_db.sync_mappings.insert(
            elder_type="issue",
            elder_id=10,
            external_platform="github",
            external_id="42",
            sync_config_id=cfg_id,
            created_at=now,
            updated_at=now,
        )
        sync_db.commit()

        with _DummySyncClient(sync_db, cfg_id, test_logger) as client:
            result = SyncResult(
                status=SyncStatus.SUCCESS,
                operation=SyncOperation(
                    "update",
                    ResourceType.ISSUE,
                    SyncDirection.EXTERNAL_TO_ELDER,
                    correlation_id="corr-1",
                ),
                items_synced=1,
            )
            client.record_sync_history(result, sync_type="webhook")
            conflict_obj = SimpleNamespace(
                conflict_type="concurrent_modification",
                elder_data={"title": "Elder"},
                external_data={"title": "Remote"},
                resolution_strategy=None,
                resolved=False,
            )
            client.record_conflict(conflict_obj, mapping_id)
            mapping_obj = SyncMapping(
                elder_type="issue",
                elder_id=10,
                external_platform="github",
                external_id="42",
                sync_config_id=cfg_id,
            )
            client.update_mapping(mapping_obj, sync_status="synced")

        assert client.client.close.called
        hist = sync_db(sync_db.sync_history.sync_config_id == cfg_id).select().first()
        assert hist is not None
        assert hist.sync_type == "webhook"
        assert hist.success is True
        conflict = (
            sync_db(sync_db.sync_conflicts.mapping_id == mapping_id).select().first()
        )
        assert conflict is not None
        assert conflict.resolved is False

    @pytest.mark.asyncio
    async def test_batch_scheduler_load_and_run_pydal_queries(
        self, sync_db, test_logger
    ):
        now = datetime.now(UTC)
        cfg_id = sync_db.sync_configs.insert(
            name="sched-cfg",
            platform="github",
            enabled=True,
            sync_interval=300,
            batch_fallback_enabled=True,
            batch_size=50,
            two_way_create=False,
            webhook_enabled=True,
            config_json={
                "api_token": "tok",
                "owner": "penguintechinc",
                "repo_name": "elder",
            },
            created_at=now,
            updated_at=now,
        )
        sync_db.sync_configs.insert(
            name="disabled-cfg",
            platform="jira",
            enabled=False,
            sync_interval=300,
            batch_fallback_enabled=True,
            batch_size=50,
            two_way_create=False,
            webhook_enabled=True,
            config_json={},
            created_at=now,
            updated_at=now,
        )
        sync_db.commit()

        dummy_client = _DummySyncClient(sync_db, cfg_id, test_logger)
        scheduler = BatchSyncScheduler(sync_db, {"github": dummy_client}, test_logger)
        scheduler._load_sync_configs()
        expected_job_id = f"github_{cfg_id}_batch"
        assert list(scheduler.jobs.keys()) == [expected_job_id]

        await scheduler._run_batch_sync(scheduler.jobs[expected_job_id])
        rows = sync_db(sync_db.sync_history.sync_config_id == cfg_id).select()
        assert len(rows) == 1
        assert rows[0].sync_type == "batch"
        assert rows[0].items_synced == 8  # 4 resource types * 2 items


class TestWebhookHandlerValidationAndParsing:
    def test_require_secret_fails_closed_when_missing(self, sync_db, test_logger):
        client = _DummySyncClient(sync_db, 1, test_logger)
        handler = WebhookHandler(
            "github",
            None,
            client,
            ConflictResolver(test_logger),
            test_logger,
            require_secret=True,
        )
        res = handler.handle_webhook({"x-github-event": "issues"}, b"{}")
        assert res.status == SyncStatus.FAILED
        assert "Invalid webhook signature" in res.errors

    def test_jira_hmac_sha256_case_insensitive_validation(self, sync_db, test_logger):
        client = _DummySyncClient(sync_db, 1, test_logger)
        secret = "jira-super-secret"
        handler = WebhookHandler(
            "jira",
            secret,
            client,
            ConflictResolver(test_logger),
            test_logger,
            require_secret=True,
        )
        body = json.dumps(
            {
                "webhookEvent": "jira:version_created",
                "version": {"id": 91, "name": "v4.1"},
            }
        ).encode("utf-8")
        digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

        # Previously broken substring check must be rejected
        bad = handler.handle_webhook({"x-hub-signature": secret}, body)
        assert bad.status == SyncStatus.FAILED

        # Official Jira Cloud header X-Hub-Signature: sha256=<hex>
        ok = handler.handle_webhook({"x-hub-signature": f"sha256={digest}"}, body)
        assert ok.status == SyncStatus.SUCCESS
        assert ok.metadata["webhook_data"]["_resource_type"] == "milestone"
        assert ok.metadata["webhook_data"]["_action"] == "created"

    def test_github_label_and_ping_events_parsed(self, sync_db, test_logger):
        client = _DummySyncClient(sync_db, 1, test_logger)
        secret = "gh-sec"
        handler = WebhookHandler(
            "github",
            secret,
            client,
            ConflictResolver(test_logger),
            test_logger,
            require_secret=True,
        )

        label_body = json.dumps(
            {"action": "created", "label": {"name": "bug", "color": "d73a4a"}}
        ).encode("utf-8")
        sig = (
            "sha256="
            + hmac.new(secret.encode(), label_body, hashlib.sha256).hexdigest()
        )
        res = handler.handle_webhook(
            {"x-hub-signature-256": sig, "x-github-event": "label"}, label_body
        )
        assert res.status == SyncStatus.SUCCESS
        assert res.metadata["webhook_data"]["_resource_type"] == "label"
        assert res.metadata["webhook_data"]["_action"] == "created"

        ping_body = json.dumps(
            {"zen": "Responsive is better than fast.", "hook_id": 5}
        ).encode()
        ping_sig = (
            "sha256=" + hmac.new(secret.encode(), ping_body, hashlib.sha256).hexdigest()
        )
        ping_res = handler.handle_webhook(
            {"X-Hub-Signature-256": ping_sig, "X-GitHub-Event": "ping"}, ping_body
        )
        assert ping_res.status == SyncStatus.SUCCESS
        assert ping_res.metadata["webhook_data"]["_event"] == "ping"

    def test_jira_project_webhook_parsed(self, sync_db, test_logger):
        client = _DummySyncClient(sync_db, 1, test_logger)
        handler = WebhookHandler(
            "jira",
            None,
            client,
            ConflictResolver(test_logger),
            test_logger,
            require_secret=False,
        )
        proj_body = json.dumps(
            {
                "webhookEvent": "project_updated",
                "project": {"key": "ENG", "name": "Engineering"},
            }
        ).encode()
        res = handler.handle_webhook({}, proj_body)
        assert res.status == SyncStatus.SUCCESS
        assert res.metadata["webhook_data"]["_resource_type"] == "project"


class TestGitHubSyncClient:
    def _make_config(self, sync_db, **cfg_overrides) -> int:
        now = datetime.now(UTC)
        cfg = {
            "api_token": "ghp_test_token",
            "org_name": "penguintechinc",
            "repo_name": "elder",
            "elder_organization_id": 1,
        }
        cfg.update(cfg_overrides)
        return sync_db.sync_configs.insert(
            name="gh-integration",
            platform="github",
            enabled=True,
            sync_interval=300,
            batch_fallback_enabled=True,
            batch_size=100,
            two_way_create=True,
            webhook_enabled=True,
            config_json=cfg,
            created_at=now,
            updated_at=now,
        )

    def test_config_owner_org_name_and_ghes_graphql_url(self, sync_db, test_logger):
        cfg_id = self._make_config(sync_db, base_url="https://git.corp.example/api/v3")
        client = GitHubSyncClient(
            {
                "api_token": "tok",
                "org_name": "penguintechinc",
                "repo_name": "elder",
                "base_url": "https://git.corp.example/api/v3",
            },
            sync_db,
            cfg_id,
            test_logger,
        )
        assert client.validate_config() is True
        assert client.owner == "penguintechinc"
        assert client.graphql_url == "https://git.corp.example/api/graphql"
        client.close()

        bad = GitHubSyncClient({"api_token": "tok"}, sync_db, cfg_id, test_logger)
        assert bad.validate_config() is False
        with pytest.raises(ValueError):
            bad._repo_path("/issues")
        bad.close()

    def test_connection_detail_uses_official_headers(self, sync_db, test_logger):
        cfg_id = self._make_config(sync_db)
        cfg = sync_db.sync_configs[cfg_id].config_json
        client = GitHubSyncClient(cfg, sync_db, cfg_id, test_logger)
        seen: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            seen.append(req)
            if req.url.path == "/repos/penguintechinc/elder":
                return httpx.Response(
                    200,
                    json={
                        "full_name": "penguintechinc/elder",
                        "private": True,
                        "default_branch": "main",
                        "html_url": "https://github.com/penguintechinc/elder",
                        "open_issues_count": 7,
                    },
                )
            return httpx.Response(404)

        _mock_client_transport(client, handler)
        detail = client.test_connection_detail()
        assert detail["connected"] is True
        assert detail["repository"]["full_name"] == "penguintechinc/elder"
        assert seen[0].headers["x-github-api-version"] == "2022-11-28"
        assert seen[0].headers["accept"] == "application/vnd.github+json"
        assert seen[0].headers["authorization"] == "Bearer ghp_test_token"
        client.close()

    def test_pull_and_push_all_resources_plus_graphql_v2(self, sync_db, test_logger):
        cfg_id = self._make_config(sync_db)
        cfg = sync_db.sync_configs[cfg_id].config_json
        client = GitHubSyncClient(cfg, sync_db, cfg_id, test_logger)
        recorded: list[tuple[str, str, dict | None]] = []

        def handler(req: httpx.Request) -> httpx.Response:
            body = json.loads(req.content.decode()) if req.content else None
            recorded.append((req.method, req.url.path, body))

            # Projects v2 GraphQL
            if req.url.path == "/graphql":
                q = body["query"]
                if "organization(login:" in q:
                    return httpx.Response(
                        200,
                        json={
                            "data": {
                                "organization": {
                                    "id": "O_kwDO123",
                                    "projectsV2": {
                                        "pageInfo": {
                                            "hasNextPage": False,
                                            "endCursor": None,
                                        },
                                        "nodes": [
                                            {
                                                "id": "PVT_1",
                                                "title": "Q4 Security",
                                                "shortDescription": "Hardening",
                                                "closed": False,
                                                "updatedAt": "2026-11-20T10:00:00Z",
                                            }
                                        ],
                                    },
                                }
                            }
                        },
                    )
                if "createProjectV2" in q:
                    return httpx.Response(
                        200,
                        json={
                            "data": {
                                "createProjectV2": {
                                    "projectV2": {
                                        "id": "PVT_NEW",
                                        "title": body["variables"]["title"],
                                    }
                                }
                            }
                        },
                    )
                if "updateProjectV2" in q:
                    return httpx.Response(
                        200,
                        json={
                            "data": {
                                "updateProjectV2": {"projectV2": {"id": "PVT_NEW"}}
                            }
                        },
                    )

            # Milestones
            if (
                req.method == "GET"
                and req.url.path == "/repos/penguintechinc/elder/milestones"
            ):
                return httpx.Response(
                    200,
                    json=[
                        {
                            "number": 3,
                            "title": "v4.1.0",
                            "description": "Release milestone",
                            "state": "open",
                            "due_on": "2026-12-31T00:00:00Z",
                            "updated_at": "2026-11-20T09:00:00Z",
                        }
                    ],
                )
            if (
                req.method == "POST"
                and req.url.path == "/repos/penguintechinc/elder/milestones"
            ):
                return httpx.Response(
                    201, json={"number": 4, "title": body["title"], "state": "open"}
                )

            # Labels
            if (
                req.method == "GET"
                and req.url.path == "/repos/penguintechinc/elder/labels"
            ):
                return httpx.Response(
                    200,
                    json=[
                        {"name": "bug", "color": "d73a4a", "description": "Bug reports"}
                    ],
                )
            if (
                req.method == "POST"
                and req.url.path == "/repos/penguintechinc/elder/labels"
            ):
                # Simulate 422 already_exists on duplicate create to verify recovery
                if body["name"] == "existing-label":
                    return httpx.Response(
                        422, json={"errors": [{"code": "already_exists"}]}
                    )
                return httpx.Response(201, json=body)
            if (
                req.method == "GET"
                and req.url.path == "/repos/penguintechinc/elder/labels/existing-label"
            ):
                return httpx.Response(
                    200,
                    json={
                        "name": "existing-label",
                        "color": "112233",
                        "description": "old",
                    },
                )
            if (
                req.method == "PATCH"
                and "/repos/penguintechinc/elder/labels/" in req.url.path
            ):
                return httpx.Response(
                    200, json={"name": body.get("new_name", "existing-label"), **body}
                )

            # Issues
            if (
                req.method == "GET"
                and req.url.path == "/repos/penguintechinc/elder/issues"
            ):
                return httpx.Response(
                    200,
                    json=[
                        {
                            "number": 101,
                            "title": "Fix TLS cipher config",
                            "body": "Disable weak ciphers",
                            "state": "open",
                            "labels": [
                                {"name": "bug", "color": "d73a4a"},
                                {"name": "priority: high", "color": "ff0000"},
                            ],
                            "milestone": {
                                "number": 3,
                                "title": "v4.1.0",
                                "state": "open",
                            },
                            "updated_at": "2026-11-20T10:30:00Z",
                        }
                    ],
                )
            if (
                req.method == "POST"
                and req.url.path == "/repos/penguintechinc/elder/issues"
            ):
                return httpx.Response(201, json={"number": 202, "title": body["title"]})

            return httpx.Response(404)

        _mock_client_transport(client, handler)

        # Pull projects, milestones, labels, issues via batch_sync
        assert (
            client.batch_sync(ResourceType.PROJECT, direction="pull").items_synced == 1
        )
        assert (
            client.batch_sync(ResourceType.MILESTONE, direction="pull").items_synced
            == 1
        )
        assert client.batch_sync(ResourceType.LABEL, direction="pull").items_synced == 1
        assert client.batch_sync(ResourceType.ISSUE, direction="pull").items_synced == 1

        # Verify DB persistence & Elder enum rules
        proj = sync_db(sync_db.projects.name == "Q4 Security").select().first()
        assert proj is not None and proj.status == "active"

        issue = (
            sync_db(sync_db.issues.title == "Fix TLS cipher config").select().first()
        )
        assert issue is not None
        assert issue.status == "OPEN"
        assert issue.priority == "HIGH"
        assert issue.issue_type == "BUG"
        assert issue.resource_type == "organization"
        assert issue.organization_id == 1
        assert issue.tenant_id == 7
        assert issue.is_incident == 0
        assert issue.reporter_id is None

        # Verify label assignment and milestone link created
        assert (
            len(sync_db(sync_db.issue_label_assignments.issue_id == issue.id).select())
            == 2
        )
        assert (
            len(sync_db(sync_db.issue_milestone_links.issue_id == issue.id).select())
            == 1
        )

        # Push label: strip '#' and truncate description to 100 chars, plus 422 recovery
        long_desc = "x" * 150
        push_lbl = client.sync_label(
            SyncOperation(
                "create",
                ResourceType.LABEL,
                SyncDirection.ELDER_TO_EXTERNAL,
                elder_data={
                    "id": 99,
                    "name": "existing-label",
                    "color": "#abcdef",
                    "description": long_desc,
                },
            )
        )
        assert push_lbl.is_success
        patch_calls = [r for r in recorded if r[0] == "PATCH" and "/labels/" in r[1]]
        assert patch_calls[0][2]["color"] == "abcdef"
        assert len(patch_calls[0][2]["description"]) == 100

        # Push issue uses `assignees` list (never singular `assignee`)
        push_issue = client.sync_issue(
            SyncOperation(
                "create",
                ResourceType.ISSUE,
                SyncDirection.ELDER_TO_EXTERNAL,
                elder_data={
                    "id": 500,
                    "title": "Push From Elder",
                    "description": "Details",
                    "status": "IN_PROGRESS",
                    "assignees": ["alice"],
                    "labels": ["bug"],
                    "milestone_id": 1,
                },
            )
        )
        assert push_issue.is_success
        post_issue_calls = [
            r for r in recorded if r[0] == "POST" and r[1].endswith("/issues")
        ]
        assert post_issue_calls[0][2]["assignees"] == ["alice"]
        assert "assignee" not in post_issue_calls[0][2]
        assert post_issue_calls[0][2]["state"] == "open"
        assert post_issue_calls[0][2]["milestone"] == 3

        # Push project via createProjectV2 + updateProjectV2
        push_proj = client.sync_project(
            SyncOperation(
                "create",
                ResourceType.PROJECT,
                SyncDirection.ELDER_TO_EXTERNAL,
                elder_data={
                    "id": 77,
                    "name": "New Board",
                    "description": "Desc",
                    "status": "archived",
                },
            )
        )
        assert push_proj.is_success
        assert any(
            r[1] == "/graphql" and "updateProjectV2" in (r[2] or {}).get("query", "")
            for r in recorded
        )

        # Webhook update preserves existing issue without creating duplicates
        wh = client.handle_webhook(
            {
                "action": "edited",
                "issue": {
                    "number": 101,
                    "title": "Fix TLS cipher config (Updated)",
                    "body": "Done",
                    "state": "closed",
                    "labels": [],
                    "updated_at": "2026-11-20T11:00:00Z",
                },
            }
        )
        assert wh.is_success
        reloaded = sync_db.issues[issue.id]
        assert reloaded.title == "Fix TLS cipher config (Updated)"
        assert reloaded.status == "CLOSED"

        client.close()

    def test_github_user_owned_projects_v2_fallback(self, sync_db, test_logger):
        cfg_id = self._make_config(sync_db, owner="octocat", org_name="")
        client = GitHubSyncClient(
            {
                "api_token": "tok",
                "owner": "octocat",
                "repo_name": "hello-world",
                "elder_organization_id": 1,
            },
            sync_db,
            cfg_id,
            test_logger,
        )

        def handler(req: httpx.Request) -> httpx.Response:
            body = json.loads(req.content.decode())
            q = body["query"]
            if "organization(login:" in q:
                return httpx.Response(
                    200,
                    json={
                        "data": {"organization": None},
                        "errors": [
                            {
                                "type": "NOT_FOUND",
                                "message": "Could not resolve to an Organization",
                            }
                        ],
                    },
                )
            if "user(login:" in q:
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "user": {
                                "id": "U_kgDO1",
                                "projectsV2": {
                                    "pageInfo": {
                                        "hasNextPage": False,
                                        "endCursor": None,
                                    },
                                    "nodes": [
                                        {
                                            "id": "PVT_USER_1",
                                            "title": "Personal Roadmap",
                                            "shortDescription": "User-owned board",
                                            "closed": False,
                                            "updatedAt": "2026-11-20T10:00:00Z",
                                        }
                                    ],
                                },
                            }
                        }
                    },
                )
            return httpx.Response(404)

        _mock_client_transport(client, handler)
        res = client.batch_sync(ResourceType.PROJECT, direction="pull")
        assert res.is_success
        assert res.items_synced == 1
        proj = sync_db(sync_db.projects.name == "Personal Roadmap").select().first()
        assert proj is not None
        client.close()


class TestJiraSyncClient:
    def _make_config(self, sync_db, **cfg_overrides) -> int:
        now = datetime.now(UTC)
        cfg = {
            "jira_url": "https://acme.atlassian.net/",
            "email": "sync@acme.com",
            "api_token": "ATATT_token",
            "project_key": "ENG",
            "elder_organization_id": 1,
        }
        cfg.update(cfg_overrides)
        return sync_db.sync_configs.insert(
            name="jira-cloud",
            platform="jira",
            enabled=True,
            sync_interval=300,
            batch_fallback_enabled=True,
            batch_size=100,
            two_way_create=True,
            webhook_enabled=True,
            config_json=cfg,
            created_at=now,
            updated_at=now,
        )

    def test_url_normalization_and_adf_round_trip(self):
        assert (
            normalize_jira_host("https://acme.atlassian.net/jira")
            == "acme.atlassian.net"
        )
        adf = text_to_adf("Line 1\n\nLine 2")
        assert adf["type"] == "doc" and adf["version"] == 1
        assert "Line 1" in adf_to_text(adf) and "Line 2" in adf_to_text(adf)
        assert adf_to_text("plain") == "plain"

    def test_jql_search_204_updates_transitions_versions_labels_and_components(
        self, sync_db, test_logger
    ):
        cfg_id = self._make_config(sync_db, label_mode="components")
        cfg = sync_db.sync_configs[cfg_id].config_json
        client = JiraSyncClient(cfg, sync_db, cfg_id, test_logger)
        calls: list[tuple[str, str, dict | None]] = []

        def handler(req: httpx.Request) -> httpx.Response:
            body = json.loads(req.content.decode()) if req.content else None
            calls.append((req.method, req.url.path, body))

            if req.method == "GET" and req.url.path == "/rest/api/3/project/ENG":
                return httpx.Response(
                    200,
                    json={
                        "id": "10000",
                        "key": "ENG",
                        "name": "Engineering",
                        "description": "Core engineering",
                        "projectTypeKey": "software",
                        "archived": False,
                    },
                )
            if req.method == "PUT" and req.url.path == "/rest/api/3/project/ENG":
                return httpx.Response(204)

            if (
                req.method == "GET"
                and req.url.path == "/rest/api/3/project/ENG/versions"
            ):
                return httpx.Response(
                    200,
                    json=[
                        {
                            "id": "55",
                            "name": "Q1 Release",
                            "description": "Milestone 1",
                            "released": False,
                            "releaseDate": "2026-03-31",
                        }
                    ],
                )
            if req.method == "POST" and req.url.path == "/rest/api/3/version":
                return httpx.Response(201, json={"id": "56", "name": body["name"]})

            if (
                req.method == "GET"
                and req.url.path == "/rest/api/3/project/ENG/components"
            ):
                return httpx.Response(
                    200, json=[{"id": "301", "name": "backend", "description": "API"}]
                )
            if req.method == "POST" and req.url.path == "/rest/api/3/component":
                return httpx.Response(201, json={"id": "302", "name": body["name"]})

            if req.method == "POST" and req.url.path == "/rest/api/3/search/jql":
                # Verify cursor pagination via nextPageToken -> isLast
                if (body or {}).get("nextPageToken") is None:
                    return httpx.Response(
                        200,
                        json={
                            "issues": [
                                {
                                    "id": "101",
                                    "key": "ENG-1",
                                    "fields": {
                                        "summary": "Rotate KMS keys",
                                        "description": text_to_adf("Annual rotation"),
                                        "status": {
                                            "name": "In Progress",
                                            "statusCategory": {"key": "indeterminate"},
                                        },
                                        "priority": {"name": "Highest"},
                                        "issuetype": {"name": "Bug"},
                                        "labels": ["secops"],
                                        "fixVersions": [
                                            {"id": "55", "name": "Q1 Release"}
                                        ],
                                        "updated": "2026-11-20T10:00:00.000+0000",
                                    },
                                }
                            ],
                            "nextPageToken": "page-2",
                            "isLast": False,
                        },
                    )
                return httpx.Response(200, json={"issues": [], "isLast": True})

            if req.method == "POST" and req.url.path == "/rest/api/3/issue":
                return httpx.Response(201, json={"id": "102", "key": "ENG-2"})
            if req.method == "PUT" and req.url.path == "/rest/api/3/issue/ENG-1":
                return httpx.Response(204)
            if (
                req.method == "GET"
                and req.url.path == "/rest/api/3/issue/ENG-1/transitions"
            ):
                return httpx.Response(
                    200,
                    json={
                        "transitions": [
                            {"id": "31", "name": "Done", "to": {"name": "Done"}}
                        ]
                    },
                )
            if (
                req.method == "POST"
                and req.url.path == "/rest/api/3/issue/ENG-1/transitions"
            ):
                return httpx.Response(204)

            return httpx.Response(404)

        _mock_client_transport(client, handler)

        detail = client.test_connection_detail()
        assert detail["connected"] is True
        assert detail["project"]["key"] == "ENG"

        # Pull projects, milestones (versions), labels (components), issues
        assert (
            client.batch_sync(ResourceType.PROJECT, direction="pull").items_synced == 1
        )
        assert (
            client.batch_sync(ResourceType.MILESTONE, direction="pull").items_synced
            == 1
        )
        assert client.batch_sync(ResourceType.LABEL, direction="pull").items_synced == 1
        assert client.batch_sync(ResourceType.ISSUE, direction="pull").items_synced == 1

        # Ensure /rest/api/3/search/jql was called (NOT deprecated /rest/api/3/search)
        assert any(c[0] == "POST" and c[1] == "/rest/api/3/search/jql" for c in calls)
        assert not any(c[1] == "/rest/api/3/search" for c in calls)

        issue = sync_db(sync_db.issues.title == "Rotate KMS keys").select().first()
        assert issue is not None
        assert issue.status == "IN_PROGRESS"
        assert issue.priority == "CRITICAL"
        assert issue.issue_type == "BUG"
        assert issue.description == "Annual rotation"

        # Push update returns 204 No Content and invokes transition
        mapping = client.get_mapping(ResourceType.ISSUE, elder_id=issue.id)
        upd_res = client.sync_issue(
            SyncOperation(
                "update",
                ResourceType.ISSUE,
                SyncDirection.ELDER_TO_EXTERNAL,
                elder_data={
                    "id": issue.id,
                    "title": "Rotate KMS keys",
                    "description": "Done",
                    "status": "CLOSED",
                },
                mapping=mapping,
            )
        )
        assert upd_res.is_success
        put_calls = [
            c for c in calls if c[0] == "PUT" and c[1] == "/rest/api/3/issue/ENG-1"
        ]
        assert put_calls[0][2]["fields"]["description"]["type"] == "doc"
        assert any(
            c[0] == "POST" and c[1] == "/rest/api/3/issue/ENG-1/transitions"
            for c in calls
        )

        # Creating a Jira project without project_lead_account_id fails explicitly
        fail_proj = client.sync_project(
            SyncOperation(
                "create",
                ResourceType.PROJECT,
                SyncDirection.ELDER_TO_EXTERNAL,
                elder_data={"id": 88, "name": "Missing Lead"},
            )
        )
        assert fail_proj.status == SyncStatus.FAILED
        assert "project_lead_account_id" in fail_proj.errors[0]

        client.close()

    def test_jira_labels_mode_pulls_global_labels(self, sync_db, test_logger):
        cfg_id = self._make_config(sync_db, label_mode="labels")
        cfg = sync_db.sync_configs[cfg_id].config_json
        client = JiraSyncClient(cfg, sync_db, cfg_id, test_logger)

        def handler(req: httpx.Request) -> httpx.Response:
            if req.method == "GET" and req.url.path == "/rest/api/3/label":
                return httpx.Response(
                    200,
                    json={
                        "values": ["incident-response", "compliance"],
                        "isLast": True,
                    },
                )
            return httpx.Response(404)

        _mock_client_transport(client, handler)
        res = client.batch_sync(ResourceType.LABEL, direction="pull")
        assert res.is_success
        assert res.items_synced == 2
        assert sync_db(sync_db.issue_labels.name == "incident-response").count() == 1
        assert sync_db(sync_db.issue_labels.name == "compliance").count() == 1
        client.close()


class TestSyncApiRoutesAndRateLimiter:
    @pytest.fixture
    def api_setup(self, sync_db):
        app = Quart(__name__)
        app.config["TESTING"] = True
        app.config["JWT_SECRET_KEY"] = "test-jwt-secret"
        app.config["SECRET_KEY"] = "test-secret"
        QuartSchema(app)
        app.db = sync_db  # type: ignore[attr-defined]
        app.register_blueprint(sync_bp)
        return app, sync_db

    def test_webhook_path_uses_platform_rate_limit_bucket(self):
        assert is_platform_request("/api/v1/sync/webhooks/github/1") is True
        assert is_platform_request("/api/v1/sync/webhooks/jira/42") is True
        assert is_platform_request("/api/v1/sync/configs") is False

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_sync_crud_secret_preservation_test_trigger_and_webhooks(
        self, mock_get_user, api_setup
    ):
        app, db = api_setup
        mock_get_user.return_value = SimpleNamespace(
            id=1, username="admin", is_superuser=True, portal_role="admin", tenant_id=7
        )
        client = app.test_client()

        # 1. Validation & Create
        bad_resp = await client.post(
            "/api/v1/sync/configs",
            json={
                "name": "Bad GH",
                "platform": "github",
                "config_json": {"owner": "x"},
            },
        )
        assert bad_resp.status_code == 400

        webhook_secret = "super-webhook-hmac-secret"
        create_resp = await client.post(
            "/api/v1/sync/configs",
            json={
                "name": "Elder GitHub",
                "platform": "github",
                "two_way_create": True,
                "webhook_enabled": True,
                "webhook_secret": webhook_secret,
                "config_json": {
                    "owner": "penguintechinc",
                    "repo_name": "elder",
                    "api_token": "ghp_real_secret_token",
                    "elder_organization_id": 1,
                },
            },
        )
        assert create_resp.status_code == 201
        created_data = json.loads(await create_resp.get_data())
        cfg_id = created_data["config"]["id"]
        assert created_data["config"]["has_webhook_secret"] is True
        assert created_data["config"]["config_json"]["api_token"] == "***REDACTED***"

        # Duplicate name -> 409
        dup_resp = await client.post(
            "/api/v1/sync/configs",
            json={
                "name": "Elder GitHub",
                "platform": "github",
                "config_json": {
                    "owner": "penguintechinc",
                    "repo_name": "elder",
                    "api_token": "ghp_other",
                },
            },
        )
        assert dup_resp.status_code == 409

        # 2. PATCH preserves redacted credentials & webhook_secret
        patch_resp = await client.patch(
            f"/api/v1/sync/configs/{cfg_id}",
            json={
                "sync_interval": 600,
                "webhook_secret": "***REDACTED***",
                "config_json": {
                    "owner": "penguintechinc",
                    "repo_name": "elder",
                    "api_token": "***REDACTED***",
                    "elder_organization_id": 1,
                },
            },
        )
        assert patch_resp.status_code == 200
        stored_row = db.sync_configs[cfg_id]
        assert stored_row.sync_interval == 600
        assert stored_row.webhook_secret == webhook_secret
        assert stored_row.config_json["api_token"] == "ghp_real_secret_token"

        # 3. Mock httpx.Client transport for /test & /trigger routes
        def mock_transport_handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/repos/penguintechinc/elder":
                return httpx.Response(
                    200,
                    json={
                        "full_name": "penguintechinc/elder",
                        "default_branch": "main",
                    },
                )
            if req.url.path == "/repos/penguintechinc/elder/labels":
                return httpx.Response(
                    200, json=[{"name": "security", "color": "ff0000"}]
                )
            if req.url.path == "/repos/penguintechinc/elder/milestones":
                return httpx.Response(200, json=[])
            if req.url.path == "/repos/penguintechinc/elder/issues":
                return httpx.Response(200, json=[])
            if req.url.path == "/graphql":
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "organization": {
                                "id": "O_1",
                                "projectsV2": {
                                    "pageInfo": {
                                        "hasNextPage": False,
                                        "endCursor": None,
                                    },
                                    "nodes": [],
                                },
                            }
                        }
                    },
                )
            return httpx.Response(404)

        real_httpx_client = httpx.Client

        def fake_httpx_client(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(mock_transport_handler)
            return real_httpx_client(*args, **kwargs)

        with patch(
            "apps.worker.sync.platforms.github_client.httpx.Client",
            side_effect=fake_httpx_client,
        ):
            test_resp = await client.post(f"/api/v1/sync/configs/{cfg_id}/test")
            assert test_resp.status_code == 200
            test_body = json.loads(await test_resp.get_data())
            assert test_body["connected"] is True

            trig_resp = await client.post(
                f"/api/v1/sync/configs/{cfg_id}/trigger",
                json={"resource_types": ["label"], "direction": "pull"},
            )
            assert trig_resp.status_code == 200
            trig_body = json.loads(await trig_resp.get_data())
            assert trig_body["success"] is True
            assert trig_body["items_synced"] == 1

        # 4. Public webhook endpoint fails closed on bad/missing secret, succeeds with HMAC
        bad_wh = await client.post(
            f"/api/v1/sync/webhooks/github/{cfg_id}",
            data=b'{"action":"opened","issue":{"number":99}}',
            headers={"X-GitHub-Event": "issues", "X-Hub-Signature-256": "sha256=wrong"},
        )
        assert bad_wh.status_code == 401

        good_payload = json.dumps(
            {
                "action": "opened",
                "issue": {
                    "number": 88,
                    "title": "Webhook Issue",
                    "body": "From webhook",
                    "state": "open",
                    "labels": [],
                    "updated_at": "2026-11-20T12:00:00Z",
                },
            }
        ).encode("utf-8")
        good_sig = (
            "sha256="
            + hmac.new(
                webhook_secret.encode(), good_payload, hashlib.sha256
            ).hexdigest()
        )
        ok_wh = await client.post(
            f"/api/v1/sync/webhooks/github/{cfg_id}",
            data=good_payload,
            headers={"X-GitHub-Event": "issues", "X-Hub-Signature-256": good_sig},
        )
        assert ok_wh.status_code == 200
        assert db(db.issues.title == "Webhook Issue").count() == 1

        # 5. Query history, mappings, status, conflicts resolve, and cascade delete
        hist_resp = await client.get(f"/api/v1/sync/history?config_id={cfg_id}")
        assert hist_resp.status_code == 200
        assert len(json.loads(await hist_resp.get_data())["history"]) == 2

        map_resp = await client.get(f"/api/v1/sync/mappings?config_id={cfg_id}")
        assert map_resp.status_code == 200
        mappings = json.loads(await map_resp.get_data())["mappings"]
        assert len(mappings) >= 2

        conflict_id = db.sync_conflicts.insert(
            mapping_id=mappings[0]["id"],
            conflict_type="concurrent_modification",
            elder_data={"a": 1},
            external_data={"a": 2},
            resolved=False,
            created_at=datetime.now(UTC),
        )
        db.commit()

        list_conf = await client.get(
            f"/api/v1/sync/conflicts?config_id={cfg_id}&resolved=false"
        )
        assert list_conf.status_code == 200
        assert len(json.loads(await list_conf.get_data())["conflicts"]) == 1

        res_conf = await client.post(
            f"/api/v1/sync/conflicts/{conflict_id}/resolve",
            json={"resolution_strategy": "external_wins"},
        )
        assert res_conf.status_code == 200
        assert json.loads(await res_conf.get_data())["conflict"]["resolved"] is True

        status_resp = await client.get("/api/v1/sync/status")
        assert status_resp.status_code == 200
        status_body = json.loads(await status_resp.get_data())
        assert status_body["configs"]["total"] == 1
        assert status_body["conflicts"]["unresolved"] == 0

        # Also check fail-closed webhook when secret cleared/disabled
        db(db.sync_configs.id == cfg_id).update(webhook_secret=None)
        db.commit()
        no_secret_wh = await client.post(
            f"/api/v1/sync/webhooks/github/{cfg_id}",
            data=good_payload,
            headers={"X-GitHub-Event": "issues"},
        )
        assert no_secret_wh.status_code == 401

        del_resp = await client.delete(f"/api/v1/sync/configs/{cfg_id}")
        assert del_resp.status_code == 200
        assert db(db.sync_configs.id == cfg_id).count() == 0
