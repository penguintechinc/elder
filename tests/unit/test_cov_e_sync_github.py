"""Unit tests for the GitHub sync client using a real SQLite DAL and a mock HTTP transport."""

import json
import logging
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from penguin_dal import DAL, Field

import tests.unit.conftest_worker_stubs  # noqa: F401
from apps.worker.sync.base import (
    ResourceType,
    SyncDirection,
    SyncMapping,
    SyncOperation,
    SyncStatus,
)
from apps.worker.sync.platforms import github_client as gh
from apps.worker.sync.platforms.github_client import (
    GitHubSyncClient,
    load_github_app_private_key,
    normalize_pem_private_key,
    parse_installation_id,
    resolve_github_auth_mode,
)

LOG = logging.getLogger("cov-e-github")
NOW = datetime(2026, 2, 3, 4, 5, 6, tzinfo=UTC)
IMAGE_JSON = {"Content-Type": "application/json"}


def _fake_token() -> str:
    """Build a throwaway API token at runtime."""
    return "-".join(("cove", uuid4().hex[:8]))


def _rsa_pem() -> str:
    """Generate an ephemeral RSA PKCS8 PEM."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


@pytest.fixture(scope="module")
def rsa_pem() -> str:
    """Return one RSA PEM shared by the module to save keygen time."""
    return _rsa_pem()


def _tables(db: DAL) -> None:
    """Define the minimal tables the GitHub client touches."""
    ts = [Field("created_at", "datetime"), Field("updated_at", "datetime")]
    db.define_table(
        "organizations", Field("name", "string"), Field("tenant_id", "integer"), *ts
    )
    db.define_table(
        "sync_configs",
        Field("name", "string"),
        Field("two_way_create", "boolean"),
        Field("last_sync_at", "datetime"),
        Field("last_batch_sync_at", "datetime"),
        Field("config_json", "json"),
        *ts,
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
        *ts,
    )
    db.define_table(
        "sync_conflicts",
        Field("mapping_id", "integer"),
        Field("conflict_type", "string"),
        Field("elder_data", "json"),
        Field("external_data", "json"),
        Field("resolution_strategy", "string"),
        Field("resolved", "boolean"),
        Field("created_at", "datetime"),
    )
    db.define_table(
        "projects",
        Field("name", "string"),
        Field("description", "text"),
        Field("status", "string"),
        Field("organization_id", "integer"),
        Field("tenant_id", "integer"),
        *ts,
    )
    db.define_table(
        "milestones",
        Field("title", "string"),
        Field("description", "text"),
        Field("status", "string"),
        Field("due_date", "date"),
        Field("closed_at", "datetime"),
        Field("organization_id", "integer"),
        Field("tenant_id", "integer"),
        *ts,
    )
    db.define_table(
        "issue_labels",
        Field("name", "string"),
        Field("color", "string"),
        Field("description", "text"),
        *ts,
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
        Field("closed_at", "datetime"),
        *ts,
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


@pytest.fixture
def db(tmp_path):
    """Provide a SQLite DAL with an org (id 1, tenant 7) and a sync config (id 1)."""
    d = DAL(f"sqlite:///{tmp_path / 'gh.db'}", migrate=True)
    _tables(d)
    d.organizations.insert(name="Org", tenant_id=7, created_at=NOW, updated_at=NOW)
    d.sync_configs.insert(
        name="c", two_way_create=False, config_json={}, created_at=NOW, updated_at=NOW
    )
    d.commit()
    yield d
    d.close()


class Router:
    """Mock HTTP router keyed by (method, path) with queued responses."""

    def __init__(self):
        """Create an empty router."""
        self.routes: dict[tuple[str, str], list] = {}
        self.calls: list[httpx.Request] = []

    def add(self, method, path, *responses):
        """Queue responses (status, body) for a route; the last one repeats."""
        self.routes[(method, path)] = list(responses)
        return self

    def __call__(self, req: httpx.Request) -> httpx.Response:
        """Serve a request from the queued responses or 404."""
        self.calls.append(req)
        q = self.routes.get((req.method, req.url.path))
        if not q:
            return httpx.Response(404, json={"message": "nf"})
        item = q.pop(0) if len(q) > 1 else q[0]
        if isinstance(item, Exception):
            raise item
        status, body = item
        if isinstance(body, str):
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=body)


class _JoinShim:
    """DB proxy that fakes the label join select (a real-DAL limitation) and delegates the rest."""

    def __init__(self, real):
        """Wrap a real DAL."""
        self._real = real

    def __getattr__(self, name):
        """Delegate attribute access to the real DAL."""
        return getattr(self._real, name)

    def __call__(self, query):
        """Return a set proxy whose field-argument selects yield one fake label row."""
        real_set = self._real(query)

        class _Set:
            """Set proxy intercepting join selects."""

            def select(self, *fields, **kw):
                """Fake rows for join selects, else delegate."""
                if fields:
                    return [SimpleNamespace(name="bug")]
                return real_set.select(**kw)

        return _Set()


def _client(db, router=None, **cfg) -> GitHubSyncClient:
    """Build a PAT-mode client wired to a mock transport."""
    config = {
        "owner": "acme",
        "repo_name": "widgets",
        "api_token": _fake_token(),
        "elder_organization_id": 1,
    }
    config.update(cfg)
    c = GitHubSyncClient(config, db, 1, LOG)
    if router is not None:
        old = c.client
        c.client = httpx.Client(
            base_url=old.base_url,
            headers=old.headers,
            transport=httpx.MockTransport(router),
        )
        old.close()
    return c


def _op(kind, rtype, direction, **kw) -> SyncOperation:
    """Build a SyncOperation."""
    return SyncOperation(
        operation_type=kind, resource_type=rtype, direction=direction, **kw
    )


E2G = SyncDirection.ELDER_TO_EXTERNAL
G2E = SyncDirection.EXTERNAL_TO_ELDER
BID = SyncDirection.BIDIRECTIONAL


def _allow_create(db):
    """Enable two_way_create on the sync config row."""
    db(db.sync_configs.id == 1).update(two_way_create=True)
    db.commit()


R = "/repos/acme/widgets"


class TestHelpers:
    """Tests for module-level helper functions."""

    def test_parse_datetime(self):
        """_parse_datetime covers datetimes, dates, strings and junk."""
        assert gh._parse_datetime(None) is None
        assert gh._parse_datetime(datetime(2026, 1, 1)).tzinfo is not None
        assert gh._parse_datetime(NOW) == NOW
        assert gh._parse_datetime(date(2026, 1, 2)) == datetime(2026, 1, 2, tzinfo=UTC)
        assert gh._parse_datetime("2026-01-01T00:00:00Z") == datetime(
            2026, 1, 1, tzinfo=UTC
        )
        assert gh._parse_datetime("2026-01-01T00:00:00") == datetime(
            2026, 1, 1, tzinfo=UTC
        )
        assert gh._parse_datetime("bad") is None
        assert gh._parse_datetime("  ") is None
        assert gh._parse_datetime(5) is None

    def test_parse_date(self):
        """_parse_date covers every input type."""
        assert gh._parse_date(None) is None
        assert gh._parse_date(NOW) == NOW.date()
        assert gh._parse_date(date(2026, 1, 2)) == date(2026, 1, 2)
        assert gh._parse_date("2026-01-02T03:04:05Z") == date(2026, 1, 2)
        assert gh._parse_date("2026-01-02") == date(2026, 1, 2)
        assert gh._parse_date("zzzz-zz-zz") is None
        assert gh._parse_date("") is None
        assert gh._parse_date(3) is None

    def test_format_due_on(self):
        """_format_github_due_on returns GitHub timestamps or None."""
        assert gh._format_github_due_on(None) is None
        assert gh._format_github_due_on("") is None
        assert gh._format_github_due_on("nope") is None
        assert gh._format_github_due_on("2026-01-02") == "2026-01-02T00:00:00Z"

    @pytest.mark.parametrize(
        ("raw", "hashed"),
        [
            ("ABCDEF", "#abcdef"),
            ("#abc", "#aabbcc"),
            ("zzzzzz", "#808080"),
            (None, "#808080"),
            ("12", "#808080"),
        ],
    )
    def test_colors(self, raw, hashed):
        """Color normalisers handle full, short, invalid and missing colors."""
        assert gh._normalize_color_with_hash(raw) == hashed
        assert gh._normalize_color_no_hash(raw) == hashed.lstrip("#")

    @pytest.mark.parametrize(
        ("base", "explicit", "want"),
        [
            ("https://api.github.com/", None, "https://api.github.com/graphql"),
            (
                "https://ghe.example.com/api/v3",
                None,
                "https://ghe.example.com/api/graphql",
            ),
            ("https://ghe.example.com", None, "https://ghe.example.com/graphql"),
            ("https://x", " https://y/graphql/ ", "https://y/graphql"),
            ("https://api.github.com", "  ", "https://api.github.com/graphql"),
        ],
    )
    def test_graphql_url(self, base, explicit, want):
        """GraphQL URL derives from cloud/GHES base or an explicit override."""
        assert gh._compute_graphql_url(base, explicit) == want

    def test_pem_helpers(self, rsa_pem):
        """PEM normalisation and loading accept RSA and reject everything else."""
        assert normalize_pem_private_key("  ") == ""
        assert normalize_pem_private_key("a\\nb\r\nc\rd") == "a\nb\nc\nd"
        assert load_github_app_private_key(rsa_pem.replace("\n", "\\n")) is not None
        with pytest.raises(ValueError, match="PEM-formatted"):
            load_github_app_private_key(5)
        with pytest.raises(ValueError, match="required"):
            load_github_app_private_key("  ")
        with pytest.raises(ValueError, match="not a valid"):
            load_github_app_private_key("garbage")
        ec_pem = (
            ec.generate_private_key(ec.SECP256R1())
            .private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
            .decode()
        )
        with pytest.raises(ValueError, match="RSA"):
            load_github_app_private_key(ec_pem)

    @pytest.mark.parametrize(
        ("val", "want"),
        [
            (None, None),
            (True, None),
            (5, 5),
            (0, None),
            ("7", 7),
            (" 8 ", 8),
            ("0", None),
            ("x", None),
            (1.5, None),
        ],
    )
    def test_installation_id(self, val, want):
        """parse_installation_id accepts positive ints and digit strings only."""
        assert parse_installation_id(val) == want

    def test_auth_mode(self):
        """resolve_github_auth_mode honours explicit mode or infers from keys."""
        assert resolve_github_auth_mode({"auth_mode": " PAT "}) == "pat"
        assert resolve_github_auth_mode({"auth_mode": "github_app"}) == "github_app"
        assert resolve_github_auth_mode({"auth_mode": "  ", "api_token": "x"}) == "pat"
        assert resolve_github_auth_mode({"app_id": 1}) == "github_app"
        assert resolve_github_auth_mode({"app_id": True, "private_key": " "}) == "pat"
        assert resolve_github_auth_mode({}) == "pat"
        with pytest.raises(ValueError, match="must be"):
            resolve_github_auth_mode({"auth_mode": 3})
        with pytest.raises(ValueError, match="Unsupported"):
            resolve_github_auth_mode({"auth_mode": "oauth"})


class TestInitAndAuth:
    """Tests for construction, JWT signing, token minting and request retry."""

    def test_init_variants(self, db):
        """Constructor tolerates bad auth_mode, odd app ids and bad private keys."""
        c = GitHubSyncClient({"auth_mode": "oauth"}, db, 1, LOG)
        assert c.auth_mode == "oauth" and c._auth_mode_error
        c = GitHubSyncClient({"auth_mode": 3}, db, 1, LOG)
        assert c._auth_mode_error
        c = GitHubSyncClient({"auth_mode": ""}, db, 1, LOG)
        assert c.auth_mode == "pat"
        for raw, want in [
            (True, None),
            (0, None),
            (12, "12"),
            ("  ", None),
            (" 9 ", "9"),
        ]:
            assert GitHubSyncClient({"app_id": raw}, db, 1, LOG).app_id == want
        c = GitHubSyncClient({"private_key": 5, "app_id": 1}, db, 1, LOG)
        assert c._private_key_invalid_type
        with pytest.raises(ValueError, match="PEM-formatted"):
            c._load_private_key()
        c = GitHubSyncClient({"owner": "o", "api_token": "t"}, db, 1, LOG)
        assert c.org_name == "o" and c.client.headers["Authorization"] == "Bearer t"
        c.close()

    def test_repo_path(self, db):
        """_repo_path quotes components and requires owner/repo."""
        assert _client(db, owner="a b")._repo_path("/x") == "/repos/a%20b/widgets/x"
        with pytest.raises(ValueError, match="must be configured"):
            _client(db, repo_name="")._repo_path()

    def _app(self, db, rsa_pem, router=None, **kw):
        """Build a github_app-mode client."""
        cfg = {
            "auth_mode": "github_app",
            "app_id": 5,
            "installation_id": 9,
            "private_key": rsa_pem,
            "api_token": "",
        }
        cfg.update(kw)
        return _client(db, router, **cfg)

    def test_jwt(self, db, rsa_pem):
        """_generate_app_jwt signs a 3-part token and requires an app id."""
        c = self._app(db, rsa_pem)
        tok = c._generate_app_jwt(NOW)
        assert len(tok.split(".")) == 3
        assert c._generate_app_jwt() and c._private_key is c._load_private_key()
        with pytest.raises(ValueError, match="App ID"):
            self._app(db, rsa_pem, app_id=None)._generate_app_jwt()

    def test_mint_errors(self, db, rsa_pem):
        """Installation token exchange surfaces HTTP and payload errors."""
        path = "/app/installations/9/access_tokens"
        with pytest.raises(ValueError, match="installation_id"):
            self._app(db, rsa_pem, installation_id=None)._mint_installation_token()
        r = Router().add("POST", path, (401, {"message": "Bad creds"}))
        with pytest.raises(RuntimeError, match="Bad creds"):
            self._app(db, rsa_pem, r)._mint_installation_token()
        r = Router().add("POST", path, (500, "oops"))
        with pytest.raises(RuntimeError, match="HTTP 500"):
            self._app(db, rsa_pem, r)._mint_installation_token()
        r = Router().add("POST", path, (500, ["x"]))
        with pytest.raises(RuntimeError, match="HTTP 500"):
            self._app(db, rsa_pem, r)._mint_installation_token()
        r = Router().add("POST", path, (201, {"token": " "}))
        with pytest.raises(RuntimeError, match="missing"):
            self._app(db, rsa_pem, r)._mint_installation_token()
        r = Router().add("POST", path, (201, ["x"]))
        with pytest.raises(RuntimeError, match="missing"):
            self._app(db, rsa_pem, r)._mint_installation_token()

    def test_mint_and_cache(self, db, rsa_pem):
        """Minting stores token/permissions; cached token is reused until near expiry."""
        path = "/app/installations/9/access_tokens"
        tok = _fake_token()
        r = Router().add(
            "POST", path, (201, {"token": tok, "permissions": {"issues": "write"}})
        )
        c = self._app(db, rsa_pem, r)
        assert c._get_auth_token() == tok
        assert c._token_permissions == {"issues": "write"}
        assert c._get_auth_token() == tok
        assert len(r.calls) == 1
        c._get_auth_token(force_refresh=True)
        assert len(r.calls) == 2
        c._token_expires_at = datetime.now(UTC) + timedelta(minutes=1)
        c._get_auth_token()
        assert len(r.calls) == 3
        r2 = Router().add(
            "POST",
            path,
            (
                200,
                {
                    "token": tok,
                    "expires_at": "2099-01-01T00:00:00Z",
                    "permissions": "x",
                },
            ),
        )
        c2 = self._app(db, rsa_pem, r2)
        c2._get_auth_token()
        assert c2._token_permissions is None

    def test_get_auth_token_errors(self, db):
        """PAT mode needs a token; unknown modes and mode errors are rejected."""
        with pytest.raises(ValueError, match="auth_mode"):
            _client(db, auth_mode="oauth")._get_auth_token()
        with pytest.raises(ValueError, match="token is required"):
            _client(db, api_token="")._get_auth_token()
        c = _client(db)
        c.auth_mode = "weird"
        with pytest.raises(ValueError, match="Unsupported"):
            c._get_auth_token()

    def test_request_retry_on_401(self, db, rsa_pem):
        """A 401 in app mode forces a token refresh and retries once."""
        path = "/app/installations/9/access_tokens"
        r = Router()
        r.add("POST", path, (201, {"token": _fake_token()}))
        r.add("GET", f"{R}", (401, {}), (200, {"ok": 1}))
        c = self._app(db, rsa_pem, r)
        resp = c._request("GET", c._repo_path(), headers={"X-Test": "1"})
        assert resp.status_code == 200
        assert len([x for x in r.calls if x.method == "GET"]) == 2
        pr = Router().add("GET", R, (401, {}))
        pc = _client(db, pr)
        assert pc._request("GET", R).status_code == 401
        assert len(pr.calls) == 1

    def test_validate_config(self, db, rsa_pem):
        """validate_config enforces required settings per auth mode."""
        assert _client(db).validate_config()
        assert not _client(db, auth_mode="oauth").validate_config()
        assert not _client(db, owner="", org_name="").validate_config()
        assert not _client(db, repo_name="").validate_config()
        assert not _client(db, api_token="").validate_config()
        assert self._app(db, rsa_pem).validate_config()
        assert not self._app(db, rsa_pem, app_id=None).validate_config()
        assert not self._app(db, rsa_pem, installation_id=0).validate_config()
        assert not self._app(db, rsa_pem, private_key="").validate_config()
        assert not self._app(db, rsa_pem, private_key=7).validate_config()
        assert not self._app(db, rsa_pem, private_key="junk").validate_config()
        c = _client(db)
        c.auth_mode = "weird"
        assert not c.validate_config()

    def test_connection_detail(self, db, rsa_pem):
        """test_connection_detail reports success, API errors and exceptions."""
        bad = _client(db, api_token="").test_connection_detail()
        assert bad["connected"] is False and "api_token" in bad["error"]
        bad = self._app(db, rsa_pem, app_id=None).test_connection_detail()
        assert "App" in bad["error"]

        ok = Router().add(
            "GET",
            R,
            (200, {"full_name": "acme/widgets", "permissions": {"push": True}}),
        )
        d = _client(db, ok).test_connection_detail()
        assert d["connected"] and d["permissions"] == {"push": True}
        ok = Router().add("GET", R, (200, {"full_name": "a/b"}))
        d = _client(db, ok).test_connection_detail()
        assert "permissions" not in d
        assert _client(db, ok).test_connection()

        r = Router()
        r.add(
            "POST",
            "/app/installations/9/access_tokens",
            (201, {"token": _fake_token()}),
        )
        r.add("GET", R, (200, {"full_name": "a/b"}))
        d = self._app(db, rsa_pem, r).test_connection_detail()
        assert d["token_expires_at"] and "permissions" in d

        nf = Router().add("GET", R, (404, {"message": "Not Found"}))
        d = _client(db, nf).test_connection_detail()
        assert d["status_code"] == 404 and "Not Found" in d["error"]
        nf = Router().add("GET", R, (500, "plain"))
        assert _client(db, nf).test_connection_detail()["error"].endswith("500")
        boom = Router().add(
            "GET",
            R,
            (200, {}),
        )
        boom.routes[("GET", R)] = [httpx.ConnectError("down")]
        d = _client(db, boom).test_connection_detail()
        assert d["error"] == "down"


class TestDbHelpers:
    """Tests for org resolution, create gating and mapping upserts."""

    def test_resolve_org(self, db):
        """Org/tenant resolution handles each failure mode."""
        assert _client(db)._resolve_org_and_tenant() == (1, 7, None)
        assert (
            _client(db, elder_organization_id=None)
            ._resolve_org_and_tenant()[2]
            .startswith("No Elder")
        )
        db(db.sync_configs.id == 1).update(config_json={"elder_organization_id": 1})
        db.commit()
        assert _client(db, elder_organization_id=None)._resolve_org_and_tenant()[0] == 1
        assert (
            "Invalid"
            in _client(db, elder_organization_id="x")._resolve_org_and_tenant()[2]
        )
        assert (
            "not found"
            in _client(db, elder_organization_id=99)._resolve_org_and_tenant()[2]
        )

    def test_should_allow_create(self, db):
        """allow_create metadata, config row and config dict decide."""
        c = _client(db)
        op = _op("create", ResourceType.ISSUE, G2E)
        assert not c._should_allow_create(op)
        op.metadata["allow_create"] = True
        assert c._should_allow_create(op)
        _allow_create(db)
        assert c._should_allow_create(_op("create", ResourceType.ISSUE, G2E))
        db(db.sync_configs.id == 1).delete()
        db.commit()
        assert not c._should_allow_create(_op("create", ResourceType.ISSUE, G2E))
        assert _client(db, two_way_create=True)._should_allow_create(
            _op("c", ResourceType.ISSUE, G2E)
        )

    def test_upsert_mapping(self, db):
        """_upsert_mapping creates, updates in place and re-keys external ids."""
        c = _client(db)
        m = c._upsert_mapping(ResourceType.ISSUE, 5, "50", elder_updated_at=NOW)
        assert m.external_id == "50"
        same = c._upsert_mapping(ResourceType.ISSUE, 5, "50", elder_updated_at=NOW)
        assert same.external_id == "50"
        moved = c._upsert_mapping(ResourceType.ISSUE, 5, "51")
        assert moved.external_id == "51"
        assert c.get_mapping(ResourceType.ISSUE, external_id="51").elder_id == 5
        by_ext = c._upsert_mapping(ResourceType.ISSUE, 99, "51")
        assert by_ext.elder_id == 5

    def test_labels_and_milestone_links(self, db):
        """Issue label/milestone linking upserts rows idempotently."""
        c = _client(db)
        c._sync_issue_labels(1, [])
        labels = [
            {"name": "bug", "color": "ff0000", "description": "d"},
            "plain",
            {"name": " "},
            {},
            " ",
        ]
        c._sync_issue_labels(1, labels)
        c._sync_issue_labels(1, labels)
        assert db(db.issue_labels.id > 0).count() == 2
        assert db(db.issue_label_assignments.id > 0).count() == 2
        c._sync_issue_milestone(1, None, 1, 7)
        c._sync_issue_milestone(1, {"title": "x"}, 1, 7)
        ms = {"number": 3, "title": "M", "state": "open"}
        c._sync_issue_milestone(1, ms, 1, 7)
        c._sync_issue_milestone(1, ms, 1, 7)
        assert db(db.issue_milestone_links.id > 0).count() == 1
        from unittest.mock import patch

        with patch.object(c, "_upsert_elder_milestone", return_value=0):
            c._sync_issue_milestone(2, ms, 1, 7)
        assert db(db.issue_milestone_links.id > 0).count() == 1


class TestIssues:
    """Tests for issue sync in each direction."""

    def test_dispatch_and_failure(self, db):
        """sync_issue dispatches by direction and converts exceptions to FAILED."""
        c = _client(db, repo_name="")
        r = c.sync_issue(_op("create", ResourceType.ISSUE, E2G, elder_data={"id": 1}))
        assert r.status == SyncStatus.FAILED and "repo_name" in r.errors[0]
        r = c.sync_issue(_op("update", ResourceType.ISSUE, G2E, external_data={}))
        assert r.status == SyncStatus.FAILED
        r = c.sync_issue(_op("update", ResourceType.ISSUE, BID, external_data={}))
        assert r.status == SyncStatus.FAILED

    def test_push_create_and_update(self, db):
        """Pushing creates then updates a GitHub issue with labels/assignees/milestone."""
        c0 = _client(db)
        c0._upsert_mapping(ResourceType.MILESTONE, 4, "40")
        r = (
            Router()
            .add(
                "POST",
                f"{R}/issues",
                (201, {"number": 11, "updated_at": "2026-01-01T00:00:00Z"}),
            )
            .add(
                "PATCH", f"{R}/issues/11", (200, {"updated_at": "2026-01-02T00:00:00Z"})
            )
        )
        c = _client(db, r)
        elder = {
            "id": 1,
            "title": "T",
            "status": "closed",
            "labels": [{"name": "a"}, {"nolabel": 1}, "b", None],
            "assignee": "me",
            "milestone_id": 4,
        }
        res = c.sync_issue(_op("create", ResourceType.ISSUE, E2G, elder_data=elder))
        assert res.status == SyncStatus.SUCCESS
        body = json.loads(r.calls[0].content)
        assert body["state"] == "closed" and body["labels"] == ["a", "b"]
        assert body["assignees"] == ["me"] and body["milestone"] == 40
        res = c.sync_issue(
            _op(
                "update",
                ResourceType.ISSUE,
                E2G,
                elder_data={"id": 1, "assignees": [None]},
            )
        )
        assert res.status == SyncStatus.SUCCESS and res.updated_mappings

    def test_push_failures(self, db):
        """HTTP errors and missing mappings produce FAILED results."""
        r = (
            Router()
            .add("POST", f"{R}/issues", (422, "bad"))
            .add("PATCH", f"{R}/issues/5", (500, "err"))
        )
        c = _client(db, r)
        res = c._sync_issue_to_github(
            _op(
                "create",
                ResourceType.ISSUE,
                E2G,
                elder_data={"id": 1, "labels": [None]},
            )
        )
        assert "422" in res.errors[0]
        res = c._sync_issue_to_github(
            _op("update", ResourceType.ISSUE, E2G, elder_data={"id": 2})
        )
        assert res.errors == ["No mapping found for issue update"]
        res = c._sync_issue_to_github(
            _op("update", ResourceType.ISSUE, E2G, elder_data={})
        )
        assert res.status == SyncStatus.FAILED
        m = SyncMapping("issue", 3, "github", "5", 1)
        res = c._sync_issue_to_github(
            _op("update", ResourceType.ISSUE, E2G, elder_data={"id": 3}, mapping=m)
        )
        assert "500" in res.errors[0]

    def test_pull_errors(self, db):
        """Pull validates payload, org mapping and create permission."""
        c = _client(db)
        r = c._sync_issue_from_github(
            _op("update", ResourceType.ISSUE, G2E, external_data={})
        )
        assert "missing 'number'" in r.errors[0]
        r = _client(db, elder_organization_id=None)._sync_issue_from_github(
            _op("update", ResourceType.ISSUE, G2E, external_data={"number": 1})
        )
        assert r.status == SyncStatus.FAILED
        r = c._sync_issue_from_github(
            _op("create", ResourceType.ISSUE, G2E, external_data={"number": 1})
        )
        assert r.metadata["reason"] == "two_way_create_disabled"
        r = c._sync_issue_from_github(
            _op("update", ResourceType.ISSUE, G2E, external_data={"number": 1})
        )
        assert r.errors == ["No mapping found for GitHub issue"]

    def test_pull_create_update(self, db):
        """Pull creates (when allowed), recurses from update, and updates mapped issues."""
        _allow_create(db)
        c = _client(db)
        gh_issue = {
            "number": 21,
            "title": "Crash",
            "body": "b",
            "state": "closed",
            "closed_at": "2026-01-05T00:00:00Z",
            "updated_at": "2026-01-06T00:00:00Z",
            "labels": [{"name": "bug"}, {"name": "priority:high"}],
            "milestone": {"number": 2, "title": "M2", "state": "open"},
        }
        r = c._sync_issue_from_github(
            _op("create", ResourceType.ISSUE, G2E, external_data=gh_issue)
        )
        assert r.status == SyncStatus.SUCCESS and r.created_mappings
        row = db(db.issues.id == r.created_mappings[0].elder_id).select().first()
        assert (
            row.status == "CLOSED"
            and row.priority == "HIGH"
            and row.issue_type == "BUG"
        )
        # update path with mapping
        gh_issue["state"] = "open"
        r = c._sync_issue_from_github(
            _op("update", ResourceType.ISSUE, G2E, external_data=gh_issue)
        )
        assert r.updated_mappings
        # recursion: mapping absent, create allowed
        other = {"number": 22, "title": "New"}
        r = c._sync_issue_from_github(
            _op("update", ResourceType.ISSUE, G2E, external_data=other)
        )
        assert r.created_mappings

    def test_bidirectional(self, db):
        """Bidirectional sync records unresolved conflicts and picks winners."""
        r = Router().add("PATCH", f"{R}/issues/5", (200, {}))
        c = _client(db, r)
        c._upsert_mapping(ResourceType.ISSUE, 3, "5")
        c._upsert_mapping(ResourceType.ISSUE, 3, "5", elder_updated_at=NOW)
        mapping = c.get_mapping(ResourceType.ISSUE, elder_id=3)
        res = c.sync_issue(
            _op(
                "update",
                ResourceType.ISSUE,
                BID,
                elder_data={"deleted": True},
                external_data={"number": 5},
                mapping=mapping,
            )
        )
        assert (
            res.status == SyncStatus.CONFLICT
            and db(db.sync_conflicts.id > 0).count() == 1
        )
        res = c._sync_issue_bidirectional(
            _op(
                "update",
                ResourceType.ISSUE,
                BID,
                elder_data={"deleted": True},
                external_data={},
                mapping=SyncMapping("issue", 777, "github", "9", 1),
            )
        )
        assert (
            res.status == SyncStatus.CONFLICT
            and db(db.sync_conflicts.id > 0).count() == 1
        )
        old, new = "2026-01-01T00:00:00Z", "2026-03-01T00:00:00Z"
        mapping.last_synced_at = datetime(2025, 1, 1, tzinfo=UTC)
        res = c._sync_issue_bidirectional(
            _op(
                "update",
                ResourceType.ISSUE,
                BID,
                elder_data={"id": 3, "updated_at": new},
                external_data={"number": 5, "updated_at": old},
                mapping=mapping,
            )
        )
        assert res.status == SyncStatus.SUCCESS  # elder newer -> pushed
        _allow_create(db)
        res = c._sync_issue_bidirectional(
            _op(
                "update",
                ResourceType.ISSUE,
                BID,
                elder_data={"id": 3, "updated_at": old},
                external_data={"number": 5, "updated_at": new},
                mapping=mapping,
            )
        )
        assert res.status == SyncStatus.SUCCESS  # external newer -> pulled
        res = c._sync_issue_bidirectional(
            _op(
                "update",
                ResourceType.ISSUE,
                BID,
                external_data={"number": 5},
                mapping=mapping,
            )
        )
        assert res.status == SyncStatus.SUCCESS


class TestMilestones:
    """Tests for milestone sync and the upsert helper."""

    def test_push(self, db):
        """Milestone push creates, updates and reports errors."""
        r = (
            Router()
            .add("POST", f"{R}/milestones", (201, {"number": 7}), (422, "bad"))
            .add(
                "PATCH",
                f"{R}/milestones/7",
                (200, {"updated_at": "2026-01-01T00:00:00Z"}),
                (500, "e"),
            )
        )
        c = _client(db, r)
        el = {"id": 1, "title": "M", "status": "closed", "due_date": "2026-05-01"}
        assert c.sync_milestone(
            _op("create", ResourceType.MILESTONE, E2G, elder_data=el)
        ).created_mappings
        res = c.sync_milestone(
            _op("create", ResourceType.MILESTONE, E2G, elder_data={"id": 2})
        )
        assert "422" in res.errors[0]
        assert c.sync_milestone(
            _op("update", ResourceType.MILESTONE, E2G, elder_data=el)
        ).updated_mappings
        res = c.sync_milestone(
            _op("update", ResourceType.MILESTONE, E2G, elder_data=el)
        )
        assert "500" in res.errors[0]
        res = c.sync_milestone(
            _op("update", ResourceType.MILESTONE, E2G, elder_data={"id": 9})
        )
        assert res.errors == ["No mapping found for milestone update"]

    def test_pull(self, db):
        """Milestone pull validates payload, gating, org and upserts."""
        c = _client(db)
        res = c.sync_milestone(
            _op("update", ResourceType.MILESTONE, G2E, external_data={})
        )
        assert "missing 'number'" in res.errors[0]
        res = c.sync_milestone(
            _op("update", ResourceType.MILESTONE, G2E, external_data={"number": 1})
        )
        assert res.metadata["skipped"]
        _allow_create(db)
        bad = _client(db, elder_organization_id=None)
        res = bad.sync_milestone(
            _op("update", ResourceType.MILESTONE, G2E, external_data={"number": 1})
        )
        assert res.status == SyncStatus.FAILED
        gm = {
            "number": 1,
            "title": "M",
            "state": "closed",
            "due_on": "2026-05-01T00:00:00Z",
            "closed_at": "2026-05-02T00:00:00Z",
        }
        assert (
            c.sync_milestone(
                _op("create", ResourceType.MILESTONE, G2E, external_data=gm)
            ).items_synced
            == 1
        )
        gm["title"] = "Renamed"
        assert (
            c.sync_milestone(
                _op("update", ResourceType.MILESTONE, G2E, external_data=gm)
            ).items_synced
            == 1
        )
        assert db(db.milestones.id > 0).select().first().title == "Renamed"

    def test_exception(self, db):
        """Unexpected errors become FAILED."""
        c = _client(db, repo_name="")
        res = c.sync_milestone(
            _op("create", ResourceType.MILESTONE, E2G, elder_data={"id": 1})
        )
        assert res.status == SyncStatus.FAILED


class TestLabels:
    """Tests for label sync."""

    def test_push_create_variants(self, db):
        """Label creation handles success, already_exists recovery and failures."""
        r = (
            Router()
            .add(
                "POST",
                f"{R}/labels",
                (201, {"name": "bug"}),
                (422, "already_exists"),
                (422, "already_exists"),
                (500, "e"),
            )
            .add("GET", f"{R}/labels/bug", (200, {}))
            .add("PATCH", f"{R}/labels/bug", (200, {"name": "bug"}), (500, "pe"))
        )
        c = _client(db, r)
        el = {"id": 1, "name": "bug", "color": "#ff0000", "description": "d"}
        assert c.sync_label(
            _op("create", ResourceType.LABEL, E2G, elder_data=el)
        ).created_mappings
        el2 = {"id": 2, "name": "bug"}
        assert c.sync_label(
            _op("create", ResourceType.LABEL, E2G, elder_data=el2)
        ).created_mappings
        res = c.sync_label(
            _op("create", ResourceType.LABEL, E2G, elder_data={"id": 3, "name": "bug"})
        )
        assert "500" in res.errors[0]
        res = c.sync_label(
            _op("create", ResourceType.LABEL, E2G, elder_data={"id": 4, "name": "bug"})
        )
        assert "500" in res.errors[0]

    def test_push_update(self, db):
        """Label update patches by mapped name; missing mapping fails."""
        r = Router().add("PATCH", f"{R}/labels/old", (200, {"name": "new"}), (404, "x"))
        c = _client(db, r)
        c._upsert_mapping(ResourceType.LABEL, 1, "old")
        el = {"id": 1, "name": "new"}
        assert c.sync_label(
            _op("update", ResourceType.LABEL, E2G, elder_data=el)
        ).updated_mappings
        c._upsert_mapping(ResourceType.LABEL, 1, "old")
        res = c.sync_label(_op("update", ResourceType.LABEL, E2G, elder_data=el))
        assert "404" in res.errors[0]
        res = c.sync_label(
            _op("update", ResourceType.LABEL, E2G, elder_data={"id": 5, "name": "z"})
        )
        assert res.errors == ["No mapping found for label update"]
        res = c.sync_label(_op("update", ResourceType.LABEL, E2G, elder_data={}))
        assert res.status == SyncStatus.FAILED

    def test_pull(self, db):
        """Label pull validates, gates creation and upserts by mapping or name."""
        c = _client(db)
        assert (
            "missing 'name'"
            in c.sync_label(_op("u", ResourceType.LABEL, G2E, external_data={})).errors[
                0
            ]
        )
        res = c.sync_label(
            _op("u", ResourceType.LABEL, G2E, external_data={"name": "x"})
        )
        assert res.metadata["skipped"]
        _allow_create(db)
        assert (
            c.sync_label(
                _op(
                    "u",
                    ResourceType.LABEL,
                    G2E,
                    external_data={"name": "x", "color": "00ff00"},
                )
            ).items_synced
            == 1
        )
        # second call finds the mapping and updates
        assert (
            c.sync_label(
                _op(
                    "u",
                    ResourceType.LABEL,
                    G2E,
                    external_data={"name": "x", "color": "0000ff"},
                )
            ).items_synced
            == 1
        )
        # rename via previous_name
        res = c.sync_label(
            _op(
                "u",
                ResourceType.LABEL,
                G2E,
                external_data={"name": "y"},
                metadata={"previous_name": "x"},
            )
        )
        assert res.items_synced == 1
        # existing row by name but no mapping
        db.issue_labels.insert(
            name="pre", color="#000000", created_at=NOW, updated_at=NOW
        )
        db.commit()
        assert (
            c.sync_label(
                _op("u", ResourceType.LABEL, G2E, external_data={"name": "pre"})
            ).items_synced
            == 1
        )

    def test_exception(self, db):
        """Unexpected errors become FAILED."""
        c = _client(db, repo_name="")
        assert (
            c.sync_label(
                _op(
                    "create", ResourceType.LABEL, E2G, elder_data={"id": 1, "name": "a"}
                )
            ).status
            == SyncStatus.FAILED
        )


class TestProjects:
    """Tests for GraphQL project sync."""

    GQL = "/graphql"

    def _proj(self, **kw):
        """Return a project node."""
        node = {
            "id": "PVT_1",
            "title": "P",
            "shortDescription": "sd",
            "closed": False,
            "updatedAt": "2026-01-01T00:00:00Z",
        }
        node.update(kw)
        return node

    def test_graphql_and_owner(self, db):
        """_graphql raises on HTTP errors; owner id falls back from org to user."""
        r = Router().add("POST", self.GQL, (500, "x"))
        with pytest.raises(RuntimeError, match="HTTP 500"):
            _client(db, r)._graphql("q")
        r = Router().add(
            "POST", self.GQL, (200, {"data": {"organization": {"id": "O1"}}})
        )
        assert _client(db, r)._fetch_graphql_owner_id() == "O1"
        r = Router().add(
            "POST",
            self.GQL,
            (200, {"data": {"organization": None}}),
            (200, {"data": {"user": {"id": "U1"}}}),
        )
        assert _client(db, r)._fetch_graphql_owner_id() == "U1"
        r = Router().add("POST", self.GQL, (200, {"data": {}, "errors": ["nope"]}))
        with pytest.raises(RuntimeError, match="Unable to resolve"):
            _client(db, r)._fetch_graphql_owner_id()

    def test_push_create(self, db):
        """Project create resolves owner, creates and optionally updates the project."""
        created = {"data": {"createProjectV2": {"projectV2": self._proj()}}}
        updated = {"data": {"updateProjectV2": {"projectV2": self._proj(title="P2")}}}
        owner = {"data": {"organization": {"id": "O1"}}}
        r = Router().add(
            "POST",
            self.GQL,
            (200, owner),
            (200, created),
            (200, updated),
            (200, owner),
            (200, created),
            (200, {"data": {}}),
        )
        c = _client(db, r)
        el = {
            "id": 1,
            "name": "P",
            "description": "d",
            "status": "archived",
            "updated_at": "2026-01-01T00:00:00Z",
        }
        assert c.sync_project(
            _op("create", ResourceType.PROJECT, E2G, elder_data=el)
        ).created_mappings
        c2 = _client(db, Router().add("POST", self.GQL, (200, owner), (200, created)))
        res = c2.sync_project(
            _op("create", ResourceType.PROJECT, E2G, elder_data={"id": 3, "title": "T"})
        )
        assert res.created_mappings

    def test_push_create_errors(self, db):
        """Project create surfaces GraphQL errors and missing nodes."""
        owner = (200, {"data": {"organization": {"id": "O1"}}})
        c = _client(
            db, Router().add("POST", self.GQL, owner, (200, {"errors": ["boom"]}))
        )
        assert (
            "boom"
            in c.sync_project(
                _op("create", ResourceType.PROJECT, E2G, elder_data={"id": 1})
            ).errors[0]
        )
        c = _client(db, Router().add("POST", self.GQL, owner, (200, {"data": {}})))
        assert (
            "no projectV2"
            in c.sync_project(
                _op("create", ResourceType.PROJECT, E2G, elder_data={"id": 1})
            ).errors[0]
        )

    def test_push_update(self, db):
        """Project update succeeds, fails on errors, and requires a mapping."""
        upd = (200, {"data": {"updateProjectV2": {"projectV2": self._proj()}}})
        r = Router().add(
            "POST", self.GQL, upd, (200, {"errors": ["bad"]}), (200, {"data": None})
        )
        c = _client(db, r)
        c._upsert_mapping(ResourceType.PROJECT, 1, "PVT_1")
        el = {"id": 1, "name": "P"}
        assert c.sync_project(
            _op("update", ResourceType.PROJECT, E2G, elder_data=el)
        ).updated_mappings
        res = c.sync_project(_op("update", ResourceType.PROJECT, E2G, elder_data=el))
        assert "bad" in res.errors[0]
        r.routes[("POST", self.GQL)] = [(200, {"data": {}})]
        assert c.sync_project(
            _op("update", ResourceType.PROJECT, E2G, elder_data=el)
        ).updated_mappings
        res = c.sync_project(
            _op("update", ResourceType.PROJECT, E2G, elder_data={"id": 77})
        )
        assert res.errors == ["No mapping found for project update"]

    def test_pull(self, db):
        """Project pull validates and upserts."""
        c = _client(db)
        assert (
            "missing 'id'"
            in c.sync_project(
                _op("u", ResourceType.PROJECT, G2E, external_data={})
            ).errors[0]
        )
        bad = _client(db, elder_organization_id=None)
        assert (
            bad.sync_project(
                _op("u", ResourceType.PROJECT, G2E, external_data=self._proj())
            ).status
            == SyncStatus.FAILED
        )
        assert (
            c.sync_project(
                _op("u", ResourceType.PROJECT, G2E, external_data=self._proj())
            ).items_synced
            == 1
        )
        assert (
            c.sync_project(
                _op(
                    "u",
                    ResourceType.PROJECT,
                    G2E,
                    external_data=self._proj(
                        closed=True, shortDescription=None, readme="rd"
                    ),
                )
            ).items_synced
            == 1
        )
        assert db(db.projects.id > 0).select().first().status == "archived"

    def test_exception(self, db):
        """Unexpected errors become FAILED."""
        c = _client(db, Router().add("POST", self.GQL, (500, "x")))
        res = c.sync_project(
            _op("create", ResourceType.PROJECT, E2G, elder_data={"id": 1})
        )
        assert res.status == SyncStatus.FAILED


class TestBatch:
    """Tests for batch sync routing, pull and push helpers."""

    def test_routing(self, db):
        """batch_sync validates direction/resource and composes bidirectional runs."""
        c = _client(db)
        r = c.batch_sync(ResourceType.ISSUE, direction="sideways")
        assert "Unsupported sync direction" in r.errors[0]
        r = c.batch_sync(ResourceType.COMMENT)
        assert "Unsupported resource type" in r.errors[0]
        from unittest.mock import MagicMock

        ok = c._aggregate_result(ResourceType.ISSUE, G2E, 2, 0, [])
        bad = c._aggregate_result(ResourceType.ISSUE, E2G, 0, 1, ["e"])
        part = c._aggregate_result(ResourceType.ISSUE, E2G, 1, 1, ["e"])
        assert ok.status == SyncStatus.SUCCESS and bad.status == SyncStatus.FAILED
        assert part.status == SyncStatus.PARTIAL_SUCCESS
        c._batch_pull_issues = MagicMock(return_value=ok)
        c._batch_push_issues = MagicMock(return_value=ok)
        assert (
            c.batch_sync(ResourceType.ISSUE, direction="external_to_elder").items_synced
            == 2
        )
        assert (
            c.batch_sync(ResourceType.ISSUE, direction="elder_to_external").items_synced
            == 2
        )
        assert (
            c.batch_sync(
                ResourceType.ISSUE, since=NOW, direction="bidirectional"
            ).status
            == SyncStatus.SUCCESS
        )
        c._batch_push_issues = MagicMock(return_value=part)
        assert (
            c.batch_sync(ResourceType.ISSUE, direction="bidirectional").status
            == SyncStatus.PARTIAL_SUCCESS
        )
        c._batch_pull_issues = MagicMock(return_value=bad)
        c._batch_push_issues = MagicMock(return_value=bad)
        assert (
            c.batch_sync(ResourceType.ISSUE, direction="").status == SyncStatus.FAILED
        )
        c._batch_pull_issues = MagicMock(return_value=bad)
        assert (
            c.batch_sync(ResourceType.ISSUE, direction="bidirectional").status
            == SyncStatus.FAILED
        )

    def test_pull_issues(self, db):
        """Issue pull skips PRs, handles errors, and paginates."""
        _allow_create(db)
        items = [{"number": 1, "title": "a"}, {"number": 2, "pull_request": {}}]
        r = Router().add("GET", f"{R}/issues", (200, items))
        c = _client(db, r)
        res = c._batch_pull_issues(since=NOW)
        assert res.items_synced == 1 and "since" in str(r.calls[0].url)
        # second pull: now mapped -> update
        assert c._batch_pull_issues().items_synced == 1
        full = [{"number": i, "title": "t"} for i in range(100, 200)]
        r = Router().add("GET", f"{R}/issues", (200, full), (200, []))
        assert _client(db, r)._batch_pull_issues().items_synced == 100
        assert len(r.calls) == 2
        assert (
            _client(db, Router().add("GET", f"{R}/issues", (500, "x")))
            ._batch_pull_issues()
            .items_failed
            == 1
        )
        bad_org = _client(
            db,
            Router().add("GET", f"{R}/issues", (200, [{"number": 5}])),
            elder_organization_id=None,
        )
        res = bad_org._batch_pull_issues()
        assert res.items_failed == 1 and res.errors
        boom = Router()
        boom.routes[("GET", f"{R}/issues")] = [RuntimeError("x")]
        assert (
            "Batch issue pull error" in _client(db, boom)._batch_pull_issues().errors[0]
        )

    def test_push_issues(self, db):
        """Issue push covers org errors, labels, milestones and failures."""
        res = _client(db, elder_organization_id=None)._batch_push_issues()
        assert res.items_failed == 1
        iid = db.issues.insert(
            title="I",
            description="d",
            status="OPEN",
            priority="LOW",
            organization_id=1,
            created_at=NOW,
            updated_at=NOW,
        )
        db.issues.insert(
            title="J",
            status="OPEN",
            resource_type="organization",
            resource_id=1,
            created_at=NOW,
            updated_at=NOW,
        )
        mid = db.milestones.insert(
            title="M", organization_id=1, created_at=NOW, updated_at=NOW
        )
        db.issue_milestone_links.insert(issue_id=iid, milestone_id=mid)
        db.commit()
        r = Router().add("POST", f"{R}/issues", (201, {"number": 1}), (500, "no"))
        c = _client(db, r)
        c.db = _JoinShim(db)
        res = c._batch_push_issues()
        assert res.items_synced == 1 and res.items_failed == 1
        assert json.loads(r.calls[0].content)["labels"] == ["bug"]

    @pytest.mark.xfail(
        strict=True,
        reason="bug: _batch_push_issues label join compares two columns, which penguin-dal binds as a parameter and fails",
    )
    def test_push_issues_real_join(self, db):
        """The label join in _batch_push_issues should work against a real DAL."""
        db.issues.insert(
            title="I", status="OPEN", organization_id=1, created_at=NOW, updated_at=NOW
        )
        db.commit()
        r = Router().add("POST", f"{R}/issues", (201, {"number": 1}))
        res = _client(db, r)._batch_push_issues()
        assert res.items_synced == 1

    def test_pull_push_milestones(self, db):
        """Milestone batch pull and push cover success and failure paths."""
        _allow_create(db)
        r = Router().add("GET", f"{R}/milestones", (200, [{"number": 1, "title": "m"}]))
        c = _client(db, r)
        assert c._batch_pull_milestones().items_synced == 1
        assert c._batch_pull_milestones().items_synced == 1
        r = Router().add(
            "GET",
            f"{R}/milestones",
            (200, [{"number": i} for i in range(100)]),
            (200, []),
        )
        assert _client(db, r)._batch_pull_milestones().items_synced == 100
        assert (
            _client(db, Router().add("GET", f"{R}/milestones", (500, "x")))
            ._batch_pull_milestones()
            .items_failed
            == 1
        )
        boom = Router()
        boom.routes[("GET", f"{R}/milestones")] = [RuntimeError("x")]
        assert _client(db, boom)._batch_pull_milestones().items_failed == 1
        bad = _client(
            db,
            Router().add("GET", f"{R}/milestones", (200, [{"number": 9}])),
            elder_organization_id=None,
        )
        assert bad._batch_pull_milestones().items_failed == 1
        assert (
            _client(db, elder_organization_id=None)
            ._batch_push_milestones()
            .items_failed
            == 1
        )
        db.milestones.insert(
            title="pm", organization_id=1, created_at=NOW, updated_at=NOW
        )
        db.commit()
        r = Router().add("POST", f"{R}/milestones", (201, {"number": 50}), (500, "x"))
        res = _client(db, r)._batch_push_milestones()
        assert (
            res.items_synced + res.items_failed
            == db(db.milestones.organization_id == 1).count()
        )

    def test_pull_push_labels(self, db):
        """Label batch pull and push cover success and failure paths."""
        _allow_create(db)
        r = Router().add("GET", f"{R}/labels", (200, [{"name": "a"}]))
        c = _client(db, r)
        assert c._batch_pull_labels().items_synced == 1
        assert c._batch_pull_labels().items_synced == 1
        r = Router().add(
            "GET",
            f"{R}/labels",
            (200, [{"name": f"l{i}"} for i in range(100)]),
            (200, []),
        )
        assert _client(db, r)._batch_pull_labels().items_synced == 100
        assert (
            _client(db, Router().add("GET", f"{R}/labels", (500, "x")))
            ._batch_pull_labels()
            .items_failed
            == 1
        )
        boom = Router()
        boom.routes[("GET", f"{R}/labels")] = [RuntimeError("x")]
        assert _client(db, boom)._batch_pull_labels().items_failed == 1
        bad = _client(db, Router().add("GET", f"{R}/labels", (200, [{"name": ""}])))
        assert bad._batch_pull_labels().items_failed == 1
        db.issue_labels.insert(name="zz", color="#000", created_at=NOW, updated_at=NOW)
        db.commit()
        r = Router().add("POST", f"{R}/labels", (201, {"name": "zz"}), (500, "x"))
        res = _client(db, r)._batch_push_labels()
        assert res.items_synced >= 1

    def test_pull_projects(self, db):
        """Project pull handles org, user fallback, pagination and errors."""
        gql = "/graphql"
        node = {"id": "P1", "title": "t"}
        org = {
            "data": {
                "organization": {
                    "projectsV2": {
                        "nodes": [node, None, {"title": "noid"}],
                        "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                    }
                }
            }
        }
        page2 = {
            "data": {
                "organization": {
                    "projectsV2": {
                        "nodes": [{"id": "P2"}],
                        "pageInfo": {"hasNextPage": False},
                    }
                }
            }
        }
        r = Router().add("POST", gql, (200, org), (200, page2))
        res = _client(db, r)._batch_pull_projects()
        assert res.items_synced == 2
        user = {
            "data": {"user": {"projectsV2": {"nodes": [{"id": "P3"}], "pageInfo": {}}}}
        }
        r = Router().add(
            "POST", gql, (200, {"data": {"organization": None}}), (200, user)
        )
        assert _client(db, r)._batch_pull_projects().items_synced == 1
        r = Router().add(
            "POST",
            gql,
            (200, {"data": {"organization": None}}),
            (200, {"errors": ["x"]}),
        )
        res = _client(db, r)._batch_pull_projects()
        assert res.items_failed == 1
        assert (
            _client(db, Router().add("POST", gql, (500, "x")))
            ._batch_pull_projects()
            .items_failed
            == 1
        )
        one = {
            "data": {"organization": {"projectsV2": {"nodes": [node], "pageInfo": {}}}}
        }
        bad = _client(
            db, Router().add("POST", gql, (200, one)), elder_organization_id=None
        )
        assert bad._batch_pull_projects().items_failed == 1

    def test_push_projects(self, db):
        """Project push covers org error and per-row results."""
        assert (
            _client(db, elder_organization_id=None)._batch_push_projects().items_failed
            == 1
        )
        db.projects.insert(
            name="p", status="active", organization_id=1, created_at=NOW, updated_at=NOW
        )
        db.commit()
        owner = (200, {"data": {"organization": {"id": "O"}}})
        created = (200, {"data": {"createProjectV2": {"projectV2": {"id": "N1"}}}})
        r = Router().add("POST", "/graphql", owner, created)
        assert _client(db, r)._batch_push_projects().items_synced == 1
        r = Router().add("POST", "/graphql", (500, "x"))
        assert _client(db, r)._batch_push_projects().items_failed == 1


class TestWebhook:
    """Tests for handle_webhook and label/priority mapping."""

    def test_ping_and_ignored(self, db):
        """Ping events and unsupported payloads are acknowledged."""
        c = _client(db)
        assert (
            c.handle_webhook({"_event": "ping", "zen": "z"}).metadata["event"] == "ping"
        )
        assert c.handle_webhook({"zen": "z", "hook_id": 1}).metadata["event"] == "ping"
        assert c.handle_webhook({"foo": 1}).metadata["ignored"]

    def test_issue_events(self, db):
        """Issue events create, update and delete."""
        _allow_create(db)
        c = _client(db)
        issue = {"number": 31, "title": "T"}
        assert c.handle_webhook({"action": "opened", "issue": issue}).created_mappings
        assert c.handle_webhook({"action": "edited", "issue": issue}).updated_mappings
        assert c.handle_webhook({"action": "weird", "issue": issue}).updated_mappings
        res = c.handle_webhook({"action": "deleted", "issue": issue})
        assert res.metadata == {"deleted": True, "external_id": "31"}

    def test_milestone_events(self, db):
        """Milestone events create, update and delete."""
        _allow_create(db)
        c = _client(db)
        ms = {"number": 4, "title": "M"}
        assert (
            c.handle_webhook({"action": "created", "milestone": ms}).items_synced == 1
        )
        assert c.handle_webhook({"action": "edited", "milestone": ms}).items_synced == 1
        assert c.handle_webhook({"action": "deleted", "milestone": ms}).metadata[
            "deleted"
        ]

    def test_label_events(self, db):
        """Label events create, rename, update and delete."""
        _allow_create(db)
        c = _client(db)
        lbl = {"name": "bug"}
        assert c.handle_webhook({"action": "created", "label": lbl}).items_synced == 1
        renamed = {
            "action": "edited",
            "label": {"name": "defect"},
            "changes": {"name": {"from": "bug"}},
        }
        assert c.handle_webhook(renamed).items_synced == 1
        assert (
            c.handle_webhook(
                {"action": "edited", "label": {"name": "defect"}}
            ).items_synced
            == 1
        )
        assert c.handle_webhook({"action": "deleted", "label": lbl}).metadata["deleted"]

    @pytest.mark.parametrize(
        ("labels", "prio", "kind"),
        [
            (["urgent"], "CRITICAL", "OTHER"),
            ([{"name": "priority:high"}, {"name": ""}], "HIGH", "OTHER"),
            (["sev/low"], "MEDIUM", "OTHER"),
            (["priority/low"], "LOW", "OTHER"),
            ([], "MEDIUM", "OTHER"),
            (["security"], "MEDIUM", "SECURITY"),
            (["bug"], "MEDIUM", "BUG"),
            (["enhancement"], "MEDIUM", "FEATURE"),
            (["ops"], "MEDIUM", "OPERATIONS"),
            (["config"], "MEDIUM", "CONFIG"),
            ([{"name": "question"}], "MEDIUM", "SUPPORT"),
        ],
    )
    def test_label_mapping(self, db, labels, prio, kind):
        """Labels map to Elder priority and issue type enums."""
        c = _client(db)
        assert c._map_github_labels_to_priority(labels) == prio
        assert c._map_github_labels_to_issue_type(labels) == kind
