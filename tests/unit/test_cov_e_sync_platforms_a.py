"""Unit tests for the Jira, Trello, OpenProject and GitLab sync clients.

The HTTP client and the DAL are the true external boundaries: the HTTP
client is replaced with a recording mock and the DAL with a small in-memory
fake that records inserts/updates and serves scripted rows per table.
"""

from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

import tests.unit.conftest_worker_stubs  # noqa: F401
from apps.worker.sync.base import (
    ResourceType as RT,
)
from apps.worker.sync.base import (
    SyncDirection as D,
)
from apps.worker.sync.base import (
    SyncMapping,
    SyncOperation,
    SyncStatus,
)
from apps.worker.sync.platforms import jira_client as jc
from apps.worker.sync.platforms.gitlab_client import GitLabSyncClient
from apps.worker.sync.platforms.jira_client import JiraSyncClient
from apps.worker.sync.platforms.openproject_client import OpenProjectSyncClient
from apps.worker.sync.platforms.trello_client import TrelloSyncClient


def fake_secret() -> str:
    """Build a credential-looking value at runtime so no literal secret is committed."""
    return "-".join(("cove", uuid4().hex[:8]))


class Expr:
    """Query expression stand-in that only remembers its first table."""

    def __init__(self, table):
        """Record the table the expression was built from."""
        self.table = table

    def __and__(self, other):
        """Combine with another expression, keeping the left table."""
        return Expr(self.table)

    __or__ = __and__


class Col:
    """Column stand-in supporting the comparison operators the client uses."""

    def __init__(self, table):
        """Remember the owning table name."""
        self.table = table

    def __eq__(self, other):
        """Equality yields a query expression."""
        return Expr(self.table)

    def __gt__(self, other):
        """Greater-than yields a query expression."""
        return Expr(self.table)

    __hash__ = object.__hash__


class Rows(list):
    """Result list offering the ``first()`` helper of a DAL select."""

    def first(self):
        """Return the first row or None."""
        return self[0] if self else None


class FakeSet:
    """The object returned by calling the DAL with a query."""

    def __init__(self, db, table):
        """Bind to the DAL and table."""
        self.db, self.table = db, table

    def select(self, *args):
        """Return scripted rows for the table."""
        return Rows(self.db.rows.get(self.table, []))

    def update(self, **kw):
        """Record an update call."""
        self.db.updates.append((self.table, kw))


class FakeTable:
    """Table stand-in: attribute access yields columns, insert records rows."""

    def __init__(self, db, name):
        """Bind to the DAL and table name."""
        self._db, self._name = db, name

    def __getattr__(self, attr):
        """Any column name yields a Col."""
        return Col(self._name)

    def insert(self, **kw):
        """Record an insert and return an incrementing id."""
        self._db.inserted.append((self._name, kw))
        self._db.next_id += 1
        return self._db.next_id


class FakeDB:
    """In-memory DAL stand-in with scripted rows, recorded writes."""

    def __init__(self):
        """Start empty."""
        self.rows, self.inserted, self.updates = {}, [], []
        self.next_id, self.commits = 100, 0

    def __getattr__(self, name):
        """Tables are created on demand."""
        return FakeTable(self, name)

    def __call__(self, expr):
        """Build a set for the expression's table."""
        return FakeSet(self, expr.table)

    def commit(self):
        """Count commits."""
        self.commits += 1


def resp(status=200, body=None, text="err"):
    """Build a fake HTTP response."""
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body
    r.text = text
    return r


def op(
    kind="create",
    direction=D.EXTERNAL_TO_ELDER,
    rt=RT.ISSUE,
    elder=None,
    ext=None,
    mapping=None,
    meta=None,
):
    """Build a SyncOperation with sensible defaults."""
    return SyncOperation(
        operation_type=kind,
        resource_type=rt,
        direction=direction,
        elder_data=elder,
        external_data=ext,
        mapping=mapping,
        metadata=meta or {},
    )


def mp(elder_id=1, ext="J-1", rt=RT.ISSUE):
    """Build a SyncMapping."""
    return SyncMapping(rt.value, elder_id, "jira", ext, 1)


def make(**cfg):
    """Build a JiraSyncClient with fake DB, mock HTTP and in-memory mappings."""
    base = {
        "jira_url": "https://acme.example.com/",
        "email": "u@example.com",
        "api_token": fake_secret(),
        "project_key": "PRJ",
        "elder_organization_id": 5,
    }
    base.update(cfg)
    db = FakeDB()
    db.rows["organizations"] = [SimpleNamespace(id=5, tenant_id=2)]
    c = JiraSyncClient(base, db, 1, MagicMock())
    c.client = MagicMock()
    c.maps = []

    def get(rt, elder_id=None, external_id=None):
        """Look up an in-memory mapping."""
        for m in c.maps:
            if m.elder_type != rt.value:
                continue
            if elder_id is not None and m.elder_id != elder_id:
                continue
            if external_id is not None and m.external_id != external_id:
                continue
            return m
        return None

    def create(mapping, sync_method="webhook"):
        """Store a new mapping."""
        c.maps.append(mapping)
        return len(c.maps)

    c.get_mapping = get
    c.create_mapping = create
    c.update_mapping = MagicMock()
    return c


# ---------------------------------------------------------------------------
# Jira pure helpers
# ---------------------------------------------------------------------------


class TestJiraHelpers:
    """Module-level conversion helpers."""

    @pytest.mark.parametrize(
        ("raw", "out"),
        [
            (None, ""),
            ("", ""),
            ("https://a.example.com/x", "a.example.com"),
            ("http://a.example.com", "a.example.com"),
            ("a.example.com:8080/p", "a.example.com:8080"),
            ("/", ""),
        ],
    )
    def test_normalize_host(self, raw, out):
        """Scheme, slashes and paths are stripped."""
        assert jc.normalize_jira_host(raw) == out

    def test_text_to_adf(self):
        """Blank text yields an empty doc; lines become paragraphs."""
        assert jc.text_to_adf(None)["content"] == []
        doc = jc.text_to_adf("a\n\nb")
        assert [len(p["content"]) for p in doc["content"]] == [1, 0, 1]

    def test_adf_to_text_inputs(self):
        """None, str, empty dict and non-dict inputs are handled."""
        assert jc.adf_to_text(None) == ""
        assert jc.adf_to_text("x") == "x"
        assert jc.adf_to_text({"type": "doc"}) == ""
        assert jc.adf_to_text(5) == "5"

    def test_adf_round_trip_and_node_types(self):
        """All node types render sensibly."""
        t = lambda s: {"type": "text", "text": s}  # noqa: E731
        doc = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [t("a"), {"type": "hardBreak"}, t("b")],
                },
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "mention", "attrs": {"text": "@x"}},
                        {"type": "emoji", "attrs": None},
                        {
                            "type": "inlineCard",
                            "attrs": {"url": "http://u.example.com"},
                        },
                    ],
                },
                {
                    "type": "bulletList",
                    "content": [{"type": "listItem", "content": [t("li")]}],
                },
                {
                    "type": "table",
                    "content": [
                        {"type": "tableRow", "content": [t("c1"), t("c2"), t("")]}
                    ],
                },
                {"type": "panel", "content": [t("pp")]},
                {"type": "heading"},
                "bare",
                7,
                None,
            ],
        }
        out = jc.adf_to_text(doc)
        assert "a\nb" in out and "@x" in out and "c1 | c2" in out
        assert "http://u.example.com" in out and "pp" in out and "bare" in out

    def test_parse_datetime(self):
        """All accepted shapes parse to UTC; junk is None."""
        assert jc._parse_datetime(None) is None
        naive = datetime(2026, 1, 2, 3, 4)
        assert jc._parse_datetime(naive).tzinfo is UTC
        aware = datetime(2026, 1, 2, tzinfo=UTC)
        assert jc._parse_datetime(aware) == aware
        assert jc._parse_datetime(date(2026, 1, 2)).day == 2
        p = jc._parse_datetime("2026-11-20T10:15:00.000+0100")
        assert p.hour == 9
        assert jc._parse_datetime("2026-11-20T10:15:00Z").hour == 10
        assert jc._parse_datetime("2026-11-20").tzinfo is UTC
        assert jc._parse_datetime("garbage-x") is None
        assert jc._parse_datetime("   ") is None
        assert jc._parse_datetime(12) is None

    def test_parse_date_and_format(self):
        """Date parsing falls back to the ISO prefix."""
        assert jc._parse_date(None) is None
        assert jc._parse_date(datetime(2026, 1, 2)) == date(2026, 1, 2)
        assert jc._parse_date(date(2026, 1, 2)) == date(2026, 1, 2)
        assert jc._parse_date("2026-01-02T00:00:00Z") == date(2026, 1, 2)
        assert jc._parse_date("2026-01-02 garbage") == date(2026, 1, 2)
        assert jc._parse_date("junkjunkjunk") is None
        assert jc._parse_date("  ") is None
        assert jc._parse_date(3) is None
        assert jc._format_iso_date("2026-01-02") == "2026-01-02"
        assert jc._format_iso_date(None) is None


# ---------------------------------------------------------------------------
# Jira construction / connection / helpers
# ---------------------------------------------------------------------------


class TestJiraSetup:
    """Constructor, validation and connectivity."""

    def test_init_options(self):
        """Config normalisation including bad label mode and empty URL."""
        c = make(
            label_mode="bogus", sync_all_projects=True, project_lead_account_id=" acct "
        )
        assert c.label_mode == "labels" and c.sync_all_projects
        assert c.project_lead_account_id == "acct"
        assert make(label_mode="Components").label_mode == "components"
        empty = make(jira_url="")
        assert empty.base_url == ""
        assert empty.validate_config() is False
        assert make().validate_config() is True
        c.close()

    def test_connection_missing_config(self):
        """Missing config short-circuits without HTTP."""
        c = make(api_token="")
        d = c.test_connection_detail()
        assert d["connected"] is False and "Missing" in d["error"]
        assert c.test_connection() is False

    def test_connection_ok(self):
        """A 200 returns project details."""
        c = make()
        c.client.get.return_value = resp(200, {"id": "1", "key": "PRJ", "name": "n"})
        d = c.test_connection_detail()
        assert d["connected"] and d["project"]["key"] == "PRJ"
        assert c.test_connection() is True

    @pytest.mark.parametrize(
        "body",
        [
            {"errorMessages": ["nope", "bad"]},
            {"errorMessages": []},
            ["x"],
            "boom",
        ],
    )
    def test_connection_http_error(self, body):
        """Non-200 responses report status and any messages."""
        c = make()
        r = resp(401, body)
        if body == "boom":
            r.json.side_effect = ValueError("no json")
        c.client.get.return_value = r
        d = c.test_connection_detail()
        assert d["status_code"] == 401 and d["connected"] is False
        if isinstance(body, dict) and body["errorMessages"]:
            assert "nope; bad" in d["error"]

    def test_connection_exception(self):
        """Transport errors are captured."""
        c = make()
        c.client.get.side_effect = RuntimeError("down")
        assert c.test_connection_detail()["error"] == "down"


class TestJiraInternals:
    """Org resolution, mapping upsert and field mapping helpers."""

    def test_resolve_org_paths(self):
        """Config id, row fallback, missing, invalid and not-found."""
        c = make()
        assert c._resolve_org_and_tenant() == (5, 2, None)
        c = make(elder_organization_id=None)
        assert c._resolve_org_and_tenant()[2].startswith("No Elder")
        c.db.rows["sync_configs"] = [
            SimpleNamespace(config_json={"elder_organization_id": 5})
        ]
        assert c._resolve_org_and_tenant()[0] == 5
        c.db.rows["sync_configs"] = [SimpleNamespace(config_json="notdict")]
        assert c._resolve_org_and_tenant()[0] is None
        assert (
            make(elder_organization_id="x")
            ._resolve_org_and_tenant()[2]
            .startswith("Invalid")
        )
        c = make(elder_organization_id=9)
        c.db.rows["organizations"] = []
        assert "not found" in c._resolve_org_and_tenant()[2]

    def test_should_allow_create(self):
        """Metadata, config row and config fallbacks."""
        c = make(two_way_create=True)
        assert c._should_allow_create(op(meta={"allow_create": True})) is True
        assert c._should_allow_create(op()) is True
        c.db.rows["sync_configs"] = [SimpleNamespace(two_way_create=False)]
        assert c._should_allow_create(op()) is False

    def test_upsert_mapping_paths(self):
        """Existing, found-by-elder, found-by-external and brand-new."""
        c = make()
        new = c._upsert_mapping(RT.ISSUE, 1, "J-1")
        assert new in c.maps
        again = c._upsert_mapping(RT.ISSUE, 1, "J-1")
        assert again is new
        c.update_mapping.assert_called_once()
        ex = mp(7, "J-7")
        assert c._upsert_mapping(RT.ISSUE, 7, "J-7", existing=ex) is ex

    @pytest.mark.parametrize(
        ("obj", "out"),
        [
            (None, "OPEN"),
            ({"statusCategory": {"key": "done"}}, "CLOSED"),
            ({"statusCategory": {"key": "indeterminate"}}, "IN_PROGRESS"),
            ({"statusCategory": {"key": "new"}}, "OPEN"),
            ({"name": "Resolved"}, "CLOSED"),
            ({"name": "In Review"}, "IN_PROGRESS"),
            ({"name": "Backlog"}, "OPEN"),
            ({}, "OPEN"),
        ],
    )
    def test_map_status(self, obj, out):
        """Status category then name heuristics."""
        assert make()._map_jira_status(obj) == out

    @pytest.mark.parametrize(
        ("obj", "out"),
        [
            (None, "MEDIUM"),
            ({"name": "Blocker"}, "CRITICAL"),
            ({"name": "High"}, "HIGH"),
            ({"name": "Trivial"}, "LOW"),
            ({"name": "Other"}, "MEDIUM"),
            ({}, "MEDIUM"),
        ],
    )
    def test_map_priority(self, obj, out):
        """Priority names map onto Elder levels."""
        assert make()._map_jira_priority(obj) == out

    @pytest.mark.parametrize(
        ("name", "out"),
        [
            ("Bug", "BUG"),
            ("Epic", "FEATURE"),
            ("Security", "SECURITY"),
            ("Task", "OPERATIONS"),
            ("Support", "SUPPORT"),
            ("Config", "CONFIG"),
            ("Architecture", "ARCHITECTURE"),
            ("Process", "PROCESS"),
            ("Approval", "APPROVAL"),
            ("Code", "CODE"),
            ("Zzz", "OTHER"),
        ],
    )
    def test_map_issue_type(self, name, out):
        """Issue type names map to Elder types."""
        c = make()
        assert c._map_jira_issue_type({"name": name}) == out
        assert c._map_jira_issue_type(None) == "OTHER"

    def test_map_elder_type(self):
        """Elder types map to Jira names with Task default."""
        c = make()
        assert c._map_elder_type_to_jira("bug") == "Bug"
        assert c._map_elder_type_to_jira(None) == "Task"
        assert c._map_elder_type_to_jira("feature") == "Story"

    def test_sync_issue_labels(self):
        """Empty, blank, new, existing label and existing assignment."""
        c = make()
        c._sync_issue_labels(1, [])
        assert c.db.inserted == []
        c._sync_issue_labels(1, [{"name": "a"}, "b", " ", {"name": ""}])
        tables = [t for t, _ in c.db.inserted]
        assert tables.count("issue_labels") == 2
        assert tables.count("issue_label_assignments") == 2
        c2 = make()
        c2.db.rows["issue_labels"] = [SimpleNamespace(id=3)]
        c2.db.rows["issue_label_assignments"] = [SimpleNamespace(id=1)]
        c2._sync_issue_labels(1, ["x"])
        assert c2.db.inserted == []

    def test_sync_issue_versions(self):
        """Invalid versions skipped; link created unless it exists."""
        c = make()
        c._sync_issue_versions(1, [], 5, 2)
        c._sync_issue_versions(
            1, ["str", {"name": "no id"}, {"id": 3, "name": "v"}], 5, 2
        )
        assert [t for t, _ in c.db.inserted].count("issue_milestone_links") == 1
        c.db.inserted.clear()
        c.db.rows["issue_milestone_links"] = [SimpleNamespace(id=1)]
        c._sync_issue_versions(1, [{"id": 3, "name": "v"}], 5, 2)
        assert "issue_milestone_links" not in [t for t, _ in c.db.inserted]


class TestJiraTransition:
    """Workflow transition selection."""

    def transitions(self):
        """Return a canned transitions payload."""
        return {
            "transitions": [
                {
                    "id": 1,
                    "name": "Start",
                    "to": {
                        "name": "In Progress",
                        "statusCategory": {"key": "indeterminate"},
                    },
                },
                {
                    "id": 2,
                    "name": "Finish",
                    "to": {"name": "Done", "statusCategory": {"key": "done"}},
                },
                {"id": 3, "name": "Reopen", "to": None},
            ]
        }

    @pytest.mark.parametrize(
        ("status", "chosen"),
        [
            ("CLOSED", "2"),
            ("RESOLVED", "2"),
            ("IN_PROGRESS", "1"),
        ],
    )
    def test_category_match(self, status, chosen):
        """Transitions are chosen by status category."""
        c = make()
        c.client.get.return_value = resp(200, self.transitions())
        c.client.post.return_value = resp(204)
        c._transition_jira_issue("PRJ-1", status)
        assert c.client.post.call_args.kwargs["json"]["transition"]["id"] == chosen

    def test_fallback_by_name(self):
        """With no category match a name match is used."""
        c = make()
        c.client.get.return_value = resp(200, self.transitions())
        c.client.post.return_value = resp(204)
        c._transition_jira_issue("PRJ-1", "OPEN")
        assert c.client.post.call_args.kwargs["json"]["transition"]["id"] == "3"

    def test_no_transition_available(self):
        """No match means no POST."""
        c = make()
        c.client.get.return_value = resp(
            200, {"transitions": [{"id": 9, "name": "zzz", "to": {"name": "zzz"}}]}
        )
        c._transition_jira_issue("PRJ-1", "OPEN")
        c.client.post.assert_not_called()

    def test_fetch_and_apply_failures(self):
        """HTTP failures are logged, not raised."""
        c = make()
        c.client.get.return_value = resp(500)
        c._transition_jira_issue("PRJ-1", "CLOSED")
        c.logger.warning.assert_called()
        c.client.get.return_value = resp(200, self.transitions())
        c.client.post.return_value = resp(400)
        c._transition_jira_issue("PRJ-1", "CLOSED")
        assert c.logger.warning.call_count == 2


# ---------------------------------------------------------------------------
# Jira issues
# ---------------------------------------------------------------------------


class TestJiraIssuesPush:
    """Elder to Jira issue sync."""

    def test_dispatch_exception(self):
        """Unexpected errors become FAILED results."""
        c = make()
        c._sync_issue_to_jira = MagicMock(side_effect=RuntimeError("x"))
        r = c.sync_issue(op(direction=D.ELDER_TO_EXTERNAL))
        assert r.status == SyncStatus.FAILED and r.errors == ["x"]

    def test_dispatch_directions(self):
        """Each direction routes to its handler."""
        c = make()
        c._sync_issue_from_jira = MagicMock(return_value="from")
        c._sync_issue_bidirectional = MagicMock(return_value="bi")
        assert c.sync_issue(op(direction=D.EXTERNAL_TO_ELDER)) == "from"
        assert c.sync_issue(op(direction=D.BIDIRECTIONAL)) == "bi"

    def issue(self, **kw):
        """Build an Elder issue payload."""
        d = {
            "id": 1,
            "title": "t",
            "description": "d",
            "priority": "urgent",
            "issue_type": "bug",
            "status": "IN_PROGRESS",
            "labels": [{"name": "a"}, "b", {"name": ""}, ""],
            "milestone_id": 4,
            "updated_at": "2026-01-02T00:00:00Z",
        }
        d.update(kw)
        return d

    def test_create_success_with_transition(self):
        """Create posts, transitions and stores a mapping."""
        c = make()
        c.maps.append(mp(4, "V1", RT.MILESTONE))
        c.client.post.return_value = resp(201, {"key": "PRJ-9"})
        c._transition_jira_issue = MagicMock()
        r = c._sync_issue_to_jira(op("create", D.ELDER_TO_EXTERNAL, elder=self.issue()))
        assert r.status == SyncStatus.SUCCESS and r.created_mappings
        c._transition_jira_issue.assert_called_once_with("PRJ-9", "IN_PROGRESS")
        sent = c.client.post.call_args.kwargs["json"]["fields"]
        assert sent["fixVersions"] == [{"id": "V1"}] and sent["labels"] == ["a", "b"]

    def test_create_success_open_no_transition(self):
        """An OPEN issue is created without transition; unknown priority maps to Medium."""
        c = make()
        c.client.post.return_value = resp(200, {"key": "PRJ-9"})
        c._transition_jira_issue = MagicMock()
        e = self.issue(status="OPEN", priority="weird", labels=None, milestone_id=None)
        r = c._sync_issue_to_jira(op("create", D.ELDER_TO_EXTERNAL, elder=e))
        assert r.is_success
        c._transition_jira_issue.assert_not_called()
        assert c.client.post.call_args.kwargs["json"]["fields"]["priority"] == {
            "name": "Medium"
        }

    def test_create_failure(self):
        """A rejected create reports the API error."""
        c = make()
        c.client.post.return_value = resp(400, text="nope")
        r = c._sync_issue_to_jira(op("create", D.ELDER_TO_EXTERNAL, elder=self.issue()))
        assert r.status == SyncStatus.FAILED and "400" in r.errors[0]

    def test_update_without_mapping_fails(self):
        """Update with no mapping (and no elder id) fails."""
        c = make()
        r = c._sync_issue_to_jira(op("update", D.ELDER_TO_EXTERNAL, elder={}))
        assert "No mapping" in r.errors[0]

    def test_update_failure_and_success(self):
        """PUT failure fails; success transitions and updates mapping."""
        c = make()
        c.maps.append(mp(1, "PRJ-1"))
        c.client.put.return_value = resp(500, text="x")
        o = op("update", D.ELDER_TO_EXTERNAL, elder=self.issue())
        assert c._sync_issue_to_jira(o).status == SyncStatus.FAILED
        c.client.put.return_value = resp(204)
        c._transition_jira_issue = MagicMock()
        r = c._sync_issue_to_jira(o)
        assert r.updated_mappings and c._transition_jira_issue.called
        c._transition_jira_issue.reset_mock()
        r = c._sync_issue_to_jira(
            op("update", D.ELDER_TO_EXTERNAL, elder=self.issue(status=None))
        )
        assert r.is_success and not c._transition_jira_issue.called

    def test_create_with_existing_mapping_updates(self):
        """A create op that already has a mapping goes down the update path."""
        c = make()
        c.client.put.return_value = resp(200)
        c._transition_jira_issue = MagicMock()
        r = c._sync_issue_to_jira(
            op(
                "create",
                D.ELDER_TO_EXTERNAL,
                elder=self.issue(),
                mapping=mp(1, "PRJ-1"),
            )
        )
        assert r.updated_mappings
        c.client.put.assert_called_once()


class TestJiraIssuesPull:
    """Jira to Elder issue sync."""

    def payload(self, **f):
        """Build a Jira issue payload."""
        fields = {
            "summary": "S",
            "description": "plain",
            "status": {"statusCategory": {"key": "done"}},
            "priority": {"name": "High"},
            "issuetype": {"name": "Bug"},
            "updated": "2026-01-02T00:00:00.000+0000",
            "duedate": "2026-02-01",
            "labels": ["x"],
            "fixVersions": [{"id": 3, "name": "v1"}],
        }
        fields.update(f)
        return {"key": "PRJ-1", "fields": fields}

    def test_missing_key(self):
        """A payload without a key fails."""
        r = make()._sync_issue_from_jira(op(ext={}))
        assert "missing 'key'" in r.errors[0]

    def test_org_error(self):
        """No org mapping fails the pull."""
        c = make(elder_organization_id=None)
        r = c._sync_issue_from_jira(op(ext=self.payload()))
        assert r.status == SyncStatus.FAILED

    def test_create_allowed_and_disabled(self):
        """Create inserts when allowed, is skipped otherwise."""
        c = make()
        r = c._sync_issue_from_jira(op(ext=self.payload(), meta={"allow_create": True}))
        assert r.created_mappings and any(t == "issues" for t, _ in c.db.inserted)
        c2 = make()
        r = c2._sync_issue_from_jira(op(ext=self.payload()))
        assert r.metadata["skipped"] is True

    def test_create_open_issue_and_no_summary(self):
        """Open status leaves closed_at empty; missing summary falls back to key."""
        c = make()
        p = self.payload(
            status={"name": "Open"}, summary=None, labels=None, fixVersions=None
        )
        r = c._sync_issue_from_jira(
            op(ext=p, meta={"allow_create": True, "sync_method": "batch"})
        )
        _, row = [i for i in c.db.inserted if i[0] == "issues"][0]
        assert row["closed_at"] is None and row["title"] == "PRJ-1" and r.is_success

    def test_update_with_mapping(self):
        """An existing mapping updates the issue row."""
        c = make()
        c.maps.append(mp(1, "PRJ-1"))
        r = c._sync_issue_from_jira(op("update", ext=self.payload()))
        assert r.updated_mappings and c.db.updates[0][0] == "issues"

    def test_update_without_mapping(self):
        """No mapping: fail when creation disabled, create when allowed."""
        c = make()
        r = c._sync_issue_from_jira(op("update", ext=self.payload()))
        assert "No mapping" in r.errors[0]
        r = c._sync_issue_from_jira(
            op("update", ext=self.payload(), meta={"allow_create": True})
        )
        assert r.created_mappings


class TestJiraBidirectional:
    """Conflict-aware bidirectional issue sync."""

    def setup_method(self):
        """Create a client with a mocked conflict resolver and handlers."""
        self.c = make()
        self.c.conflict_resolver = MagicMock()
        self.c._sync_issue_to_jira = MagicMock(return_value="to")
        self.c._sync_issue_from_jira = MagicMock(return_value="from")

    def test_no_conflict(self):
        """Without a conflict Jira wins by pulling."""
        self.c.conflict_resolver.detect_conflict.return_value = None
        assert self.c._sync_issue_bidirectional(op(direction=D.BIDIRECTIONAL)) == "from"

    def test_unresolved_conflict(self):
        """A manual conflict yields CONFLICT status."""
        cr = self.c.conflict_resolver
        cr.detect_conflict.return_value = object()
        cr.resolve_conflict.return_value = SimpleNamespace(resolved=False)
        cr.get_conflict_summary.return_value = {"s": 1}
        r = self.c._sync_issue_bidirectional(op(direction=D.BIDIRECTIONAL))
        assert r.status == SyncStatus.CONFLICT and r.conflicts == [{"s": 1}]

    def test_resolved_elder_wins_and_other(self):
        """Elder-wins pushes; otherwise Jira data is pulled."""
        cr = self.c.conflict_resolver
        cr.detect_conflict.return_value = object()
        cr.resolve_conflict.return_value = SimpleNamespace(
            resolved=True, resolution_data={"a": 1}
        )
        assert (
            self.c._sync_issue_bidirectional(
                op(direction=D.BIDIRECTIONAL, elder={"a": 1})
            )
            == "to"
        )
        assert (
            self.c._sync_issue_bidirectional(
                op(direction=D.BIDIRECTIONAL, elder={"a": 2})
            )
            == "from"
        )


# ---------------------------------------------------------------------------
# Jira projects / milestones / labels
# ---------------------------------------------------------------------------


class TestJiraProjects:
    """Project sync in both directions."""

    def test_push_create_needs_lead(self):
        """Create without a lead account fails fast."""
        r = make().sync_project(
            op("create", D.ELDER_TO_EXTERNAL, RT.PROJECT, elder={"id": 1, "name": "x"})
        )
        assert "project_lead_account_id" in r.errors[0]

    @pytest.mark.parametrize(
        ("key", "name", "expect"),
        [
            (None, "my proj", "MYPROJ"),
            (None, "1abc", "EL1ABC"),
            ("KEY", "n", "KEY"),
            (None, "", "EL"),
        ],
    )
    def test_push_create_key_derivation(self, key, name, expect):
        """Project keys are derived and sanitised."""
        c = make(project_lead_account_id="acct")
        c.client.post.return_value = resp(201, {})
        e = {"id": 1, "name": name, "key": key}
        r = c.sync_project(op("create", D.ELDER_TO_EXTERNAL, RT.PROJECT, elder=e))
        assert r.created_mappings
        assert c.client.post.call_args.kwargs["json"]["key"] == expect

    def test_push_create_failure(self):
        """API rejection is reported."""
        c = make(project_lead_account_id="acct")
        c.client.post.return_value = resp(400, text="bad")
        r = c.sync_project(
            op(
                "create",
                D.ELDER_TO_EXTERNAL,
                RT.PROJECT,
                elder={"id": 1, "name": "n", "lead_account_id": "a2"},
            )
        )
        assert r.status == SyncStatus.FAILED

    def test_push_update_paths(self):
        """No mapping, PUT failure and PUT success."""
        c = make()
        o = op("update", D.ELDER_TO_EXTERNAL, RT.PROJECT, elder={"id": 1, "name": "n"})
        assert "No mapping" in c.sync_project(o).errors[0]
        c.maps.append(mp(1, "PRJ", RT.PROJECT))
        c.client.put.return_value = resp(500)
        assert c.sync_project(o).status == SyncStatus.FAILED
        c.client.put.return_value = resp(204)
        assert c.sync_project(o).updated_mappings

    def test_pull_paths(self):
        """Payload validation, org error, create and update."""
        c = make()
        assert "missing" in c.sync_project(op(rt=RT.PROJECT, ext={})).errors[0]
        bad = make(elder_organization_id=None)
        assert (
            bad.sync_project(op(rt=RT.PROJECT, ext={"key": "K"})).status
            == SyncStatus.FAILED
        )
        r = c.sync_project(
            op(
                rt=RT.PROJECT,
                ext={"key": "K", "name": "n", "archived": True, "description": "d"},
            )
        )
        assert r.is_success
        assert c.db.inserted[-1][0] == "projects" or any(
            t == "projects" for t, _ in c.db.inserted
        )
        c.sync_project(
            op(
                "update",
                rt=RT.PROJECT,
                ext={"key": "K", "name": "n2"},
                mapping=mp(1, "K", RT.PROJECT),
            )
        )
        assert any(t == "projects" for t, _ in c.db.updates)

    def test_exception(self):
        """Unexpected errors become FAILED."""
        c = make()
        c._resolve_org_and_tenant = MagicMock(side_effect=RuntimeError("z"))
        assert c.sync_project(op(rt=RT.PROJECT, ext={"key": "K"})).errors == ["z"]

    def test_upsert_project_existing_found_by_lookup(self):
        """_upsert_elder_project finds a mapping by external id."""
        c = make()
        c.maps.append(mp(3, "K", RT.PROJECT))
        assert c._upsert_elder_project({"id": 9}, org_id=5, tenant_id=2) is not None
        assert (
            c._upsert_elder_project({"key": "K", "name": "x"}, org_id=5, tenant_id=2)
            == 3
        )


class TestJiraMilestones:
    """Milestone (Jira version) sync."""

    def test_push_create_variants(self):
        """Create succeeds (closed + due date) or fails."""
        c = make()
        e = {"id": 2, "title": "v", "status": "closed", "due_date": "2026-03-01"}
        c.client.post.return_value = resp(201, {"id": 77})
        r = c.sync_milestone(op("create", D.ELDER_TO_EXTERNAL, RT.MILESTONE, elder=e))
        assert r.created_mappings
        sent = c.client.post.call_args.kwargs["json"]
        assert sent["released"] is True and sent["releaseDate"] == "2026-03-01"
        c.client.post.return_value = resp(400)
        assert (
            c.sync_milestone(
                op("create", D.ELDER_TO_EXTERNAL, RT.MILESTONE, elder={"id": 3})
            ).status
            == SyncStatus.FAILED
        )

    def test_push_update_paths(self):
        """No mapping, failure, success."""
        c = make()
        o = op(
            "update", D.ELDER_TO_EXTERNAL, RT.MILESTONE, elder={"id": 2, "title": "v"}
        )
        assert "No mapping" in c.sync_milestone(o).errors[0]
        c.maps.append(mp(2, "77", RT.MILESTONE))
        c.client.put.return_value = resp(500)
        assert c.sync_milestone(o).status == SyncStatus.FAILED
        c.client.put.return_value = resp(200)
        assert c.sync_milestone(o).updated_mappings

    def test_pull_paths(self):
        """Missing id, org error, create (closed/open) and update."""
        c = make()
        assert "missing" in c.sync_milestone(op(rt=RT.MILESTONE, ext={})).errors[0]
        assert (
            make(elder_organization_id=None)
            .sync_milestone(op(rt=RT.MILESTONE, ext={"id": 1}))
            .status
            == SyncStatus.FAILED
        )
        assert c.sync_milestone(
            op(
                rt=RT.MILESTONE,
                ext={
                    "id": 1,
                    "name": "a",
                    "released": True,
                    "releaseDate": "2026-01-01",
                },
            )
        ).is_success
        assert c.sync_milestone(
            op(rt=RT.MILESTONE, ext={"id": 2, "name": "b"})
        ).is_success
        c.maps.append(mp(8, "5", RT.MILESTONE))
        assert c.sync_milestone(
            op(
                "update",
                rt=RT.MILESTONE,
                ext={"id": 5, "name": "c"},
                mapping=c.maps[-1],
            )
        ).is_success
        assert any(t == "milestones" for t, _ in c.db.updates)

    def test_exception(self):
        """Unexpected errors become FAILED."""
        c = make()
        c._resolve_org_and_tenant = MagicMock(side_effect=RuntimeError("m"))
        assert c.sync_milestone(op(rt=RT.MILESTONE, ext={"id": 1})).errors == ["m"]


class TestJiraLabels:
    """Label sync in labels and components mode."""

    def test_push_labels_mode_noop(self):
        """Labels mode is managed on the issue itself."""
        r = make().sync_label(op(direction=D.ELDER_TO_EXTERNAL, rt=RT.LABEL))
        assert r.metadata["managed_on_issue"] is True

    def test_push_components(self):
        """Component create/update paths."""
        c = make(label_mode="components")
        e = {"id": 4, "name": "n", "description": "d"}
        c.client.post.return_value = resp(201, {"id": 12})
        assert c.sync_label(
            op("create", D.ELDER_TO_EXTERNAL, RT.LABEL, elder=e)
        ).created_mappings
        c.client.post.return_value = resp(400)
        c.maps.clear()
        assert (
            c.sync_label(op("create", D.ELDER_TO_EXTERNAL, RT.LABEL, elder=e)).status
            == SyncStatus.FAILED
        )
        o = op("update", D.ELDER_TO_EXTERNAL, RT.LABEL, elder=e)
        assert "No mapping" in c.sync_label(o).errors[0]
        c.maps.append(mp(4, "12", RT.LABEL))
        c.client.put.return_value = resp(500)
        assert c.sync_label(o).status == SyncStatus.FAILED
        c.client.put.return_value = resp(204)
        assert c.sync_label(o).updated_mappings

    def test_pull_shapes(self):
        """Dict, string and nameless payloads."""
        c = make()
        d = op(rt=RT.LABEL, ext={"name": " bug ", "id": 3, "description": "x"})
        assert c.sync_label(d).is_success
        assert c.sync_label(op(rt=RT.LABEL, ext="plain")).is_success
        assert "missing 'name'" in c.sync_label(op(rt=RT.LABEL, ext={})).errors[0]
        c.sync_label(op(rt=RT.LABEL, ext={"label": "l"}))

    def test_exception(self):
        """Unexpected errors become FAILED."""
        c = make()
        c._upsert_elder_label = MagicMock(side_effect=RuntimeError("l"))
        assert c.sync_label(op(rt=RT.LABEL, ext={"name": "a"})).errors == ["l"]

    def test_upsert_label_branches(self):
        """Mapped, name-matched (with and without description) and new labels."""
        c = make()
        c.maps.append(mp(6, "ext", RT.LABEL))
        assert c._upsert_elder_label("n", external_id="ext", description="d") == 6
        c2 = make()
        c2.db.rows["issue_labels"] = [SimpleNamespace(id=9, description="old")]
        assert c2._upsert_elder_label("n", external_id="e1", description=None) == 9
        assert c2.db.updates[-1][1]["description"] == "old"
        assert c2._upsert_elder_label("n", external_id="e2", description="new") == 9
        assert make()._upsert_elder_label("fresh", external_id="e3") > 100


# ---------------------------------------------------------------------------
# Jira batch + webhook
# ---------------------------------------------------------------------------


class TestJiraBatch:
    """Batch sync dispatch and each pull/push helper."""

    def test_direction_validation(self):
        """Bad directions and resource types fail; aliases normalise."""
        c = make()
        assert (
            "Unsupported sync direction"
            in c.batch_sync(RT.ISSUE, direction="sideways").errors[0]
        )
        assert "Unsupported resource" in c.batch_sync(RT.COMMENT).errors[0]
        c._batch_pull_labels = MagicMock(return_value="pull")
        c._batch_push_labels = MagicMock(return_value="push")
        assert c.batch_sync(RT.LABEL, direction="external_to_elder") == "pull"
        assert c.batch_sync(RT.LABEL, direction="elder_to_external") == "push"
        assert c.batch_sync(RT.LABEL, direction=None) == "pull"

    def test_bidirectional_aggregates(self):
        """Bidirectional runs both and aggregates counts."""
        c = make()
        ok = SimpleNamespace(items_synced=2, items_failed=0, errors=[])
        bad = SimpleNamespace(items_synced=0, items_failed=1, errors=["e"])
        c._batch_pull_issues = MagicMock(return_value=ok)
        c._batch_push_issues = MagicMock(return_value=bad)
        r = c.batch_sync(RT.ISSUE, direction="bidirectional")
        assert r.status == SyncStatus.PARTIAL_SUCCESS and r.items_synced == 2

    def test_aggregate_statuses(self):
        """Success, partial and failed aggregation."""
        c = make()

        def f(s, fl, e):
            """Aggregate and return the status."""
            return c._aggregate_result(RT.ISSUE, D.BIDIRECTIONAL, s, fl, e).status

        assert f(1, 0, []) == SyncStatus.SUCCESS
        assert f(1, 1, []) == SyncStatus.PARTIAL_SUCCESS
        assert f(0, 1, ["e"]) == SyncStatus.FAILED
        assert f(0, 0, ["e"]) == SyncStatus.FAILED

    def test_pull_issues_paged(self):
        """Pages follow nextPageToken; sync outcomes are counted."""
        c = make()
        c.maps.append(mp(1, "PRJ-1"))
        good = {"key": "PRJ-1", "fields": {"summary": "s"}}
        new = {"key": "PRJ-2", "fields": {"summary": "s"}}
        nokey = {"fields": {}}
        c.client.post.side_effect = [
            resp(200, {"issues": [good, new], "isLast": False, "nextPageToken": "t"}),
            resp(200, {"issues": [nokey], "isLast": True}),
        ]
        r = c._batch_pull_issues(since=datetime(2026, 1, 1, 10, 30))
        assert r.items_synced == 2 and r.items_failed == 1
        assert c.client.post.call_args_list[1].kwargs["json"]["nextPageToken"] == "t"
        assert "updated >=" in c.client.post.call_args_list[0].kwargs["json"]["jql"]

    def test_pull_issues_errors(self):
        """HTTP and exception errors stop paging."""
        c = make()
        c.client.post.return_value = resp(500)
        assert c._batch_pull_issues().status == SyncStatus.FAILED
        c.client.post.side_effect = RuntimeError("x")
        assert "pull error" in c._batch_pull_issues().errors[0]
        c.client.post.side_effect = None
        c.client.post.return_value = resp(
            200, {"issues": [], "isLast": False, "nextPageToken": None}
        )
        assert c._batch_pull_issues().status == SyncStatus.SUCCESS

    def test_push_issues(self):
        """Org errors fail; rows push with labels and milestone links."""
        assert (
            make(elder_organization_id=None)._batch_push_issues().status
            == SyncStatus.FAILED
        )
        c = make()
        row = SimpleNamespace(
            id=1,
            title="t",
            description="d",
            status="OPEN",
            priority="LOW",
            issue_type="bug",
            updated_at=None,
        )
        row2 = SimpleNamespace(
            id=2,
            title="t",
            description="d",
            status="OPEN",
            priority="LOW",
            issue_type="bug",
            updated_at=None,
        )
        c.db.rows["issues"] = [row, row2]
        c.db.rows["issue_label_assignments"] = [SimpleNamespace(name="l")]
        c.db.rows["issue_milestone_links"] = [SimpleNamespace(milestone_id=4)]
        c.maps.append(mp(2, "PRJ-2"))
        c.client.post.return_value = resp(201, {"key": "PRJ-3"})
        c.client.put.return_value = resp(500)
        r = c._batch_push_issues()
        assert r.items_synced == 1 and r.items_failed == 1

    def test_pull_projects_single_and_all(self):
        """Single-project and paged all-projects pulls with failures."""
        c = make()
        c.maps.append(mp(1, "PRJ", RT.PROJECT))
        c.client.get.return_value = resp(200, {"key": "PRJ", "name": "n"})
        assert c._batch_pull_projects().items_synced == 1
        c.client.get.return_value = resp(500)
        assert c._batch_pull_projects().items_failed == 1
        c.client.get.return_value = resp(200, {})
        assert c._batch_pull_projects().items_failed == 1
        c.client.get.side_effect = RuntimeError("x")
        assert "project pull error" in c._batch_pull_projects().errors[0]

        a = make(sync_all_projects=True)
        a.maps.append(mp(1, "A", RT.PROJECT))
        a.client.get.side_effect = [
            resp(
                200,
                {
                    "values": [
                        {"key": "A", "name": "a"},
                        {"key": "B", "name": "b"},
                        {},
                    ],
                    "isLast": False,
                },
            ),
            resp(200, {"values": [], "isLast": False}),
        ]
        r = a._batch_pull_projects()
        assert r.items_synced == 2 and r.items_failed == 1
        a.client.get.side_effect = [resp(500)]
        assert a._batch_pull_projects().items_failed == 1
        a.client.get.side_effect = RuntimeError("y")
        assert "batch project pull error" in a._batch_pull_projects().errors[0]

    def test_push_projects_and_milestones(self):
        """Push helpers handle org errors and per-row outcomes."""
        bad = make(elder_organization_id=None)
        assert bad._batch_push_projects().status == SyncStatus.FAILED
        assert bad._batch_push_milestones().status == SyncStatus.FAILED
        c = make(project_lead_account_id="a")
        c.db.rows["projects"] = [
            SimpleNamespace(
                id=1, name="n", description="d", status="active", updated_at=None
            ),
            SimpleNamespace(
                id=2, name="m", description="d", status="active", updated_at=None
            ),
        ]
        c.maps.append(mp(2, "PRJ", RT.PROJECT))
        c.client.post.return_value = resp(201, {"key": "NEW"})
        c.client.put.return_value = resp(500)
        r = c._batch_push_projects()
        assert r.items_synced == 1 and r.items_failed == 1
        c.db.rows["milestones"] = [
            SimpleNamespace(
                id=1,
                title="t",
                description="d",
                status="open",
                due_date=None,
                updated_at=None,
            ),
            SimpleNamespace(
                id=2,
                title="t",
                description="d",
                status="open",
                due_date=None,
                updated_at=None,
            ),
        ]
        c.maps.append(mp(2, "9", RT.MILESTONE))
        c.client.post.return_value = resp(201, {"id": 5})
        r = c._batch_push_milestones()
        assert r.items_synced == 1 and r.items_failed == 1

    def test_pull_milestones(self):
        """Success, per-item failure, HTTP error and exception."""
        c = make()
        c.maps.append(mp(1, "1", RT.MILESTONE))
        c.client.get.return_value = resp(
            200, [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
        )
        assert c._batch_pull_milestones().items_synced == 2
        c.client.get.return_value = resp(200, [{"id": 3, "name": "a"}])
        c.logger.reset_mock()
        c._resolve_org_and_tenant = MagicMock(return_value=(None, None, "no org"))
        assert c._batch_pull_milestones().items_failed == 1
        c.client.get.return_value = resp(500)
        assert c._batch_pull_milestones().items_failed == 1
        c.client.get.side_effect = RuntimeError("x")
        assert "version pull error" in c._batch_pull_milestones().errors[0]

    def test_pull_labels_components(self):
        """Component mode pull: success, failure, HTTP error, exception."""
        c = make(label_mode="components")
        c.maps.append(mp(1, "1", RT.LABEL))
        c.client.get.return_value = resp(
            200, [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
        )
        assert c._batch_pull_labels().items_synced == 2
        c.client.get.return_value = resp(200, [{"id": 3}])
        assert c._batch_pull_labels().items_failed == 1
        c.client.get.return_value = resp(500)
        assert c._batch_pull_labels().items_failed == 1
        c.client.get.side_effect = RuntimeError("x")
        assert "component pull error" in c._batch_pull_labels().errors[0]

    def test_pull_labels_plain(self):
        """Label mode pull pages, handles failures and errors."""
        c = make()
        c.maps.append(mp(1, "a", RT.LABEL))
        c.client.get.side_effect = [
            resp(200, {"values": ["a", "b", ""], "isLast": False}),
            resp(200, {"values": [], "isLast": False}),
        ]
        r = c._batch_pull_labels()
        assert r.items_synced == 2 and r.items_failed == 1
        c.client.get.side_effect = [resp(500)]
        assert c._batch_pull_labels().items_failed == 1
        c.client.get.side_effect = RuntimeError("x")
        assert "label pull error" in c._batch_pull_labels().errors[0]

    def test_push_labels(self):
        """Labels mode is a no-op; component mode pushes rows."""
        assert make()._batch_push_labels().metadata["managed_on_issue"] is True
        c = make(label_mode="components")
        c.db.rows["issue_labels"] = [
            SimpleNamespace(id=1, name="a", description="d", updated_at=None),
            SimpleNamespace(id=2, name="b", description="d", updated_at=None),
        ]
        c.maps.append(mp(2, "9", RT.LABEL))
        c.client.post.return_value = resp(201, {"id": 5})
        c.client.put.return_value = resp(500)
        r = c._batch_push_labels()
        assert r.items_synced == 1 and r.items_failed == 1


class TestJiraWebhook:
    """Webhook routing."""

    def test_issue_events(self):
        """Created, updated and deleted issue events."""
        c = make(two_way_create=True)
        payload = {
            "webhookEvent": "jira:issue_created",
            "issue": {"key": "PRJ-1", "fields": {"summary": "s"}},
        }
        assert c.handle_webhook(payload).is_success
        payload["webhookEvent"] = "jira:issue_updated"
        assert c.handle_webhook(payload).is_success
        payload["webhookEvent"] = "jira:issue_deleted"
        r = c.handle_webhook(payload)
        assert r.metadata["deleted"] is True
        r = c.handle_webhook({"webhookEvent": "jira:issue_deleted", "issue": {}})
        assert r.operation.mapping is None

    def test_version_events(self):
        """Version create/update/delete events."""
        c = make()
        for ev in ("jira:version_created", "jira:version_deleted"):
            r = c.handle_webhook(
                {"webhookEvent": ev, "version": {"id": 5, "name": "v"}}
            )
            assert r.is_success
        r = c.handle_webhook({"webhookEvent": "jira:version_deleted", "version": {}})
        assert r.metadata["deleted"] is True

    def test_project_events(self):
        """Project create/delete events."""
        c = make()
        r = c.handle_webhook(
            {"webhookEvent": "project_updated", "project": {"key": "K", "name": "n"}}
        )
        assert r.is_success
        r = c.handle_webhook(
            {"webhookEvent": "project_deleted", "project": {"key": "K"}}
        )
        assert r.metadata["external_id"] == "K"

    def test_ignored(self):
        """Unknown events are ignored."""
        r = make().handle_webhook({"webhookEvent": "other"})
        assert r.metadata["ignored"] is True
        r = make().handle_webhook({})
        assert r.metadata["ignored"] is True


# ---------------------------------------------------------------------------
# Thin clients: Trello, OpenProject, GitLab
# ---------------------------------------------------------------------------


CLIENTS = [
    (
        TrelloSyncClient,
        lambda: {"api_key": fake_secret(), "api_token": fake_secret(), "board_id": "b"},
        {"action": {"type": "x"}},
    ),
    (
        OpenProjectSyncClient,
        lambda: {
            "api_key": fake_secret(),
            "base_url": "https://op.example.com",
            "project_id": "1",
        },
        {"action": "created"},
    ),
    (
        GitLabSyncClient,
        lambda: {"api_token": fake_secret(), "project_id": "1"},
        {"object_kind": "issue"},
    ),
]


@pytest.mark.parametrize(("cls", "cfg", "hook"), CLIENTS)
class TestThinClients:
    """Shared behaviour of the lightweight platform clients."""

    def build(self, cls, cfg):
        """Instantiate the client with a mocked HTTP client."""
        c = cls(cfg(), FakeDB(), 1, MagicMock())
        c.client = MagicMock()
        return c

    def test_validate_config(self, cls, cfg, hook):
        """Complete config validates; stripping a key invalidates it."""
        c = self.build(cls, cfg)
        assert c.validate_config() is True
        broken = cfg()
        broken[list(broken)[-1]] = None
        assert cls(broken, FakeDB(), 1, MagicMock()).validate_config() is False

    def test_connection(self, cls, cfg, hook):
        """200 is connected, other statuses and exceptions are not."""
        c = self.build(cls, cfg)
        c.client.get.return_value = resp(200)
        assert c.test_connection() is True
        c.client.get.return_value = resp(404)
        assert c.test_connection() is False
        c.client.get.side_effect = RuntimeError("x")
        assert c.test_connection() is False
        c.logger.error.assert_called()

    def test_sync_methods(self, cls, cfg, hook):
        """Every sync method reports success; delete counts differently on GitLab."""
        c = self.build(cls, cfg)
        for kind in ("create", "delete"):
            o = op(kind)
            assert c.sync_issue(o).status == SyncStatus.SUCCESS
        assert c.sync_issue(op("create")).items_synced == 1
        assert c.sync_issue(op("delete")).items_synced == (
            0 if cls is GitLabSyncClient else 1
        )
        for fn in (c.sync_project, c.sync_milestone, c.sync_label):
            assert fn(op()).status == SyncStatus.SUCCESS

    def test_batch_and_webhook(self, cls, cfg, hook):
        """Batch and webhook handlers return successful results."""
        c = self.build(cls, cfg)
        r = c.batch_sync(RT.ISSUE)
        assert r.operation.direction == D.BIDIRECTIONAL and r.is_success
        w = c.handle_webhook(hook)
        assert w.operation.operation_type == "webhook"
        assert c.handle_webhook({}).is_success
        c.close()


@pytest.mark.xfail(
    strict=True,
    reason="bug: GitLabSyncClient.__init__ raises TypeError on a missing api_token "
    "(None header) instead of letting validate_config() return False",
)
def test_gitlab_missing_token_validates_false():
    """A GitLab config without a token should fail validation, not crash."""
    client = GitLabSyncClient({"project_id": "1"}, FakeDB(), 1, MagicMock())
    assert client.validate_config() is False
