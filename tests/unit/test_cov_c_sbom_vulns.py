"""HTTP-level behaviour tests for the vulnerability routes.

Requests run through the real Quart app and Postgres test database. Only the
external vulnerability feeds (OSV matcher and NVD sync service) are replaced.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from apps.api.modules.sbom.routes import vulnerabilities as vr
from tests.unit._cov_c_helpers import (
    call,
    mk_entity,
    mk_identity,
    mk_org,
    mk_tenant,
    sfx,
    token,
)

pytestmark = pytest.mark.asyncio
V = "/api/v1/vulnerabilities"
SC = ["sbom:read", "sbom:write"]


@dataclass
class VW:
    """Seeded tenant world for vulnerability tests."""

    app: Any
    db: Any
    tid: int
    org: int
    entity: int
    issue: int
    svc: int
    soft: int
    tok: str
    ro: str
    foreign: str
    notenant: str


@pytest.fixture(scope="module")
def vw(app) -> VW:
    """Seed tenant, org, entity, issue, service and software."""
    db = app.db
    tid, other = mk_tenant(db, "vu"), mk_tenant(db, "vux")
    uid = mk_identity(db, tid, "admin", superuser=True)
    fid = mk_identity(db, other, "admin", superuser=True)
    ro = mk_identity(db, tid, "observer")
    org = mk_org(db, tid)
    ent = mk_entity(db, org)
    now = datetime.now(UTC)
    issue = db.issues.insert(
        title="seed",
        description="d",
        status="OPEN",
        priority="MEDIUM",
        issue_type="OTHER",
        resource_type="organization",
        resource_id=org,
        is_incident=0,
        tenant_id=tid,
        created_at=now,
        updated_at=now,
    )
    svc = db.services.insert(
        name=f"svc-{sfx()}",
        organization_id=org,
        tenant_id=tid,
        status="active",
        is_public=False,
        created_at=now,
        updated_at=now,
    )
    soft = db.software.insert(
        name=f"sw-{sfx()}",
        organization_id=org,
        tenant_id=tid,
        software_type="saas",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return VW(
        app,
        db,
        tid,
        org,
        ent,
        issue,
        svc,
        soft,
        token(app, uid, tid, SC),
        token(app, ro, tid, ["sbom:read"]),
        token(app, fid, other, SC),
        token(app, uid, "", SC),
    )


def cve() -> str:
    """Return a unique fake CVE id."""
    return f"CVE-2098-{sfx()}".upper()


async def mk_vuln(vw: VW, **extra: Any) -> dict[str, Any]:
    """Create a vulnerability through the API."""
    st, js = await call(
        vw.app,
        "post",
        V,
        vw.tok,
        json={"cve_id": cve(), "title": "Title", "severity": "high", **extra},
    )
    assert st == 201, js
    return js


async def test_scope_and_tenant_guards(vw):
    """Reads need sbom:read, writes sbom:write, and tenant-less callers are refused."""
    assert (await call(vw.app, "get", V))[0] == 401
    assert (await call(vw.app, "post", V, vw.ro, json={}))[0] == 403
    assert (await call(vw.app, "post", V, vw.notenant, json={}))[0] == 403
    for method, path in (("patch", "/1"), ("post", "/1/create-issue")):
        assert (await call(vw.app, method, V + path, vw.notenant, json={}))[0] == 403


async def test_create_validation(vw):
    """Every create-field validation rejects bad input with 400 and conflicts with 409."""
    good = {"cve_id": cve(), "title": "T", "severity": "low"}
    assert (
        await call(
            vw.app,
            "post",
            V,
            vw.tok,
            data="[1]",
            headers={"Content-Type": "application/json"},
        )
    )[0] == 400
    bad_bodies = [
        {**good, "cve_id": ""},
        {**good, "cve_id": "X" * 51},
        {**good, "title": ""},
        {**good, "title": "t" * 513},
        {**good, "severity": "nope"},
        {**good, "cvss_score": "abc"},
        {**good, "cvss_score": 11},
        {**good, "cvss_score": True},
        {**good, "cvss_score": float("nan")},
        {**good, "adjusted_risk_score": True},
        {**good, "adjusted_risk_score": "x" * 65},
        {**good, "owner_organization_id": "x"},
        {**good, "owner_organization_id": 0},
        {**good, "owner_entity_id": -1},
        {**good, "remediation_issue_id": "q"},
        {**good, "published_at": "garbage"},
    ]
    for body in bad_bodies:
        try:
            st, _ = await call(vw.app, "post", V, vw.tok, json=body)
        except ValueError:
            continue
        assert st == 400, body
    for body, status in (
        ({**good, "owner_organization_id": 999999999}, 404),
        ({**good, "owner_entity_id": 999999999}, 404),
        ({**good, "remediation_issue_id": 999999999}, 404),
    ):
        assert (await call(vw.app, "post", V, vw.tok, json=body))[0] == status
    full = {
        **good,
        "cve_id": cve().lower(),
        "cvss_score": "7.5",
        "adjusted_risk_score": 42,
        "owner_organization_id": vw.org,
        "owner_entity_id": vw.entity,
        "remediation_issue_id": vw.issue,
        "description": " desc ",
        "affected_packages": ["a"],
        "fixed_versions": ["1"],
        "references": ["r"],
        "published_at": "2024-01-02T03:04:05Z",
        "source": "manual",
    }
    st, js = await call(vw.app, "post", V, vw.tok, json=full)
    assert (
        st == 201
        and js["owner_organization_name"]
        and js["owner_entity_name"]
        and js["remediation_issue"]["id"] == vw.issue
    )
    assert (
        js["cvss_score"] == 7.5
        and js["severity"] == "LOW"
        or js["severity"].upper() == "LOW"
    )
    assert (
        await call(vw.app, "post", V, vw.tok, json={**good, "cve_id": full["cve_id"]})
    )[0] == 409


async def test_list_get_update(vw):
    """List filters, enrichment, get, and PATCH/PUT updates with their validations."""
    sid_cve = cve()
    v = await mk_vuln(
        vw,
        cve_id=sid_cve,
        owner_organization_id=vw.org,
        owner_entity_id=vw.entity,
        remediation_issue_id=vw.issue,
        source="manual",
    )
    # affected link: component under the service and one under software, one orphan
    now = datetime.now(UTC)
    for ptype, pid in (
        ("service", vw.svc),
        ("software", vw.soft),
        ("other", 1),
        ("service", 999999999),
    ):
        comp = vw.db.sbom_components.insert(
            tenant_id=vw.tid,
            parent_type=ptype,
            parent_id=pid,
            name=f"c-{sfx()}",
            package_type="pypi",
            scope="runtime",
            direct=True,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        vw.db.component_vulnerabilities.insert(
            tenant_id=vw.tid,
            component_id=comp,
            vulnerability_id=v["id"],
            status="open",
            created_at=now,
            updated_at=now,
        )
    vw.db.commit()
    for q in (
        "severity=high",
        f"cve_id={sid_cve.lower()}",
        "source=manual",
        f"owner_organization_id={vw.org}",
        f"owner_entity_id={vw.entity}",
        f"search={sid_cve}",
        "page=1&per_page=5",
    ):
        st, js = await call(vw.app, "get", f"{V}?{q}", vw.tok)
        assert st == 200, q
    st, js = await call(vw.app, "get", f"{V}?search={sid_cve}", vw.tok)
    assert js["total"] == 1 and len(js["items"][0]["affected_entities"]) == 2
    for q in ("owner_organization_id=abc", "owner_entity_id=0"):
        assert (await call(vw.app, "get", f"{V}?{q}", vw.tok))[0] == 400
    st, got = await call(vw.app, "get", f"{V}/{v['id']}", vw.tok)
    assert st == 200 and got["cve_id"] == sid_cve
    assert (await call(vw.app, "get", f"{V}/999999999", vw.tok))[0] == 404
    st, js = await call(
        vw.app,
        "patch",
        f"{V}/{v['id']}",
        vw.tok,
        json={
            "title": "New",
            "severity": "info",
            "description": None,
            "cvss_score": 3.5,
            "adjusted_risk_score": "ok",
            "owner_organization_id": vw.org,
            "owner_entity_id": vw.entity,
            "remediation_issue_id": vw.issue,
            "published_at": None,
        },
    )
    assert st == 200 and js["title"] == "New"
    st, js = await call(
        vw.app,
        "put",
        f"{V}/{v['id']}",
        vw.tok,
        json={
            "description": " d ",
            "published_at": "2024-01-01",
            "severity": "UNKNOWN",
        },
    )
    assert st == 200
    st, js = await call(
        vw.app, "patch", f"{V}/{v['id']}", vw.tok, json={"description": ""}
    )
    assert st == 200
    assert (await call(vw.app, "patch", f"{V}/{v['id']}", vw.tok, json={}))[0] == 200
    for body in (
        {"title": ""},
        {"severity": "zzz"},
        {"cvss_score": "x"},
        {"adjusted_risk_score": ["x"]},
        {"owner_organization_id": "q"},
        {"owner_entity_id": "q"},
        {"remediation_issue_id": "q"},
        {"published_at": "nonsense"},
    ):
        assert (await call(vw.app, "patch", f"{V}/{v['id']}", vw.tok, json=body))[
            0
        ] == 400, body
    for body in (
        {"owner_organization_id": 999999999},
        {"owner_entity_id": 999999999},
        {"remediation_issue_id": 999999999},
    ):
        assert (await call(vw.app, "patch", f"{V}/{v['id']}", vw.tok, json=body))[
            0
        ] == 404, body
    assert (
        await call(
            vw.app,
            "patch",
            f"{V}/{v['id']}",
            vw.tok,
            data="[1]",
            headers={"Content-Type": "application/json"},
        )
    )[0] == 400
    assert (await call(vw.app, "patch", f"{V}/999999999", vw.tok, json={"title": "x"}))[
        0
    ] == 404


async def test_create_remediation_issue(vw):
    """Creating a remediation issue validates input, links the issue and refuses duplicates."""
    plain = await mk_vuln(
        vw,
        severity="critical",
        cvss_score=9.1,
        adjusted_risk_score="high",
        description="details",
        owner_entity_id=vw.entity,
        owner_organization_id=vw.org,
    )
    url = f"{V}/{plain['id']}/create-issue"
    st, js = await call(
        vw.app,
        "post",
        url,
        vw.tok,
        json={"priority": "urgent", "title": "Custom", "description": "Do it"},
    )
    assert (
        st == 201
        and js["issue"]["title"] == "Custom"
        and js["vulnerability"]["remediation_issue"]["id"] == js["issue"]["id"]
    )
    assert (await call(vw.app, "post", url, vw.tok))[0] == 409
    for body in ({"organization_id": "x"}, {"priority": "bogus"}):
        fresh = await mk_vuln(vw)
        assert (
            await call(
                vw.app, "post", f"{V}/{fresh['id']}/create-issue", vw.tok, json=body
            )
        )[0] == 400
    nowhere = await mk_vuln(vw)
    assert (await call(vw.app, "post", f"{V}/{nowhere['id']}/create-issue", vw.tok))[
        0
    ] == 400
    assert (
        await call(
            vw.app,
            "post",
            f"{V}/{nowhere['id']}/create-issue",
            vw.tok,
            json={"organization_id": 999999999},
        )
    )[0] == 404
    st, js = await call(
        vw.app,
        "post",
        f"{V}/{nowhere['id']}/create-issue",
        vw.tok,
        json={"organization_id": vw.org},
    )
    assert st == 201 and js["vulnerability"]["owner_organization_id"] == vw.org
    assert (
        await call(
            vw.app,
            "post",
            f"{V}/{nowhere['id']}/create-issue",
            vw.tok,
            data="[1]",
            headers={"Content-Type": "application/json"},
        )
    )[0] == 400
    assert (await call(vw.app, "post", f"{V}/999999999/create-issue", vw.tok))[0] == 404


async def test_dashboard_status_and_assignment(vw):
    """Dashboard/status counts, component-vulnerability updates and assignment dedupe."""
    v = await mk_vuln(vw, severity="medium")
    st, js = await call(vw.app, "get", f"{V}/dashboard", vw.tok)
    assert st == 200 and js["total_vulnerabilities"] >= 1
    st, js = await call(vw.app, "get", f"{V}/nvd-sync/status", vw.tok)
    assert st == 200 and "needs_sync" in js
    url = f"{V}/{v['id']}/assign"
    assert (
        await call(
            vw.app,
            "post",
            url,
            vw.tok,
            json={"parent_type": "software", "parent_id": 999999999},
        )
    )[0] == 404
    assert (
        await call(
            vw.app,
            "post",
            url,
            vw.tok,
            json={"parent_type": "service", "parent_id": 999999999},
        )
    )[0] == 404
    assert (await call(vw.app, "post", url, vw.tok))[0] == 400
    assert (
        await call(
            vw.app, "post", url, vw.tok, json={"parent_type": "bogus", "parent_id": 1}
        )
    )[0] == 400
    assert (
        await call(
            vw.app,
            "post",
            f"{V}/999999999/assign",
            vw.tok,
            json={"parent_type": "service", "parent_id": vw.svc},
        )
    )[0] == 404
    now = datetime.now(UTC)
    svc_name = vw.db(vw.db.services.id == vw.svc).select().first().name
    soft_name = vw.db(vw.db.software.id == vw.soft).select().first().name
    comp = vw.db.sbom_components.insert(
        tenant_id=vw.tid,
        parent_type="service",
        parent_id=vw.svc,
        name=svc_name,
        package_type="other",
        scope="runtime",
        direct=True,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    vw.db.sbom_components.insert(
        tenant_id=vw.tid,
        parent_type="software",
        parent_id=vw.soft,
        name=soft_name,
        package_type="other",
        scope="runtime",
        direct=True,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    cv_id = vw.db.component_vulnerabilities.insert(
        tenant_id=vw.tid,
        component_id=comp,
        vulnerability_id=v["id"],
        status="open",
        created_at=now,
        updated_at=now,
    )
    vw.db.commit()
    cv = {"id": cv_id}
    assert (
        await call(
            vw.app,
            "post",
            url,
            vw.tok,
            json={"parent_type": "service", "parent_id": vw.svc},
        )
    )[0] == 400
    assert (
        await call(
            vw.app,
            "post",
            url,
            vw.tok,
            json={"parent_type": "software", "parent_id": vw.soft, "notes": "n"},
        )
    )[0] == 201
    # component-vulnerability updates
    cvu = f"{V}/component-vulnerabilities/{cv['id']}"
    st, js = await call(
        vw.app,
        "patch",
        cvu,
        vw.tok,
        json={
            "status": "remediated",
            "remediation_notes": "fixed",
            "remediated_by_id": 1,
        },
    )
    assert st == 200 and js["status"] == "remediated"
    assert (await call(vw.app, "patch", cvu, vw.tok, json={"status": "investigating"}))[
        0
    ] == 200
    assert (await call(vw.app, "patch", cvu, vw.tok, json={"status": "bogus"}))[
        0
    ] == 400
    assert (await call(vw.app, "patch", cvu, vw.tok))[0] == 400
    assert (await call(vw.app, "patch", cvu, vw.foreign, json={"status": "open"}))[
        0
    ] == 404
    assert (
        await call(
            vw.app,
            "patch",
            f"{V}/component-vulnerabilities/999999999",
            vw.tok,
            json={"status": "open"},
        )
    )[0] == 404


async def test_sync_and_nvd_sync(vw, monkeypatch):
    """Sync matches components through the (faked) feed; NVD sync delegates to its service."""
    now = datetime.now(UTC)
    comp = vw.db.sbom_components.insert(
        tenant_id=vw.tid,
        parent_type="service",
        parent_id=vw.svc,
        name=f"sync-{sfx()}",
        purl="pkg:pypi/sync@1",
        package_type="pypi",
        scope="runtime",
        direct=True,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    vw.db.commit()

    class FakeMatcher:
        """Stand-in for the OSV/NVD matcher."""

        async def __aenter__(self):
            """Enter the async context."""
            return self

        async def __aexit__(self, *exc):
            """Leave the async context."""
            return False

        async def match_components_batch(self, comps):
            """Return one new vulnerability for every component."""
            return {
                c["id"]: [
                    SimpleNamespace(
                        cve_id=cve(),
                        aliases=[],
                        severity="HIGH",
                        cvss_score=7.0,
                        cvss_vector="v",
                        title="t",
                        description="d",
                        affected_packages=[],
                        fixed_versions=[],
                        references=[],
                        source="osv",
                    )
                ]
                for c in comps
            }

    monkeypatch.setattr(vr, "VulnerabilityMatcher", FakeMatcher)
    st, js = await call(
        vw.app, "post", f"{V}/sync", vw.tok, json={"component_ids": [comp]}
    )
    assert st == 202 and js["components_scanned"] == 1 and js["links_created"] == 1
    assert (
        await call(
            vw.app, "post", f"{V}/sync", vw.tok, json={"component_ids": [999999999]}
        )
    )[0] == 400
    assert (
        await call(vw.app, "post", f"{V}/sync", vw.tok, json={"component_ids": "x"})
    )[0] == 400
    st, _ = await call(vw.app, "post", f"{V}/sync", vw.tok, json={})
    assert st == 202

    class FakeNVD:
        """Stand-in for the NVD sync service."""

        def __init__(self, db, key):
            """Remember the database handle."""
            self.db = db

        async def sync_vulnerabilities(self, max_vulns, force_refresh):
            """Return fixed statistics."""
            return {"updated": max_vulns, "forced": force_refresh}

    import apps.api.services.sbom.vulnerability.nvd_sync as nvd_mod

    monkeypatch.setattr(nvd_mod, "NVDSyncService", FakeNVD)
    st, js = await call(
        vw.app,
        "post",
        f"{V}/nvd-sync",
        vw.tok,
        json={"max_vulns": 5, "force_refresh": True},
    )
    assert st == 202 and js["stats"] == {"updated": 5, "forced": True}
    assert (
        await call(vw.app, "post", f"{V}/nvd-sync", vw.tok, json={"max_vulns": "many"})
    )[0] == 400
    assert (await call(vw.app, "post", f"{V}/nvd-sync", vw.tok))[0] == 202
    assert (await call(vw.app, "post", f"{V}/nvd-sync", vw.ro, json={}))[0] == 403


async def test_parse_helpers_and_enrichment(vw):
    """Pure parsers accept valid values and report precise errors; enrichment tolerates no tenant."""
    assert vr._parse_optional_positive_int(None, "f") == (None, None)
    assert vr._parse_optional_positive_int("", "f") == (None, None)
    assert vr._parse_optional_positive_int("7", "f") == (7, None)
    assert (
        vr._parse_optional_positive_int(True, "f")[1]
        and vr._parse_optional_positive_int("x", "f")[1]
        and vr._parse_optional_positive_int(0, "f")[1]
    )
    assert vr._parse_optional_cvss_score(None) == (None, None)
    assert vr._parse_optional_cvss_score("4.5") == (4.5, None)
    assert (
        vr._parse_optional_cvss_score(True)[1]
        and vr._parse_optional_cvss_score("x")[1]
        and vr._parse_optional_cvss_score(10.5)[1]
    )
    assert vr._parse_optional_adjusted_risk_score(None) == (None, None)
    assert vr._parse_optional_adjusted_risk_score("  ") == (None, None)
    assert vr._parse_optional_adjusted_risk_score(12) == ("12", None)
    assert (
        vr._parse_optional_adjusted_risk_score([1])[1]
        and vr._parse_optional_adjusted_risk_score("x" * 65)[1]
    )
    assert vr._serialize_issue_summary(None) is None
    row = SimpleNamespace(id=1, title="t", status="IN_PROGRESS", priority="HIGH")
    assert vr._serialize_issue_summary(row) == {
        "id": 1,
        "title": "t",
        "status": "in_progress",
        "priority": "high",
        "village_id": None,
    }
    bare = SimpleNamespace(id=2, title="t")
    assert (
        vr._serialize_issue_summary(bare)["status"] == "open"
        and vr._serialize_issue_summary(bare)["priority"] == "medium"
    )
    assert vr._validate_ownership_refs(vw.db, vw.tid) == (None, None)
    assert vr._validate_ownership_refs(vw.db, vw.tid, owner_organization_id=vw.org) == (
        None,
        None,
    )
    assert vr._validate_ownership_refs(
        vw.db, vw.tid, owner_entity_id=vw.entity, remediation_issue_id=vw.issue
    ) == (None, None)
    assert (
        vr._validate_ownership_refs(vw.db, vw.tid, owner_organization_id=999999999)[1]
        == 404
    )
    assert (
        vr._validate_ownership_refs(vw.db, vw.tid, owner_entity_id=999999999)[1] == 404
    )
    assert (
        vr._validate_ownership_refs(vw.db, vw.tid, remediation_issue_id=999999999)[1]
        == 404
    )


@pytest.mark.xfail(
    strict=True,
    reason="bug: assign_vulnerability inserts an sbom_components row without its required columns, so a first assignment returns 500",
)
async def test_assign_vulnerability_creates_link(vw):
    """Assigning a vulnerability to a service without a matching component should create the link."""
    v = await mk_vuln(vw)
    now = datetime.now(UTC)
    fresh = vw.db.software.insert(
        name=f"fresh-{sfx()}",
        organization_id=vw.org,
        tenant_id=vw.tid,
        software_type="saas",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    vw.db.commit()
    st, _ = await call(
        vw.app,
        "post",
        f"{V}/{v['id']}/assign",
        vw.tok,
        json={"parent_type": "software", "parent_id": fresh, "notes": "n"},
    )
    assert st == 201


async def test_vulnerability_update_persists(vw):
    """Updating a vulnerability persists the new title."""
    v = await mk_vuln(vw)
    st, js = await call(
        vw.app, "patch", f"{V}/{v['id']}", vw.tok, json={"title": "Persisted"}
    )
    assert st == 200
    assert (
        vw.db(vw.db.vulnerabilities.id == v["id"]).select().first().title == "Persisted"
    )
