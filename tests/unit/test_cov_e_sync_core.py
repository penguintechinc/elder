"""Unit tests for worker sync core: base, conflict resolver, webhook handler, batch scheduler."""

import base64
import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

import tests.unit.conftest_worker_stubs  # noqa: F401
from apps.worker.sync import batch_scheduler as bs
from apps.worker.sync.base import (
    BaseSyncClient,
    ConflictResolution,
    ResourceType,
    SyncDirection,
    SyncMapping,
    SyncOperation,
    SyncResult,
    SyncStatus,
)
from apps.worker.sync.batch_scheduler import BatchSyncJob, BatchSyncScheduler
from apps.worker.sync.conflict_resolver import (
    ConflictResolver,
    ConflictType,
    ResolutionStrategy,
)
from apps.worker.sync.webhook_handler import (
    WebhookEvent,
    WebhookHandler,
    WebhookPayload,
)

LOG = logging.getLogger("cov-e-sync-core")


def _fake_secret() -> str:
    """Build a throwaway shared secret at runtime for signature tests."""
    return "-".join(("cove", uuid4().hex[:6]))


def _op(**kw) -> SyncOperation:
    """Build a SyncOperation with defaults."""
    return SyncOperation(
        operation_type=kw.pop("operation_type", "update"),
        resource_type=ResourceType.ISSUE,
        direction=SyncDirection.BIDIRECTIONAL,
        **kw,
    )


class _Client(BaseSyncClient):
    """Concrete BaseSyncClient with trivial abstract implementations for tests."""

    def validate_config(self) -> bool:
        """Always valid."""
        return True

    def test_connection(self) -> bool:
        """Report a configurable connection state."""
        return getattr(self, "ok", True)

    def sync_issue(self, operation):
        """Return a success result."""
        return SyncResult(SyncStatus.SUCCESS, operation)

    sync_project = sync_milestone = sync_label = sync_issue

    def batch_sync(self, resource_type, since=None, direction="pull"):
        """Return a success result."""
        return SyncResult(SyncStatus.SUCCESS, _op())

    def handle_webhook(self, webhook_data):
        """Echo the webhook data in metadata."""
        return SyncResult(SyncStatus.SUCCESS, _op(), metadata=webhook_data)


class TestBase:
    """Tests for dataclasses and BaseSyncClient helpers."""

    def test_result_properties(self):
        """is_success and has_conflicts reflect status and conflicts."""
        ok = SyncResult(SyncStatus.PARTIAL_SUCCESS, _op())
        assert ok.is_success and not ok.has_conflicts
        bad = SyncResult(SyncStatus.FAILED, _op(), conflicts=[{"a": 1}])
        assert not bad.is_success and bad.has_conflicts

    def test_close_and_context_manager(self):
        """close() closes a present client; missing/closeless clients are fine."""
        c = _Client("github", {}, MagicMock(), 1, LOG)
        c.close()
        c.client = object()
        c.close()
        c.client = MagicMock()
        with c as entered:
            assert entered is c
        c.client.close.assert_called_once()

    def test_connection_detail(self):
        """test_connection_detail wraps the boolean connection result."""
        c = _Client("github", {}, MagicMock(), 1, LOG)
        c.ok = False
        assert c.test_connection_detail() == {
            "connected": False,
            "success": False,
            "platform": "github",
        }

    def test_get_mapping_found_and_missing(self):
        """get_mapping builds a SyncMapping from a row, or None."""
        db = MagicMock()
        row = SimpleNamespace(
            elder_type="issue",
            elder_id=3,
            external_platform="github",
            external_id="9",
            sync_config_id=1,
            last_synced_at=None,
            elder_updated_at=None,
            external_updated_at=None,
        )
        db.return_value.select.return_value.first.return_value = row
        c = _Client("github", {}, db, 1, LOG)
        m = c.get_mapping(ResourceType.ISSUE, elder_id=3, external_id="9")
        assert m.elder_id == 3 and m.external_id == "9"
        db.return_value.select.return_value.first.return_value = None
        assert c.get_mapping(ResourceType.ISSUE) is None

    def test_mapping_history_conflict_writes(self):
        """create/update mapping, history and conflict records hit the DB and commit."""
        db = MagicMock()
        db.sync_mappings.insert.return_value = 11
        db.sync_conflicts.insert.return_value = 12
        c = _Client("github", {}, db, 1, LOG)
        m = SyncMapping("issue", 1, "github", "5", 1)
        assert c.create_mapping(m) == 11
        c.update_mapping(m, sync_status="conflict")
        db.return_value.update.assert_called_once()
        c.record_sync_history(
            SyncResult(SyncStatus.FAILED, _op(correlation_id="x"), errors=["a", "b"])
        )
        assert db.sync_history.insert.call_args.kwargs["error_message"] == "a; b"
        c.record_sync_history(SyncResult(SyncStatus.SUCCESS, _op()))
        assert db.sync_history.insert.call_args.kwargs["error_message"] is None
        conf = ConflictResolution("both_modified", {}, {})
        assert c.record_conflict(conf, 11) == 12
        assert db.commit.call_count == 5


@pytest.fixture
def resolver():
    """Return a ConflictResolver with a mock logger."""
    return ConflictResolver(MagicMock())


NOW = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)


class TestDetect:
    """Tests for ConflictResolver.detect_conflict."""

    def test_no_mapping(self, resolver):
        """No mapping means first sync: no conflict."""
        assert resolver.detect_conflict({}, {}, None) is None

    def test_deleted_local(self, resolver):
        """Local deletion yields DELETED_LOCAL/manual."""
        m = SyncMapping("issue", 1, "g", "1", 1)
        c = resolver.detect_conflict({"deleted": True}, {}, m)
        assert c.conflict_type == ConflictType.DELETED_LOCAL.value
        assert c.resolution_strategy == "manual"

    def test_deleted_external(self, resolver):
        """External deletion yields DELETED_EXTERNAL."""
        m = SyncMapping("issue", 1, "g", "1", 1)
        c = resolver.detect_conflict({}, {"deleted": True}, m)
        assert c.conflict_type == ConflictType.DELETED_EXTERNAL.value

    def test_both_modified(self, resolver):
        """Both sides newer than last sync is BOTH_MODIFIED."""
        m = SyncMapping("issue", 1, "g", "1", 1, last_synced_at=NOW)
        e = {"updated_at": (NOW + timedelta(hours=1)).isoformat()}
        x = {"updated_at": (NOW + timedelta(hours=2)).isoformat()}
        c = resolver.detect_conflict(e, x, m)
        assert c.conflict_type == ConflictType.BOTH_MODIFIED.value

    def test_only_one_modified_then_mismatch(self, resolver):
        """One-sided modification falls through to field mismatch detection."""
        m = SyncMapping("issue", 1, "g", "1", 1, last_synced_at=NOW)
        e = {"updated_at": (NOW - timedelta(hours=1)).isoformat(), "t": "a"}
        x = {"updated_at": (NOW + timedelta(hours=2)).isoformat(), "t": "b"}
        c = resolver.detect_conflict(e, x, m)
        assert c.conflict_type == ConflictType.FIELD_MISMATCH.value

    def test_unparseable_timestamp_and_no_conflict(self, resolver):
        """Unparseable timestamps skip the timestamp rule; equal data is no conflict."""
        m = SyncMapping("issue", 1, "g", "1", 1, last_synced_at=NOW)
        e = {"updated_at": "garbage", "t": "a"}
        x = {"updated_at": "garbage", "t": "a"}
        assert resolver.detect_conflict(e, x, m) is None
        assert resolver.detect_conflict({"t": 1}, {"t": 1}, m) is None


class TestResolve:
    """Tests for resolve_conflict and strategy helpers."""

    def _c(self, strat, e, x):
        """Build a ConflictResolution for a strategy."""
        return ConflictResolution("t", e, x, resolution_strategy=strat)

    def test_default_strategy_from_conflict(self, resolver):
        """Strategy defaults to the conflict's own strategy value."""
        c = resolver.resolve_conflict(self._c("elder_wins", {"a": 1}, {"a": 2}))
        assert c.resolved and c.resolution_data == {"a": 1}

    def test_external_wins(self, resolver):
        """EXTERNAL_WINS picks external data."""
        c = resolver.resolve_conflict(
            self._c("manual", {"a": 1}, {"a": 2}), ResolutionStrategy.EXTERNAL_WINS
        )
        assert c.resolution_data == {"a": 2}
        assert c.resolution_strategy == "external_wins"

    def test_manual_unresolved(self, resolver):
        """MANUAL leaves the conflict unresolved."""
        c = resolver.resolve_conflict(self._c("manual", {}, {}))
        assert not c.resolved and c.resolution_data is None

    def test_last_modified_variants(self, resolver):
        """Newer side wins; missing timestamps fall back to elder."""
        old, new = NOW.isoformat(), (NOW + timedelta(hours=1)).isoformat()
        s = ResolutionStrategy.LAST_MODIFIED_WINS
        r = resolver.resolve_conflict(
            self._c("manual", {"updated_at": new, "v": 1}, {"updated_at": old, "v": 2}),
            s,
        )
        assert r.resolution_data["v"] == 1
        r = resolver.resolve_conflict(
            self._c("manual", {"updated_at": old, "v": 1}, {"updated_at": new, "v": 2}),
            s,
        )
        assert r.resolution_data["v"] == 2
        r = resolver.resolve_conflict(self._c("manual", {"v": 1}, {"v": 2}), s)
        assert r.resolution_data["v"] == 1

    def test_field_merge(self, resolver):
        """Field merge covers one-sided, differing, same and metadata keys."""
        old, new = NOW.isoformat(), (NOW + timedelta(hours=1)).isoformat()
        e = {
            "updated_at": new,
            "id": 0,
            "only_e": 1,
            "diff": "e",
            "same": "s",
            "n": None,
        }
        x = {"updated_at": old, "id": 7, "only_x": 2, "diff": "x", "same": "s", "n": 5}
        out = resolver.resolve_conflict(
            self._c("manual", e, x), ResolutionStrategy.FIELD_MERGE
        ).resolution_data
        assert out["only_e"] == 1 and out["only_x"] == 2
        assert out["diff"] == "e" and out["same"] == "s"
        assert out["id"] == 7 and out["n"] == 5
        e["updated_at"], x["updated_at"] = old, new
        out = resolver._resolve_field_merge(self._c("manual", e, x))
        assert out["diff"] == "x"
        out = resolver._resolve_field_merge(self._c("manual", {"d": 1}, {"d": 2}))
        assert out["d"] == 1

    def test_parse_datetime(self, resolver):
        """_parse_datetime handles datetimes, strings, numbers and junk."""
        p = resolver._parse_datetime
        assert p(None) is None
        assert p(datetime(2026, 1, 1)).tzinfo is not None
        aware = datetime(2026, 1, 1, tzinfo=UTC)
        assert p(aware) == aware
        assert p("2026-01-01T00:00:00Z") == aware
        assert p("2026-01-01T00:00:00") == aware
        assert p("nope") is None
        assert p(0) == datetime.fromtimestamp(0, tz=UTC)
        assert p(object()) is None

    @pytest.mark.xfail(
        strict=True,
        reason="bug: _parse_datetime does not catch OverflowError for huge timestamps",
    )
    def test_parse_datetime_overflow(self, resolver):
        """A huge numeric timestamp should yield None but raises OverflowError."""
        assert resolver._parse_datetime(10**30) is None

    def test_summary(self, resolver):
        """get_conflict_summary reports mismatches and resolution state."""
        c = self._c("manual", {"a": 1, "updated_at": "x"}, {"a": 2, "updated_at": "y"})
        s = resolver.get_conflict_summary(c)
        assert s["mismatched_fields"] == ["a"] and s["resolution_data_set"] is False
        assert s["elder_modified"] == "x"


def _handler(platform, secret="unset", require=False):
    """Build a WebhookHandler with a mocked sync client; return (handler, secret)."""
    client = MagicMock()
    client.handle_webhook.return_value = SyncResult(SyncStatus.SUCCESS, _op())
    sec = _fake_secret() if secret == "unset" else secret
    h = WebhookHandler(
        platform.upper(), sec, client, ConflictResolver(LOG), LOG, require
    )
    return h, sec


class TestSignatures:
    """Tests for per-platform signature validation."""

    def test_no_secret(self):
        """No secret: allowed unless require_secret is set."""
        h, _ = _handler("github", None)
        assert h.validate_signature(b"x", "") is True
        h, _ = _handler("github", None, True)
        assert h.validate_signature(b"x", "") is False

    def test_missing_signature(self):
        """A configured secret with no signature is rejected."""
        h, _ = _handler("github")
        assert h.validate_signature(b"x", "") is False

    def test_github_and_jira(self):
        """HMAC-SHA256 sha256= prefixed signatures validate."""
        for plat in ("github", "jira"):
            h, sec = _handler(plat)
            body = b"{}"
            sig = "sha256=" + hmac.new(sec.encode(), body, hashlib.sha256).hexdigest()
            assert h.validate_signature(body, sig)
            assert not h.validate_signature(body, "sha256=bad")
            assert not h.validate_signature(body, "md5=bad")

    def test_jira_without_secret(self):
        """_validate_jira_signature rejects when no secret is set."""
        h, _ = _handler("jira", None)
        assert h._validate_jira_signature(b"", "sha256=x") is False

    def test_gitlab(self):
        """GitLab compares the plain token."""
        h, sec = _handler("gitlab")
        assert h.validate_signature(b"", sec)
        assert not h.validate_signature(b"", "other")

    def test_trello_and_openproject(self):
        """Trello (sha1 b64) and OpenProject (sha256 hex) signatures validate."""
        h, sec = _handler("trello")
        body = b"abc"
        sig = base64.b64encode(
            hmac.new(sec.encode(), body, hashlib.sha1).digest()
        ).decode()
        assert h.validate_signature(body, sig) and not h.validate_signature(body, "z")
        h, sec = _handler("openproject")
        sig = hmac.new(sec.encode(), body, hashlib.sha256).hexdigest()
        assert h.validate_signature(body, sig) and not h.validate_signature(body, "z")

    def test_unknown_platform(self):
        """An unknown platform fails closed."""
        h, _ = _handler("bogus")
        assert h.validate_signature(b"", "sig") is False

    def test_extract_signature_headers(self):
        """Signature header names are platform specific and case-insensitive."""
        for plat, name in [
            ("github", "X-Hub-Signature-256"),
            ("jira", "X-Hub-Signature"),
            ("gitlab", "X-Gitlab-Token"),
            ("trello", "X-Trello-Webhook"),
            ("openproject", "X-OpenProject-Signature"),
        ]:
            h, _ = _handler(plat)
            assert h._extract_signature({name: "v"}) == "v"
            assert h._extract_signature({name.lower(): "w"}) == "w"
            assert h._extract_signature({}) == ""
        h, _ = _handler("other")
        assert h._extract_signature({"a": "b"}) == ""


def _signed(sec, data, event):
    """Return (headers, payload) carrying a valid GitHub signature."""
    body = json.dumps(data).encode()
    sig = "sha256=" + hmac.new(sec.encode(), body, hashlib.sha256).hexdigest()
    return {"X-Hub-Signature-256": sig, "X-GitHub-Event": event}, body


class TestHandleWebhook:
    """Tests for handle_webhook flow."""

    def test_bad_signature(self):
        """Invalid signature returns FAILED."""
        h, _ = _handler("github")
        r = h.handle_webhook({"X-Hub-Signature-256": "sha256=bad"}, b"{}")
        assert r.status == SyncStatus.FAILED and "signature" in r.errors[0]

    def test_bad_json(self):
        """Invalid JSON returns FAILED."""
        h, sec = _handler("github")
        body = b"\xff\xfe"
        sig = "sha256=" + hmac.new(sec.encode(), body, hashlib.sha256).hexdigest()
        r = h.handle_webhook({"X-Hub-Signature-256": sig}, body)
        assert r.status == SyncStatus.FAILED and "Invalid JSON" in r.errors[0]

    def test_no_parser(self):
        """A platform without a parser returns FAILED (no secret skips validation)."""
        h, _ = _handler("bogus", None)
        r = h.handle_webhook({}, b"{}")
        assert r.errors == ["Unsupported platform: bogus"]

    def test_unknown_event_ignored(self):
        """Unknown events succeed with ignored metadata."""
        h, sec = _handler("github")
        hdr, body = _signed(sec, {"action": "x"}, "push")
        r = h.handle_webhook(hdr, body)
        assert r.metadata == {"event": "unknown", "ignored": True}

    def test_routed(self):
        """Known events are routed to the sync client with _event/_action keys."""
        h, sec = _handler("github")
        hdr, body = _signed(sec, {"action": "opened", "issue": {"number": 4}}, "issues")
        h.handle_webhook(hdr, body)
        routed = h.sync_client.handle_webhook.call_args.args[0]
        assert routed["_event"] == "issues" and routed["_action"] == "opened"
        assert routed["_resource_type"] == "issue"

    def test_routed_event_fallbacks(self):
        """_event falls back to webhookEvent then the event enum value."""
        h, _ = _handler("jira", None)
        h.handle_webhook(
            {}, json.dumps({"webhookEvent": "jira:issue_created"}).encode()
        )
        routed = h.sync_client.handle_webhook.call_args.args[0]
        assert routed["_event"] == "jira:issue_created"
        h, _ = _handler("openproject", None)
        h.handle_webhook({}, json.dumps({"action": "created"}).encode())
        routed = h.sync_client.handle_webhook.call_args.args[0]
        assert routed["_event"] == "issue_created"

    def test_payload_default_timestamp(self):
        """WebhookPayload stamps a timestamp when none is provided."""
        p = WebhookPayload(WebhookEvent.PING, "g", ResourceType.ISSUE, "1", "ping", {})
        assert p.timestamp is not None


class TestParsers:
    """Tests for the per-platform parsers."""

    @pytest.mark.parametrize(
        ("event", "action", "expect"),
        [
            ("ping", None, WebhookEvent.PING),
            ("issues", "opened", WebhookEvent.ISSUE_CREATED),
            ("issues", "labeled", WebhookEvent.ISSUE_UPDATED),
            ("issues", "closed", WebhookEvent.ISSUE_CLOSED),
            ("issues", "reopened", WebhookEvent.ISSUE_REOPENED),
            ("issues", "deleted", WebhookEvent.ISSUE_DELETED),
            ("issues", "zzz", WebhookEvent.UNKNOWN),
            ("milestone", "created", WebhookEvent.MILESTONE_CREATED),
            ("milestone", "edited", WebhookEvent.MILESTONE_UPDATED),
            ("milestone", "closed", WebhookEvent.MILESTONE_CLOSED),
            ("milestone", "deleted", WebhookEvent.MILESTONE_DELETED),
            ("milestone", "zzz", WebhookEvent.UNKNOWN),
            ("label", "created", WebhookEvent.LABEL_CREATED),
            ("label", "edited", WebhookEvent.LABEL_UPDATED),
            ("label", "deleted", WebhookEvent.LABEL_DELETED),
            ("label", "zzz", WebhookEvent.UNKNOWN),
            ("push", "x", WebhookEvent.UNKNOWN),
        ],
    )
    def test_github(self, event, action, expect):
        """GitHub events map to WebhookEvent values."""
        h, _ = _handler("github")
        data = {"action": action} if action else {}
        p = h._parse_github_webhook({"x-github-event": event}, data)
        assert p.event_type == expect

    @pytest.mark.parametrize(
        ("action", "expect"),
        [
            ("open", WebhookEvent.ISSUE_CREATED),
            ("update", WebhookEvent.ISSUE_UPDATED),
            ("close", WebhookEvent.ISSUE_CLOSED),
            ("reopen", WebhookEvent.ISSUE_REOPENED),
            ("zzz", WebhookEvent.UNKNOWN),
        ],
    )
    def test_gitlab(self, action, expect):
        """GitLab issue actions map; other kinds are unknown."""
        h, _ = _handler("gitlab")
        d = {"object_kind": "issue", "object_attributes": {"action": action, "id": 3}}
        assert h._parse_gitlab_webhook({}, d).event_type == expect
        assert h._parse_gitlab_webhook({}, {}).event_type == WebhookEvent.UNKNOWN

    @pytest.mark.parametrize(
        ("ev", "expect", "rtype"),
        [
            ("jira:issue_created", WebhookEvent.ISSUE_CREATED, ResourceType.ISSUE),
            ("jira:issue_updated", WebhookEvent.ISSUE_UPDATED, ResourceType.ISSUE),
            ("jira:issue_deleted", WebhookEvent.ISSUE_DELETED, ResourceType.ISSUE),
            ("jira:issue_zzz", WebhookEvent.UNKNOWN, ResourceType.ISSUE),
            (
                "jira:version_created",
                WebhookEvent.MILESTONE_CREATED,
                ResourceType.MILESTONE,
            ),
            (
                "jira:version_released",
                WebhookEvent.MILESTONE_UPDATED,
                ResourceType.MILESTONE,
            ),
            (
                "jira:version_deleted",
                WebhookEvent.MILESTONE_DELETED,
                ResourceType.MILESTONE,
            ),
            ("jira:version_zzz", WebhookEvent.UNKNOWN, ResourceType.MILESTONE),
            ("project_created", WebhookEvent.PROJECT_CREATED, ResourceType.PROJECT),
            ("project_archived", WebhookEvent.PROJECT_UPDATED, ResourceType.PROJECT),
            ("project_deleted", WebhookEvent.PROJECT_DELETED, ResourceType.PROJECT),
            ("project_zzz", WebhookEvent.UNKNOWN, ResourceType.PROJECT),
            ("other", WebhookEvent.UNKNOWN, ResourceType.ISSUE),
        ],
    )
    def test_jira(self, ev, expect, rtype):
        """Jira events map to events and resource types."""
        h, _ = _handler("jira")
        p = h._parse_jira_webhook({}, {"webhookEvent": ev, "project": {"key": "K"}})
        assert p.event_type == expect and p.resource_type == rtype

    def test_jira_missing_event(self):
        """A payload without webhookEvent is unknown."""
        h, _ = _handler("jira")
        assert h._parse_jira_webhook({}, {}).event_type == WebhookEvent.UNKNOWN

    @pytest.mark.parametrize(
        ("typ", "expect"),
        [
            ("createCard", WebhookEvent.ISSUE_CREATED),
            ("updateCard", WebhookEvent.ISSUE_UPDATED),
            ("deleteCard", WebhookEvent.ISSUE_DELETED),
            ("zzz", WebhookEvent.UNKNOWN),
        ],
    )
    def test_trello(self, typ, expect):
        """Trello card actions map to issue events."""
        h, _ = _handler("trello")
        d = {"action": {"type": typ, "data": {"card": {"id": "c1"}}}}
        p = h._parse_trello_webhook({}, d)
        assert p.event_type == expect and p.resource_id == "c1"

    @pytest.mark.parametrize(
        ("act", "expect"),
        [
            ("created", WebhookEvent.ISSUE_CREATED),
            ("updated", WebhookEvent.ISSUE_UPDATED),
            ("deleted", WebhookEvent.ISSUE_DELETED),
            ("zzz", WebhookEvent.UNKNOWN),
        ],
    )
    def test_openproject(self, act, expect):
        """OpenProject actions map to issue events."""
        h, _ = _handler("openproject")
        p = h._parse_openproject_webhook({}, {"action": act, "work_package": {"id": 9}})
        assert p.event_type == expect and p.resource_id == "9"

    def test_get_header_default(self):
        """_get_header returns the default when a header is absent."""
        assert WebhookHandler._get_header({"a": "b"}, "x", "d") == "d"


def _db_with(rows=None):
    """Build a mock DB whose select().first()/iteration returns rows."""
    db = MagicMock()
    sel = db.return_value.select
    sel.return_value.first.return_value = rows[0] if rows else None
    sel.return_value.__iter__.side_effect = lambda: iter(rows or [])
    return db


@pytest.fixture
def sched():
    """Return a BatchSyncScheduler with a mock DB and a mock scheduler."""
    clients = {"github": MagicMock()}
    s = BatchSyncScheduler(_db_with(), clients, MagicMock())
    s.scheduler = MagicMock()
    return s


class TestScheduler:
    """Tests for BatchSyncScheduler."""

    def test_add_get_list_remove(self, sched):
        """Jobs can be added, inspected, listed and removed."""
        with (
            patch.object(bs.settings, "sync_batch_fallback_enabled", True),
            patch.object(bs.settings, "sync_batch_interval", 60),
        ):
            jid = sched.add_batch_job("github", 5, [ResourceType.ISSUE])
        assert jid == "github_5_batch" and sched.jobs[jid].interval_seconds == 60
        sched.scheduler.add_job.assert_called_once()
        sched.jobs[jid].last_run = NOW
        sched.scheduler.get_job.return_value = SimpleNamespace(next_run_time=NOW)
        st = sched.get_job_status(jid)
        assert st["last_run"] == NOW.isoformat() and st["next_run"] == NOW.isoformat()
        assert len(sched.list_jobs()) == 1
        sched.scheduler.get_job.return_value = None
        sched.jobs[jid].last_run = None
        st = sched.get_job_status(jid)
        assert st["next_run"] is None and st["last_run"] is None
        assert sched.get_job_status("nope") is None
        sched.remove_batch_job(jid)
        assert jid not in sched.jobs
        sched.remove_batch_job("nope")

    def test_add_disabled_and_remove_error(self, sched):
        """Disabled fallback skips scheduling; remove tolerates scheduler errors."""
        with patch.object(bs.settings, "sync_batch_fallback_enabled", False):
            jid = sched.add_batch_job("github", 6, [], interval_seconds=30)
        sched.scheduler.add_job.assert_not_called()
        sched.scheduler.remove_job.side_effect = RuntimeError("x")
        sched.remove_batch_job(jid)
        assert jid not in sched.jobs

    def test_start_stop(self, sched):
        """start loads configs and optionally schedules the health monitor."""
        cfg = SimpleNamespace(platform="github", id=2, sync_interval=10)
        sched.db = _db_with([cfg])
        with patch.object(bs.settings, "sync_batch_fallback_enabled", True):
            sched.start()
        assert "github_2_batch" in sched.jobs
        assert sched.scheduler.add_job.call_count == 2
        sched.scheduler.start.assert_called_once()
        sched.stop()
        sched.scheduler.shutdown.assert_called_once_with(wait=True)
        sched.scheduler.reset_mock()
        with patch.object(bs.settings, "sync_batch_fallback_enabled", False):
            sched.start()
        sched.scheduler.add_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_batch_sync(self, sched):
        """A batch run syncs each type, counting failures from exceptions."""
        job = BatchSyncJob(
            "j", "github", 1, [ResourceType.ISSUE, ResourceType.LABEL], 10
        )
        sched.db = _db_with([SimpleNamespace(last_sync_at=NOW)])
        ok = SimpleNamespace(items_synced=3, items_failed=1)
        sched.sync_clients["github"].batch_sync.side_effect = [ok, RuntimeError("boom")]
        await sched._run_batch_sync(job)
        kw = sched.db.sync_history.insert.call_args.kwargs
        assert kw["items_synced"] == 3 and kw["items_failed"] == 2
        assert kw["success"] is False

    @pytest.mark.asyncio
    async def test_run_batch_sync_no_row_and_no_client(self, sched):
        """A missing config row means since=None; a missing client aborts early."""
        job = BatchSyncJob("j", "github", 1, [ResourceType.ISSUE], 10)
        sched.sync_clients["github"].batch_sync.return_value = SimpleNamespace(
            items_synced=0, items_failed=0
        )
        await sched._run_batch_sync(job)
        assert sched.sync_clients["github"].batch_sync.call_args.kwargs["since"] is None
        assert sched.db.sync_history.insert.call_args.kwargs["success"] is True
        sched.db.sync_history.insert.reset_mock()
        await sched._run_batch_sync(BatchSyncJob("k", "other", 1, [], 1))
        sched.db.sync_history.insert.assert_not_called()

    @pytest.mark.asyncio
    async def test_monitor_webhook_health(self, sched):
        """Repeated webhook failures trigger a fallback batch for enabled jobs."""
        fails = [SimpleNamespace(sync_config_id=1)] * 3 + [
            SimpleNamespace(sync_config_id=2)
        ]
        sched.db = _db_with(fails)

        class _Col:
            """Column stand-in supporting comparison with datetimes."""

            def __gt__(self, other):
                """Return a truthy comparison result."""
                return True

        sched.db.sync_history.started_at = _Col()
        sched.jobs = {
            "a": BatchSyncJob("a", "github", 1, [], 1, enabled=True),
            "b": BatchSyncJob("b", "github", 1, [], 1, enabled=False),
            "c": BatchSyncJob("c", "github", 2, [], 1, enabled=True),
        }
        with patch.object(sched, "_run_batch_sync", new=AsyncMock()) as run:
            await sched._monitor_webhook_health()
        run.assert_awaited_once_with(sched.jobs["a"])
