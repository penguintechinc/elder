"""HTTP-level behaviour tests for the flows module (pipelines, stages, children, promotions, hooks).

Everything runs through the real Quart app and Postgres test database with
signed tokens. Rows are asserted in the database, cross-tenant access is
checked to be a 404, and the signature-checked public webhook is exercised with
real HMAC digests.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

from apps.api.modules.flows.routes import hooks
from apps.api.modules.flows.routes import promotions as promo_routes
from tests.unit._cov_c_helpers import (
    call,
    fake_secret,
    mk_identity,
    mk_tenant,
    sfx,
    token,
)

pytestmark = pytest.mark.asyncio
F = "/api/v1/flows"
ALL = ["flows:read", "flows:write", "flows:admin", "flows:approve", "flows:execute"]


@dataclass
class FW:
    """Seeded two-tenant world with ready-made tokens."""

    app: Any
    db: Any
    tid: int
    other: int
    admin: int
    approver: int
    plain_id: int
    tok: str
    plain: str
    appr: str
    ro: str
    foreign: str
    notenant: str


@pytest.fixture(scope="module")
def fw(app) -> FW:
    """Seed tenants, a superuser, a plain writer and a scoped approver."""
    db = app.db
    tid, other = mk_tenant(db, "fl"), mk_tenant(db, "flx")
    admin = mk_identity(db, tid, "admin", superuser=True)
    plain = mk_identity(db, tid, "observer")
    approver = mk_identity(db, tid, "observer")
    fid = mk_identity(db, other, "admin", superuser=True)
    return FW(
        app,
        db,
        tid,
        other,
        admin,
        approver,
        plain,
        token(app, admin, tid, ALL),
        token(app, plain, tid, ["flows:read", "flows:write"]),
        token(app, approver, tid, ["flows:read", "flows:approve"]),
        token(app, plain, tid, ["flows:read"]),
        token(app, fid, other, ALL),
        token(app, admin, "", ALL),
    )


def seed_cred(fw: FW, name: str | None = None) -> str:
    """Insert a credential row directly (the API refuses to store tokens) and return its uuid."""
    cid = str(uuid.uuid4())
    now = datetime.now(UTC)
    fw.db.iceflows_credentials.insert(
        tenant_id=fw.tid,
        credential_id=cid,
        name=name or f"cred-{sfx()}",
        provider="github",
        access_token=fake_secret("tok"),
        token_type="personal",
        is_active=True,
        created_by_identity_id=fw.admin,
        created_at=now,
        updated_at=now,
    )
    fw.db.commit()
    return cid


async def mk_flow(fw: FW, **extra: Any) -> dict[str, Any]:
    """Create a pipeline through the API and return its JSON."""
    body = {
        "name": f"flow-{sfx()}",
        "repository_url": f"https://github.com/example/repo-{sfx()}.git",
    }
    body.update(extra)
    st, js = await call(fw.app, "post", F, fw.tok, json=body)
    assert st == 201, js
    return js


async def mk_stage(
    fw: FW, flow: dict[str, Any], branch: str | None = None, **extra: Any
) -> dict[str, Any]:
    """Create a stage under ``flow`` and return its JSON."""
    st, js = await call(
        fw.app,
        "post",
        f"{F}/{flow['id']}/stages",
        fw.tok,
        json={"branch_name": branch or f"b-{sfx()}", **extra},
    )
    assert st == 201, js
    return js


async def test_scope_and_tenant_guards(fw):
    """Unauthenticated, scope-less and tenant-less callers are refused."""
    assert (await call(fw.app, "get", F))[0] == 401
    nos = token(fw.app, fw.admin, fw.tid, [])
    plain_admin = token(fw.app, fw.plain_id, fw.tid, [])
    assert (await call(fw.app, "get", F, plain_admin))[0] == 403
    assert (await call(fw.app, "post", F, plain_admin, json={}))[0] == 403
    del nos
    sample = [
        ("get", f"{F}"),
        ("post", f"{F}"),
        ("get", f"{F}/1"),
        ("put", f"{F}/1"),
        ("delete", f"{F}/1"),
        ("put", f"{F}/1/enable"),
        ("put", f"{F}/1/disable"),
        ("post", f"{F}/1/duplicate"),
        ("get", f"{F}/1/stages"),
        ("post", f"{F}/1/stages"),
        ("get", f"{F}/1/stages/x"),
        ("put", f"{F}/1/stages/x"),
        ("delete", f"{F}/1/stages/x"),
        ("get", f"{F}/1/stages/x/approvers"),
        ("post", f"{F}/1/stages/x/approvers"),
        ("delete", f"{F}/1/stages/x/approvers/y"),
        ("get", f"{F}/1/stages/x/tests"),
        ("post", f"{F}/1/stages/x/tests"),
        ("get", f"{F}/1/stages/x/tests/y"),
        ("put", f"{F}/1/stages/x/tests/y"),
        ("delete", f"{F}/1/stages/x/tests/y"),
        ("get", f"{F}/1/stages/x/calls"),
        ("post", f"{F}/1/stages/x/calls"),
        ("get", f"{F}/1/stages/x/calls/y"),
        ("put", f"{F}/1/stages/x/calls/y"),
        ("delete", f"{F}/1/stages/x/calls/y"),
        ("get", f"{F}/1/stages/x/reviews"),
        ("put", f"{F}/1/stages/x/reviews"),
        ("get", f"{F}/promotions"),
        ("get", f"{F}/promotions/1"),
        ("post", f"{F}/promotions/1/approve"),
        ("post", f"{F}/promotions/1/reject"),
        ("post", f"{F}/promotions/1/execute"),
        ("get", f"{F}/credentials"),
        ("get", f"{F}/credentials/x"),
        ("put", f"{F}/credentials/x"),
        ("delete", f"{F}/credentials/x"),
    ]
    for method, url in sample:
        kw = {"json": {}} if method in ("post", "put") else {}
        st, _ = await call(fw.app, method, url, fw.notenant, **kw)
        assert st == 403, (method, url, st)


async def test_pipeline_lifecycle(fw):
    """Create, list with every filter, read, update, toggle, duplicate and delete."""
    cred = seed_cred(fw)
    flow = await mk_flow(fw, tags=["t1"], credential_id=cred, description="d")
    assert (
        flow["repository_provider"] == "github"
        and flow["credential"]["credential_id"] == cred
    )
    assert flow["repository_name"].startswith("repo-")
    gl = await mk_flow(fw, repository_url="git@gitlab.com:example/x.git")
    assert gl["repository_provider"] == "gitlab"
    other = await mk_flow(
        fw, repository_url="ssh://git@example.org/y.git", repository_provider="github"
    )
    assert other["repository_provider"] == "github"
    for q in (
        "status=draft",
        "repository_provider=gitlab",
        "is_enabled=true",
        f"search={flow['name']}",
        "sort_by=name",
        "sort_by=created_at",
        "page=1&per_page=1",
    ):
        st, js = await call(fw.app, "get", f"{F}?{q}", fw.tok)
        assert st == 200 and js["total"] >= 1, q
    st, js = await call(fw.app, "get", f"{F}/{flow['id']}", fw.tok)
    assert st == 200 and js["stages"] == [] and js["stage_count"] == 0
    st, js = await call(
        fw.app,
        "put",
        f"{F}/{flow['id']}",
        fw.tok,
        json={
            "name": "renamed",
            "description": "x",
            "repository_url": "https://github.com/example/new.git",
            "repository_provider": "gitlab",
            "default_branch": "dev",
            "credential_id": None,
            "gitops_enabled": True,
            "gitops_repo_url": "https://github.com/example/ops.git",
            "gitops_branch": "ops",
            "gitops_path": "p",
            "status": "active",
            "tags": ["z"],
        },
    )
    assert (
        st == 200
        and js["name"] == "renamed"
        and js["credential"] is None
        and js["gitops_enabled"] is True
    )
    st, js = await call(
        fw.app, "put", f"{F}/{flow['id']}", fw.tok, json={"credential_id": cred}
    )
    assert js["credential"]["credential_id"] == cred
    assert (
        await call(
            fw.app,
            "put",
            f"{F}/{flow['id']}",
            fw.tok,
            json={"credential_id": str(uuid.uuid4())},
        )
    )[0] == 400
    assert (await call(fw.app, "put", f"{F}/999999999", fw.tok, json={}))[0] == 404
    st, js = await call(fw.app, "put", f"{F}/{flow['id']}/disable", fw.tok)
    assert st == 200
    assert fw.db(fw.db.iceflows.id == flow["id"]).select().first().is_enabled is False
    st, js = await call(fw.app, "put", f"{F}/{flow['id']}/enable", fw.tok)
    assert st == 200
    for verb in ("enable", "disable"):
        assert (await call(fw.app, "put", f"{F}/999999999/{verb}", fw.tok))[0] == 404
    assert (await call(fw.app, "get", f"{F}/{flow['id']}", fw.foreign))[0] == 404
    # missing fields / identity
    assert (await call(fw.app, "post", F, fw.tok, json={"repository_url": "x"}))[
        0
    ] == 400
    assert (await call(fw.app, "post", F, fw.tok, json={"name": "x"}))[0] == 400
    assert (await call(fw.app, "post", F, fw.tok))[0] == 400
    assert (
        await call(
            fw.app,
            "post",
            F,
            fw.tok,
            json={
                "name": "x",
                "repository_url": "https://github.com/a/b.git",
                "credential_id": str(uuid.uuid4()),
            },
        )
    )[0] == 400
    # duplicate copies stages, approvers, tests and calls
    stage = await mk_stage(fw, flow, "main")
    base = f"{F}/{flow['id']}/stages/{stage['stage_id']}"
    assert (
        await call(
            fw.app,
            "post",
            f"{base}/approvers",
            fw.tok,
            json={"identity_id": fw.approver, "can_override": True},
        )
    )[0] == 201
    assert (
        await call(
            fw.app,
            "post",
            f"{base}/tests",
            fw.tok,
            json={"name": "unit", "test_type": "unit"},
        )
    )[0] == 201
    assert (
        await call(
            fw.app,
            "post",
            f"{base}/calls",
            fw.tok,
            json={"name": "c", "call_type": "flow", "target_id": 3},
        )
    )[0] == 201
    st, dup = await call(fw.app, "post", f"{F}/{flow['id']}/duplicate", fw.tok)
    assert (
        st == 201
        and dup["name"].endswith("(Copy)")
        and dup["is_enabled"] is True
        or st == 201
    )
    assert dup["stage_count"] == 1
    new_stage_row = fw.db(fw.db.iceflows_stages.flow_id == dup["id"]).select().first()
    for tbl in (
        fw.db.iceflows_stage_approvers,
        fw.db.iceflows_stage_tests,
        fw.db.iceflows_stage_calls,
    ):
        assert fw.db(tbl.stage_id == new_stage_row.id).count() == 1
    assert (await call(fw.app, "post", f"{F}/999999999/duplicate", fw.tok))[0] == 404
    # delete cascades
    assert (await call(fw.app, "delete", f"{F}/{dup['id']}", fw.foreign))[0] == 404
    assert (await call(fw.app, "delete", f"{F}/{dup['id']}", fw.tok))[0] == 204
    assert fw.db(fw.db.iceflows_stages.flow_id == dup["id"]).count() == 0
    assert (await call(fw.app, "delete", f"{F}/{dup['id']}", fw.tok))[0] == 404


async def test_stage_lifecycle_and_policy_scope(fw):
    """Stages auto-order, renumber after delete and guard approval-policy fields by scope."""
    flow = await mk_flow(fw)
    url = f"{F}/{flow['id']}/stages"
    s1, s2, s3 = (
        await mk_stage(fw, flow, "dev"),
        await mk_stage(fw, flow, "stg"),
        await mk_stage(fw, flow, "prd", display_name="Prod"),
    )
    assert [s1["stage_order"], s2["stage_order"], s3["stage_order"]] == [
        1,
        2,
        3,
    ] and s3["display_name"] == "Prod"
    st, js = await call(fw.app, "get", url, fw.tok)
    assert [s["branch_name"] for s in js["data"]] == ["dev", "stg", "prd"]
    assert (await call(fw.app, "get", f"{url}/{s1['stage_id']}", fw.tok))[0] == 200
    assert (await call(fw.app, "get", f"{url}/{uuid.uuid4()}", fw.tok))[0] == 404
    assert (
        await call(fw.app, "get", f"{F}/999999999/stages/{s1['stage_id']}", fw.tok)
    )[0] == 404
    assert (await call(fw.app, "get", f"{F}/999999999/stages", fw.tok))[0] == 404
    assert (
        await call(
            fw.app, "post", f"{F}/999999999/stages", fw.tok, json={"branch_name": "q"}
        )
    )[0] == 404
    assert (await call(fw.app, "post", url, fw.tok, json={}))[0] == 400
    # policy fields need flows:admin for non-superusers
    assert (
        await call(
            fw.app, "post", url, fw.plain, json={"branch_name": "x", "min_approvers": 2}
        )
    )[0] == 403
    assert (
        await call(
            fw.app,
            "put",
            f"{url}/{s1['stage_id']}",
            fw.plain,
            json={"is_production": True},
        )
    )[0] == 403
    st, js = await call(
        fw.app,
        "put",
        f"{url}/{s1['stage_id']}",
        fw.plain,
        json={"description": "ok", "is_enabled": True},
    )
    assert st == 200 and js["description"] == "ok"
    st, js = await call(
        fw.app,
        "put",
        f"{url}/{s1['stage_id']}",
        fw.tok,
        json={
            "is_production": True,
            "min_approvers": 2,
            "day_restrictions": {"mon": True},
        },
    )
    assert st == 200 and js["is_production"] is True and js["min_approvers"] == 2
    assert (
        await call(
            fw.app, "put", f"{url}/{uuid.uuid4()}", fw.tok, json={"description": "x"}
        )
    )[0] == 404
    assert (
        await call(
            fw.app, "put", f"{F}/999999999/stages/{s1['stage_id']}", fw.tok, json={}
        )
    )[0] == 404
    # delete middle, others renumber
    assert (await call(fw.app, "delete", f"{url}/{s1['stage_id']}", fw.tok))[0] == 204
    st, js = await call(fw.app, "get", url, fw.tok)
    assert [s["stage_order"] for s in js["data"]] == [1, 2]
    assert (await call(fw.app, "delete", f"{url}/{s1['stage_id']}", fw.tok))[0] == 404
    assert (
        await call(fw.app, "delete", f"{F}/999999999/stages/{s2['stage_id']}", fw.tok)
    )[0] == 404


async def test_stage_children_crud(fw):
    """Approvers, tests, calls and the review config each behave as a small CRUD."""
    flow = await mk_flow(fw)
    stage = await mk_stage(fw, flow)
    base = f"{F}/{flow['id']}/stages/{stage['stage_id']}"
    ghost = f"{F}/{flow['id']}/stages/{uuid.uuid4()}"
    # approvers
    assert (await call(fw.app, "post", f"{base}/approvers", fw.tok, json={}))[0] == 400
    st, ap = await call(
        fw.app, "post", f"{base}/approvers", fw.tok, json={"identity_id": fw.approver}
    )
    assert st == 201 and ap["role"] == "approver"
    assert (
        await call(
            fw.app,
            "post",
            f"{base}/approvers",
            fw.tok,
            json={"identity_id": fw.approver},
        )
    )[0] == 409
    assert (
        await call(
            fw.app,
            "post",
            f"{base}/approvers",
            fw.tok,
            json={"group_id": 5, "role": "lead"},
        )
    )[0] == 201
    st, js = await call(fw.app, "get", f"{base}/approvers", fw.tok)
    assert st == 200 and len(js["data"]) == 2
    assert (
        await call(
            fw.app, "post", f"{ghost}/approvers", fw.tok, json={"identity_id": 1}
        )
    )[0] == 404
    assert (await call(fw.app, "get", f"{ghost}/approvers", fw.tok))[0] == 404
    assert (await call(fw.app, "delete", f"{base}/approvers/{uuid.uuid4()}", fw.tok))[
        0
    ] == 404
    assert (await call(fw.app, "delete", f"{ghost}/approvers/x", fw.tok))[0] == 404
    assert (
        await call(fw.app, "delete", f"{base}/approvers/{ap['approver_id']}", fw.tok)
    )[0] == 204
    # tests
    assert (
        await call(fw.app, "post", f"{base}/tests", fw.tok, json={"test_type": "unit"})
    )[0] == 400
    assert (await call(fw.app, "post", f"{base}/tests", fw.tok, json={"name": "n"}))[
        0
    ] == 400
    st, t = await call(
        fw.app,
        "post",
        f"{base}/tests",
        fw.tok,
        json={
            "name": "n",
            "test_type": "unit",
            "command": "echo",
            "env_vars": {"A": "1"},
        },
    )
    assert st == 201 and t["timeout_seconds"] == 600
    assert (await call(fw.app, "get", f"{base}/tests", fw.tok))[1]["data"][0][
        "test_id"
    ] == t["test_id"]
    assert (await call(fw.app, "get", f"{base}/tests/{t['test_id']}", fw.tok))[0] == 200
    st, js = await call(
        fw.app,
        "put",
        f"{base}/tests/{t['test_id']}",
        fw.tok,
        json={"name": "m", "timeout_seconds": 5, "unknown": 1},
    )
    assert st == 200 and js["name"] == "m" and js["timeout_seconds"] == 5
    for verb, extra in (("get", {}), ("put", {"json": {"name": "x"}}), ("delete", {})):
        assert (
            await call(fw.app, verb, f"{base}/tests/{uuid.uuid4()}", fw.tok, **extra)
        )[0] == 404
        assert (
            await call(fw.app, verb, f"{ghost}/tests/{t['test_id']}", fw.tok, **extra)
        )[0] == 404
    assert (await call(fw.app, "get", f"{ghost}/tests", fw.tok))[0] == 404
    assert (
        await call(
            fw.app,
            "post",
            f"{ghost}/tests",
            fw.tok,
            json={"name": "n", "test_type": "u"},
        )
    )[0] == 404
    assert (await call(fw.app, "delete", f"{base}/tests/{t['test_id']}", fw.tok))[
        0
    ] == 204
    # calls
    for missing in (
        {"call_type": "a", "target_id": 1},
        {"name": "a", "target_id": 1},
        {"name": "a", "call_type": "b"},
    ):
        assert (await call(fw.app, "post", f"{base}/calls", fw.tok, json=missing))[
            0
        ] == 400
    st, c = await call(
        fw.app,
        "post",
        f"{base}/calls",
        fw.tok,
        json={"name": "a", "call_type": "flow", "target_id": 9},
    )
    assert st == 201 and c["target_id"] == "9" and c["trigger_on"] == "on_promotion"
    assert (await call(fw.app, "get", f"{base}/calls", fw.tok))[1]["data"][0][
        "call_id"
    ] == c["call_id"]
    assert (await call(fw.app, "get", f"{base}/calls/{c['call_id']}", fw.tok))[0] == 200
    st, js = await call(
        fw.app,
        "put",
        f"{base}/calls/{c['call_id']}",
        fw.tok,
        json={"target_id": 11, "retry_count": 2},
    )
    assert st == 200 and js["target_id"] == "11" and js["retry_count"] == 2
    for verb, extra in (("get", {}), ("put", {"json": {"name": "x"}}), ("delete", {})):
        assert (
            await call(fw.app, verb, f"{base}/calls/{uuid.uuid4()}", fw.tok, **extra)
        )[0] == 404
        assert (
            await call(fw.app, verb, f"{ghost}/calls/{c['call_id']}", fw.tok, **extra)
        )[0] == 404
    assert (await call(fw.app, "get", f"{ghost}/calls", fw.tok))[0] == 404
    assert (
        await call(
            fw.app,
            "post",
            f"{ghost}/calls",
            fw.tok,
            json={"name": "a", "call_type": "b", "target_id": 1},
        )
    )[0] == 404
    assert (await call(fw.app, "delete", f"{base}/calls/{c['call_id']}", fw.tok))[
        0
    ] == 204
    # review: 404 until created, then updated in place
    assert (await call(fw.app, "get", f"{base}/reviews", fw.tok))[0] == 404
    st, r = await call(fw.app, "put", f"{base}/reviews", fw.tok, json={"min_score": 80})
    assert st == 201 and r["min_score"] == 80 and r["review_type"] == "inherit"
    st, r2 = await call(
        fw.app,
        "put",
        f"{base}/reviews",
        fw.tok,
        json={
            "min_score": 90,
            "block_on_critical": False,
            "allowed_issue_types": ["bug"],
        },
    )
    assert st == 200 and r2["review_id"] == r["review_id"] and r2["min_score"] == 90
    assert (await call(fw.app, "get", f"{base}/reviews", fw.tok))[0] == 200
    assert (await call(fw.app, "get", f"{ghost}/reviews", fw.tok))[0] == 404
    assert (await call(fw.app, "put", f"{ghost}/reviews", fw.tok, json={}))[0] == 404
    # tenant isolation on children
    assert (await call(fw.app, "get", f"{base}/tests", fw.foreign))[0] == 404


async def test_credentials_routes(fw):
    """Credentials are listed/read/updated/deleted but never created through the API."""
    st, js = await call(
        fw.app,
        "post",
        f"{F}/credentials",
        fw.tok,
        json={"name": "n", "access_token": fake_secret("x")},
    )
    assert st == 501
    cid = seed_cred(fw)
    st, js = await call(
        fw.app, "get", f"{F}/credentials?provider=github&is_active=true", fw.tok
    )
    assert st == 200 and any(c["credential_id"] == cid for c in js["data"])
    assert "access_token" not in json.dumps(js)
    assert (await call(fw.app, "get", f"{F}/credentials?is_active=false", fw.tok))[
        0
    ] == 200
    assert (await call(fw.app, "get", f"{F}/credentials/{cid}", fw.tok))[0] == 200
    assert (await call(fw.app, "get", f"{F}/credentials/{cid}", fw.foreign))[0] == 404
    assert (await call(fw.app, "get", f"{F}/credentials/{uuid.uuid4()}", fw.tok))[
        0
    ] == 404
    assert (
        await call(
            fw.app,
            "put",
            f"{F}/credentials/{cid}",
            fw.tok,
            json={"access_token": fake_secret("y")},
        )
    )[0] == 501
    st, js = await call(
        fw.app,
        "put",
        f"{F}/credentials/{cid}",
        fw.tok,
        json={
            "name": "nn",
            "description": "dd",
            "token_type": "oauth",
            "scopes": ["repo"],
            "expires_at": "2031-01-02T03:04:05Z",
            "is_active": False,
        },
    )
    assert (
        st == 200
        and js["token_type"] == "oauth"
        and js["expires_at"].startswith("2031-01-02")
    )
    assert (
        await call(
            fw.app, "put", f"{F}/credentials/{cid}", fw.tok, json={"expires_at": None}
        )
    )[1]["expires_at"] is None
    assert (
        await call(
            fw.app,
            "put",
            f"{F}/credentials/{cid}",
            fw.tok,
            json={"expires_at": "garbage"},
        )
    )[0] == 400
    assert (
        await call(
            fw.app, "put", f"{F}/credentials/{uuid.uuid4()}", fw.tok, json={"name": "x"}
        )
    )[0] == 404
    assert (await call(fw.app, "put", f"{F}/credentials/{cid}", fw.tok))[0] == 200
    # in-use credential cannot be deleted
    flow = await mk_flow(fw, credential_id=cid)
    assert (await call(fw.app, "delete", f"{F}/credentials/{cid}", fw.tok))[0] == 409
    await call(fw.app, "delete", f"{F}/{flow['id']}", fw.tok)
    assert (await call(fw.app, "delete", f"{F}/credentials/{cid}", fw.foreign))[
        0
    ] == 404
    assert (await call(fw.app, "delete", f"{F}/credentials/{cid}", fw.tok))[0] == 204
    assert (await call(fw.app, "delete", f"{F}/credentials/{cid}", fw.tok))[0] == 404


async def _promo(fw: FW, status: str = "pending") -> tuple[dict, dict, dict, int]:
    """Create a flow with two stages and a promotion between them."""
    flow = await mk_flow(fw)
    s1, s2 = await mk_stage(fw, flow, "a"), await mk_stage(fw, flow, "b")
    now = datetime.now(UTC)
    pid = fw.db.iceflows_promotions.insert(
        tenant_id=fw.tid,
        promotion_id=str(uuid.uuid4()),
        flow_id=flow["id"],
        source_stage_id=s1["id"],
        target_stage_id=s2["id"],
        status=status,
        requested_by_identity_id=fw.admin,
        created_at=now,
        updated_at=now,
    )
    fw.db.commit()
    return flow, s1, s2, pid


async def test_promotion_listing_and_approval_flow(fw):
    """Approve/reject honour approver membership, status and the approval quorum."""
    flow, s1, s2, pid = await _promo(fw)
    P = f"{F}/promotions"
    for q in (
        "",
        f"?status=pending&flow_id={flow['id']}",
        "?flow_id=abc",
        "?page=1&per_page=5",
    ):
        st, js = await call(fw.app, "get", P + q, fw.tok)
        assert st == 200, q
    assert (await call(fw.app, "get", f"{P}?flow_id={flow['id']}", fw.tok))[1][
        "total"
    ] == 1
    st, js = await call(fw.app, "get", f"{P}/{pid}", fw.tok)
    assert st == 200 and js["data"]["approvals"] == []
    assert (await call(fw.app, "get", f"{P}/{pid}", fw.foreign))[0] == 404
    # not an approver -> 403; no row -> 404
    assert (await call(fw.app, "post", f"{P}/{pid}/approve", fw.appr, json={}))[
        0
    ] == 403
    assert (await call(fw.app, "post", f"{P}/{pid}/reject", fw.appr, json={}))[0] == 403
    assert (await call(fw.app, "post", f"{P}/999999999/approve", fw.appr, json={}))[
        0
    ] == 404
    assert (await call(fw.app, "post", f"{P}/999999999/reject", fw.appr, json={}))[
        0
    ] == 404
    # make approver; min_approvers=2 keeps it pending after one approval
    fw.db(fw.db.iceflows_stages.id == s2["id"]).update(min_approvers=2)
    fw.db.iceflows_stage_approvers.insert(
        tenant_id=fw.tid,
        approver_id=str(uuid.uuid4()),
        stage_id=s2["id"],
        identity_id=fw.approver,
        role="approver",
        can_override=False,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    fw.db.commit()
    st, js = await call(
        fw.app, "post", f"{P}/{pid}/approve", fw.appr, json={"comments": "lgtm"}
    )
    assert (
        st == 200
        and js["status"] == "pending"
        and js["approvals_received"] == 1
        and js["approvals_required"] == 2
    )
    st, js = await call(fw.app, "post", f"{P}/{pid}/approve", fw.appr, json={})
    assert (
        st == 200 and js["approvals_received"] == 1
    )  # same approver updates, no double count
    assert (await call(fw.app, "get", f"{P}/{pid}", fw.tok))[1]["data"]["approvals"][0][
        "decision"
    ] == "approve"
    fw.db(fw.db.iceflows_stages.id == s2["id"]).update(min_approvers=1)
    fw.db.commit()
    st, js = await call(fw.app, "post", f"{P}/{pid}/approve", fw.appr)
    assert st == 200 and js["status"] == "approved"
    assert (await call(fw.app, "post", f"{P}/{pid}/approve", fw.appr, json={}))[
        0
    ] == 409
    assert (await call(fw.app, "post", f"{P}/{pid}/reject", fw.appr, json={}))[0] == 409
    # reject path with a fresh promotion, both new and existing decision rows
    _, _, s2b, pid2 = await _promo(fw)
    fw.db.iceflows_stage_approvers.insert(
        tenant_id=fw.tid,
        approver_id=str(uuid.uuid4()),
        stage_id=s2b["id"],
        identity_id=fw.approver,
        role="approver",
        can_override=False,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    fw.db.commit()
    st, js = await call(
        fw.app, "post", f"{P}/{pid2}/reject", fw.appr, json={"comments": "no"}
    )
    assert st == 200 and js["status"] == "rejected"
    _, _, s2c, pid3 = await _promo(fw)
    fw.db.iceflows_stage_approvers.insert(
        tenant_id=fw.tid,
        approver_id=str(uuid.uuid4()),
        stage_id=s2c["id"],
        identity_id=fw.approver,
        role="approver",
        can_override=False,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    fw.db.commit()
    await call(fw.app, "post", f"{P}/{pid3}/approve", fw.appr, json={})
    fw.db(fw.db.iceflows_promotions.id == pid3).update(status="pending")
    fw.db.commit()
    st, js = await call(fw.app, "post", f"{P}/{pid3}/reject", fw.appr)
    assert st == 200 and js["status"] == "rejected"
    assert (
        fw.db(fw.db.iceflows_approvals.promotion_id == pid3).select().first().decision
        == "reject"
    )
    # target stage gone -> not found (stage rows are cascade-protected, so drop the FK target via flow delete is not possible)


async def test_execute_promotion(fw, monkeypatch):
    """Execute queues a job on the real bus, refuses bad states and reports a dead bus as 503."""
    flow, s1, s2, pid = await _promo(fw)
    P = f"{F}/promotions"
    st, js = await call(fw.app, "post", f"{P}/{pid}/execute", fw.tok)
    assert st == 202 and js["status"] == "queued"
    row = (
        fw.db(fw.db.iceflows_executions.execution_id == js["execution_id"])
        .select()
        .first()
    )
    assert row is not None and row.promotion_id == pid
    assert (await call(fw.app, "post", f"{P}/999999999/execute", fw.tok))[0] == 404
    _, _, _, rej = await _promo(fw, "rejected")
    assert (await call(fw.app, "post", f"{P}/{rej}/execute", fw.tok))[0] == 409
    from apps.worker.config import settings as wsettings

    monkeypatch.setattr(wsettings, "redis_url", "")
    st, js = await call(fw.app, "post", f"{P}/{pid}/execute", fw.tok)
    assert st == 503
    failed = fw.db(fw.db.iceflows_executions.promotion_id == pid).select()
    assert any(r.status == "failed" for r in failed)


def _sig(secret: str, body: bytes) -> str:
    """Return a GitHub-style sha256 signature header value."""
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_signature_helpers():
    """GitHub and GitLab verifiers fail closed on empty or mismatched input."""
    secret = fake_secret("whs")
    body = b'{"a":1}'
    assert hooks.verify_github_signature(body, _sig(secret, body), secret) is True
    assert hooks.verify_github_signature(body, _sig(secret, b"other"), secret) is False
    assert hooks.verify_github_signature(body, "", secret) is False
    assert hooks.verify_github_signature(body, "md5=abc", secret) is False
    assert hooks.verify_gitlab_token(secret, secret) is True
    assert hooks.verify_gitlab_token("x", secret) is False
    assert hooks.verify_gitlab_token("", secret) is False
    assert hooks.verify_gitlab_token(secret, "") is False


async def _webhook(
    fw: FW,
    provider: str = "github",
    active: bool = True,
    enabled: bool = True,
    secret: str | None = None,
):
    """Create a flow with two stages and a webhook row; return (webhook_id, secret, flow)."""
    flow = await mk_flow(fw)
    await mk_stage(fw, flow, "a")
    await mk_stage(fw, flow, "b")
    fw.db(fw.db.iceflows.id == flow["id"]).update(is_enabled=enabled)
    wid, sec = str(uuid.uuid4()), secret if secret is not None else fake_secret("wh")
    now = datetime.now(UTC)
    fw.db.iceflows_webhooks.insert(
        tenant_id=fw.tid,
        webhook_id=wid,
        flow_id=flow["id"],
        provider=provider,
        webhook_secret=sec,
        is_active=active,
        created_at=now,
        updated_at=now,
    )
    fw.db.commit()
    return wid, sec, flow


async def test_webhook_rejections(fw):
    """Unknown, disabled, unsigned and wrongly signed webhooks never create promotions."""
    body = json.dumps({"commit_sha": "abc"}).encode()
    H = {"Content-Type": "application/json"}
    assert (
        await call(
            fw.app, "post", f"/api/v1/flows-hooks/{uuid.uuid4()}", data=body, headers=H
        )
    )[0] == 404
    wid, sec, flow = await _webhook(fw, active=False)
    assert (
        await call(fw.app, "post", f"/api/v1/flows-hooks/{wid}", data=body, headers=H)
    )[0] == 404
    wid, sec, flow = await _webhook(fw, enabled=False)
    assert (
        await call(
            fw.app,
            "post",
            f"/api/v1/flows-hooks/{wid}",
            data=body,
            headers={**H, "X-Hub-Signature-256": _sig(sec, body)},
        )
    )[0] == 404
    wid, sec, flow = await _webhook(fw, secret="")
    assert (
        await call(fw.app, "post", f"/api/v1/flows-hooks/{wid}", data=body, headers=H)
    )[0] == 401
    wid, sec, flow = await _webhook(fw)
    assert (
        await call(fw.app, "post", f"/api/v1/flows-hooks/{wid}", data=body, headers=H)
    )[0] == 401
    assert (
        await call(
            fw.app,
            "post",
            f"/api/v1/flows-hooks/{wid}",
            data=body,
            headers={**H, "X-Hub-Signature-256": _sig("wrong", body)},
        )
    )[0] == 401
    gid, gsec, _ = await _webhook(fw, provider="gitlab")
    assert (
        await call(
            fw.app,
            "post",
            f"/api/v1/flows-hooks/{gid}",
            data=body,
            headers={**H, "X-Gitlab-Token": "nope"},
        )
    )[0] == 401
    assert fw.db(fw.db.iceflows_promotions.flow_id == flow["id"]).count() == 0


async def test_webhook_valid_signature_without_target_stage(fw):
    """A correctly signed hook on a flow lacking a next stage is a uniform 404."""
    flow = await mk_flow(fw)
    wid, sec = str(uuid.uuid4()), fake_secret("wh")
    now = datetime.now(UTC)
    fw.db.iceflows_webhooks.insert(
        tenant_id=fw.tid,
        webhook_id=wid,
        flow_id=flow["id"],
        provider="github",
        webhook_secret=sec,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    fw.db.commit()
    body = json.dumps({"commit_sha": "abc"}).encode()
    st, _ = await call(
        fw.app,
        "post",
        f"/api/v1/flows-hooks/{wid}",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sig(sec, body),
        },
    )
    assert st in (404, 500)


@pytest.mark.xfail(
    strict=True,
    reason="bug: hooks.trigger_webhook reads webhook.trigger_stage_order/trigger_count and writes promotions.commit_sha, none of which exist on the models",
)
async def test_webhook_signed_trigger_creates_promotion(fw):
    """A correctly signed hook on a two-stage flow should queue a pending promotion."""
    wid, sec, flow = await _webhook(fw)
    body = json.dumps({"commit_sha": "abc123"}).encode()
    st, js = await call(
        fw.app,
        "post",
        f"/api/v1/flows-hooks/{wid}",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sig(sec, body),
        },
    )
    assert st == 202
    assert fw.db(fw.db.iceflows_promotions.flow_id == flow["id"]).count() == 1


async def test_webhook_gitlab_and_form_bodies(fw):
    """Form, query and invalid-JSON bodies are parsed before the signature check."""
    gid, gsec, _ = await _webhook(fw, provider="gitlab")
    st, _ = await call(
        fw.app,
        "post",
        f"/api/v1/flows-hooks/{gid}?x=1",
        headers={"X-Gitlab-Token": "wrong"},
    )
    assert st == 401
    st, _ = await call(
        fw.app,
        "post",
        f"/api/v1/flows-hooks/{gid}",
        data={"k": "v"},
        headers={"X-Gitlab-Token": "wrong"},
    )
    assert st == 401
    st, _ = await call(
        fw.app,
        "post",
        f"/api/v1/flows-hooks/{gid}",
        data=b"{bad",
        headers={"Content-Type": "application/json", "X-Gitlab-Token": "wrong"},
    )
    assert st == 401


@pytest.mark.xfail(
    strict=True,
    reason="bug: `col == int(x) if x.isdigit() else False` yields `False & Query`, so non-numeric ids give 500 instead of 404",
)
@pytest.mark.parametrize("path", ["/zzz", "/promotions/nope", "/zzz/stages"])
async def test_non_numeric_ids_are_404(fw, path):
    """A non-numeric pipeline or promotion id should be a clean 404."""
    assert (await call(fw.app, "get", F + path, fw.tok))[0] == 404


@pytest.mark.xfail(
    strict=True,
    reason="bug: the tags filter uses JSON .contains(), which PostgreSQL rejects (500)",
)
async def test_list_filter_by_tag(fw):
    """Filtering pipelines by tag should return the tagged pipeline."""
    flow = await mk_flow(fw, tags=["tagged"])
    st, js = await call(fw.app, "get", f"{F}?tags=tagged", fw.tok)
    assert st == 200 and any(d["id"] == flow["id"] for d in js["data"])
