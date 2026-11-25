"""HTTP-level behaviour tests for SBOM software, services, components and licence policies.

Requests run through the real Quart app against the Postgres test database
with signed tokens. Assertions cover response bodies, stored rows, exporter
output and tenant isolation of by-id access.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

from apps.api.modules.sbom.routes import license_policies as lp
from tests.unit._cov_c_helpers import call, mk_identity, mk_org, mk_tenant, sfx, token

pytestmark = pytest.mark.asyncio
SC = ["sbom:read", "sbom:write"]


@dataclass
class SW:
    """Seeded tenants, organizations and tokens."""

    app: Any
    db: Any
    tid: int
    org: int
    org2: int
    uid: int
    tok: str
    ro: str
    foreign: str
    notenant: str


@pytest.fixture(scope="module")
def sw(app) -> SW:
    """Seed one tenant with two orgs, a superuser and a foreign tenant."""
    db = app.db
    tid, other = mk_tenant(db, "sb"), mk_tenant(db, "sbx")
    uid = mk_identity(db, tid, "admin", superuser=True)
    fid = mk_identity(db, other, "admin", superuser=True)
    ro_id = mk_identity(db, tid, "observer")
    return SW(
        app,
        db,
        tid,
        mk_org(db, tid),
        mk_org(db, tid),
        uid,
        token(app, uid, tid, SC),
        token(app, ro_id, tid, ["sbom:read"]),
        token(app, fid, other, SC),
        token(app, uid, "", SC),
    )


def mk_component(sw: SW, parent_type: str, parent_id: int, **kw: Any) -> int:
    """Insert an SBOM component row directly for the given parent."""
    now = datetime.now(UTC)
    cid = sw.db.sbom_components.insert(
        tenant_id=sw.tid,
        parent_type=parent_type,
        parent_id=parent_id,
        name=kw.pop("name", f"pkg-{sfx()}"),
        version=kw.pop("version", "1.0.0"),
        package_type=kw.pop("package_type", "pypi"),
        scope="runtime",
        direct=True,
        is_active=True,
        created_at=now,
        updated_at=now,
        **kw,
    )
    sw.db.commit()
    return cid


async def test_scope_rejections(sw):
    """Read-only callers cannot write and anonymous callers are refused."""
    assert (await call(sw.app, "get", "/api/v1/software"))[0] == 401
    for url, body in (
        ("/api/v1/software", {}),
        ("/api/v1/services", {}),
        ("/api/v1/license-policies", {}),
        ("/api/v1/sbom/components", {}),
    ):
        assert (await call(sw.app, "post", url, sw.ro, json=body))[0] == 403


async def test_software_lifecycle(sw):
    """Software CRUD with filters, POC validation, SBOM listing and exports."""
    name = f"app-{sfx()}"
    st, s = await call(
        sw.app,
        "post",
        "/api/v1/software",
        sw.tok,
        json={
            "name": name,
            "organization_id": sw.org,
            "software_type": "saas",
            "purchasing_poc_id": sw.uid,
            "vendor": "Acme",
            "tags": ["a"],
            "description": "findme",
        },
    )
    assert st == 201, s
    sid = s["id"]
    for q in (
        f"organization_id={sw.org}",
        "software_type=saas",
        "is_active=true",
        "is_active=false",
        f"search={name}",
    ):
        st, js = await call(sw.app, "get", f"/api/v1/software?{q}", sw.tok)
        assert st == 200, q
    st, js = await call(sw.app, "get", f"/api/v1/software?search={name}", sw.tok)
    assert js["total"] == 1
    assert (await call(sw.app, "get", f"/api/v1/software/{sid}", sw.tok))[0] == 200
    assert (await call(sw.app, "get", f"/api/v1/software/{sid}", sw.foreign))[0] == 404
    assert (
        await call(
            sw.app,
            "post",
            "/api/v1/software",
            sw.tok,
            json={"name": "x", "organization_id": 999999999, "software_type": "saas"},
        )
    )[0] == 404
    assert (
        await call(
            sw.app,
            "post",
            "/api/v1/software",
            sw.tok,
            json={
                "name": "x",
                "organization_id": sw.org,
                "software_type": "saas",
                "purchasing_poc_id": 999999999,
            },
        )
    )[0] == 404
    assert (await call(sw.app, "post", "/api/v1/software", sw.tok, json={"name": "x"}))[
        0
    ] == 400
    assert (
        await call(
            sw.app,
            "put",
            f"/api/v1/software/{sid}",
            sw.tok,
            json={"purchasing_poc_id": 999999999},
        )
    )[0] == 404
    # components + exports
    st, js = await call(sw.app, "get", f"/api/v1/software/{sid}/sbom", sw.tok)
    assert st == 200 and js == []
    assert (await call(sw.app, "get", f"/api/v1/software/{sid}/sbom/export", sw.tok))[
        0
    ] == 400
    mk_component(
        sw, "software", sid, purl="pkg:pypi/requests@2.0.0", license_name="MIT"
    )
    assert (
        len((await call(sw.app, "get", f"/api/v1/software/{sid}/sbom", sw.tok))[1]) == 1
    )
    for fmt in ("cyclonedx_json", "cyclonedx_xml", "spdx"):
        st, _ = await call(
            sw.app, "get", f"/api/v1/software/{sid}/sbom/export?format={fmt}", sw.tok
        )
        assert st == 200, fmt
    assert (
        await call(
            sw.app, "get", f"/api/v1/software/{sid}/sbom/export?format=bogus", sw.tok
        )
    )[0] == 400
    assert (await call(sw.app, "get", "/api/v1/software/999999999/sbom", sw.tok))[
        0
    ] == 404
    assert (
        await call(sw.app, "get", "/api/v1/software/999999999/sbom/export", sw.tok)
    )[0] == 404
    assert (await call(sw.app, "delete", f"/api/v1/software/{sid}", sw.foreign))[
        0
    ] == 404
    assert (await call(sw.app, "delete", "/api/v1/software/999999999", sw.tok))[
        0
    ] == 404


async def test_services_lifecycle(sw):
    """Service CRUD, repository scan seeding, SBOM listing/export and endpoint listing."""
    name = f"svc-{sfx()}"
    st, s = await call(
        sw.app,
        "post",
        "/api/v1/services",
        sw.tok,
        json={
            "name": name,
            "organization_id": sw.org,
            "poc_identity_id": sw.uid,
            "repository_url": "https://example.test/r.git",
            "port": 8080,
            "tags": ["t"],
            "paths": ["/a", "/b"],
        },
    )
    assert st == 201, s
    sid = s["id"]
    sw.db(sw.db.services.id == sid).update(
        paths=[{"path": "/a", "method": "get"}, {"path": "/c"}, "/b"]
    )
    sw.db.commit()
    assert (
        sw.db(
            (sw.db.sbom_scans.parent_type == "service")
            & (sw.db.sbom_scans.parent_id == sid)
        ).count()
        == 1
    )
    st, s2 = await call(
        sw.app,
        "post",
        "/api/v1/services",
        sw.tok,
        json={"name": f"svc-{sfx()}", "organization_id": sw.org},
    )
    assert st == 201
    for q in (f"organization_id={sw.org}", "status=active", f"search={name}"):
        assert (await call(sw.app, "get", f"/api/v1/services?{q}", sw.tok))[0] == 200, q
    assert (await call(sw.app, "get", f"/api/v1/services/{sid}", sw.tok))[0] == 200
    assert (await call(sw.app, "get", f"/api/v1/services/{sid}", sw.foreign))[0] == 404
    assert (
        await call(
            sw.app,
            "post",
            "/api/v1/services",
            sw.tok,
            json={"name": "x", "organization_id": 999999999},
        )
    )[0] == 404
    assert (
        await call(
            sw.app,
            "post",
            "/api/v1/services",
            sw.tok,
            json={"name": "x", "organization_id": sw.org, "poc_identity_id": 999999999},
        )
    )[0] == 404
    # sbom + scan
    assert (await call(sw.app, "get", f"/api/v1/services/{sid}/sbom", sw.tok))[1] == []
    assert (await call(sw.app, "get", f"/api/v1/services/{sid}/sbom/export", sw.tok))[
        0
    ] == 400
    mk_component(sw, "service", sid, purl="pkg:pypi/flask@3.0.0")
    assert (
        len((await call(sw.app, "get", f"/api/v1/services/{sid}/sbom", sw.tok))[1]) == 1
    )
    for fmt in ("cyclonedx_json", "cyclonedx_xml", "spdx"):
        assert (
            await call(
                sw.app,
                "get",
                f"/api/v1/services/{sid}/sbom/export?format={fmt}",
                sw.tok,
            )
        )[0] == 200, fmt
    assert (
        await call(
            sw.app, "get", f"/api/v1/services/{sid}/sbom/export?format=zzz", sw.tok
        )
    )[0] == 400
    for p in ("sbom", "sbom/export"):
        assert (await call(sw.app, "get", f"/api/v1/services/999999999/{p}", sw.tok))[
            0
        ] == 404
    st, js = await call(
        sw.app,
        "post",
        f"/api/v1/services/{sid}/sbom/scan",
        sw.tok,
        json={"scan_type": "git_clone", "repository_branch": "dev"},
    )
    assert st == 201 and js["status"] == "pending"
    assert (await call(sw.app, "post", f"/api/v1/services/{sid}/sbom/scan", sw.tok))[
        0
    ] == 201
    assert (await call(sw.app, "post", "/api/v1/services/999999999/sbom/scan", sw.tok))[
        0
    ] == 404
    assert (await call(sw.app, "delete", f"/api/v1/services/{sid}", sw.foreign))[
        0
    ] == 404
    assert (await call(sw.app, "delete", "/api/v1/services/999999999", sw.tok))[
        0
    ] == 404


async def test_components_lifecycle(sw):
    """Components attach to in-tenant parents, filter, update, delete and expose vulnerabilities."""
    st, svc = await call(
        sw.app,
        "post",
        "/api/v1/services",
        sw.tok,
        json={"name": f"svc-{sfx()}", "organization_id": sw.org},
    )
    base = {
        "parent_type": "service",
        "parent_id": svc["id"],
        "name": f"lib-{sfx()}",
        "package_type": "npm",
        "version": "1.2.3",
    }
    st, c = await call(
        sw.app,
        "post",
        "/api/v1/sbom/components",
        sw.tok,
        json={**base, "license_id": "MIT", "license_name": "MIT", "direct": False},
    )
    assert st == 201, c
    cid = c["id"]
    st, child = await call(
        sw.app,
        "post",
        "/api/v1/sbom/components",
        sw.tok,
        json={
            **base,
            "parent_type": "sbom_component",
            "parent_id": cid,
            "name": f"c-{sfx()}",
        },
    )
    assert st == 201
    soft = mk_component(sw, "software", 0)
    for pt, pid in (
        ("software", 999999999),
        ("service", 999999999),
        ("sbom_component", 999999999),
        ("bogus", 1),
    ):
        assert (
            await call(
                sw.app,
                "post",
                "/api/v1/sbom/components",
                sw.tok,
                json={**base, "parent_type": pt, "parent_id": pid},
            )
        )[0] == 404
    assert (
        await call(
            sw.app, "post", "/api/v1/sbom/components", sw.tok, json={"name": "x"}
        )
    )[0] == 400
    assert (await call(sw.app, "post", "/api/v1/sbom/components", sw.tok))[0] in (
        400,
        415,
    )
    for q in (
        "parent_type=service",
        f"parent_id={svc['id']}",
        "package_type=npm",
        "license_id=MIT",
        "direct=false",
        f"search={base['name']}",
    ):
        st, js = await call(sw.app, "get", f"/api/v1/sbom/components?{q}", sw.tok)
        assert st == 200 and js["total"] >= 1, q
    assert (await call(sw.app, "get", f"/api/v1/sbom/components/{cid}", sw.tok))[
        0
    ] == 200
    assert (await call(sw.app, "get", f"/api/v1/sbom/components/{cid}", sw.foreign))[
        0
    ] == 404
    assert (
        await call(
            sw.app,
            "put",
            "/api/v1/sbom/components/999999999",
            sw.tok,
            json={"name": "z"},
        )
    )[0] == 404
    assert (await call(sw.app, "put", f"/api/v1/sbom/components/{cid}", sw.tok))[0] in (
        400,
        415,
    )
    # vulnerabilities for the component
    now = datetime.now(UTC)
    vids = []
    for sev, score in (("CRITICAL", 9.8), ("LOW", 2.0)):
        vids.append(
            sw.db.vulnerabilities.insert(
                tenant_id=sw.tid,
                cve_id=f"CVE-2099-{sfx()}",
                severity=sev,
                cvss_score=score,
                title="t",
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        )
    for v, status in zip(vids, ("open", "fixed"), strict=True):
        sw.db.component_vulnerabilities.insert(
            tenant_id=sw.tid,
            component_id=cid,
            vulnerability_id=v,
            status=status,
            created_at=now,
            updated_at=now,
        )
    sw.db.commit()
    st, js = await call(
        sw.app, "get", f"/api/v1/sbom/components/{cid}/vulnerabilities", sw.tok
    )
    assert st == 200 and len(js) == 2 and js[0]["link_status"] in ("open", "fixed")
    st, js = await call(
        sw.app,
        "get",
        f"/api/v1/sbom/components/{cid}/vulnerabilities?status=open&severity=critical",
        sw.tok,
    )
    assert st == 200 and len(js) == 1
    assert (
        await call(
            sw.app,
            "get",
            f"/api/v1/sbom/components/{child['id']}/vulnerabilities",
            sw.tok,
        )
    )[1] == []
    assert (
        await call(
            sw.app, "get", "/api/v1/sbom/components/999999999/vulnerabilities", sw.tok
        )
    )[0] == 404
    del soft
    assert (
        await call(
            sw.app, "delete", f"/api/v1/sbom/components/{child['id']}", sw.foreign
        )
    )[0] == 404
    assert (await call(sw.app, "delete", "/api/v1/sbom/components/999999999", sw.tok))[
        0
    ] == 404


async def test_license_policies(sw):
    """Policy CRUD with validation, plus the stateless component check."""
    base = {
        "name": f"pol-{sfx()}",
        "organization_id": sw.org,
        "allowed_licenses": ["MIT", "Apache-*"],
        "denied_licenses": ["GPL-*"],
        "action": "block",
    }
    st, p = await call(sw.app, "post", "/api/v1/license-policies", sw.tok, json=base)
    assert st == 201, p
    pid = p["id"]
    for q in (f"organization_id={sw.org}", "is_active=true", "is_active=false"):
        assert (await call(sw.app, "get", f"/api/v1/license-policies?{q}", sw.tok))[
            0
        ] == 200, q
    assert (await call(sw.app, "get", f"/api/v1/license-policies/{pid}", sw.tok))[
        0
    ] == 200
    assert (await call(sw.app, "get", f"/api/v1/license-policies/{pid}", sw.foreign))[
        0
    ] == 404
    assert (await call(sw.app, "post", "/api/v1/license-policies", sw.tok))[0] in (
        400,
        415,
    )
    assert (
        await call(
            sw.app, "post", "/api/v1/license-policies", sw.tok, json={"name": "x"}
        )
    )[0] == 400
    assert (
        await call(
            sw.app,
            "post",
            "/api/v1/license-policies",
            sw.tok,
            json={**base, "organization_id": 999999999},
        )
    )[0] == 404
    assert (
        await call(
            sw.app,
            "post",
            "/api/v1/license-policies",
            sw.tok,
            json={**base, "action": "allow"},
        )
    )[0] == 400
    assert (
        await call(
            sw.app,
            "put",
            f"/api/v1/license-policies/{pid}",
            sw.tok,
            json={"action": "nope"},
        )
    )[0] == 400
    assert (await call(sw.app, "put", f"/api/v1/license-policies/{pid}", sw.tok))[
        0
    ] in (400, 415)
    assert (
        await call(
            sw.app,
            "put",
            "/api/v1/license-policies/999999999",
            sw.tok,
            json={"name": "x"},
        )
    )[0] == 404
    # check endpoint
    st, js = await call(
        sw.app,
        "post",
        "/api/v1/license-policies/check",
        sw.tok,
        json={
            "organization_id": sw.org,
            "components": [
                {"name": "a", "license_name": "AGPL-3.0"},
                {"name": "b", "license_name": "MIT"},
                {"name": "nolic"},
            ],
        },
    )
    assert st == 200
    assert (
        await call(
            sw.app,
            "post",
            "/api/v1/license-policies/check",
            sw.tok,
            json={"components": "x"},
        )
    )[0] == 400
    assert (
        await call(sw.app, "post", "/api/v1/license-policies/check", sw.tok, json={})
    )[0] == 400
    assert (await call(sw.app, "post", "/api/v1/license-policies/check", sw.tok))[
        0
    ] in (400, 415)
    assert (
        await call(
            sw.app,
            "post",
            "/api/v1/license-policies/check",
            sw.tok,
            json={"components": []},
        )
    )[0] == 400
    assert (
        await call(sw.app, "delete", f"/api/v1/license-policies/{pid}", sw.foreign)
    )[0] == 404
    assert (await call(sw.app, "delete", "/api/v1/license-policies/999999999", sw.tok))[
        0
    ] == 404


async def test_license_policy_matching_helpers():
    """Pattern and policy matching cover denied, not-allowed, allowed and missing licences."""
    assert lp._match_license_pattern("MIT", "mit") is True
    assert lp._match_license_pattern("GPL-3.0", "GPL-*") is True
    assert lp._match_license_pattern("", "x") is False
    assert lp._match_license_pattern("x", "") is False
    pol = {
        "name": "p",
        "action": "block",
        "allowed_licenses": ["MIT"],
        "denied_licenses": ["GPL-*"],
    }
    assert lp._check_component_against_policy(
        {"name": "a", "license_name": "GPL-2.0"}, pol
    )["reason"].endswith("denied by policy")
    assert lp._check_component_against_policy(
        {"name": "a", "license_name": "BSD"}, pol
    )["reason"].endswith("not in allowed list")
    assert (
        lp._check_component_against_policy({"name": "a", "license_name": "mit"}, pol)
        is None
    )
    assert lp._check_component_against_policy({"name": "a"}, pol) is None
    assert (
        lp._check_component_against_policy(
            {"name": "a", "license_name": "BSD"}, {"name": "p", "action": "warn"}
        )
        is None
    )


XF2 = "bug: update handlers read body.organization_id, which the Update request model does not define, so every PUT returns 500"
XF_DEL = "bug: handler deletes through a TableProxy (del db.<table>[id]) which penguin-dal rejects, so the delete returns 500"


@pytest.mark.xfail(strict=True, reason=XF2)
async def test_software_update_persists(sw):
    """Updating software should persist and return the new state."""
    st, s = await call(
        sw.app,
        "post",
        "/api/v1/software",
        sw.tok,
        json={"name": f"u-{sfx()}", "organization_id": sw.org, "software_type": "saas"},
    )
    st, js = await call(
        sw.app,
        "put",
        f"/api/v1/software/{s['id']}",
        sw.tok,
        json={"name": "renamed", "vendor": "Beta", "seats": 4},
    )
    assert st == 200 and js["name"] == "renamed"
    assert (
        await call(
            sw.app, "put", "/api/v1/software/999999999", sw.tok, json={"name": "z"}
        )
    )[0] == 404
    assert (
        await call(
            sw.app, "put", f"/api/v1/software/{s['id']}", sw.foreign, json={"name": "z"}
        )
    )[0] == 404


@pytest.mark.xfail(strict=True, reason=XF2)
async def test_service_update_persists(sw):
    """Updating a service should persist and return the new state."""
    st, s = await call(
        sw.app,
        "post",
        "/api/v1/services",
        sw.tok,
        json={"name": f"u-{sfx()}", "organization_id": sw.org},
    )
    st, js = await call(
        sw.app,
        "put",
        f"/api/v1/services/{s['id']}",
        sw.tok,
        json={"name": "renamed", "port": 99},
    )
    assert st == 200 and js["name"] == "renamed"
    assert (
        await call(
            sw.app,
            "put",
            f"/api/v1/services/{s['id']}",
            sw.tok,
            json={"poc_identity_id": 999999999},
        )
    )[0] == 404
    assert (
        await call(
            sw.app, "put", "/api/v1/services/999999999", sw.tok, json={"name": "z"}
        )
    )[0] == 404


async def test_component_update_persists(sw):
    """Updating a component persists the new version and flags."""
    cid = mk_component(sw, "software", 0)
    st, js = await call(
        sw.app,
        "put",
        f"/api/v1/sbom/components/{cid}",
        sw.tok,
        json={"version": "9.9.9", "description": "d", "is_active": False, "unknown": 1},
    )
    assert (
        st == 200 and js["data"]["version"] == "9.9.9"
        if "data" in js
        else js["version"] == "9.9.9"
    )
    row = sw.db(sw.db.sbom_components.id == cid).select().first()
    assert row.version == "9.9.9" and row.is_active is False


@pytest.mark.xfail(
    strict=True,
    reason="bug: license policy updates assign through a TableProxy (db.license_policies[id] = ...) and return 500",
)
async def test_license_policy_update_persists(sw):
    """Updating a licence policy should persist and return the new state."""
    st, p = await call(
        sw.app,
        "post",
        "/api/v1/license-policies",
        sw.tok,
        json={"name": f"p-{sfx()}", "organization_id": sw.org, "action": "block"},
    )
    st, js = await call(
        sw.app,
        "put",
        f"/api/v1/license-policies/{p['id']}",
        sw.tok,
        json={"name": "n2", "action": "warn", "is_active": True},
    )
    assert st == 200 and js["action"] == "warn"


@pytest.mark.xfail(
    strict=True,
    reason="bug: services/endpoints filters with `paths is not None` and `paths != ''` on a JSON column, which PostgreSQL rejects (500)",
)
async def test_service_endpoints_listing(sw):
    """Listing service endpoints should expand the path entries and honour search and method filters."""
    st, js = await call(sw.app, "get", "/api/v1/services/endpoints", sw.tok)
    assert st == 200
    st, js = await call(
        sw.app, "get", "/api/v1/services/endpoints?search=/a&method=get", sw.tok
    )
    assert st == 200


@pytest.mark.xfail(strict=True, reason=XF_DEL)
@pytest.mark.parametrize("kind", ["software", "service", "component", "policy"])
async def test_deletes_persist(sw, kind):
    """Deleting software, a service, a component or a licence policy should remove the row."""
    if kind == "software":
        st, r = await call(
            sw.app,
            "post",
            "/api/v1/software",
            sw.tok,
            json={
                "name": f"d-{sfx()}",
                "organization_id": sw.org,
                "software_type": "saas",
            },
        )
        url = f"/api/v1/software/{r['id']}"
    elif kind == "service":
        st, r = await call(
            sw.app,
            "post",
            "/api/v1/services",
            sw.tok,
            json={"name": f"d-{sfx()}", "organization_id": sw.org},
        )
        url = f"/api/v1/services/{r['id']}"
    elif kind == "component":
        url = f"/api/v1/sbom/components/{mk_component(sw, 'software', 0)}"
    else:
        st, r = await call(
            sw.app,
            "post",
            "/api/v1/license-policies",
            sw.tok,
            json={"name": f"p-{sfx()}", "organization_id": sw.org, "action": "block"},
        )
        url = f"/api/v1/license-policies/{r['id']}"
    assert (await call(sw.app, "delete", url, sw.tok))[0] == 204
    assert (await call(sw.app, "get", url, sw.tok))[0] == 404


async def test_software_column_ensure_helper(monkeypatch):
    """The lazy column migration handles a missing engine, inspection errors and sqlite DDL."""
    from sqlalchemy import create_engine, inspect, text

    from apps.api.modules.sbom.routes import software as sr

    monkeypatch.setattr(sr, "_columns_ensured", False)
    sr._ensure_software_columns(SimpleNamespaceDB(engine=None))
    assert sr._columns_ensured is True
    sr._ensure_software_columns(
        SimpleNamespaceDB(engine=None)
    )  # already ensured: no-op
    monkeypatch.setattr(sr, "_columns_ensured", False)

    class NoEngine:
        """Database whose engine attribute raises."""

        @property
        def engine(self):
            """Raise like a closed connection."""
            raise RuntimeError("no engine")

    sr._ensure_software_columns(NoEngine())
    assert sr._columns_ensured is True
    monkeypatch.setattr(sr, "_columns_ensured", False)
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE software (id INTEGER PRIMARY KEY, name TEXT, access_url VARCHAR(1024))"
            )
        )
    sr._ensure_software_columns(SimpleNamespaceDB(engine=engine))
    cols = {c["name"] for c in inspect(engine).get_columns("software")}
    assert {
        "access_url",
        "parent_system_id",
        "git_url",
        "git_path",
        "release_shasum",
    } <= cols
    monkeypatch.setattr(sr, "_columns_ensured", False)
    broken = create_engine("sqlite://")
    sr._ensure_software_columns(
        SimpleNamespaceDB(engine=broken)
    )  # table missing: DDL failures are swallowed
    assert sr._columns_ensured is True


class SimpleNamespaceDB:
    """Tiny database stand-in exposing only an ``engine`` attribute."""

    def __init__(self, engine):
        """Remember the engine."""
        self.engine = engine


@pytest.mark.xfail(
    strict=True,
    reason="bug: list endpoints for software/services/components do not filter by the caller's tenant",
)
async def test_lists_are_tenant_scoped(sw):
    """A foreign tenant must not see this tenant's software in the listing."""
    name = f"iso-{sfx()}"
    st, _ = await call(
        sw.app,
        "post",
        "/api/v1/software",
        sw.tok,
        json={"name": name, "organization_id": sw.org, "software_type": "saas"},
    )
    assert st == 201
    st, js = await call(sw.app, "get", f"/api/v1/software?search={name}", sw.foreign)
    assert js["total"] == 0
    assert json.dumps(js).count(name) == 0
