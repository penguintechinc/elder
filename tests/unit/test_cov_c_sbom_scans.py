"""HTTP-level behaviour tests for SBOM scans (claim/lease/results/upload) and schedules.

The scanner-worker protocol is exercised end to end with real signed tokens,
including a machine ``svc-scanner`` JWT for the platform-wide paths. Only the
external vulnerability feed client is replaced.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from apps.api.modules.sbom.routes import sbom_scans
from tests.unit._cov_c_helpers import (
    call,
    fake_secret,
    mk_identity,
    mk_org,
    mk_tenant,
    sfx,
    token,
)

pytestmark = pytest.mark.asyncio
SC = ["sbom:read", "sbom:write"]
S = "/api/v1/sbom/scans"
SCH = "/api/v1/sbom/schedules"


@dataclass
class SX:
    """Seeded tenant, parents and tokens for scan tests."""

    app: Any
    db: Any
    tid: int
    org: int
    uid: int
    svc: int
    soft: int
    tok: str
    ro: str
    foreign: str
    worker: str
    notenant: str


def worker_token(app: Any, tid: str = "system", sub: str = "svc-scanner") -> str:
    """Mint a short-lived machine ``service_worker`` JWT for the scanner."""
    now = datetime.now(UTC)
    return token(
        app,
        sub,
        tid,
        ["sbom:read", "sbom:write", "vulnerabilities:write"],
        ttype="service_worker",
        roles=["service_worker"],
        iat=now,
        exp=now + timedelta(minutes=5),
    )


@pytest.fixture(scope="module")
def sx(app) -> SX:
    """Seed a tenant with a service and a software parent."""
    db = app.db
    tid, other = mk_tenant(db, "sc"), mk_tenant(db, "scx")
    uid = mk_identity(db, tid, "admin", superuser=True)
    fid = mk_identity(db, other, "admin", superuser=True)
    ro = mk_identity(db, tid, "observer")
    org = mk_org(db, tid)
    now = datetime.now(UTC)
    svc = db.services.insert(
        name=f"svc-{sfx()}",
        organization_id=org,
        tenant_id=tid,
        status="active",
        is_public=False,
        created_at=now,
        updated_at=now,
        repository_url="https://example.test/svc.git",
    )
    soft = db.software.insert(
        name=f"sw-{sfx()}",
        organization_id=org,
        tenant_id=tid,
        software_type="saas",
        is_active=True,
        created_at=now,
        updated_at=now,
        git_url="https://example.test/sw.git",
    )
    db.commit()
    return SX(
        app,
        db,
        tid,
        org,
        uid,
        svc,
        soft,
        token(app, uid, tid, SC),
        token(app, ro, tid, ["sbom:read"]),
        token(app, fid, other, SC),
        worker_token(app),
        token(app, uid, "", SC),
    )


async def mk_scan(
    sx: SX, parent: str = "service", tok: str | None = None, **extra: Any
) -> dict[str, Any]:
    """Create a pending scan through the API."""
    pid = sx.svc if parent == "service" else sx.soft
    st, js = await call(
        sx.app,
        "post",
        S,
        tok or sx.tok,
        json={
            "parent_type": parent,
            "parent_id": pid,
            "scan_type": "git_clone",
            **extra,
        },
    )
    assert st == 201, js
    return js


async def test_scan_create_list_get_delete(sx):
    """Scans create with inherited repo URLs, list with filters and reject bad input."""
    a = await mk_scan(sx, "service")
    b = await mk_scan(sx, "software", scan_type="git_clone", repository_branch="dev")
    assert (
        a["repository_url"] == "https://example.test/svc.git"
        and b["repository_url"] == "https://example.test/sw.git"
    )
    c = await mk_scan(sx, "service", repository_url="https://example.test/x.git")
    assert c["repository_url"] == "https://example.test/x.git"
    for q in (
        "parent_type=service",
        f"parent_id={sx.svc}",
        "status=pending",
        "scan_type=git_clone",
    ):
        st, js = await call(sx.app, "get", f"{S}?{q}", sx.tok)
        assert st == 200 and js["total"] >= 1, q
    assert (await call(sx.app, "get", f"{S}/{a['id']}", sx.tok))[0] == 200
    assert (await call(sx.app, "get", f"{S}/{a['id']}", sx.foreign))[0] == 404
    assert (
        await call(
            sx.app,
            "post",
            S,
            sx.tok,
            json={
                "parent_type": "service",
                "parent_id": 999999999,
                "scan_type": "git_clone",
            },
        )
    )[0] == 404
    assert (
        await call(
            sx.app,
            "post",
            S,
            sx.tok,
            json={"parent_type": "bogus", "parent_id": 1, "scan_type": "git_clone"},
        )
    )[0] in (400, 404)
    assert (
        await call(
            sx.app,
            "post",
            S,
            sx.tok,
            json={"parent_type": "service", "parent_id": sx.svc, "scan_type": "nope"},
        )
    )[0] == 400
    assert (
        await call(
            sx.app,
            "post",
            S,
            sx.ro,
            json={
                "parent_type": "service",
                "parent_id": sx.svc,
                "scan_type": "git_clone",
            },
        )
    )[0] == 403
    assert (await call(sx.app, "delete", f"{S}/{a['id']}", sx.foreign))[0] == 404
    assert (await call(sx.app, "delete", "/api/v1/sbom/scans/999999999", sx.tok))[
        0
    ] == 404


async def test_worker_creates_scan_for_any_tenant_parent(sx):
    """A platform scanner token may create scans for active tenants' parents only."""
    st, js = await call(
        sx.app,
        "post",
        S,
        sx.worker,
        json={
            "parent_type": "software",
            "parent_id": sx.soft,
            "scan_type": "git_clone",
        },
    )
    assert st == 201 and js["parent_id"] == sx.soft
    assert sx.db(sx.db.sbom_scans.id == js["id"]).select().first().tenant_id == sx.tid
    assert (
        await call(
            sx.app,
            "post",
            S,
            sx.worker,
            json={
                "parent_type": "service",
                "parent_id": 999999999,
                "scan_type": "git_clone",
            },
        )
    )[0] == 404
    assert (
        await call(
            sx.app,
            "post",
            S,
            sx.worker,
            json={"parent_type": "other", "parent_id": 1, "scan_type": "git_clone"},
        )
    )[0] in (400, 404)


async def test_pending_and_requeue(sx):
    """Pending lists per tenant or platform-wide, resolves credentials and requeues stale claims."""
    assert (await call(sx.app, "get", f"{S}/pending", sx.notenant))[0] == 400
    scan = await mk_scan(sx)
    st, js = await call(sx.app, "get", f"{S}/pending", sx.tok)
    assert st == 200 and any(r["id"] == scan["id"] for r in js)
    st, js = await call(sx.app, "get", f"{S}/pending", sx.worker)
    assert st == 200 and any(r["id"] == scan["id"] for r in js)
    # a pending scan with a builtin credential resolves the secret token into the payload
    secret = fake_secret("pat")
    now = datetime.now(UTC)
    rid = sx.db.builtin_secrets.insert(
        name=f"cred-{sfx()}",
        organization_id=sx.org,
        secret_type="token",
        secret_json={"token": secret},
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    sx.db(sx.db.sbom_scans.id == scan["id"]).update(
        credential_type="builtin_secret",
        credential_id=rid,
        credential_mapping={"field": "token"},
    )
    sx.db.commit()
    st, js = await call(sx.app, "get", f"{S}/pending", sx.tok)
    row = next(r for r in js if r["id"] == scan["id"])
    assert (
        row["_resolved_token"] == secret
        and "credential_id" not in row
        and "credential_mapping" not in row
    )
    # stale running scans are requeued by the next pending call
    stale = await mk_scan(sx)
    sx.db(sx.db.sbom_scans.id == stale["id"]).update(
        status="running",
        started_at=now - timedelta(hours=3),
        claim_expires_at=now - timedelta(hours=2),
    )
    legacy = await mk_scan(sx)
    sx.db(sx.db.sbom_scans.id == legacy["id"]).update(
        status="running", started_at=now - timedelta(hours=3), claim_expires_at=None
    )
    sx.db.commit()
    await call(sx.app, "get", f"{S}/pending", sx.tok)
    assert (
        sx.db(sx.db.sbom_scans.id == stale["id"]).select().first().status == "pending"
    )
    assert (
        sx.db(sx.db.sbom_scans.id == legacy["id"]).select().first().status == "pending"
    )
    sx.db(sx.db.sbom_scans.id == stale["id"]).update(
        status="running",
        started_at=now - timedelta(hours=3),
        claim_expires_at=now - timedelta(hours=2),
    )
    sx.db.commit()
    await call(sx.app, "get", f"{S}/pending", sx.worker)
    assert (
        sx.db(sx.db.sbom_scans.id == stale["id"]).select().first().status == "pending"
    )


async def test_start_and_results_protocol(sx, monkeypatch):
    """A scan is claimed once, completed with its claim token, and duplicates are idempotent."""
    scan = await mk_scan(sx)
    sid = scan["id"]
    st, started = await call(sx.app, "post", f"{S}/{sid}/start", sx.tok)
    assert st == 200 and started["claim_token"] and started["status"] == "running"
    st, js = await call(sx.app, "post", f"{S}/{sid}/start", sx.tok)
    assert st == 400 and "not pending" in js["error"]
    assert (await call(sx.app, "post", f"{S}/999999999/start", sx.tok))[0] == 404
    assert (await call(sx.app, "post", f"{S}/{sid}/start", sx.foreign))[0] == 404
    comps = [
        {
            "name": "alpha",
            "version": "1.0",
            "package_type": "pypi",
            "purl": "pkg:pypi/alpha@1.0",
            "license_name": "GPL-3.0",
        },
        {"name": "beta", "version": "2.0", "package_type": "npm"},
    ]
    # missing / wrong claim token
    assert (
        await call(
            sx.app,
            "post",
            f"{S}/{sid}/results",
            sx.tok,
            json={"success": True, "components": comps},
        )
    )[0] == 409
    assert (
        await call(
            sx.app,
            "post",
            f"{S}/{sid}/results",
            sx.tok,
            json={"success": True, "components": comps, "claim_token": "wrong"},
        )
    )[0] == 409
    assert (
        await call(
            sx.app, "post", f"{S}/{sid}/results", sx.tok, json={"components": comps}
        )
    )[0] == 400
    assert (
        await call(
            sx.app, "post", f"{S}/999999999/results", sx.tok, json={"success": True}
        )
    )[0] == 404
    # license policy violation is logged but does not block completion
    now = datetime.now(UTC)
    sx.db.license_policies.insert(
        tenant_id=sx.tid,
        name="p",
        organization_id=sx.org,
        allowed_licenses=[],
        denied_licenses=["GPL-*"],
        action="warn",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    sx.db.commit()

    class FakeMatcher:
        """Stand-in for the external vulnerability feed matcher."""

        async def __aenter__(self):
            """Enter the async context."""
            return self

        async def __aexit__(self, *exc):
            """Leave the async context."""
            return False

        async def match_components_batch(self, comps):
            """Return one matched vulnerability for the first component."""
            v = SimpleNamespace(
                cve_id=f"CVE-2099-{sfx()}",
                aliases=[],
                severity="HIGH",
                cvss_score=7.5,
                cvss_vector="v",
                title="t",
                description="d",
                affected_packages=[],
                fixed_versions=[],
                references=[],
                source="osv",
            )
            return {comps[0]["id"]: [v, v]}

    import apps.api.services.sbom.vulnerability.matcher as matcher_mod

    monkeypatch.setattr(matcher_mod, "VulnerabilityMatcher", FakeMatcher)
    st, js = await call(
        sx.app,
        "post",
        f"{S}/{sid}/results",
        sx.tok,
        json={
            "success": True,
            "components": comps,
            "files_scanned": ["a"],
            "commit_hash": "abc",
            "scan_duration_ms": 5,
            "claim_token": started["claim_token"],
        },
    )
    assert st == 200 and js["status"] == "completed" and js["components_added"] == 2
    await asyncio.sleep(1.0)
    comp_ids = [
        r.id
        for r in sx.db(sx.db.sbom_components.parent_id == sx.svc).select()
        if r.name == "alpha"
    ]
    assert (
        comp_ids
        and sx.db(sx.db.component_vulnerabilities.component_id == comp_ids[0]).count()
        >= 1
    )
    # duplicate completion is idempotent
    st, js = await call(
        sx.app,
        "post",
        f"{S}/{sid}/results",
        sx.tok,
        json={"success": True, "claim_token": started["claim_token"]},
    )
    assert st == 200 and js["status"] == "completed"
    # second scan updates existing components via the header token
    scan2 = await mk_scan(sx)
    st, started2 = await call(sx.app, "post", f"{S}/{scan2['id']}/start", sx.worker)
    assert st == 200
    st, js = await call(
        sx.app,
        "post",
        f"{S}/{scan2['id']}/results",
        sx.worker,
        json={"success": True, "components": comps},
        headers={"X-Claim-Token": started2["claim_token"]},
    )
    assert st == 200 and js["components_updated"] == 2
    # failed scan, then a legacy transition without a claim token
    scan3 = await mk_scan(sx)
    await call(sx.app, "post", f"{S}/{scan3['id']}/start", sx.tok)
    st, js = await call(
        sx.app,
        "post",
        f"{S}/{scan3['id']}/results",
        sx.tok,
        json={"success": False, "error_message": "boom", "claim_token": "x" * 10},
    )
    assert st == 409
    monkeypatch.setenv("ELDER_SCANNER_CLAIM_TOKEN_REQUIRED", "false")
    st, js = await call(
        sx.app,
        "post",
        f"{S}/{scan3['id']}/results",
        sx.tok,
        json={"success": False, "error_message": "boom"},
    )
    assert st == 200 and js["status"] == "failed" and js["error_message"] == "boom"


async def test_results_processing_error_marks_failed(sx, monkeypatch):
    """A crash while applying results marks the scan failed instead of leaving it running."""
    scan = await mk_scan(sx)
    st, started = await call(sx.app, "post", f"{S}/{scan['id']}/start", sx.tok)
    bad = [{"version": "1", "package_type": "pypi"}]  # missing required "name" key
    st, _ = await call(
        sx.app,
        "post",
        f"{S}/{scan['id']}/results",
        sx.tok,
        json={
            "success": True,
            "components": bad,
            "claim_token": started["claim_token"],
        },
    )
    assert st == 500
    row = sx.db(sx.db.sbom_scans.id == scan["id"]).select().first()
    assert row.status == "failed" and row.error_message == "Result processing failed"


async def test_upload_sbom(sx):
    """Uploads import CycloneDX components, update on re-upload and reject bad files."""
    doc = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.4",
        "components": [
            {
                "type": "library",
                "name": "left-pad",
                "version": "1.3.0",
                "purl": "pkg:npm/left-pad@1.3.0",
                "licenses": [{"license": {"id": "MIT"}}],
            },
            {
                "type": "library",
                "name": "right-pad",
                "version": "2.0.0",
                "purl": "pkg:npm/right-pad@2.0.0",
            },
        ],
    }
    body = {
        "parent_type": "service",
        "parent_id": sx.svc,
        "filename": "sbom.json",
        "file_content": json.dumps(doc),
    }
    st, js = await call(sx.app, "post", f"{S}/upload", sx.tok, json=body)
    assert (
        st == 201
        and js["scan"]["components_added"] == 2
        and js["scan"]["status"] == "completed"
    )
    st, js = await call(sx.app, "post", f"{S}/upload", sx.tok, json=body)
    assert st == 201 and js["scan"]["components_updated"] == 2
    assert (
        await call(
            sx.app,
            "post",
            f"{S}/upload",
            sx.tok,
            json={**body, "filename": "notes.txt"},
        )
    )[0] == 400
    assert (
        await call(
            sx.app,
            "post",
            f"{S}/upload",
            sx.tok,
            json={**body, "file_content": "{not json"},
        )
    )[0] == 400
    empty = {"bomFormat": "CycloneDX", "specVersion": "1.4", "components": []}
    assert (
        await call(
            sx.app,
            "post",
            f"{S}/upload",
            sx.tok,
            json={**body, "file_content": json.dumps(empty)},
        )
    )[0] == 400
    assert (
        await call(
            sx.app, "post", f"{S}/upload", sx.tok, json={**body, "parent_id": 999999999}
        )
    )[0] == 404
    assert (
        await call(
            sx.app, "post", f"{S}/upload", sx.tok, json={"parent_type": "service"}
        )
    )[0] == 400


async def test_lease_and_license_helpers(sx, monkeypatch):
    """Lease env parsing and the scan-side licence/credential helpers behave as documented."""
    monkeypatch.delenv("ELDER_SBOM_CLAIM_LEASE_SECONDS", raising=False)
    assert (
        sbom_scans._claim_lease_seconds() == sbom_scans.DEFAULT_SBOM_CLAIM_LEASE_SECONDS
    )
    monkeypatch.setenv("ELDER_SBOM_CLAIM_LEASE_SECONDS", "120")
    assert sbom_scans._claim_lease_seconds() == 120
    monkeypatch.setenv("ELDER_SBOM_CLAIM_LEASE_SECONDS", "nope")
    assert (
        sbom_scans._claim_lease_seconds() == sbom_scans.DEFAULT_SBOM_CLAIM_LEASE_SECONDS
    )
    monkeypatch.setenv("ELDER_SBOM_CLAIM_LEASE_SECONDS", "5")
    assert (
        sbom_scans._claim_lease_seconds() == sbom_scans.DEFAULT_SBOM_CLAIM_LEASE_SECONDS
    )
    assert sbom_scans._match_license_pattern(
        "MIT", "mit"
    ) and not sbom_scans._match_license_pattern("", "x")
    pol = {
        "name": "p",
        "action": "block",
        "allowed_licenses": ["MIT"],
        "denied_licenses": ["GPL-*"],
    }
    assert sbom_scans._check_component_against_policy({"license_name": "GPL-2.0"}, pol)[
        "reason"
    ].endswith("denied by policy")
    assert sbom_scans._check_component_against_policy({"license_name": "BSD"}, pol)[
        "reason"
    ].endswith("not in allowed list")
    assert (
        sbom_scans._check_component_against_policy({"license_name": "MIT"}, pol) is None
    )
    assert sbom_scans._check_component_against_policy({}, pol) is None
    assert sbom_scans._resolve_credential(sx.db, "other", 1, {}, sx.tid) is None
    assert (
        sbom_scans._resolve_credential(sx.db, "builtin_secret", 0, {}, sx.tid) is None
    )
    assert (
        sbom_scans._resolve_credential(sx.db, "builtin_secret", 999999999, {}, sx.tid)
        is None
    )


async def test_resolve_credential_returns_secret_field(sx):
    """A builtin secret in the caller's tenant yields the mapped field; inactive ones are ignored."""
    now = datetime.now(UTC)
    secret = fake_secret("pat")
    tbl = sx.db.builtin_secrets
    rid = tbl.insert(
        name=f"c-{sfx()}",
        organization_id=sx.org,
        secret_type="token",
        secret_json={"token": secret, "alt": "z"},
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    sx.db.commit()
    assert (
        sbom_scans._resolve_credential(sx.db, "builtin_secret", rid, None, sx.tid)
        == secret
    )
    assert (
        sbom_scans._resolve_credential(
            sx.db, "builtin_secret", rid, {"field": "alt"}, sx.tid
        )
        == "z"
    )
    sx.db(tbl.id == rid).update(is_active=False)
    sx.db.commit()
    assert (
        sbom_scans._resolve_credential(sx.db, "builtin_secret", rid, None, sx.tid)
        is None
    )


async def test_schedules_lifecycle(sx):
    """Schedules validate cron and parents, list with filters and expose due ones."""
    assert (
        await call(
            sx.app,
            "post",
            SCH,
            sx.tok,
            json={"parent_type": "service", "parent_id": sx.svc},
        )
    )[0] == 400
    assert (await call(sx.app, "post", SCH, sx.tok))[0] in (400, 415)
    assert (
        await call(
            sx.app,
            "post",
            SCH,
            sx.tok,
            json={
                "parent_type": "service",
                "parent_id": 999999999,
                "schedule_cron": "0 * * * *",
            },
        )
    )[0] == 404
    assert (
        await call(
            sx.app,
            "post",
            SCH,
            sx.tok,
            json={"parent_type": "bogus", "parent_id": 1, "schedule_cron": "0 * * * *"},
        )
    )[0] == 404
    assert (
        await call(
            sx.app,
            "post",
            SCH,
            sx.tok,
            json={
                "parent_type": "service",
                "parent_id": sx.svc,
                "schedule_cron": "not a cron",
            },
        )
    )[0] == 400
    st, a = await call(
        sx.app,
        "post",
        SCH,
        sx.tok,
        json={
            "parent_type": "service",
            "parent_id": sx.svc,
            "schedule_cron": "0 * * * *",
            "credential_type": "builtin_secret",
            "credential_id": 1,
            "credential_mapping": {"field": "token"},
        },
    )
    assert st == 201, a
    st, b = await call(
        sx.app,
        "post",
        SCH,
        sx.tok,
        json={
            "parent_type": "software",
            "parent_id": sx.soft,
            "schedule_cron": "*/5 * * * *",
            "is_active": False,
        },
    )
    assert st == 201
    for q in (
        "parent_type=service",
        f"parent_id={sx.svc}",
        "is_active=true",
        "is_active=false",
    ):
        st, js = await call(sx.app, "get", f"{SCH}?{q}", sx.tok)
        assert st == 200, q
    assert (await call(sx.app, "get", f"{SCH}/{a['id']}", sx.tok))[0] == 200
    assert (await call(sx.app, "get", f"{SCH}/{a['id']}", sx.foreign))[0] == 404
    assert (
        await call(sx.app, "put", f"{SCH}/999999999", sx.tok, json={"is_active": True})
    )[0] == 404
    assert (await call(sx.app, "put", f"{SCH}/{a['id']}", sx.tok))[0] in (400, 415)
    assert (
        await call(
            sx.app, "put", f"{SCH}/{a['id']}", sx.tok, json={"schedule_cron": "garbage"}
        )
    )[0] == 400
    assert (await call(sx.app, "delete", f"{SCH}/{a['id']}", sx.foreign))[0] == 404
    assert (await call(sx.app, "delete", f"{SCH}/999999999", sx.tok))[0] == 404
    # due schedules
    past = datetime.now(UTC) - timedelta(minutes=5)
    sx.db(sx.db.sbom_scan_schedules.id == a["id"]).update(
        next_run_at=past, is_active=True
    )
    sx.db.commit()
    assert (await call(sx.app, "get", f"{SCH}/due", sx.notenant))[0] == 400
    st, js = await call(sx.app, "get", f"{SCH}/due", sx.tok)
    assert st == 200 and any(r["id"] == a["id"] for r in js)
    st, js = await call(sx.app, "get", f"{SCH}/due", sx.worker)
    assert st == 200 and any(r["id"] == a["id"] for r in js)


XF = "bug: handler deletes through a TableProxy (del db.<table>[id]) which penguin-dal rejects, so the delete returns 500"


async def test_schedule_update_persists(sx):
    """Updating a schedule persists cron/timestamps and a platform worker may update it too."""
    st, a = await call(
        sx.app,
        "post",
        SCH,
        sx.tok,
        json={
            "parent_type": "service",
            "parent_id": sx.svc,
            "schedule_cron": "0 * * * *",
        },
    )
    when = datetime.now(UTC).isoformat()
    st, js = await call(
        sx.app,
        "put",
        f"{SCH}/{a['id']}",
        sx.tok,
        json={
            "schedule_cron": "*/10 * * * *",
            "is_active": False,
            "credential_type": "x",
            "credential_id": None,
            "credential_mapping": {},
            "last_run_at": when,
            "next_run_at": when,
        },
    )
    assert st == 200 and js["schedule_cron"] == "*/10 * * * *"
    st, _ = await call(
        sx.app,
        "put",
        f"{SCH}/{a['id']}",
        sx.worker,
        json={"last_run_at": when, "next_run_at": when},
    )
    assert st == 200
    st, _ = await call(
        sx.app,
        "put",
        f"{SCH}/{a['id']}",
        sx.worker,
        json={"is_active": True, "next_run_at": 5},
    )
    assert st == 200
    row = sx.db(sx.db.sbom_scan_schedules.id == a["id"]).select().first()
    assert row.schedule_cron == "*/10 * * * *" and row.is_active is True


@pytest.mark.xfail(strict=True, reason=XF)
async def test_schedule_delete_persists(sx):
    """Deleting a schedule should remove the row."""
    st, a = await call(
        sx.app,
        "post",
        SCH,
        sx.tok,
        json={
            "parent_type": "service",
            "parent_id": sx.svc,
            "schedule_cron": "0 * * * *",
        },
    )
    assert (await call(sx.app, "delete", f"{SCH}/{a['id']}", sx.tok))[0] == 204
    assert sx.db(sx.db.sbom_scan_schedules.id == a["id"]).count() == 0


@pytest.mark.xfail(strict=True, reason=XF)
async def test_scan_delete_persists(sx):
    """Deleting a scan should remove the row."""
    scan = await mk_scan(sx)
    assert (await call(sx.app, "delete", f"{S}/{scan['id']}", sx.tok))[0] == 204
    assert sx.db(sx.db.sbom_scans.id == scan["id"]).count() == 0
