"""Behaviour tests for issue metadata routes, the shadowed comments router and the issue/metadata models.

Metadata is exercised over HTTP against the real app and database. The comments
router is mounted on an isolated Quart app (the main router registers a
same-path copy first, which shadows it) backed by the same database and token
verification. The legacy ORM model methods are exercised as plain objects.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from quart import Quart

from apps.api.main import _register_before_request
from apps.api.modules.issues.models import issue as imodels
from apps.api.modules.issues.models import metadata as mmodels
from apps.api.modules.issues.models.issue import Issue, IssueStatus
from apps.api.modules.issues.models.metadata import MetadataField, MetadataFieldType
from apps.api.modules.issues.routes import comments as comments_routes
from apps.api.modules.issues.routes import metadata as mroutes
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
SC = ["issues:read", "issues:write"]
M = "/api/v1/metadata"


@dataclass
class MW:
    """Seeded tenant world for metadata and comments tests."""

    app: Any
    db: Any
    tid: int
    org: int
    foreign_org: int
    entity: int
    foreign_entity: int
    uid: int
    uid2: int
    issue: int
    tok: str
    other: str
    ro: str
    foreign: str
    notenant: str


@pytest.fixture(scope="module")
def mw(app) -> MW:
    """Seed tenants, an entity, an issue and tokens."""
    db = app.db
    tid, other = mk_tenant(db, "mt"), mk_tenant(db, "mtx")
    uid, uid2 = (
        mk_identity(db, tid, "admin", superuser=True),
        mk_identity(db, tid, "observer"),
    )
    ro = mk_identity(db, tid, "observer")
    fid = mk_identity(db, other, "admin", superuser=True)
    org, forg = mk_org(db, tid), mk_org(db, other)
    ent, fent = mk_entity(db, org), mk_entity(db, forg)
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
    db.commit()
    return MW(
        app,
        db,
        tid,
        org,
        forg,
        ent,
        fent,
        uid,
        uid2,
        issue,
        token(app, uid, tid, SC),
        token(app, uid2, tid, SC),
        token(app, ro, tid, ["issues:read"]),
        token(app, fid, other, SC),
        token(app, uid, "", SC),
    )


@pytest.mark.parametrize("kind", ["entities", "organizations"])
async def test_metadata_reads_and_rejections(mw, kind):
    """Metadata listing is tenant-scoped and writes need the write scope."""
    rid = mw.entity if kind == "entities" else mw.org
    foreign = mw.foreign_entity if kind == "entities" else mw.foreign_org
    url = f"{M}/{kind}/{rid}/metadata"
    st, js = await call(mw.app, "get", url, mw.tok)
    assert st == 200 and js == {"metadata": {}}
    assert (await call(mw.app, "get", f"{M}/{kind}/{foreign}/metadata", mw.tok))[
        0
    ] == 404
    assert (await call(mw.app, "get", f"{M}/{kind}/999999999/metadata", mw.tok))[
        0
    ] == 404
    assert (
        await call(
            mw.app,
            "post",
            url,
            mw.ro,
            json={"key": "k", "field_type": "string", "value": "v"},
        )
    )[0] == 403
    assert (
        await call(
            mw.app,
            "post",
            f"{M}/{kind}/999999999/metadata",
            mw.tok,
            json={"key": "k", "field_type": "string", "value": "v"},
        )
    )[0] == 404
    assert (
        await call(
            mw.app,
            "patch",
            f"{M}/{kind}/999999999/metadata/k",
            mw.tok,
            json={"value": "v"},
        )
    )[0] == 404
    assert (await call(mw.app, "delete", f"{M}/{kind}/999999999/metadata/k", mw.tok))[
        0
    ] == 404
    assert (await call(mw.app, "patch", f"{url}/nokey", mw.tok, json={"value": "v"}))[
        0
    ] == 404
    assert (await call(mw.app, "delete", f"{url}/nokey", mw.tok))[0] == 404


@pytest.mark.parametrize("kind", ["entities", "organizations"])
async def test_metadata_system_fields_are_protected(mw, kind):
    """System fields can be read but never modified, overwritten or deleted."""
    rid = mw.entity if kind == "entities" else mw.org
    rtype = "entity" if kind == "entities" else "organization"
    now = datetime.now(UTC)
    mw.db.metadata_fields.insert(
        field_key="sys",
        field_value="x",
        field_type="STRING",
        resource_type=rtype,
        resource_id=rid,
        is_system=True,
        village_id=f"sys-{sfx()}",
        created_at=now,
        updated_at=now,
    )
    mw.db.commit()
    url = f"{M}/{kind}/{rid}/metadata"
    assert (
        await call(
            mw.app,
            "post",
            url,
            mw.tok,
            json={"key": "sys", "field_type": "string", "value": "y"},
        )
    )[0] == 403
    assert (await call(mw.app, "patch", f"{url}/sys", mw.tok, json={"value": "y"}))[
        0
    ] == 403
    assert (await call(mw.app, "delete", f"{url}/sys", mw.tok))[0] == 403
    st, js = await call(mw.app, "get", url, mw.tok)
    assert st == 200 and "sys" in js["metadata"]


async def test_metadata_create_validation(mw):
    """Bad number coercion is a 400 and unknown bodies are rejected before any write."""
    url = f"{M}/entities/{mw.entity}/metadata"
    assert (
        await call(
            mw.app,
            "post",
            url,
            mw.tok,
            json={"key": "n", "field_type": "number", "value": "abc"},
        )
    )[0] == 400
    assert (await call(mw.app, "post", url, mw.tok, json={"key": "n"}))[0] == 400
    assert (
        await call(
            mw.app,
            "post",
            f"{M}/organizations/{mw.org}/metadata",
            mw.tok,
            json={"key": "n", "field_type": "number", "value": "abc"},
        )
    )[0] == 400


@pytest.mark.parametrize("kind", ["entities", "organizations"])
async def test_metadata_read_patch_delete(mw, kind):
    """Typed fields are parsed on read; a value PATCH and a DELETE work on user fields."""
    rid = mw.entity if kind == "entities" else mw.org
    rtype = "entity" if kind == "entities" else "organization"
    now = datetime.now(UTC)
    for key, ftype, value in (
        ("owner", "STRING", "ops"),
        ("n", "NUMBER", "4.5"),
        ("i", "NUMBER", "7"),
        ("b", "BOOLEAN", "yes"),
        ("d", "DATE", "2031-01-01"),
        ("j", "JSON", '{"a": 1}'),
    ):
        mw.db.metadata_fields.insert(
            field_key=f"{kind}-{key}",
            field_value=value,
            field_type=ftype,
            resource_type=rtype,
            resource_id=rid,
            is_system=False,
            village_id=f"mf-{sfx()}",
            created_at=now,
            updated_at=now,
        )
    mw.db.commit()
    url = f"{M}/{kind}/{rid}/metadata"
    st, js = await call(mw.app, "get", url, mw.tok)
    md = js["metadata"]
    assert (
        md[f"{kind}-owner"] == "ops"
        and md[f"{kind}-n"] in ("4.5", 4.5)
        and len(md) >= 6
    )
    st, f = await call(
        mw.app, "patch", f"{url}/{kind}-owner", mw.tok, json={"value": "dev"}
    )
    assert st == 200 and f["field_value"] == "dev"
    assert (await call(mw.app, "delete", f"{url}/{kind}-owner", mw.tok))[0] == 204
    assert (await call(mw.app, "delete", f"{url}/{kind}-owner", mw.tok))[0] == 404


@pytest.mark.xfail(
    strict=True,
    reason="bug: the metadata listing compares the uppercase enum member name to lowercase type names, so typed values come back as raw strings",
)
async def test_metadata_listing_is_typed(mw):
    """A NUMBER field stored as '4.5' should be returned as the number 4.5."""
    now = datetime.now(UTC)
    mw.db.metadata_fields.insert(
        field_key="typed-n",
        field_value="4.5",
        field_type="NUMBER",
        resource_type="entity",
        resource_id=mw.entity,
        is_system=False,
        village_id=f"mf-{sfx()}",
        created_at=now,
        updated_at=now,
    )
    mw.db.commit()
    st, js = await call(mw.app, "get", f"{M}/entities/{mw.entity}/metadata", mw.tok)
    assert js["metadata"]["typed-n"] == 4.5


@pytest.mark.xfail(
    strict=True,
    reason="bug: metadata create and type changes store the lowercase enum value (e.g. 'string') where PostgreSQL expects the member name, returning 500",
)
@pytest.mark.parametrize("kind", ["entities", "organizations"])
async def test_metadata_create_and_type_change(mw, kind):
    """Creating a field, overwriting it and changing its type should succeed."""
    rid = mw.entity if kind == "entities" else mw.org
    url = f"{M}/{kind}/{rid}/metadata"
    st, f = await call(
        mw.app,
        "post",
        url,
        mw.tok,
        json={"key": "owner", "field_type": "string", "value": "ops"},
    )
    assert st == 201 and f["field_value"] == "ops"
    assert (
        await call(
            mw.app,
            "post",
            url,
            mw.tok,
            json={"key": "owner", "field_type": "string", "value": "dev"},
        )
    )[0] == 201
    assert (
        await call(
            mw.app,
            "patch",
            f"{url}/owner",
            mw.tok,
            json={"value": 5, "field_type": "number"},
        )
    )[0] == 200


async def test_coerce_and_parse_helpers():
    """Value coercion and parsing cover every field type and the error branches."""
    c, p = mroutes._coerce_value, mroutes._parse_value
    assert c(5, "string") == "5" and c("7", "number") == 7 and c("7.5", "number") == 7.5
    with pytest.raises(ValueError):
        c("x", "number")
    assert (
        c(True, "boolean") is True
        and c("Yes", "boolean") is True
        and c("no", "boolean") is False
        and c(0, "boolean") is False
    )
    now = datetime(2030, 1, 2, tzinfo=UTC)
    assert c(now, "date") == now.isoformat() and c("2030-01-02", "date") == "2030-01-02"
    assert (
        c({"a": 1}, "json") == '{"a": 1}'
        and c("raw", "json") == "raw"
        and c("x", "weird") == "x"
    )
    assert (
        p(None, "string") is None
        and p("s", "string") == "s"
        and p("4", "number") == 4
        and p("4.5", "number") == 4.5
        and p("zz", "number") == "zz"
    )
    assert (
        p("true", "boolean") is True
        and p("0", "boolean") is False
        and p("2030", "date") == "2030"
    )
    assert (
        p('{"a": 1}', "json") == {"a": 1}
        and p("{bad", "json") == "{bad"
        and p("x", "weird") == "x"
    )


async def _comments_app(mw: MW) -> Quart:
    """Build an isolated Quart app serving only the comments router over the real database."""
    qa = Quart("comments-only")
    qa.config.update(mw.app.config)
    qa.config["TESTING"] = True
    qa.db = mw.db
    qa.redis_client = getattr(mw.app, "redis_client", None)
    _register_before_request(qa)
    qa.register_blueprint(comments_routes.bp, url_prefix="/api/v1/issues")
    return qa


async def test_comments_router_endpoints(mw):
    """The comments router itself: list/create/update/delete with author, superuser and tenant rules."""
    qa = await _comments_app(mw)
    url = f"/api/v1/issues/{mw.issue}/comments"
    st, c = await call(qa, "post", url, mw.other, json={"content": "hello"})
    assert st == 201 and c["content"] == "hello"
    st, js = await call(qa, "get", url, mw.tok)
    assert st == 200 and js["total"] >= 1
    assert (await call(qa, "post", url, mw.tok, json={}))[0] == 400
    assert (await call(qa, "get", f"/api/v1/issues/999999999/comments", mw.tok))[
        0
    ] == 404
    assert (
        await call(
            qa,
            "post",
            f"/api/v1/issues/999999999/comments",
            mw.tok,
            json={"content": "x"},
        )
    )[0] == 404
    assert (await call(qa, "get", url, mw.notenant))[0] == 403
    assert (await call(qa, "post", url, mw.notenant, json={"content": "x"}))[0] == 403
    assert (
        await call(qa, "patch", f"{url}/{c['id']}", mw.notenant, json={"content": "x"})
    )[0] == 403
    assert (await call(qa, "delete", f"{url}/{c['id']}", mw.notenant))[0] == 403
    mine = (await call(qa, "post", url, mw.tok, json={"content": "admin's"}))[1]
    assert (
        await call(
            qa, "patch", f"{url}/{mine['id']}", mw.other, json={"content": "hijack"}
        )
    )[0] == 403
    assert (await call(qa, "delete", f"{url}/{mine['id']}", mw.other))[0] == 403
    assert (await call(qa, "patch", f"{url}/999999999", mw.tok, json={"content": "x"}))[
        0
    ] == 404
    assert (await call(qa, "delete", f"{url}/999999999", mw.tok))[0] == 404
    assert (
        await call(
            qa,
            "patch",
            f"/api/v1/issues/999999999/comments/1",
            mw.tok,
            json={"content": "x"},
        )
    )[0] == 404
    assert (await call(qa, "delete", f"/api/v1/issues/999999999/comments/1", mw.tok))[
        0
    ] == 404
    assert (await call(qa, "get", url, mw.foreign))[0] == 404


@pytest.mark.xfail(
    strict=True,
    reason="bug: comment update assigns through a TableProxy (db.issue_comments[id] = ...), returning 500",
)
async def test_comments_router_update_and_delete(mw):
    """The author edits their own comment; a superuser edits and deletes others'."""
    qa = await _comments_app(mw)
    url = f"/api/v1/issues/{mw.issue}/comments"
    c = (await call(qa, "post", url, mw.other, json={"content": "hello"}))[1]
    st, js = await call(
        qa, "patch", f"{url}/{c['id']}", mw.other, json={"content": "edited"}
    )
    assert st == 200 and js["content"] == "edited"
    assert (
        await call(
            qa, "patch", f"{url}/{c['id']}", mw.tok, json={"content": "by admin"}
        )
    )[0] == 200
    assert (await call(qa, "delete", f"{url}/{c['id']}", mw.tok))[0] == 204


async def test_issue_model_methods():
    """Issue.close/reopen/is_overdue and the model reprs behave without a database."""
    issue = Issue(title="t", status=IssueStatus.OPEN)
    assert issue.is_overdue() is False
    issue.due_date = datetime.now(UTC) - timedelta(days=1)
    assert issue.is_overdue() is True
    issue.close(7)
    assert (
        issue.status == IssueStatus.CLOSED
        and issue.closed_by_id == 7
        and issue.closed_at is not None
    )
    assert issue.is_overdue() is False
    issue.reopen()
    assert (
        issue.status == IssueStatus.OPEN
        and issue.closed_at is None
        and issue.closed_by_id is None
    )
    label = imodels.IssueLabel(name="bug", color="#fff")
    assert "bug" in repr(label)
    short = imodels.IssueComment(issue_id=1, content="short")
    long = imodels.IssueComment(issue_id=1, content="x" * 80)
    assert "short" in repr(short) and "..." in repr(long)
    link = imodels.IssueEntityLink(
        issue_id=1, entity_id=2, link_type=imodels.IssueLinkType.RELATED
    )
    assert "related" in repr(link)
    res = imodels.IssueResourceLink(issue_id=1, resource_type="vault", resource_id=3)
    assert "vault" in repr(res)


class _Query:
    """Minimal chainable stand-in for a SQLAlchemy query returning canned rows."""

    def __init__(self, rows):
        """Remember the canned rows."""
        self.rows = rows

    def filter(self, *a, **k):
        """Return self so calls can be chained."""
        return self

    def filter_by(self, **k):
        """Return self so calls can be chained."""
        return self

    def all(self):
        """Return every canned row."""
        return self.rows

    def first(self):
        """Return the first canned row or None."""
        return self.rows[0] if self.rows else None


class _Session:
    """Fake session that serves canned rows per queried entity."""

    def __init__(self, by_model, org=None):
        """Store rows keyed by model and an optional organization."""
        self.by_model, self.org, self.added = by_model, org, []

    def query(self, model, *cols):
        """Return a query over the canned rows for ``model``."""
        return _Query(self.by_model.get(model, []))

    def get(self, model, pk):
        """Return the canned organization."""
        return self.org

    def add(self, obj):
        """Record an added object."""
        self.added.append(obj)


async def test_entity_issue_helper_with_fake_session(monkeypatch):
    """get_entity_issues returns the rows the session yields for an entity."""
    import shared.database as sdb

    rows = [type("I", (), {"id": 10})()]
    monkeypatch.setattr(
        sdb, "db", type("DB", (), {"session": _Session({Issue: rows})})(), raising=False
    )
    assert imodels.get_entity_issues(5) == rows


@pytest.mark.xfail(
    strict=True,
    reason="bug: get_organization_issues imports Organization/Entity from apps.api.models, which does not export them (ImportError)",
)
async def test_organization_issue_helper(monkeypatch):
    """get_organization_issues should dedupe issues across an org and its children."""
    import shared.database as sdb

    child = type("Org", (), {"id": 2})()
    org = type(
        "Org", (), {"id": 1, "get_all_children": lambda self, recursive: [child]}
    )()
    i1 = type("I", (), {"id": 10})()
    monkeypatch.setattr(
        sdb,
        "db",
        type("DB", (), {"session": _Session({Issue: [i1, i1]}, org=org)})(),
        raising=False,
    )
    assert [i.id for i in imodels.get_organization_issues(1)] == [10]


async def test_metadata_model_values(monkeypatch):
    """MetadataField validates, coerces and decodes values per field type."""
    f = MetadataField(
        resource_type="entity",
        resource_id=1,
        field_key="k",
        field_type=MetadataFieldType.STRING,
    )
    assert "k" in repr(f)
    f.set_value(None)
    assert f.get_value() is None
    f.field_value = ""
    assert f.get_value() is None
    f.set_value(12)
    assert f.get_value() == "12"
    with pytest.raises(ValueError):
        f.set_value("x" * 1001)
    with pytest.raises(ValueError):
        f.field_value = "{not json"
    f.field_type = MetadataFieldType.NUMBER
    f.set_value("3.5")
    assert f.get_value() == 3.5
    f.set_value(4)
    assert f.get_value() == 4.0
    for bad in ("abc", [1]):
        with pytest.raises(ValueError):
            f.set_value(bad)
    f.field_type = MetadataFieldType.DATE
    f.set_value("2030-01-02T03:04:05Z")
    assert f.get_value().year == 2030
    f.set_value(datetime(2031, 1, 1, tzinfo=UTC))
    assert f.get_value().year == 2031
    with pytest.raises(ValueError):
        f.set_value("not-a-date")
    with pytest.raises(ValueError):
        f.set_value(5)
    f.field_value = json.dumps(None)
    assert f.get_value() is None
    f.field_value = json.dumps(12)
    assert f.get_value() == 12
    f.field_type = MetadataFieldType.BOOLEAN
    f.set_value("Yes")
    assert f.get_value() is True
    f.set_value(0)
    assert f.get_value() is False
    f.field_value = json.dumps(None)
    assert f.get_value() is None
    f.field_type = MetadataFieldType.JSON
    f.set_value('{"a": 1}')
    assert f.get_value() == {"a": 1}
    f.set_value({"b": 2})
    assert f.get_value() == {"b": 2}
    with pytest.raises(ValueError):
        f.set_value("{bad")
    with pytest.raises(ValueError):
        f.set_value({1, 2})


async def test_metadata_model_session_helpers(monkeypatch):
    """get_metadata/set_metadata read and upsert through the (faked) session."""
    import shared.database as sdb

    existing = MetadataField(
        resource_type="entity",
        resource_id=1,
        field_key="a",
        field_type=MetadataFieldType.STRING,
    )
    existing.set_value("old")
    sess = _Session({MetadataField: [existing]})
    monkeypatch.setattr(sdb, "db", type("DB", (), {"session": sess})(), raising=False)
    assert mmodels.MetadataField.get_metadata("entity", 1) == {"a": "old"}
    updated = mmodels.MetadataField.set_metadata(
        "entity", 1, "a", MetadataFieldType.STRING, "new", 1
    )
    assert updated is existing and existing.get_value() == "new"
    sess2 = _Session({MetadataField: []})
    monkeypatch.setattr(sdb, "db", type("DB", (), {"session": sess2})(), raising=False)
    created = mmodels.MetadataField.set_metadata(
        "entity", 1, "b", MetadataFieldType.NUMBER, 2, 1
    )
    assert created in sess2.added and created.get_value() == 2.0
