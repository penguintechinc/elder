"""Route tests for ``/api/v1/integrations/internal`` (svc-integrations worker API).

Requests are authenticated with a signed ``service_worker`` token for the
``svc-integrations`` system principal, so the real JWT verification, scope and
identity gates run.  Persistence goes through the real Postgres schema and the
real discovery service; only the structured logger is replaced when a
generic-failure branch needs to be forced.
"""

# flake8: noqa: E501

import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

import jwt
import pytest

from tests.unit._cov_d_helpers import call, fake_secret, in_db, now, vid

BASE = "/api/v1/integrations/internal"
MOD = "apps.api.modules.discovery.routes.integrations_internal"


def _worker_token(app, sub="svc-integrations", scope=None):
    """Mint a signed system service_worker token for ``sub``."""
    issued = now()
    payload = {
        "iss": app.config.get("JWT_ISSUER", "elder-api"),
        "aud": app.config.get("JWT_AUDIENCE", "elder-api"),
        "iat": int(issued.timestamp()),
        "exp": int((issued + timedelta(minutes=5)).timestamp()),
        "sub": sub,
        "type": "service_worker",
        "tenant": "system",
        "scope": ["integrations:sync"] if scope is None else scope,
    }
    secret = app.config.get("JWT_SECRET_KEY") or app.config.get("SECRET_KEY")
    return jwt.encode(
        payload, secret, algorithm=app.config.get("JWT_ALGORITHM", "HS256")
    )


async def _seed_org(app, *, tenant_active=True, org_active=True):
    """Create a tenant + organization and return (tenant_id, org_id)."""

    def _mk():
        """Insert the tenant and organization rows."""
        db = app.db
        sfx = uuid.uuid4().hex[:8]
        t = now()
        tid = db.tenants.insert(
            name=f"cr-{sfx}",
            slug=f"cr-{sfx}",
            is_active=tenant_active,
            created_at=t,
            updated_at=t,
        )
        org = db.organizations.insert(
            name=f"cr-org-{sfx}",
            tenant_id=tid,
            is_active=org_active,
            created_at=t,
            updated_at=t,
        )
        db.commit()
        return tid, org

    return await in_db(_mk)


@pytest.fixture
def wtok(app):
    """Return a valid svc-integrations worker token."""
    return _worker_token(app)


# ------------------------------------------------------------------ pending


@pytest.mark.asyncio
async def test_pending_covers_sync_scoping_and_failure(app, wtok):
    """Pending sync configs are scoped by org/tenant, due-checked and fail closed."""
    tid, org = await _seed_org(app)
    itid, iorg = await _seed_org(app, tenant_active=False)

    def _seed():
        """Insert sync configs exercising each scoping branch."""
        db = app.db
        t = now()
        ids = {}

        def cfg(label, **cfg_json):
            """Insert a sync config and remember its id under ``label``."""
            ids[label] = db.sync_configs.insert(
                name=fake_secret(label),
                platform="trello",
                enabled=True,
                sync_interval=3600,
                batch_fallback_enabled=True,
                batch_size=10,
                two_way_create=False,
                webhook_enabled=True,
                config_json=cfg_json,
                village_id=vid(),
                last_sync_at=t,
                last_batch_sync_at=t,
                created_at=t,
                updated_at=t,
            )

        cfg("inactive_org", elder_organization_id=iorg)
        cfg("by_tenant", tenant_id=tid)
        cfg("inactive_tenant", tenant_id=itid)
        cfg("unscoped")
        cfg("by_org_recent", elder_organization_id=org)
        ids["gws_inactive"] = db.google_workspace_providers.insert(
            name=fake_secret("gws"),
            domain="example.com",
            admin_email="admin@example.com",
            credentials_json={},
            organization_id=iorg,
            enabled=True,
            village_id=vid(),
            created_at=t,
            updated_at=t,
        )
        db.commit()
        return ids

    ids = await in_db(_seed)
    status, data = await call(app, "get", f"{BASE}/pending", wtok)
    got = {c["id"] for c in data["sync_configs"]}
    assert status == 200
    assert ids["inactive_org"] not in got and ids["inactive_tenant"] not in got
    assert ids["by_tenant"] not in got and ids["by_org_recent"] not in got
    gws_ids = {p["id"] for p in data["google_workspace_providers"]}
    assert ids["gws_inactive"] not in gws_ids

    status, data = await call(app, "get", f"{BASE}/pending?force=yes", wtok)
    got = {c["id"]: c for c in data["sync_configs"]}
    assert ids["by_tenant"] in got and got[ids["by_tenant"]]["tenant_id"] == tid
    assert got[ids["by_org_recent"]]["organization_id"] == org
    assert ids["unscoped"] in got and got[ids["unscoped"]]["tenant_id"] is None
    assert ids["inactive_org"] not in got and ids["inactive_tenant"] not in got

    with patch(f"{MOD}.logger") as fake_log:
        fake_log.info.side_effect = RuntimeError("log down")
        status, data = await call(app, "get", f"{BASE}/pending", wtok)
    assert status == 500
    assert data == {"error": "Failed to fetch pending integrations"}


# ------------------------------------------------------- discovery lifecycle


@pytest.mark.asyncio
async def test_discovery_start_failure_and_payload_validation(app, wtok):
    """Start handles unexpected errors; complete rejects bad payloads and statuses."""
    tid, org = await _seed_org(app)

    def _job():
        """Insert a non-scanner discovery job for the organization."""
        db = app.db
        t = now()
        jid = db.discovery_jobs.insert(
            name=fake_secret("job"),
            provider="aws",
            config_json={"organization_id": org},
            schedule_interval=0,
            enabled=True,
            created_at=t,
            updated_at=t,
        )
        db.commit()
        return jid

    jid = await in_db(_job)
    with patch(f"{MOD}._utc_now", side_effect=RuntimeError("clock")):
        status, data = await call(app, "post", f"{BASE}/discovery/{jid}/start", wtok)
        assert status == 500 and data["error"] == "Failed to start discovery job"
        status, data = await call(
            app, "post", f"{BASE}/discovery/{jid}/complete", wtok, json={}
        )
        assert status == 500 and data["error"] == "Failed to complete discovery job"

    url = f"{BASE}/discovery/{jid}/complete"
    assert (await call(app, "post", url, wtok, json=[1]))[0] == 400
    status, data = await call(app, "post", url, wtok, json={"status": "weird"})
    assert status == 400 and "Invalid discovery status" in data["error"]
    assert (
        await call(app, "post", f"{BASE}/discovery/99999999/complete", wtok, json={})
    )[0] == 404


@pytest.mark.asyncio
async def test_discovery_complete_payload_shapes(app, wtok):
    """Dict/odd resource shapes, standalone edges and failed runs are all handled."""
    tid, org = await _seed_org(app)

    def _seed():
        """Insert a job plus two pre-existing entities for standalone edges."""
        db = app.db
        t = now()
        jid = db.discovery_jobs.insert(
            name=fake_secret("job"),
            provider="aws",
            config_json={"_organization_id": org},
            schedule_interval=0,
            enabled=True,
            created_at=t,
            updated_at=t,
        )
        for ext in ("sa", "sb"):
            db.entities.insert(
                name=ext,
                type="vm",
                organization_id=org,
                external_id=ext,
                village_id=vid(),
                created_at=t,
                updated_at=t,
            )
        db.commit()
        return jid

    jid = await in_db(_seed)
    url = f"{BASE}/discovery/{jid}/complete"

    status, data = await call(
        app,
        "post",
        url,
        wtok,
        json={
            "resources": {
                "compute": [
                    {"resource_id": "d1", "name": "d1", "resource_type": "vm"},
                    "junk",
                ],
                "network": "not-a-list",
            },
            "dependencies": [
                {
                    "source_external_id": "sa",
                    "target_external_id": "sb",
                    "edge_type": "calls",
                },
                {"source_external_id": "sa", "target_external_id": "missing"},
                "junk",
            ],
            "summary": {"entities_discovered": "3", "entities_created": 1},
        },
    )
    assert status == 200, data
    assert data["entities_discovered"] == 3 and data["dependencies_created"] >= 1

    status, data = await call(
        app, "post", url, wtok, json={"resources": "weird", "dependencies": "weird"}
    )
    assert status == 200 and data["entities_discovered"] == 0

    status, data = await call(
        app,
        "post",
        url,
        wtok,
        json={
            "status": "failed",
            "error_message": "scan blew up",
            "resources": [{"resource_id": "x", "name": "x"}],
            "history_id": "not-an-int",
        },
    )
    assert status == 200 and data["status"] == "failed"

    status, data = await call(
        app, "post", url, wtok, json={"organization_id": None, "history_id": 99999999}
    )
    assert status == 200


# ---------------------------------------------------------------- sync apply


@pytest.mark.asyncio
async def test_sync_apply_branches(app, wtok):
    """Pulled items, pushed mappings and conflicts cover every apply branch."""
    tid, org = await _seed_org(app)

    def _seed():
        """Insert two sync configs (one two-way) and an existing mapped issue."""
        db = app.db
        t = now()

        def cfg(two_way, **cj):
            """Insert a sync config."""
            return db.sync_configs.insert(
                name=fake_secret("cfg"),
                platform="trello",
                enabled=True,
                sync_interval=60,
                batch_fallback_enabled=True,
                batch_size=10,
                two_way_create=two_way,
                webhook_enabled=True,
                config_json=cj,
                village_id=vid(),
                created_at=t,
                updated_at=t,
            )

        issue = db.issues.insert(
            tenant_id=tid,
            organization_id=org,
            resource_type="organization",
            resource_id=org,
            title="existing",
            status="OPEN",
            priority="LOW",
            issue_type="BUG",
            is_incident=0,
            reporter_id=None,
            village_id=vid(),
            created_at=t,
            updated_at=t,
        )
        plain = cfg(False, elder_organization_id=org)
        two_way = cfg(True, organization_id=org)
        no_scope = cfg(True)
        db.commit()
        return plain, two_way, no_scope, issue

    plain, two_way, no_scope, issue = await in_db(_seed)
    url = lambda cid: f"{BASE}/sync/{cid}/apply"  # noqa: E731

    assert (await call(app, "post", url(plain), wtok, json=[1]))[0] == 400
    assert (await call(app, "post", url(99999999), wtok, json={}))[0] == 404

    status, data = await call(
        app,
        "post",
        url(plain),
        wtok,
        json={
            "sync_type": "webhook",
            "correlation_id": "corr-1",
            "success": False,
            "error_message": "partial",
            "items_failed": "2",
            "items_synced": 7,
            "started_at": "2026-01-01T00:00:00Z",
            "metadata": "not-a-dict",
            "pulled_items": [
                "junk",
                {
                    "external_id": "P1",
                    "elder_id": issue,
                    "title": "t" * 300,
                    "status": "done",
                    "metadata": {"k": 1},
                },
                {"external_id": "P2", "title": "skipped: create not allowed"},
                {
                    "external_id": "P3",
                    "allow_create": True,
                    "elder_id": 99999999,
                    "title": "created",
                    "resource_type": "organization",
                },
                {
                    "id": "P4",
                    "allow_create": True,
                    "tenant_id": None,
                    "title": "no-title-id",
                },
                {"elder_id": issue, "title": "no external id"},
            ],
            "pushed_mappings": [
                "junk",
                {"elder_id": None, "external_id": "x"},
                {"elder_id": issue, "external_id": ""},
                {
                    "elder_id": issue,
                    "external_id": "PM1",
                    "elder_type": "issue",
                    "elder_updated_at": "2026-01-01T00:00:00Z",
                    "external_updated_at": "2026-01-02T00:00:00Z",
                },
            ],
            "conflicts": [
                "junk",
                {
                    "external_id": "P1",
                    "conflict_type": "field",
                    "elder_data": {"a": 1},
                    "external_data": {"a": 2},
                    "resolution_strategy": "manual",
                },
                {"elder_id": issue},
                {"external_id": "no-such-mapping"},
                {"elder_id": 99999999},
                {},
            ],
        },
    )
    assert status == 200, data
    assert data["items_synced"] == 7 and data["items_failed"] >= 2
    assert data["conflicts_recorded"] >= 1

    status, data = await call(
        app,
        "post",
        url(two_way),
        wtok,
        json={
            "organization_id": org,
            "pulled_items": [
                {
                    "external_id": "T1",
                    "title": "two-way created",
                    "status": "in progress",
                    "priority": "urgent",
                    "issue_type": "story",
                },
            ],
            "conflicts": [{"conflict_type": "x"}],
        },
    )
    assert status == 200 and data["items_synced"] == 1

    status, data = await call(
        app,
        "post",
        url(no_scope),
        wtok,
        json={"pulled_items": [{"external_id": "N1", "title": "no tenant"}]},
    )
    assert status == 200 and data["items_failed"] == 1 and data["items_synced"] == 0

    with patch(f"{MOD}._utc_now", side_effect=RuntimeError("clock")):
        status, data = await call(app, "post", url(plain), wtok, json={})
    assert status == 500 and data["error"] == "Failed to apply sync results"


# ------------------------------------------------------- IAM / GWS apply


@pytest.mark.asyncio
async def test_iam_and_gws_apply_paths(app, wtok):
    """IAM and Google Workspace applies handle failure payloads, 404s and 500s."""
    tid, org = await _seed_org(app)

    def _seed():
        """Insert an IAM provider and a Google Workspace provider."""
        db = app.db
        t = now()
        iam = db.iam_providers.insert(
            name=fake_secret("iam"),
            provider_type="aws",
            config_json={},
            organization_id=org,
            enabled=True,
            village_id=vid(),
            created_at=t,
            updated_at=t,
        )
        gws = db.google_workspace_providers.insert(
            name=fake_secret("gws"),
            domain="example.com",
            admin_email="admin@example.com",
            credentials_json={},
            organization_id=org,
            enabled=True,
            village_id=vid(),
            created_at=t,
            updated_at=t,
        )
        db.commit()
        return iam, gws

    iam, gws = await in_db(_seed)
    iam_url, gws_url = f"{BASE}/iam/{iam}/apply", f"{BASE}/google-workspace/{gws}/apply"
    for url in (iam_url, gws_url):
        assert (await call(app, "post", url, wtok, json=[1]))[0] == 400
        status, data = await call(app, "post", url, wtok, json={"success": False})
        assert status == 200 and data["success"] is False and data["users_synced"] == 0
        with patch(f"{MOD}._utc_now", side_effect=RuntimeError("clock")):
            assert (await call(app, "post", url, wtok, json={}))[0] == 500
    assert (await call(app, "post", f"{BASE}/iam/99999999/apply", wtok, json={}))[
        0
    ] == 404
    assert (
        await call(
            app, "post", f"{BASE}/google-workspace/99999999/apply", wtok, json={}
        )
    )[0] == 404

    sfx = uuid.uuid4().hex[:8]
    status, data = await call(
        app,
        "post",
        iam_url,
        wtok,
        json={
            "users": [{"id": f"iu-{sfx}", "uuid": f"tok-{sfx}"}],
            "groups": [{"id": f"ig-{sfx}"}],
            "roles": [{"name": f"ir-{sfx}"}],
            "memberships": [{"user_id": f"iu-{sfx}", "group_id": f"ig-{sfx}"}],
        },
    )
    assert (
        status == 200 and data["users_synced"] == 1 and data["memberships_synced"] == 1
    )
    status, data = await call(
        app,
        "post",
        gws_url,
        wtok,
        json={
            "users": [{"id": f"gu-{sfx}", "primaryEmail": f"u-{sfx}@example.com"}],
            "groups": [{"id": f"gg-{sfx}"}],
            "memberships": [{"user_id": f"gu-{sfx}", "group_id": f"gg-{sfx}"}],
        },
    )
    assert status == 200 and data["users_synced"] == 1 and "roles_synced" not in data
