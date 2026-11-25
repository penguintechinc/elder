"""Route tests for the discovery job API (``/api/v1/discovery``) via the real app.

Jobs are stored in the real Postgres schema using the real worker
``DiscoveryService``.  Only operations that would reach a cloud provider
(``test_job``, legacy synchronous ``run_discovery``) are patched, plus error
injection to reach the generic 404/500 mapping branches.
"""

# flake8: noqa: E501

from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio

from apps.worker.discovery.service import (
    DiscoveryClaimTokenError,
    DiscoveryJobNotClaimableError,
    DiscoveryService,
)
from shared.claim_tokens import CLAIM_TOKEN_HEADER
from tests.unit._cov_d_helpers import call, in_db, now, world  # noqa: F401

BASE = "/api/v1/discovery"
SCOPES = ["discovery:read", "discovery:write", "discovery:admin"]
MOD = "apps.api.modules.discovery.routes.discovery"


@pytest_asyncio.fixture
async def dworld(world):  # noqa: F811
    """Promote the tenant-A identity to portal admin and mint role tokens."""

    def _promote():
        """Set portal_role=admin for the tenant-A user."""
        db = world["db"]
        db(db.identities.id == world["a"]["user"]).update(portal_role="admin")
        db.commit()

    await in_db(_promote)
    world["admin"] = world["tok"]("a", SCOPES)
    world["reader"] = world["tok"]("a", ["discovery:read"])
    world["writer"] = world["tok"]("a", ["discovery:read", "discovery:write"])
    world["b_writer"] = world["tok"]("b", ["discovery:read", "discovery:write"])
    return world


async def _make_job(app, w, side="a", provider="network", interval=0, **cfg):
    """Insert a scanner-type discovery job owned by the side's organization."""

    def _ins():
        """Insert the job row directly with the org stored in config_json."""
        db = w["db"]
        jid = db.discovery_jobs.insert(
            name=f"cdj-{provider}-{interval}",
            provider=provider,
            config_json={"_organization_id": w[side]["org"], **cfg},
            schedule_interval=interval,
            enabled=True,
            created_at=now(),
            updated_at=now(),
        )
        db.commit()
        return jid

    return await in_db(_ins)


def _sys_worker():
    """Patch the system-service-worker predicate to True for the route module."""
    return patch(f"{MOD}.is_system_service_worker", return_value=True)


@pytest.mark.asyncio
async def test_job_crud_lifecycle(app, dworld):
    """Create, list (with filters), get, update, queue, history and delete a job."""
    tok = dworld["admin"]
    org = dworld["a"]["org"]
    status, data = await call(app, "post", f"{BASE}/jobs", tok, json={})
    assert status == 400 and "required" in data["error"].lower()
    status, data = await call(app, "post", f"{BASE}/jobs", tok, json={"name": "x"})
    assert status == 400 and "Missing required fields" in data["error"]
    status, data = await call(
        app,
        "post",
        f"{BASE}/jobs",
        tok,
        json={"name": "j", "provider": "nope", "config": {}, "organization_id": org},
    )
    assert status == 400

    status, job = await call(
        app,
        "post",
        f"{BASE}/jobs",
        tok,
        json={
            "name": "cd-net",
            "provider": "network",
            "config": {"targets": ["10.0.0.0/30"]},
            "organization_id": org,
            "schedule_interval": 120,
            "description": "d",
            "enabled": False,
        },
    )
    assert status == 201, job
    jid = job["id"]

    status, got = await call(app, "get", f"{BASE}/jobs/{jid}", dworld["reader"])
    assert status == 200 and got["name"] == "cd-net"
    status, _ = await call(app, "get", f"{BASE}/jobs/99999999", dworld["reader"])
    assert status == 404

    q = f"?provider=network&enabled=false&organization_id={org}"
    status, listed = await call(app, "get", f"{BASE}/jobs{q}", dworld["reader"])
    assert status == 200 and jid in {j["id"] for j in listed["jobs"]}
    status, listed = await call(
        app, "get", f"{BASE}/jobs?enabled=true&organization_id={org}", dworld["reader"]
    )
    assert jid not in {j["id"] for j in listed["jobs"]}

    status, _ = await call(app, "put", f"{BASE}/jobs/{jid}", tok, json={})
    assert status == 400
    status, upd = await call(
        app,
        "put",
        f"{BASE}/jobs/{jid}",
        tok,
        json={"name": "renamed", "enabled": True, "description": "nd"},
    )
    assert status == 200 and upd["name"] == "renamed"
    status, _ = await call(app, "put", f"{BASE}/jobs/99999999", tok, json={"name": "z"})
    assert status == 404

    status, queued = await call(app, "post", f"{BASE}/jobs/{jid}/run", tok)
    assert status == 202 and queued["success"] is True
    status, _ = await call(app, "post", f"{BASE}/jobs/99999999/run", tok)
    assert status == 404

    status, hist = await call(app, "get", f"{BASE}/jobs/{jid}/history?limit=5", tok)
    assert status == 200 and hist["count"] == 0
    status, hist = await call(app, "get", f"{BASE}/history", tok)
    assert status == 200 and "history" in hist

    status, _ = await call(app, "delete", f"{BASE}/jobs/{jid}", tok)
    assert status == 200
    status, _ = await call(app, "delete", f"{BASE}/jobs/{jid}", tok)
    assert status == 404


@pytest.mark.asyncio
async def test_scope_and_role_enforcement(app, dworld):
    """Missing scope, observer role and anonymous callers are refused."""
    jid = await _make_job(app, dworld)
    status, _ = await call(app, "get", f"{BASE}/jobs", None)
    assert status == 401
    none = dworld["tok"]("a", [])
    status, _ = await call(app, "get", f"{BASE}/jobs", none)
    assert status == 403
    status, _ = await call(app, "post", f"{BASE}/jobs/{jid}/test", dworld["reader"])
    assert status == 403
    obs = dworld["tok"]("b", SCOPES)
    status, data = await call(app, "delete", f"{BASE}/jobs/{jid}", obs)
    assert status == 403 and data["your_role"] == "observer"


@pytest.mark.asyncio
async def test_job_endpoints_error_mapping(app, dworld):
    """Service errors map to 404 on 'not found' text, else the route's generic code."""
    adm, rd, wr = dworld["admin"], dworld["reader"], dworld["writer"]
    cases = [
        ("get", f"{BASE}/jobs", rd, None, "list_jobs", 500),
        ("get", f"{BASE}/jobs/1", rd, None, "get_job", 500),
        ("put", f"{BASE}/jobs/1", adm, {"name": "n"}, "update_job", 400),
        ("delete", f"{BASE}/jobs/1", adm, None, "delete_job", 500),
        ("post", f"{BASE}/jobs/1/test", wr, None, "test_job", 500),
        ("post", f"{BASE}/jobs/1/run", adm, None, "queue_job_for_worker", 500),
        ("get", f"{BASE}/jobs/1/history", rd, None, "get_discovery_history", 500),
        ("get", f"{BASE}/history", rd, None, "get_discovery_history", 500),
        ("get", f"{BASE}/jobs/pending", rd, None, "get_pending_jobs", 500),
    ]
    for method, url, tok, body, svc, generic in cases:
        kw = {"json": body} if body is not None else {}
        with patch.object(
            DiscoveryService, svc, side_effect=Exception("Job not found")
        ):
            status, _ = await call(app, method, url, tok, **kw)
        expect_nf = svc not in {
            "list_jobs",
            "get_discovery_history",
            "get_pending_jobs",
        }
        assert status == (404 if expect_nf else generic), (svc, status)
        with patch.object(DiscoveryService, svc, side_effect=Exception("kaboom")):
            status, data = await call(app, method, url, tok, **kw)
        assert status == generic, (svc, status)
        assert "kaboom" not in str(data)

    with patch.object(DiscoveryService, "create_job", side_effect=Exception("kaboom")):
        status, _ = await call(
            app,
            "post",
            f"{BASE}/jobs",
            adm,
            json={"name": "n", "provider": "aws", "config": {}, "organization_id": 1},
        )
    assert status == 400


@pytest.mark.asyncio
async def test_test_job_success_and_legacy_run(app, dworld):
    """test_job returns the provider result; legacy run reports success/failure."""
    adm, wr = dworld["admin"], dworld["writer"]
    with patch.object(DiscoveryService, "test_job", return_value={"success": True}):
        status, data = await call(app, "post", f"{BASE}/jobs/1/test", wr)
    assert status == 200 and data == {"success": True}
    with patch.object(
        DiscoveryService, "run_discovery", return_value={"success": True}
    ):
        status, _ = await call(app, "post", f"{BASE}/jobs/1/run?legacy=true", adm)
    assert status == 202
    with patch.object(
        DiscoveryService, "run_discovery", return_value={"success": False}
    ):
        status, _ = await call(app, "post", f"{BASE}/jobs/1/run?legacy=true", adm)
    assert status == 500


@pytest.mark.asyncio
async def test_pending_jobs_tenant_and_worker_scoping(app, dworld):
    """Pending listing is tenant-scoped; a system worker sees active orgs' jobs."""
    ja = await _make_job(app, dworld, "a")
    jb = await _make_job(app, dworld, "b")
    status, data = await call(app, "get", f"{BASE}/jobs/pending", dworld["reader"])
    ids = {j["id"] for j in data["jobs"]}
    assert status == 200 and ja in ids and jb not in ids

    with patch(f"{MOD}.get_current_tenant_id", return_value=None):
        status, data = await call(app, "get", f"{BASE}/jobs/pending", dworld["reader"])
    assert status == 400 and "tenant" in data["error"].lower()

    with _sys_worker():
        status, data = await call(app, "get", f"{BASE}/jobs/pending", dworld["reader"])
    ids = {j["id"] for j in data["jobs"]}
    assert status == 200 and {ja, jb} <= ids

    def _deactivate():
        """Deactivate every tenant so no organization remains active."""
        db = dworld["db"]
        rows = db(db.tenants.is_active == True).select(db.tenants.id)  # noqa: E712
        db(db.tenants.is_active == True).update(is_active=False)  # noqa: E712
        db.commit()
        return [r.id for r in rows]

    def _restore(ids):
        """Reactivate the tenants deactivated above."""
        db = dworld["db"]
        db(db.tenants.id.belongs(ids)).update(is_active=True)
        db.commit()

    ids = await in_db(_deactivate)
    try:
        with _sys_worker():
            status, data = await call(
                app, "get", f"{BASE}/jobs/pending", dworld["reader"]
            )
        assert status == 200 and data["jobs"] == []
    finally:
        await in_db(lambda: _restore(ids))


@pytest.mark.asyncio
async def test_start_and_complete_real_claim_flow(app, dworld):
    """Claim a one-shot job, reject a bad token, then complete it with the token."""
    jid = await _make_job(app, dworld, "a")
    wr = dworld["writer"]
    status, _ = await call(app, "post", f"{BASE}/jobs/99999999/start", wr)
    assert status == 404
    status, _ = await call(app, "post", f"{BASE}/jobs/{jid}/start", dworld["b_writer"])
    assert status == 404

    status, started = await call(app, "post", f"{BASE}/jobs/{jid}/start", wr)
    assert status == 200 and started["claim_token"]
    status, _ = await call(app, "post", f"{BASE}/jobs/{jid}/start", wr)
    assert status == 409

    status, _ = await call(app, "post", f"{BASE}/jobs/{jid}/complete", wr, json={})
    assert status == 400
    status, _ = await call(
        app, "post", f"{BASE}/jobs/{jid}/complete", wr, json={"success": True}
    )
    assert status == 409
    status, _ = await call(
        app,
        "post",
        f"{BASE}/jobs/{jid}/complete",
        dworld["b_writer"],
        json={"success": True},
    )
    assert status == 404
    status, _ = await call(
        app, "post", f"{BASE}/jobs/99999999/complete", wr, json={"success": True}
    )
    assert status == 404

    status, done = await call(
        app,
        "post",
        f"{BASE}/jobs/{jid}/complete",
        wr,
        json={"success": True, "results": {"resources": []}},
        headers={CLAIM_TOKEN_HEADER: started["claim_token"]},
    )
    assert status == 200, done
    assert done["status"] == "completed"

    status, again = await call(
        app,
        "post",
        f"{BASE}/jobs/{jid}/complete",
        wr,
        json={"success": True, "claim_token": started["claim_token"]},
    )
    assert status == 200 and again["duplicate"] is True


@pytest.mark.asyncio
async def test_start_complete_as_system_worker(app, dworld):
    """A system worker may start/complete jobs of active orgs; unknown jobs are 404."""
    jid = await _make_job(app, dworld, "a")
    wr = dworld["writer"]
    with _sys_worker():
        status, _ = await call(app, "post", f"{BASE}/jobs/99999999/start", wr)
        assert status == 404
        status, started = await call(app, "post", f"{BASE}/jobs/{jid}/start", wr)
        assert status == 200
        status, _ = await call(
            app, "post", f"{BASE}/jobs/99999999/complete", wr, json={"success": True}
        )
        assert status == 404
        status, done = await call(
            app,
            "post",
            f"{BASE}/jobs/{jid}/complete",
            wr,
            json={"success": False, "error_message": "boom"},
            headers={CLAIM_TOKEN_HEADER: started["claim_token"]},
        )
    assert status == 200 and done["status"] == "failed"


@pytest.mark.asyncio
async def test_start_complete_error_mapping(app, dworld):
    """Not-claimable, bad claim token and unexpected errors map to 409/404/500."""
    jid = await _make_job(app, dworld, "a")
    wr = dworld["writer"]
    start = f"{BASE}/jobs/{jid}/start"
    done = f"{BASE}/jobs/{jid}/complete"
    body = {"success": True}
    with patch.object(
        DiscoveryService,
        "mark_job_running",
        side_effect=DiscoveryJobNotClaimableError("x"),
    ):
        assert (await call(app, "post", start, wr))[0] == 409
    with patch.object(
        DiscoveryService, "mark_job_running", side_effect=Exception("Job not found")
    ):
        assert (await call(app, "post", start, wr))[0] == 404
    with patch.object(
        DiscoveryService, "mark_job_running", side_effect=Exception("kaboom")
    ):
        assert (await call(app, "post", start, wr))[0] == 500
    with patch.object(
        DiscoveryService, "complete_job", side_effect=DiscoveryClaimTokenError("x")
    ):
        assert (await call(app, "post", done, wr, json=body))[0] == 409
    with patch.object(
        DiscoveryService, "complete_job", side_effect=Exception("Job not found")
    ):
        assert (await call(app, "post", done, wr, json=body))[0] == 404
    with patch.object(
        DiscoveryService, "complete_job", side_effect=Exception("kaboom")
    ):
        assert (await call(app, "post", done, wr, json=body))[0] == 500


def test_get_discovery_service_picks_replica():
    """get_discovery_service wraps the read replica only when asked."""
    from apps.api.modules.discovery.routes import discovery as mod

    fake = MagicMock(db="primary", db_read="replica")
    with patch.object(mod, "current_app", fake):
        assert mod.get_discovery_service().db == "primary"
        assert mod.get_discovery_service(read_only=True).db == "replica"
        assert mod._tenant_organization_ids(None, None) == set()
        assert mod._job_organization_id(None) is None
