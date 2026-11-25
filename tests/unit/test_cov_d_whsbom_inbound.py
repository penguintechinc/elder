"""Coverage tests for the inbound-webhook action resolver and executors.

Validation paths run without a database; create/update paths run against the
real Postgres schema with tenant-scoped rows.
"""

# flake8: noqa: E501

import datetime as dt
import uuid
from types import SimpleNamespace

import pytest

from apps.api.services.webhooks import inbound_actions as ia
from apps.api.utils.async_utils import run_in_threadpool
from tests.unit import _cov_d_helpers as _cov_helpers
from tests.unit._cov_d_helpers import in_db

#: Re-export the shared two-tenant fixture for pytest discovery in this module.
world = _cov_helpers.world


def _hook(tenant: int = 1, **kw) -> SimpleNamespace:
    """Build a hook stand-in with the attributes the executors read."""
    base = {
        "id": 5,
        "slug": "cov",
        "tenant_id": tenant,
        "organization_id": None,
        "stream_id": None,
        "scopes": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


# ----------------------------------------------------------- scope handling


def test_normalize_stored_scopes() -> None:
    """Stored scope values are parsed, filtered, deduped and defaulted."""
    assert ia.normalize_stored_scopes('["issues:create","issues:create","bogus"]') == [
        "issues:create"
    ]
    assert ia.normalize_stored_scopes("issues:create, events:log") == [
        "issues:create",
        "events:log",
    ]
    assert ia.normalize_stored_scopes(["events:log", 5, " "]) == ["events:log"]
    assert ia.normalize_stored_scopes({"a": 1}) == ["events:log"]
    assert ia.normalize_stored_scopes(None) == ["events:log"]
    assert ia.normalize_stored_scopes(["bogus"]) == ["events:log"]
    assert ia.normalize_stored_scopes("[not json") == ["events:log"]


def test_validate_inbound_scopes() -> None:
    """Scope validation covers defaults, shape errors and unknown scopes."""
    assert ia.validate_inbound_scopes(None) == (["events:log"], None)
    assert ia.validate_inbound_scopes(None, has_stream_id=True)[0] == [
        "events:log",
        "playbooks:trigger",
    ]
    assert ia.validate_inbound_scopes([])[1] is not None
    assert ia.validate_inbound_scopes("x")[1] is not None
    assert "non-empty strings" in ia.validate_inbound_scopes([""])[1]
    assert "non-empty strings" in ia.validate_inbound_scopes([3])[1]
    assert "Unsupported" in ia.validate_inbound_scopes(["nope", "nope"])[1]
    assert ia.validate_inbound_scopes([" events:log ", "events:log"]) == (
        ["events:log"],
        None,
    )


def test_scalar_parsers() -> None:
    """Scalar coercion helpers handle every accepted and rejected shape."""
    assert ia._is_pos_int(3) == 3
    assert ia._is_pos_int(True) is None
    assert ia._is_pos_int(0) is None
    assert ia._is_pos_int("3") is None
    assert ia._parse_pos_int_flexible(" 4 ") == 4
    assert ia._parse_pos_int_flexible(4) == 4
    assert ia._parse_pos_int_flexible(0) is None
    assert ia._parse_pos_int_flexible("0") is None
    assert ia._parse_pos_int_flexible(True) is None
    assert ia._parse_pos_int_flexible(None) is None
    assert ia._parse_pos_int_flexible("x") is None
    assert ia._parse_pos_int_flexible(1.5) is None
    assert ia._safe_str(None, 5) == (None, None)
    assert ia._safe_str(5, 5)[1] == "must be a string"
    assert ia._safe_str("abcdef", 5)[1].startswith("exceeds")
    assert ia._safe_str(" ab ", 5) == ("ab", None)
    assert ia._parse_int_bounds(None, min_val=1, max_val=3) == (None, None)
    assert ia._parse_int_bounds("2", min_val=1, max_val=3) == (2, None)
    assert ia._parse_int_bounds("x", min_val=1, max_val=3)[1] == "must be an integer"
    assert ia._parse_int_bounds(True, min_val=1, max_val=3)[1] == "must be an integer"
    assert "between" in ia._parse_int_bounds(9, min_val=1, max_val=3)[1]
    assert ia._parse_float_bounds(None, min_val=0, max_val=1) == (None, None)
    assert ia._parse_float_bounds("0.5", min_val=0, max_val=1) == (0.5, None)
    assert ia._parse_float_bounds(True, min_val=0, max_val=1)[1] == "must be a number"
    assert ia._parse_float_bounds("abc", min_val=0, max_val=1)[1] == "must be a number"
    assert "between" in ia._parse_float_bounds(5, min_val=0, max_val=1)[1]
    assert ia._validate_http_url(None) == (None, None)
    assert ia._validate_http_url("") == (None, None)
    assert ia._validate_http_url(5)[1] == "must be a URL string"
    assert ia._validate_http_url("ftp://x")[1].startswith("must be a valid")
    assert ia._validate_http_url(" https://e.com ") == ("https://e.com", None)
    assert ia._err(403, "m")[1] == "forbidden"
    assert ia._err(404, "m")[1] == "not_found"
    assert ia._err(400, "m")[1] == "failed"


def test_parse_iso_dt() -> None:
    """ISO parsing handles empty, non-string, zulu, naive, offset and date-only."""
    assert ia._parse_iso_dt(None) == (None, None)
    assert ia._parse_iso_dt("") == (None, None)
    assert ia._parse_iso_dt(5)[1] is not None
    assert ia._parse_iso_dt("2024-01-02T03:04:05Z")[0].tzinfo is not None
    assert ia._parse_iso_dt("2024-01-02T03:04:05")[0].tzinfo == dt.UTC
    assert ia._parse_iso_dt("2024-01-02T03:04:05+02:00")[0].hour == 1
    assert ia._parse_iso_dt("2024-01-02")[0].day == 2
    assert ia._parse_iso_dt("garbage")[1] is not None


# ------------------------------------------------------------- resolve action


def test_resolve_inbound_action_matrix() -> None:
    """Action resolution covers logging, playbooks, resources and errors."""
    plain = _hook()
    piped = _hook(stream_id=9, scopes=["events:log", "playbooks:trigger"])
    assert ia.resolve_inbound_action(plain, {"a": 1})[0] == "events:log"
    assert ia.resolve_inbound_action(plain, [1])[1] == {"kind": "log_only"}
    scope, spec, _ = ia.resolve_inbound_action(piped, {"a": 1})
    assert scope == "playbooks:trigger" and spec["input"] == {"a": 1}
    assert ia.resolve_inbound_action(piped, [1])[1]["input"] == {"payload": [1]}
    assert (
        ia.resolve_inbound_action(plain, {"action": ""})[2]
        == "action must be a non-empty string"
    )
    assert ia.resolve_inbound_action(plain, {"action": 5})[2] is not None
    for log in ("events.log", "events:log", " LOG "):
        assert ia.resolve_inbound_action(plain, {"action": log})[0] == "events:log"
    pb = ia.resolve_inbound_action(
        plain, {"action": "playbook.trigger", "input": {"a": 1}, "stream_id": 4}
    )
    assert pb[1]["playbook_id"] == 4 and pb[1]["input"] == {"a": 1}
    pb2 = ia.resolve_inbound_action(
        plain, {"action": "playbooks:trigger", "playbook_id": 8}
    )
    assert pb2[1]["playbook_id"] == 8 and pb2[1]["input"]["playbook_id"] == 8
    assert (
        ia.resolve_inbound_action(plain, {"action": "playbook.trigger", "input": [1]})[
            2
        ]
        == "input must be a JSON object"
    )

    ok = ia.resolve_inbound_action(
        plain,
        {"action": "resource.create", "resource": "Issues", "data": {"title": "t"}},
    )
    assert ok[0] == "issues:create" and ok[1]["kind"] == "resource_create"
    assert ia.resolve_inbound_action(
        plain, {"action": "create", "resource": 5, "data": {"a": 1}}
    )[2].startswith("Unsupported")
    assert (
        ia.resolve_inbound_action(plain, {"action": "issues:create", "data": {"a": 1}})[
            0
        ]
        == "issues:create"
    )
    assert (
        ia.resolve_inbound_action(plain, {"action": "issues.create", "data": {"a": 1}})[
            0
        ]
        == "issues:create"
    )
    assert ia.resolve_inbound_action(plain, {"action": "bogus"})[2].startswith(
        "Unsupported"
    )
    assert ia.resolve_inbound_action(
        plain, {"action": "issues:delete", "data": {"a": 1}}
    )[2].startswith("Unsupported")
    assert (
        ia.resolve_inbound_action(plain, {"action": "issues:create", "data": {}})[2]
        == "data must be a non-empty JSON object"
    )
    assert (
        ia.resolve_inbound_action(plain, {"action": "issues:create", "data": [1]})[2]
        is not None
    )

    upd = ia.resolve_inbound_action(
        plain,
        {
            "action": "resource.update",
            "resource": "issues",
            "resource_id": "7",
            "data": {"a": 1},
        },
    )
    assert upd[1]["resource_id"] == 7
    by_id = ia.resolve_inbound_action(
        plain, {"action": "issues:update", "id": 3, "data": {"a": 1}}
    )
    assert by_id[1]["resource_id"] == 3
    assert (
        "resource_id/id is required"
        in ia.resolve_inbound_action(
            plain, {"action": "issues:update", "data": {"a": 1}}
        )[2]
    )
    vuln = ia.resolve_inbound_action(
        plain,
        {"action": "vulnerabilities:update", "cve_id": " cve-1 ", "data": {"a": 1}},
    )
    assert vuln[1]["cve_id"] == "CVE-1" and vuln[1]["resource_id"] is None
    assert (
        "or cve_id"
        in ia.resolve_inbound_action(
            plain, {"action": "vulnerabilities:update", "data": {"a": 1}}
        )[2]
    )
    assert (
        "or cve_id"
        in ia.resolve_inbound_action(
            plain,
            {"action": "vulnerabilities:update", "cve_id": "  ", "data": {"a": 1}},
        )[2]
    )


# ---------------------------------------------------- executors: validation


def _run(kind: str, resource: str, data: dict, rid=None, cve=None, db=None, hook=None):
    """Execute an inbound resource action and return ``(status, label, body)``."""
    spec = {
        "kind": kind,
        "resource": resource,
        "data": data,
        "resource_id": rid,
        "cve_id": cve,
    }
    return ia.execute_inbound_action(db, hook or _hook(), "x", spec, None)


VALIDATION_CASES = [
    ("issues", {"title": 5}, "title must be a string"),
    ("issues", {"title": " "}, "title cannot be empty"),
    ("issues", {"title": "t", "description": 5}, "description must be a string"),
    ("issues", {"title": "t", "status": "nope"}, "status must be one of"),
    ("issues", {"title": "t", "priority": "nope"}, "priority must be one of"),
    ("issues", {"title": "t", "issue_type": "nope"}, "issue_type must be one of"),
    ("issues", {"title": "t", "metadata": [1]}, "metadata must be a JSON object"),
    ("issues", {"description": "x"}, "title is required"),
    ("vulnerabilities", {"title": " "}, "title cannot be empty"),
    ("vulnerabilities", {"severity": "nope"}, "severity must be one of"),
    ("vulnerabilities", {"cvss_score": 99}, "cvss_score must be between"),
    ("vulnerabilities", {"description": 5}, "description must be a string"),
    ("vulnerabilities", {"published_at": "junk"}, "published_at must be a valid"),
    ("vulnerabilities", {"title": "t"}, "cve_id is required"),
    ("vulnerabilities", {"cve_id": "C" * 60, "title": "t"}, "cve_id exceeds"),
    ("vulnerabilities", {"cve_id": "CVE-1"}, "title is required"),
    ("vulnerabilities", {"cve_id": "CVE-1", "title": "t"}, "severity is required"),
    ("entities", {"name": " "}, "name cannot be empty"),
    ("entities", {"type": " "}, "type cannot be empty"),
    ("entities", {"name": "n", "sub_type": 5}, "sub_type must be a string"),
    ("entities", {"name": "n", "status": "weird"}, "status must be active or inactive"),
    ("entities", {"name": "n", "status": 5}, "status must be active or inactive"),
    ("entities", {"name": "n", "metadata": 5}, "metadata must be a JSON object"),
    ("entities", {"type": "t"}, "name is required"),
    ("entities", {"name": "n"}, "type is required"),
    ("data_stores", {"name": " "}, "name cannot be empty"),
    (
        "data_stores",
        {"name": "n", "data_classification": "x"},
        "data_classification must be one of",
    ),
    ("data_stores", {"name": "n", "storage_type": "x"}, "storage_type must be one of"),
    (
        "data_stores",
        {"name": "n", "location_region": 5},
        "location_region must be a string",
    ),
    ("data_stores", {"name": "n", "description": 5}, "description must be a string"),
    ("data_stores", {"name": "n", "metadata": 5}, "metadata must be a JSON object"),
    ("data_stores", {"description": "d"}, "name is required"),
    ("services", {"name": " "}, "name cannot be empty"),
    ("services", {"name": "n", "port": 0}, "port must be between"),
    ("services", {"name": "n", "status": "x"}, "status must be one of"),
    ("services", {"name": "n", "description": 5}, "description must be a string"),
    ("services", {"port": 80}, "name is required"),
    ("software", {"name": " "}, "name cannot be empty"),
    ("software", {"name": "n", "software_type": "x"}, "software_type must be one of"),
    ("software", {"name": "n", "version": 5}, "version must be a string"),
    ("software", {"name": "n", "description": 5}, "description must be a string"),
    ("software", {"version": "1"}, "name is required"),
    ("software", {"name": "n"}, "software_type is required"),
    ("ai_resources", {"name": " "}, "name cannot be empty"),
    ("ai_resources", {"name": "n", "provider": " "}, "provider cannot be empty"),
    ("ai_resources", {"name": "n", "model_name": 5}, "model_name must be a string"),
    (
        "ai_resources",
        {"name": "n", "resource_type": " "},
        "resource_type cannot be empty",
    ),
    (
        "ai_resources",
        {"name": "n", "endpoint_url": "ftp://x"},
        "endpoint_url must be a valid",
    ),
    ("ai_resources", {"name": "n", "status": "x"}, "status must be one of"),
    ("ai_resources", {"name": "n", "description": 5}, "description must be a string"),
    ("ai_resources", {"name": "n", "metadata": 5}, "metadata must be a JSON object"),
    ("ai_resources", {"provider": "p", "resource_type": "r"}, "name is required"),
    ("ai_resources", {"name": "n", "resource_type": "r"}, "provider is required"),
    ("ai_resources", {"name": "n", "provider": "p"}, "resource_type is required"),
]


@pytest.mark.parametrize(
    "resource,data,msg", [c for c in VALIDATION_CASES if c[0] != "ai_resources"]
)
def test_create_validation_errors(resource, data, msg) -> None:
    """Invalid create payloads are rejected with a 400 before any write."""
    status, label, body = _run("resource_create", resource, data)
    assert status == 400 and label == "failed" and msg in body["error"]


@pytest.mark.parametrize(
    "resource,data,msg", [c for c in VALIDATION_CASES if c[0] == "ai_resources"]
)
def test_ai_validation_errors(app, resource, data, msg) -> None:
    """AI-resource validation also rejects bad payloads (table ensured first)."""
    status, _label, body = _run("resource_create", resource, data, db=app.db)
    assert status == 400 and msg in body["error"]


@pytest.mark.parametrize(
    "resource",
    ["issues", "entities", "data_stores", "services", "software"],
)
def test_update_requires_id_and_fields(resource) -> None:
    """Updates without a resource id are rejected before any DB access."""
    status, _label, body = _run("resource_update", resource, {"x": 1}, rid=None)
    assert status == 400 and "resource_id" in body["error"]


def test_execute_dispatch_edges() -> None:
    """Log-only, unknown kinds, and unknown resources are handled."""
    assert ia.execute_inbound_action(
        None, _hook(), "events:log", {"kind": "log_only"}, None
    ) == (
        202,
        "received",
        {"logged": True},
    )
    out = ia.execute_inbound_action(None, _hook(), "zzz", {"kind": "mystery"}, None)
    assert out[0] == 400 and "Unsupported" in out[2]["error"]
    out = _run("resource_create", "unknown", {"a": 1})
    assert out[0] == 400
    assert _run("resource_update", "vulnerabilities", {"title": "t"})[0] == 404


def test_ai_update_requires_id(app) -> None:
    """AI-resource updates need a resource id."""
    status, _l, body = _run("resource_update", "ai_resources", {"name": "x"}, db=app.db)
    assert status == 400 and "resource_id" in body["error"]


# --------------------------------------------------------- executors: DB paths


def _vid() -> str:
    """Return a unique short token."""
    return uuid.uuid4().hex[:10]


@pytest.mark.asyncio
async def test_playbook_trigger_paths(world) -> None:
    """Playbook triggers resolve the target, enforce pinning and queue an execution."""
    db = world["db"]
    tid = world["a"]["tenant"]
    other = world["b"]["tenant"]

    def _seed() -> tuple[int, int]:
        """Insert one enabled playbook for each tenant."""
        now = dt.datetime.now(dt.UTC)
        ids = []
        for tenant, owner in ((tid, world["a"]["user"]), (other, world["b"]["user"])):
            ids.append(
                db.stream_playbooks.insert(
                    tenant_id=tenant,
                    owner_identity_id=owner,
                    name="pb-" + _vid(),
                    is_enabled=True,
                    created_at=now,
                    updated_at=now,
                )
            )
        db.commit()
        return ids[0], ids[1]

    own, foreign = await in_db(_seed)

    def _go(hook, spec):
        """Run a playbook trigger spec."""
        spec = {"kind": "playbook_trigger", **spec}
        return ia.execute_inbound_action(db, hook, "playbooks:trigger", spec, "1.2.3.4")

    def _all():
        """Run every playbook-trigger scenario on the DB thread."""
        free = _hook(tid)
        pinned = _hook(tid, stream_id=own)
        results = {
            "ok": _go(free, {"playbook_id": own, "input": {"a": 1}}),
            "pinned": _go(pinned, {"playbook_id": None, "input": None}),
            "str_id": _go(free, {"playbook_id": str(own), "input": {}}),
            "bad_id": _go(free, {"playbook_id": "x", "input": {}}),
            "mismatch": _go(pinned, {"playbook_id": own + 10**6, "input": {}}),
            "missing": _go(free, {"playbook_id": None, "input": {}}),
            "foreign": _go(free, {"playbook_id": foreign, "input": {}}),
        }
        return results

    r = await in_db(_all)
    assert r["ok"][0] == 202 and r["ok"][1] == "triggered"
    assert r["pinned"][0] == 202 and r["str_id"][0] == 202
    assert r["bad_id"][0] == 400 and r["mismatch"][0] == 403
    assert r["missing"][0] == 400 and r["foreign"][0] == 404


@pytest.mark.asyncio
async def test_resource_crud_paths(world) -> None:
    """Every resource can be created and updated within its tenant, and not across."""
    db = world["db"]
    a, b = world["a"], world["b"]

    def _body() -> dict:
        """Create and update each resource type; collect statuses."""
        hook = _hook(a["tenant"], organization_id=a["org"])
        hook_b = _hook(b["tenant"], organization_id=b["org"])
        out: dict = {}
        cve = "CVE-" + _vid().upper()

        def go(h, kind, res, data, rid=None, cve_id=None):
            """Execute one action with the given hook."""
            return _run(kind, res, data, rid=rid, cve=cve_id, db=db, hook=h)

        issue = go(
            hook,
            "resource_create",
            "issues",
            {
                "title": "t",
                "description": "d",
                "status": "open",
                "priority": "high",
                "issue_type": "bug",
                "is_incident": True,
                "metadata": {"k": 1},
            },
        )
        out["issue_create"] = issue
        iid = issue[2].get("id")
        out["issue_update"] = go(
            hook,
            "resource_update",
            "issues",
            {"status": "closed", "title": "t2"},
            rid=iid,
        )
        out["issue_update_empty"] = go(
            hook, "resource_update", "issues", {"unknown": 1}, rid=iid
        )
        out["issue_update_foreign"] = go(
            hook_b, "resource_update", "issues", {"title": "x"}, rid=iid
        )
        out["issue_org_missing"] = go(
            _hook(a["tenant"]), "resource_create", "issues", {"title": "t"}
        )
        out["issue_org_bad"] = go(
            hook, "resource_create", "issues", {"title": "t", "organization_id": "zz"}
        )
        out["issue_org_foreign"] = go(
            hook,
            "resource_create",
            "issues",
            {"title": "t", "organization_id": b["org"]},
        )
        out["issue_org_explicit"] = go(
            _hook(a["tenant"]),
            "resource_create",
            "issues",
            {"title": "t", "organization_id": str(a["org"])},
        )

        vuln = go(
            hook,
            "resource_create",
            "vulnerabilities",
            {
                "cve_id": cve.lower(),
                "title": "v",
                "severity": "high",
                "cvss_score": "7.5",
                "description": "d",
                "published_at": "2024-01-01",
                "is_active": False,
            },
        )
        out["vuln_create"] = vuln
        out["vuln_dup"] = go(
            hook,
            "resource_create",
            "vulnerabilities",
            {"cve_id": cve, "title": "v", "severity": "low"},
        )
        out["vuln_update_id"] = go(
            hook,
            "resource_update",
            "vulnerabilities",
            {"severity": "low"},
            rid=vuln[2]["id"],
        )
        out["vuln_update_cve"] = go(
            hook, "resource_update", "vulnerabilities", {"title": "new"}, cve_id=cve
        )
        out["vuln_update_none"] = go(
            hook,
            "resource_update",
            "vulnerabilities",
            {"title": "n"},
            cve_id="CVE-NOPE-" + _vid(),
        )
        out["vuln_update_empty"] = go(
            hook, "resource_update", "vulnerabilities", {"x": 1}, cve_id=cve
        )

        ent = go(
            hook,
            "resource_create",
            "entities",
            {
                "name": "e",
                "type": "server",
                "sub_type": "vm",
                "status": "Active",
                "metadata": {"a": 1},
            },
        )
        out["ent_create"] = ent
        out["ent_update"] = go(
            hook,
            "resource_update",
            "entities",
            {"status": "inactive"},
            rid=ent[2]["id"],
        )
        out["ent_update_empty"] = go(
            hook, "resource_update", "entities", {"x": 1}, rid=ent[2]["id"]
        )
        out["ent_update_foreign"] = go(
            hook_b, "resource_update", "entities", {"name": "x"}, rid=ent[2]["id"]
        )

        ds = go(
            hook,
            "resource_create",
            "data_stores",
            {
                "name": "d",
                "data_classification": "Public",
                "storage_type": "cache",
                "location_region": "us",
                "description": "x",
                "metadata": {"a": 1},
                "is_active": True,
            },
        )
        out["ds_create"] = ds
        out["ds_update"] = go(
            hook, "resource_update", "data_stores", {"name": "d2"}, rid=ds[2]["id"]
        )
        out["ds_update_empty"] = go(
            hook, "resource_update", "data_stores", {"x": 1}, rid=ds[2]["id"]
        )
        out["ds_update_foreign"] = go(
            hook_b, "resource_update", "data_stores", {"name": "x"}, rid=ds[2]["id"]
        )

        svc = go(
            hook,
            "resource_create",
            "services",
            {
                "name": "s",
                "port": "8080",
                "status": "Active",
                "is_public": True,
                "description": "x",
            },
        )
        out["svc_create"] = svc
        out["svc_update"] = go(
            hook, "resource_update", "services", {"port": 9090}, rid=svc[2]["id"]
        )
        out["svc_update_empty"] = go(
            hook, "resource_update", "services", {"x": 1}, rid=svc[2]["id"]
        )
        out["svc_update_foreign"] = go(
            hook_b, "resource_update", "services", {"name": "x"}, rid=svc[2]["id"]
        )

        sw = go(
            hook,
            "resource_create",
            "software",
            {
                "name": "w",
                "software_type": "SaaS",
                "version": "1.0",
                "description": "x",
                "is_active": False,
            },
        )
        out["sw_create"] = sw
        out["sw_update"] = go(
            hook, "resource_update", "software", {"version": "2"}, rid=sw[2]["id"]
        )
        out["sw_update_empty"] = go(
            hook, "resource_update", "software", {"x": 1}, rid=sw[2]["id"]
        )
        out["sw_update_foreign"] = go(
            hook_b, "resource_update", "software", {"name": "x"}, rid=sw[2]["id"]
        )

        ai = go(
            hook,
            "resource_create",
            "ai_resources",
            {
                "name": "a",
                "provider": "p",
                "resource_type": "llm",
                "model_name": "m",
                "endpoint_url": "https://e.example.com",
                "status": "Active",
                "description": "x",
                "metadata": {"a": 1},
                "is_active": True,
            },
        )
        out["ai_create"] = ai
        out["ai_update"] = go(
            hook,
            "resource_update",
            "ai_resources",
            {"status": "inactive"},
            rid=ai[2]["id"],
        )
        out["ai_update_empty"] = go(
            hook, "resource_update", "ai_resources", {"x": 1}, rid=ai[2]["id"]
        )
        out["ai_update_foreign"] = go(
            hook_b, "resource_update", "ai_resources", {"name": "x"}, rid=ai[2]["id"]
        )
        return out

    r = await in_db(_body)
    created = {k: v for k, v in r.items() if k.endswith("_create")}
    for key, val in created.items():
        assert val[0] == 201 and val[1] == "created", (key, val)
    for key in (
        "issue_update",
        "vuln_update_id",
        "vuln_update_cve",
        "ent_update",
        "ds_update",
        "svc_update",
        "sw_update",
        "ai_update",
    ):
        assert r[key][0] == 200 and r[key][1] == "updated", (key, r[key])
    for key in (
        "issue_update_empty",
        "vuln_update_empty",
        "ent_update_empty",
        "ds_update_empty",
        "svc_update_empty",
        "sw_update_empty",
        "ai_update_empty",
    ):
        assert r[key][0] == 400, (key, r[key])
    for key in (
        "issue_update_foreign",
        "vuln_update_none",
        "ent_update_foreign",
        "ds_update_foreign",
        "svc_update_foreign",
        "sw_update_foreign",
        "ai_update_foreign",
    ):
        assert r[key][0] == 404, (key, r[key])
    assert r["vuln_dup"][0] == 409
    assert r["issue_org_missing"][0] == 400 and r["issue_org_bad"][0] == 400
    assert r["issue_org_foreign"][0] == 404 and r["issue_org_explicit"][0] == 201
