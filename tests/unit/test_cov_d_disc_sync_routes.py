"""Route and helper tests for the sync API (``/api/v1/sync``).

Routes run through the real Quart app and Postgres schema.  Platform sync
clients talk to third-party HTTP APIs, so ``_build_sync_client`` is replaced by
a small fake client for the connectivity, trigger and webhook paths; the
validators and merge helpers are exercised directly.
"""

# flake8: noqa: E501

import hashlib
import hmac
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from apps.api.modules.discovery.routes import sync as sync_mod
from apps.worker.sync.base import (
    ResourceType,
    SyncDirection,
    SyncOperation,
    SyncResult,
    SyncStatus,
)
from shared.redaction import REDACTED_PLACEHOLDER
from tests.unit._cov_d_helpers import call, fake_secret, in_db, now, world  # noqa: F401

BASE = "/api/v1/sync"
SCOPES = ["discovery:read", "discovery:write", "discovery:admin"]


def _op():
    """Return a throwaway SyncOperation for building SyncResult objects."""
    return SyncOperation(
        operation_type="batch",
        resource_type=ResourceType.ISSUE,
        direction=SyncDirection.EXTERNAL_TO_ELDER,
    )


def _result(status=SyncStatus.SUCCESS, **kw):
    """Build a SyncResult with the given status and fields."""
    return SyncResult(status=status, operation=_op(), **kw)


def _pem():
    """Generate an unencrypted RSA PEM private key at runtime."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


class FakeClient:
    """Stand-in third-party sync client (the external HTTP boundary)."""

    def __init__(self, valid=True, with_direction=True, results=None, webhook=None):
        """Store behaviour switches and track whether close() was called."""
        self.valid = valid
        self.closed = False
        self.results = results or {}
        self.webhook = webhook or _result()
        self.calls = []
        if with_direction:
            self.batch_sync = self._batch_dir
        else:
            self.batch_sync = self._batch_nodir

    def validate_config(self):
        """Report whether the configuration is usable."""
        return self.valid

    def _batch_dir(self, resource_type, since=None, direction="pull"):
        """Record the call and return a canned per-type result."""
        self.calls.append((resource_type, since, direction))
        return self.results.get(resource_type, _result(items_synced=1))

    def _batch_nodir(self, resource_type, since=None):
        """Variant of batch_sync without a direction parameter."""
        self.calls.append((resource_type, since))
        return self.results.get(resource_type, _result(items_synced=1))

    def test_connection_detail(self):
        """Return a canned connectivity result."""
        return {"success": True, "message": "ok"}

    def handle_webhook(self, data):
        """Return the canned webhook result."""
        self.calls.append(("webhook", data))
        return self.webhook

    def close(self):
        """Mark the client as closed."""
        self.closed = True


@pytest_asyncio.fixture
async def sworld(world):  # noqa: F811
    """Promote the tenant-A identity to portal admin and mint tokens."""

    def _promote():
        """Set portal_role=admin for the tenant-A user."""
        db = world["db"]
        db(db.identities.id == world["a"]["user"]).update(portal_role="admin")
        db.commit()

    await in_db(_promote)
    world["admin"] = world["tok"]("a", SCOPES)
    world["reader"] = world["tok"]("a", ["discovery:read"])
    return world


async def _mk_config(app, tok, platform="gitlab", **extra):
    """Create a sync config through the API and return its JSON."""
    body = {"name": fake_secret("cfg"), "platform": platform, "config_json": {}}
    body.update(extra)
    status, data = await call(app, "post", f"{BASE}/configs", tok, json=body)
    assert status == 201, data
    return data["config"]


# ---------------------------------------------------------------- helpers


def test_is_blank_or_redacted():
    """None, blank and sentinel strings are blank; other values are not."""
    f = sync_mod._is_blank_or_redacted
    assert f(None) and f("  ") and f(REDACTED_PLACEHOLDER)
    assert not f("x") and not f(0) and not f({"a": 1})


def test_merge_config_json_preserves_credentials():
    """Blank or redacted credential values are replaced by the stored ones."""
    existing = {"api_token": "stored", "owner": "o", "Password": "p"}
    merged = sync_mod._merge_config_json(
        existing, {"api_token": REDACTED_PLACEHOLDER, "owner": "new", "Password": "np"}
    )
    assert merged["api_token"] == "stored"
    assert merged["owner"] == "new"
    assert merged["Password"] == "np"
    assert sync_mod._merge_config_json(None, {"a": 1}) == {"a": 1}
    assert sync_mod._merge_config_json("bad", {"a": 1}) == {"a": 1}


def test_validate_platform_config_common_rules():
    """resource_types and direction are validated for every platform."""
    v = sync_mod._validate_platform_config
    assert v("gitlab", []) == "config_json must be a JSON object"
    assert "resource_types" in v("gitlab", {"resource_types": "issue"})
    assert "resource_types" in v("gitlab", {"resource_types": ["nope"]})
    assert "direction" in v("gitlab", {"direction": 5})
    assert "direction" in v("gitlab", {"direction": "sideways"})
    assert v("gitlab", {"resource_types": ["issue"], "direction": "PULL"}) is None
    assert v("trello", {}) is None


def test_validate_platform_config_github():
    """GitHub PAT and GitHub App configs are validated field by field."""
    v = sync_mod._validate_platform_config
    assert "requires owner" in v("github", {})
    assert "requires owner" in v("github", {"owner": "o"})
    assert "auth_mode" in v(
        "github", {"owner": "o", "repo_name": "r", "auth_mode": "x"}
    )
    assert "api_token" in v("github", {"owner": "o", "repo_name": "r"})
    assert "api_token" in v(
        "github", {"org_name": "o", "repo_name": "r", "api_token": REDACTED_PLACEHOLDER}
    )
    assert v("github", {"owner": "o", "repo_name": "r", "api_token": "t"}) is None

    base = {"owner": "o", "repo_name": "r", "auth_mode": "github_app"}
    assert "app_id" in v("github", base)
    assert "app_id" in v("github", {**base, "app_id": True})
    assert "app_id" in v("github", {**base, "app_id": 0})
    assert "app_id" in v("github", {**base, "app_id": "  "})
    with_app = {**base, "app_id": 12}
    assert "installation_id" in v("github", with_app)
    with_inst = {**with_app, "installation_id": "5"}
    assert "private_key" in v("github", with_inst)
    assert "PEM" in v("github", {**with_inst, "private_key": "not-a-key"})
    assert v("github", {**with_inst, "private_key": _pem()}) is None


def test_validate_platform_config_jira():
    """Jira needs token, email, url and project key plus a valid label mode."""
    v = sync_mod._validate_platform_config
    full = {
        "api_token": "t",
        "email": "e@example.com",
        "jira_url": "https://j.example.com",
        "project_key": "P",
    }
    assert "Jira config_json requires" in v("jira", {})
    assert "Jira config_json requires" in v("jira", {**full, "email": ""})
    assert "label_mode" in v("jira", {**full, "label_mode": "tags"})
    assert "label_mode" in v("jira", {**full, "label_mode": 3})
    assert v("jira", {**full, "label_mode": "Labels"}) is None
    assert v("jira", full) is None


def test_build_sync_client_and_serializer():
    """Known platforms build real clients; unknown ones raise; results serialize."""
    with pytest.raises(ValueError):
        sync_mod._build_sync_client("nope", {}, None, 1)
    client = sync_mod._build_sync_client("trello", None, None, 1)
    assert type(client).__name__ == "TrelloSyncClient"
    out = sync_mod._serialize_sync_result(
        _result(
            SyncStatus.PARTIAL_SUCCESS, items_synced=2, errors=["e"], metadata={"k": 1}
        )
    )
    assert out["status"] == "partial_success" and out["errors"] == ["e"]
    assert out["metadata"] == {"k": 1}


# ------------------------------------------------------------------ configs


@pytest.mark.asyncio
async def test_config_crud_lifecycle(app, sworld):
    """Create, read, list, patch and delete a sync config with secrets redacted."""
    tok = sworld["admin"]
    secret = fake_secret("hook")
    cfg = await _mk_config(
        app,
        tok,
        "gitlab",
        webhook_secret=f"  {secret}  ",
        config_json={"api_token": "tok-value", "project_id": 3},
    )
    cid = cfg["id"]
    assert cfg["has_webhook_secret"] is True
    assert cfg["config_json"]["api_token"] != "tok-value"

    status, got = await call(app, "get", f"{BASE}/configs/{cid}", sworld["reader"])
    assert status == 200 and got["config"]["id"] == cid
    status, _ = await call(app, "get", f"{BASE}/configs/99999999", sworld["reader"])
    assert status == 404
    status, listed = await call(app, "get", f"{BASE}/configs", sworld["reader"])
    assert status == 200 and cid in {c["id"] for c in listed["configs"]}

    status, dup = await call(
        app,
        "post",
        f"{BASE}/configs",
        tok,
        json={"name": cfg["name"], "platform": "gitlab"},
    )
    assert status == 409

    status, upd = await call(
        app,
        "patch",
        f"{BASE}/configs/{cid}",
        tok,
        json={
            "enabled": False,
            "sync_interval": 60,
            "webhook_secret": REDACTED_PLACEHOLDER,
            "config_json": {"api_token": REDACTED_PLACEHOLDER, "project_id": 4},
            "ignored_field": 1,
        },
    )
    assert status == 200 and upd["config"]["enabled"] is False
    status, upd = await call(
        app, "patch", f"{BASE}/configs/{cid}", tok, json={"clear_webhook_secret": True}
    )
    assert status == 200 and upd["config"]["has_webhook_secret"] is False
    status, upd = await call(
        app, "patch", f"{BASE}/configs/{cid}", tok, json={"webhook_secret": "new-one"}
    )
    assert upd["config"]["has_webhook_secret"] is True

    status, _ = await call(app, "delete", f"{BASE}/configs/{cid}", tok)
    assert status == 200
    status, _ = await call(app, "delete", f"{BASE}/configs/{cid}", tok)
    assert status == 404


@pytest.mark.asyncio
async def test_config_create_and_update_validation(app, sworld):
    """Invalid bodies, platforms and config contents are rejected with 400/404/409."""
    tok = sworld["admin"]
    url = f"{BASE}/configs"
    assert (await call(app, "post", url, tok, data="notjson"))[0] == 400
    assert (await call(app, "post", url, tok, json={"name": "x"}))[0] == 400
    status, data = await call(
        app, "post", url, tok, json={"name": "x", "platform": "bogus"}
    )
    assert status == 400 and "Unsupported platform" in data["error"]
    status, data = await call(
        app,
        "post",
        url,
        tok,
        json={"name": "x", "platform": "github", "config_json": {}},
    )
    assert status == 400 and "GitHub" in data["error"]
    status, _ = await call(
        app, "post", url, tok, json={"name": fake_secret("nocfg"), "platform": "trello"}
    )
    assert status == 201

    cfg = await _mk_config(app, tok, "trello")
    other = await _mk_config(app, tok, "trello")
    one = f"{url}/{cfg['id']}"
    assert (await call(app, "patch", one, tok, data="notjson"))[0] == 400
    assert (await call(app, "patch", f"{url}/99999999", tok, json={"name": "z"}))[
        0
    ] == 404
    assert (await call(app, "patch", one, tok, json={"name": "  "}))[0] == 400
    assert (await call(app, "patch", one, tok, json={"name": other["name"]}))[0] == 409
    assert (await call(app, "patch", one, tok, json={"name": fake_secret("ren")}))[
        0
    ] == 200
    assert (await call(app, "patch", one, tok, json={"config_json": "x"}))[0] == 400
    assert (
        await call(app, "patch", one, tok, json={"config_json": {"direction": "zig"}})
    )[0] == 400


@pytest.mark.asyncio
async def test_config_auth_enforced(app, sworld):
    """Writes need admin scope+role; reads need discovery:read; anonymous gets 401."""
    url = f"{BASE}/configs"
    assert (await call(app, "get", url, None))[0] == 401
    assert (await call(app, "get", url, sworld["tok"]("a", [])))[0] == 403
    assert (await call(app, "post", url, sworld["reader"], json={}))[0] == 403
    obs = sworld["tok"]("b", SCOPES)
    status, data = await call(app, "post", url, obs, json={})
    assert status == 403 and data["your_role"] == "observer"


# -------------------------------------------------------- test connection


@pytest.mark.asyncio
async def test_connection_test_endpoint(app, sworld):
    """The connectivity test reports the client's detail and handles bad input."""
    tok = sworld["admin"]
    cfg = await _mk_config(app, tok, "trello")
    url = f"{BASE}/configs/{cfg['id']}/test"
    fake = FakeClient()
    with patch.object(sync_mod, "_build_sync_client", return_value=fake):
        status, data = await call(app, "post", url, tok)
    assert status == 200 and data["success"] is True and fake.closed
    assert (await call(app, "post", f"{BASE}/configs/99999999/test", tok))[0] == 404
    with patch.object(sync_mod, "_build_sync_client", side_effect=ValueError("bad")):
        status, data = await call(app, "post", url, tok)
    assert status == 400 and data["error"] == "bad"


# ----------------------------------------------------------------- trigger


@pytest.mark.asyncio
async def test_trigger_validation(app, sworld):
    """Invalid trigger parameters and unknown configs are rejected."""
    tok = sworld["admin"]
    cfg = await _mk_config(app, tok, "trello")
    url = f"{BASE}/configs/{cfg['id']}/trigger"
    assert (await call(app, "post", url, tok, json=[1]))[0] == 400
    assert (await call(app, "post", url, tok, json={"resource_types": "issue"}))[
        0
    ] == 400
    assert (await call(app, "post", url, tok, json={"resource_types": ["zz"]}))[
        0
    ] == 400
    assert (await call(app, "post", url, tok, json={"direction": "up"}))[0] == 400
    assert (await call(app, "post", url, tok, json={"direction": 3}))[0] == 400
    assert (await call(app, "post", url, tok, json={"since": "garbage"}))[0] == 400
    assert (await call(app, "post", f"{BASE}/configs/99999999/trigger", tok))[0] == 404
    with patch.object(sync_mod, "_build_sync_client", side_effect=ValueError("bad")):
        assert (await call(app, "post", url, tok))[0] == 400
    fake = FakeClient(valid=False)
    with patch.object(sync_mod, "_build_sync_client", return_value=fake):
        status, data = await call(app, "post", url, tok)
    assert (
        status == 400 and "missing required settings" in data["error"] and fake.closed
    )


@pytest.mark.asyncio
async def test_trigger_runs_batches_and_records_history(app, sworld):
    """A manual trigger syncs every resource type and writes history + timestamps."""
    tok = sworld["admin"]
    cfg = await _mk_config(app, tok, "trello", two_way_create=True)
    url = f"{BASE}/configs/{cfg['id']}/trigger"
    fake = FakeClient()
    with patch.object(sync_mod, "_build_sync_client", return_value=fake):
        status, data = await call(
            app, "post", url, tok, json={"since": "2026-01-01T00:00:00Z"}
        )
    assert status == 200, data
    assert data["success"] is True and data["direction"] == "bidirectional"
    assert data["items_synced"] == 4 and len(fake.calls) == 4

    fake2 = FakeClient(
        with_direction=False,
        results={
            ResourceType.ISSUE: _result(
                SyncStatus.FAILED, items_failed=2, errors=["boom"]
            )
        },
    )
    with patch.object(sync_mod, "_build_sync_client", return_value=fake2):
        status, data = await call(
            app,
            "post",
            url,
            tok,
            json={"resource_types": ["issue"], "direction": "PUSH"},
        )
    assert status == 200
    assert data["success"] is False and data["errors"] == ["boom"]
    assert data["direction"] == "push" and data["items_failed"] == 2

    status, hist = await call(
        app, "get", f"{BASE}/history?config_id={cfg['id']}&sync_type=manual", tok
    )
    assert status == 200 and hist["pagination"]["total"] == 2

    cfg_json = await _mk_config(
        app,
        tok,
        "trello",
        config_json={"direction": "push", "resource_types": ["label"]},
    )
    fake3 = FakeClient()
    with patch.object(sync_mod, "_build_sync_client", return_value=fake3):
        status, data = await call(
            app, "post", f"{BASE}/configs/{cfg_json['id']}/trigger", tok
        )
    assert data["resource_types"] == ["label"] and data["direction"] == "push"


# ----------------------------------------------------------------- webhook


def _hook_headers(secret, body, platform="gitlab"):
    """Return the signature headers a platform would send for ``body``."""
    if platform == "github":
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return {"X-Hub-Signature-256": f"sha256={sig}"}
    return {"X-Gitlab-Token": secret}


@pytest.mark.asyncio
async def test_webhook_receiver(app, sworld):
    """The public receiver verifies signatures and records history for valid hooks."""
    tok = sworld["admin"]
    secret = fake_secret("whk")
    cfg = await _mk_config(
        app,
        tok,
        "gitlab",
        webhook_secret=secret,
        config_json={"api_token": fake_secret("gl")},
    )
    url = f"{BASE}/webhooks/gitlab/{cfg['id']}"

    assert (
        await call(app, "post", f"{BASE}/webhooks/bogus/{cfg['id']}", None, data=b"{}")
    )[0] == 400
    assert (
        await call(app, "post", f"{BASE}/webhooks/gitlab/99999999", None, data=b"{}")
    )[0] == 404
    assert (
        await call(app, "post", f"{BASE}/webhooks/github/{cfg['id']}", None, data=b"{}")
    )[0] == 400

    body = b'{"object_kind": "other"}'
    status, _ = await call(
        app, "post", url, None, data=body, headers={"X-Gitlab-Token": "wrong"}
    )
    assert status == 401
    status, _ = await call(
        app, "post", url, None, data=b"not json", headers=_hook_headers(secret, b"")
    )
    assert status == 400
    status, data = await call(
        app, "post", url, None, data=body, headers=_hook_headers(secret, body)
    )
    assert status == 200 and data["status"] == "success"

    issue = json.dumps(
        {"object_kind": "issue", "object_attributes": {"action": "open", "id": 5}}
    ).encode()
    fake = FakeClient(webhook=_result(SyncStatus.FAILED, errors=["downstream"]))
    with patch.object(sync_mod, "_build_sync_client", return_value=fake):
        status, data = await call(
            app, "post", url, None, data=issue, headers=_hook_headers(secret, issue)
        )
    assert status == 500 and data["errors"] == ["downstream"] and fake.closed
    fake = FakeClient(webhook=_result(items_synced=1))
    with patch.object(sync_mod, "_build_sync_client", return_value=fake):
        status, data = await call(
            app, "post", url, None, data=issue, headers=_hook_headers(secret, issue)
        )
    assert status == 200 and data["items_synced"] == 1


@pytest.mark.asyncio
async def test_webhook_disabled_configs_are_unauthorized(app, sworld):
    """Configs with webhooks off, disabled, or without a secret refuse deliveries."""
    tok = sworld["admin"]
    no_secret = await _mk_config(app, tok, "gitlab")
    off = await _mk_config(
        app, tok, "gitlab", webhook_secret="s", webhook_enabled=False
    )
    disabled = await _mk_config(app, tok, "gitlab", webhook_secret="s", enabled=False)
    for cfg in (no_secret, off, disabled):
        status, data = await call(
            app, "post", f"{BASE}/webhooks/gitlab/{cfg['id']}", None, data=b"{}"
        )
        assert status == 401 and "disabled" in data["error"]


# ------------------------------------------------- history/conflicts/mappings


@pytest.mark.asyncio
async def test_history_conflicts_mappings_status(app, sworld):
    """History paging, conflict listing/resolution, mappings and status summary."""
    tok = sworld["admin"]
    cfg = await _mk_config(app, tok, "trello")
    cid = cfg["id"]
    other = await _mk_config(app, tok, "trello")

    def _seed():
        """Insert two history rows, a mapping and an unresolved conflict."""
        db = sworld["db"]
        t = now()
        for ok_flag in (True, False):
            db.sync_history.insert(
                sync_config_id=cid,
                correlation_id=fake_secret("c"),
                sync_type="webhook",
                items_synced=1,
                items_failed=0 if ok_flag else 1,
                started_at=t,
                completed_at=t,
                success=ok_flag,
            )
        mid = db.sync_mappings.insert(
            elder_type="issue",
            elder_id=1,
            external_platform="trello",
            external_id=fake_secret("ext"),
            sync_config_id=cid,
            sync_status="synced",
            sync_method="batch",
            last_synced_at=t,
            created_at=t,
        )
        conflict = db.sync_conflicts.insert(
            mapping_id=mid,
            conflict_type="concurrent_edit",
            elder_data={},
            external_data={},
            resolved=False,
            created_at=t,
        )
        db.commit()
        return mid, conflict

    mid, conflict = await in_db(_seed)
    status, hist = await call(
        app, "get", f"{BASE}/history?config_id={cid}&page=0&per_page=1", tok
    )
    assert status == 200
    assert hist["pagination"]["per_page"] == 1 and hist["pagination"]["pages"] == 2
    status, hist = await call(app, "get", f"{BASE}/history?per_page=9999", tok)
    assert hist["pagination"]["per_page"] == 200

    status, data = await call(app, "get", f"{BASE}/conflicts?config_id={cid}", tok)
    assert [c["id"] for c in data["conflicts"]] == [conflict]
    status, data = await call(
        app, "get", f"{BASE}/conflicts?config_id={other['id']}", tok
    )
    assert data == {"conflicts": []}
    status, data = await call(app, "get", f"{BASE}/conflicts?resolved=true", tok)
    assert status == 200 and conflict not in {c["id"] for c in data["conflicts"]}

    status, data = await call(
        app, "get", f"{BASE}/mappings?config_id={cid}&elder_type=issue", tok
    )
    assert [m["id"] for m in data["mappings"]] == [mid]
    status, data = await call(app, "get", f"{BASE}/mappings", tok)
    assert status == 200

    status, data = await call(app, "get", f"{BASE}/status", tok)
    assert status == 200
    assert data["configs"]["total"] >= 2 and data["conflicts"]["unresolved"] >= 1
    assert data["recent_activity"]["failures_24h"] >= 1

    res = f"{BASE}/conflicts/{conflict}/resolve"
    assert (
        await call(app, "post", f"{BASE}/conflicts/99999999/resolve", tok, json={})
    )[0] == 404
    status, data = await call(
        app, "post", res, tok, json={"resolution_strategy": "elder_wins"}
    )
    assert status == 200
    assert data["conflict"]["resolved"] is True
    assert data["conflict"]["resolution_strategy"] == "elder_wins"
    status, data = await call(app, "post", res, tok, json=["x"])
    assert status == 200 and data["conflict"]["resolution_strategy"] == "manual"
    status, _ = await call(app, "delete", f"{BASE}/configs/{cid}", tok)
    assert status == 200
    status, data = await call(app, "get", f"{BASE}/mappings?config_id={cid}", tok)
    assert data == {"mappings": []}
