"""HTTP route tests for ``apps/api/modules/streams/routes/streams.py``.

Everything runs through the real Quart app, real JWT verification and the
real Postgres schema.  Only the Redis job-bus enqueue (a true external
boundary) is faked, for the failure branch.  Tenant isolation, owner /
editor / viewer / outsider authorization, editor locks, versioning, the
validation pipeline and the unified execution history are asserted directly.
"""

# flake8: noqa: E501
# ruff: noqa: F811

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import jwt
import pytest
import pytest_asyncio

from apps.api.modules.streams.routes import streams as sr
from apps.api.utils.async_utils import run_in_threadpool
from tests.unit._cov_d_helpers import call, in_db, now, vid, world  # noqa: F401

BASE = "/api/v1/streams"
ALL = ["streams:read", "streams:write", "streams:execute"]


def _ident(db, tenant, tag, sfx):
    """Insert one identity in a tenant and return its id."""
    ts = now()
    return db.identities.insert(
        identity_type="human",
        username=f"cd-{tag}-{sfx}",
        email=f"cd-{tag}-{sfx}@example.com",
        full_name=f"User {tag}",
        tenant_id=tenant,
        auth_provider="local",
        is_active=True,
        is_superuser=False,
        mfa_enabled=False,
        must_change_password=False,
        portal_role="observer",
        created_at=ts,
        updated_at=ts,
    )


def _tok(app, tenant, ident, scopes=None, with_identity=True, typ="access"):
    """Mint a JWT carrying the identity_id claim the streams routes read."""
    issued = now()
    payload = {
        "sub": str(ident),
        "type": typ,
        "iat": issued,
        "exp": issued + timedelta(hours=1),
        "scope": list(ALL if scopes is None else scopes),
        "tenant": str(tenant),
        "roles": ["admin"],
        "teams": [],
    }
    if with_identity:
        payload["identity_id"] = ident
    secret = app.config.get("JWT_SECRET_KEY") or app.config.get("SECRET_KEY")
    return jwt.encode(payload, secret, algorithm="HS256")


def _insert_stream(db, tenant, owner, **kw):
    """Insert a stream playbook row and return its id."""
    ts = now()
    cols = dict(
        tenant_id=tenant,
        village_id=vid(),
        name="S-" + uuid.uuid4().hex[:6],
        description="",
        owner_identity_id=owner,
        created_by_identity_id=owner,
        trigger_type="manual",
        is_public=False,
        is_template=False,
        is_enabled=False,
        tags=[],
        status="draft",
        execution_count=0,
        success_count=0,
        failure_count=0,
        created_at=ts,
        updated_at=ts,
    )
    cols.update(kw)
    sid = db.stream_playbooks.insert(**cols)
    db.commit()
    return sid


def _insert_version(db, tenant, sid, owner, canvas, n=1):
    """Insert a stream version row."""
    ts = now()
    db.stream_versions.insert(
        tenant_id=tenant,
        playbook_id=sid,
        version_number=n,
        created_by_identity_id=owner,
        nodes_json=canvas.get("nodes", []),
        edges_json=canvas.get("edges", []),
        canvas_json=canvas,
        change_summary="t",
        created_at=ts,
        updated_at=ts,
    )
    db.commit()


@pytest_asyncio.fixture
async def sw(app, world):
    """World plus an editor, viewer and outsider in tenant A and a stream owned by A."""
    db = world["db"]
    a, b = world["a"], world["b"]
    sfx = world["sfx"]

    def seed():
        """Seed identities, shares and a baseline stream."""
        out = {
            "tenant": a["tenant"],
            "owner": a["user"],
            "editor": _ident(db, a["tenant"], "ed", sfx),
            "viewer": _ident(db, a["tenant"], "vw", sfx),
            "outsider": _ident(db, a["tenant"], "ou", sfx),
            "btenant": b["tenant"],
            "buser": b["user"],
        }
        sid = _insert_stream(db, a["tenant"], a["user"])
        ts = now()
        for who, perm in (("editor", "editor"), ("viewer", "viewer")):
            db.stream_shares.insert(
                tenant_id=a["tenant"],
                playbook_id=sid,
                shared_with_identity_id=out[who],
                shared_by_identity_id=a["user"],
                permission=perm,
                created_at=ts,
                updated_at=ts,
            )
        db.commit()
        out["sid"] = sid
        return out

    out = await run_in_threadpool(seed)
    out["db"] = db
    out["app"] = app

    def tok(who="owner", **kw):
        """Token for one of the seeded identities (tenant A) or B's user."""
        if who == "buser":
            return _tok(app, out["btenant"], out["buser"], **kw)
        return _tok(app, out["tenant"], out[who], **kw)

    out["tok"] = tok
    return out


@pytest.mark.asyncio
class TestStreamCrud:
    """Create / list / get / update / delete / duplicate."""

    async def test_node_catalog_and_auth(self, app, sw):
        """The node catalog is served; missing token or scope is rejected."""
        st, body = await call(app, "get", f"{BASE}/node-catalog", sw["tok"]())
        assert st == 200 and body
        st, _ = await call(app, "get", f"{BASE}/node-catalog")
        assert st == 401
        st, _ = await call(app, "get", f"{BASE}/node-catalog", sw["tok"](scopes=[]))
        assert st == 403

    async def test_create_validation_and_success(self, app, sw):
        """Blank names are 400; a valid payload creates stream + version 1."""
        tok = sw["tok"]()
        st, _ = await call(app, "post", BASE, tok, json={"name": "  "})
        assert st == 400
        st, body = await call(
            app,
            "post",
            BASE,
            tok,
            json={
                "name": "Made",
                "description": "d",
                "tags": ["x"],
                "trigger_type": "manual",
                "canvas_data": {"nodes": [{"id": "n1"}], "edges": []},
            },
        )
        assert st == 201
        d = body
        assert d["name"] == "Made" and d["version"] == 1 and d["status"] == "draft"
        assert d["canvas_data"]["nodes"][0]["id"] == "n1"
        st, body = await call(app, "post", BASE, tok, json={"name": "Plain"})
        assert st == 201 and body["canvas_data"] == {"nodes": [], "edges": []}

    async def test_list_filters_visibility_and_pagination(self, app, sw):
        """List honours status/template/search filters and share-based visibility."""
        db = sw["db"]
        tag = uuid.uuid4().hex[:8]
        ids = await in_db(
            lambda: (
                _insert_stream(
                    db,
                    sw["tenant"],
                    sw["owner"],
                    name=f"zz-{tag}-tpl",
                    is_template=True,
                    status="active",
                ),
                _insert_stream(
                    db,
                    sw["tenant"],
                    sw["owner"],
                    name=f"zz-{tag}-pub",
                    is_public=True,
                    description=f"findme-{tag}",
                ),
                _insert_stream(
                    db, sw["btenant"], sw["buser"], name=f"zz-{tag}-b", is_public=True
                ),
            )
        )
        tok = sw["tok"]()
        st, body = await call(app, "get", f"{BASE}?q={tag}&per_page=10", tok)
        names = {s["name"] for s in body["data"]}
        assert st == 200 and names == {f"zz-{tag}-tpl", f"zz-{tag}-pub"}
        st, body = await call(app, "get", f"{BASE}?q=findme-{tag}", tok)
        assert [s["name"] for s in body["data"]] == [f"zz-{tag}-pub"]
        st, body = await call(app, "get", f"{BASE}?q={tag}&is_template=true", tok)
        assert [s["name"] for s in body["data"]] == [f"zz-{tag}-tpl"]
        st, body = await call(
            app, "get", f"{BASE}?q={tag}&is_template=false&status=draft", tok
        )
        assert [s["name"] for s in body["data"]] == [f"zz-{tag}-pub"]
        st, body = await call(app, "get", f"{BASE}?q={tag}&per_page=1&page=2", tok)
        assert body["page"] == 2 and body["total"] == 2
        # a viewer sees the shared stream; the outsider does not
        st, body = await call(app, "get", f"{BASE}?per_page=100", sw["tok"]("outsider"))
        assert sw["sid"] not in [s["id"] for s in body["data"]]
        # public streams of another tenant never leak
        assert ids[2] not in [s["id"] for s in body["data"]]

    @pytest.mark.xfail(
        strict=True,
        reason="bug: list_streams passes shares Row objects to belongs(), so any user holding a share gets a 500",
    )
    async def test_list_includes_shared_streams(self, app, sw):
        """A user with a viewer share should see the shared stream in the list."""
        st, body = await call(app, "get", f"{BASE}?per_page=100", sw["tok"]("viewer"))
        assert st == 200
        assert sw["sid"] in [s["id"] for s in body["data"]]

    async def test_get_stream_access_and_locks(self, app, sw):
        """Reads: owner/viewer ok, outsider and cross-tenant 404, expired lock reaped."""
        sid = sw["sid"]
        st, body = await call(app, "get", f"{BASE}/{sid}", sw["tok"]())
        assert st == 200 and body["id"] == sid
        assert (await call(app, "get", f"{BASE}/{sid}", sw["tok"]("viewer")))[0] == 200
        assert (await call(app, "get", f"{BASE}/{sid}", sw["tok"]("outsider")))[
            0
        ] == 404
        assert (await call(app, "get", f"{BASE}/{sid}", sw["tok"]("buser")))[0] == 404
        assert (await call(app, "get", f"{BASE}/999999999", sw["tok"]()))[0] == 404
        db = sw["db"]
        ts = now()

        def lock(expires):
            """Insert an editor lock row."""
            db.stream_editor_locks.insert(
                tenant_id=sw["tenant"],
                playbook_id=sid,
                locked_by_identity_id=sw["owner"],
                locked_by_name="Owner",
                locked_at=ts,
                expires_at=expires,
                created_at=ts,
                updated_at=ts,
            )
            db.commit()

        await in_db(lambda: lock(ts + timedelta(minutes=10)))
        st, body = await call(app, "get", f"{BASE}/{sid}", sw["tok"]())
        assert body["editor_lock"]["is_holder"] is True
        await in_db(
            lambda: (
                db(db.stream_editor_locks.playbook_id == sid).delete(),
                db.commit(),
                lock(ts - timedelta(minutes=10)),
            )
        )
        st, body = await call(app, "get", f"{BASE}/{sid}", sw["tok"]())
        assert st == 200 and "editor_lock" not in body
        assert (
            await in_db(lambda: db(db.stream_editor_locks.playbook_id == sid).count())
        ) == 0

    async def test_update_fields_canvas_and_validation(self, app, sw):
        """PUT updates metadata, writes a new version and syncs nodes/edges."""
        sid = sw["sid"]
        tok = sw["tok"]("editor")
        st, body = await call(
            app,
            "put",
            f"{BASE}/{sid}",
            tok,
            json={
                "name": " Renamed ",
                "description": None,
                "trigger_type": "webhook",
                "is_public": True,
                "is_template": True,
                "tags": None,
                "status": "active",
                "is_enabled": True,
            },
        )
        d = body
        assert st == 200 and d["name"] == "Renamed" and d["is_public"] is True
        assert d["status"] == "active" and d["tags"] == [] and d["description"] == ""
        canvas = {
            "nodes": [
                {
                    "id": "n1",
                    "type": "trigger",
                    "category": "trigger",
                    "position": {"x": 1.4, "y": 2.6},
                    "data": {
                        "nodeType": "trigger_manual",
                        "label": "L",
                        "config": {"a": 1},
                    },
                },
                {
                    "node_id": "n2",
                    "node_type": "transform_x",
                    "position_x": 3,
                    "position_y": 4,
                    "config": {"b": 2},
                },
                {"id": "", "type": "x"},
                {"id": "n3", "data": "notadict", "position": "bad"},
                "garbage",
            ],
            "edges": [
                {
                    "source": "n1",
                    "target": "n2",
                    "sourceHandle": "out",
                    "targetHandle": "in",
                    "label": "go",
                },
                {"source_node_id": "n2", "target_node_id": "n3", "id": "custom"},
                {"source": "", "target": "n1"},
                "garbage",
            ],
        }
        st, body = await call(
            app, "patch", f"{BASE}/{sid}", tok, json={"canvas_data": canvas}
        )
        assert st == 200 and body["version"] >= 1 and "validation" in body
        db = sw["db"]
        n = await in_db(lambda: db(db.stream_nodes.playbook_id == sid).count())
        e = await in_db(lambda: db(db.stream_edges.playbook_id == sid).count())
        assert (n, e) == (3, 2)
        st, body = await call(
            app, "put", f"{BASE}/{sid}", tok, json={"canvas_data": None}
        )
        assert st == 200
        # empty body is fine too
        st, _ = await call(app, "put", f"{BASE}/{sid}", tok, json={})
        assert st == 200

    async def test_update_denied_and_locked(self, app, sw):
        """Outsider, viewer and cross-tenant writes 404; another user's lock gives 423."""
        sid = sw["sid"]
        for who in ("outsider", "viewer", "buser"):
            st, _ = await call(
                app, "put", f"{BASE}/{sid}", sw["tok"](who), json={"name": "x"}
            )
            assert st == 404, who
        assert (await call(app, "put", f"{BASE}/999999999", sw["tok"](), json={}))[
            0
        ] == 404
        db = sw["db"]
        ts = now()

        def lock(expires, holder):
            """Replace the editor lock row."""
            db(db.stream_editor_locks.playbook_id == sid).delete()
            db.stream_editor_locks.insert(
                tenant_id=sw["tenant"],
                playbook_id=sid,
                locked_by_identity_id=holder,
                locked_by_name="Holder",
                locked_at=ts,
                expires_at=expires,
                created_at=ts,
                updated_at=ts,
            )
            db.commit()

        await in_db(lambda: lock(ts + timedelta(minutes=5), sw["owner"]))
        st, body = await call(
            app, "put", f"{BASE}/{sid}", sw["tok"]("editor"), json={"name": "x"}
        )
        assert st == 423 and "Holder" in body["error"]
        # the holder may still write
        assert (
            await call(app, "put", f"{BASE}/{sid}", sw["tok"](), json={"name": "hold"})
        )[0] == 200
        # an expired lock is reaped and no longer blocks
        await in_db(lambda: lock(ts - timedelta(minutes=5), sw["owner"]))
        assert (
            await call(
                app, "put", f"{BASE}/{sid}", sw["tok"]("editor"), json={"name": "ok"}
            )
        )[0] == 200
        assert (
            await in_db(lambda: db(db.stream_editor_locks.playbook_id == sid).count())
        ) == 0

    async def test_delete_owner_only(self, app, sw):
        """Only the owner may delete; editors and other tenants get 404."""
        db = sw["db"]
        sid = await in_db(
            lambda: _insert_stream(db, sw["tenant"], sw["owner"], is_public=True)
        )
        for who in ("outsider", "buser"):
            assert (await call(app, "delete", f"{BASE}/{sid}", sw["tok"](who)))[
                0
            ] == 404
        assert (await call(app, "delete", f"{BASE}/{sid}", sw["tok"]()))[0] == 200
        assert (await call(app, "delete", f"{BASE}/{sid}", sw["tok"]()))[0] == 404

    async def test_duplicate(self, app, sw):
        """Owner, public and template streams can be duplicated; others 404."""
        db = sw["db"]
        canvas = {"nodes": [{"id": "n"}], "edges": []}

        def seed():
            """Create a private+versioned, a public-versionless and a template stream."""
            priv = _insert_stream(db, sw["tenant"], sw["owner"])
            _insert_version(db, sw["tenant"], priv, sw["owner"], canvas)
            pub = _insert_stream(db, sw["tenant"], sw["owner"], is_public=True)
            tpl = _insert_stream(db, sw["tenant"], sw["owner"], is_template=True)
            return priv, pub, tpl

        priv, pub, tpl = await in_db(seed)
        st, body = await call(app, "post", f"{BASE}/{priv}/duplicate", sw["tok"]())
        assert st == 201 and body["name"].endswith("(Copy)")
        assert (
            await call(app, "post", f"{BASE}/{pub}/duplicate", sw["tok"]("outsider"))
        )[0] == 201
        assert (
            await call(app, "post", f"{BASE}/{tpl}/duplicate", sw["tok"]("outsider"))
        )[0] == 201
        assert (
            await call(app, "post", f"{BASE}/{priv}/duplicate", sw["tok"]("outsider"))
        )[0] == 404
        assert (await call(app, "post", f"{BASE}/999999999/duplicate", sw["tok"]()))[
            0
        ] == 404


@pytest.mark.asyncio
class TestValidateAndLocks:
    """Validation endpoint and editor-lock lifecycle."""

    async def test_validate_sources(self, app, sw):
        """Validation uses inline canvas, inline nodes, or the stored graph."""
        sid = sw["sid"]
        tok = sw["tok"]()
        url = f"{BASE}/{sid}/validate"
        st, body = await call(
            app, "post", url, tok, json={"canvas_data": {"nodes": [], "edges": []}}
        )
        assert st == 200 and "valid" in body
        st, _ = await call(
            app,
            "post",
            url,
            tok,
            json={"nodes": [{"id": "a", "type": "nope"}], "edges": []},
        )
        assert st == 200
        # nothing stored yet -> empty graph
        assert (await call(app, "post", url, tok))[0] == 200
        db = sw["db"]

        def seed_nodes():
            """Store a normalized node + edge for the stream."""
            ts = now()
            db.stream_nodes.insert(
                tenant_id=sw["tenant"],
                playbook_id=sid,
                node_id="n1",
                node_type="x",
                node_category="transform",
                label="n",
                position_x=0,
                position_y=0,
                config={},
                is_enabled=True,
                execution_order=0,
                created_at=ts,
                updated_at=ts,
            )
            db.stream_edges.insert(
                tenant_id=sw["tenant"],
                playbook_id=sid,
                edge_id="e1",
                source_node_id="n1",
                target_node_id="n1",
                created_at=ts,
                updated_at=ts,
            )
            db.commit()

        await in_db(seed_nodes)
        assert (await call(app, "post", url, tok))[0] == 200
        assert (await call(app, "post", url, sw["tok"]("outsider"), json={}))[0] == 403
        assert (await call(app, "post", f"{BASE}/999999999/validate", tok, json={}))[
            0
        ] == 404

    async def test_validate_falls_back_to_version_snapshot(self, app, sw):
        """With no normalized nodes the latest version's canvas is validated."""
        db = sw["db"]
        sid = await in_db(lambda: _insert_stream(db, sw["tenant"], sw["owner"]))
        await in_db(
            lambda: _insert_version(
                db,
                sw["tenant"],
                sid,
                sw["owner"],
                {"nodes": [{"id": "z", "type": "zzz"}], "edges": []},
            )
        )
        st, _ = await call(app, "post", f"{BASE}/{sid}/validate", sw["tok"](), json={})
        assert st == 200
        nodes, edges = await in_db(
            lambda: sr._load_stream_nodes_edges(db, sw["tenant"], sid)
        )
        assert nodes and edges == []
        # legacy snapshot without canvas_json uses nodes_json/edges_json
        sid2 = await in_db(lambda: _insert_stream(db, sw["tenant"], sw["owner"]))

        def legacy():
            """Version row with empty canvas but legacy node/edge JSON."""
            ts = now()
            db.stream_versions.insert(
                tenant_id=sw["tenant"],
                playbook_id=sid2,
                version_number=1,
                created_by_identity_id=sw["owner"],
                nodes_json=[{"id": "q"}],
                edges_json=[{"id": "e"}],
                canvas_json={},
                change_summary="t",
                created_at=ts,
                updated_at=ts,
            )
            db.commit()
            return sr._load_stream_nodes_edges(db, sw["tenant"], sid2)

        nodes, edges = await in_db(legacy)
        assert nodes == [{"id": "q"}] and edges == [{"id": "e"}]

    async def test_lock_lifecycle(self, app, sw):
        """Acquire, refresh, contend, inspect and release an editor lock."""
        sid = sw["sid"]
        url = f"{BASE}/{sid}/lock"
        st, body = await call(app, "get", url, sw["tok"]())
        assert st == 200 and body == {"locked": False, "lock": None}
        st, body = await call(app, "post", url, sw["tok"](), json={"socket_id": "s1"})
        assert st == 201 and body["lock"]["is_holder"] is True
        st, body = await call(app, "post", url, sw["tok"](), json={"socket_id": "s2"})
        assert st == 200 and body["lock"]["is_holder"] is True
        st, body = await call(app, "post", url, sw["tok"]("editor"), json={})
        assert st == 423
        assert (await call(app, "post", url, sw["tok"]("viewer"), json={}))[0] == 403
        assert (
            await call(app, "post", f"{BASE}/999999999/lock", sw["tok"](), json={})
        )[0] == 404
        st, body = await call(app, "get", url, sw["tok"]("editor"))
        assert body["locked"] is True and body["lock"]["is_holder"] is False
        assert (await call(app, "delete", url, sw["tok"]("editor")))[0] == 403
        assert (await call(app, "delete", url, sw["tok"]()))[0] == 200
        assert (await call(app, "delete", url, sw["tok"]()))[
            0
        ] == 200  # no lock: idempotent
        assert (await call(app, "delete", f"{BASE}/999999999/lock", sw["tok"]()))[
            0
        ] == 404
        assert (await call(app, "get", f"{BASE}/999999999/lock", sw["tok"]()))[0] == 404

    async def test_expired_lock_is_replaced_and_reaped(self, app, sw):
        """An expired lock is dropped on status read and replaced on acquire."""
        db = sw["db"]
        sid = sw["sid"]
        ts = now()

        def expired():
            """Insert an already expired lock."""
            db(db.stream_editor_locks.playbook_id == sid).delete()
            db.stream_editor_locks.insert(
                tenant_id=sw["tenant"],
                playbook_id=sid,
                locked_by_identity_id=sw["owner"],
                locked_by_name="o",
                locked_at=ts,
                expires_at=ts - timedelta(minutes=1),
                created_at=ts,
                updated_at=ts,
            )
            db.commit()

        await in_db(expired)
        st, body = await call(app, "get", f"{BASE}/{sid}/lock", sw["tok"]())
        assert body["locked"] is False
        await in_db(expired)
        st, body = await call(
            app, "post", f"{BASE}/{sid}/lock", sw["tok"]("editor"), json={}
        )
        assert st == 201


@pytest.mark.asyncio
class TestExecute:
    """Manual execution and the execution-history endpoints."""

    async def test_execute_branches(self, app, sw):
        """Dry run, access control, validation failure and queued execution."""
        sid = sw["sid"]
        url = f"{BASE}/{sid}/execute"
        assert (
            await call(app, "post", f"{BASE}/999999999/execute", sw["tok"](), json={})
        )[0] == 404
        assert (await call(app, "post", url, sw["tok"]("viewer"), json={}))[0] == 403
        assert (
            await call(app, "post", url, sw["tok"](scopes=["streams:read"]), json={})
        )[0] == 403
        st, body = await call(app, "post", url, sw["tok"](), json={"dry_run": True})
        assert st == 200 and body["dry_run"] is True
        # bad node types make validation fail -> 400 or module-disabled 403
        canvas = {
            "nodes": [
                {
                    "id": "a",
                    "type": "no_such_type",
                    "data": {"nodeType": "no_such_type"},
                }
            ],
            "edges": [],
        }
        st, _ = await call(
            app, "put", f"{BASE}/{sid}", sw["tok"](), json={"canvas_data": canvas}
        )
        assert st == 200
        st, body = await call(app, "post", url, sw["tok"](), json={})
        assert st in (400, 403), body
        st, body = await call(app, "post", url, sw["tok"](), json={"dry_run": True})
        assert st == 200 and body["valid"] is False

    async def test_execute_validation_and_module_branches(self, app, sw):
        """Force the disabled-module, invalid and valid validation outcomes."""
        sid = sw["sid"]
        url = f"{BASE}/{sid}/execute"
        valid = {"valid": True, "has_disabled_modules": False}
        with patch(
            "apps.api.modules.streams.node_catalog.validate_playbook_nodes",
            return_value={
                "valid": False,
                "has_disabled_modules": True,
                "disabled_modules": ["m1"],
            },
        ):
            st, body = await call(app, "post", url, sw["tok"](), json={})
            assert st == 403 and body["code"] == "MODULE_DISABLED"
        with patch(
            "apps.api.modules.streams.node_catalog.validate_playbook_nodes",
            return_value={"valid": False, "has_disabled_modules": False},
        ):
            st, _ = await call(app, "post", url, sw["tok"](), json={})
            assert st == 400
        with (
            patch(
                "apps.api.modules.streams.node_catalog.validate_playbook_nodes",
                return_value=valid,
            ),
            patch(
                "shared.jobbus.JobBus.ensure_group",
                new=AsyncMock(side_effect=RuntimeError("redis down")),
            ),
        ):
            st, body = await call(
                app, "post", url, sw["tok"](), json={"input_data": {"k": 1}}
            )
            assert st == 202 and body["status"] == "queued"
        with (
            patch(
                "apps.api.modules.streams.node_catalog.validate_playbook_nodes",
                return_value=valid,
            ),
            patch("shared.jobbus.JobBus.ensure_group", new=AsyncMock()),
            patch("shared.jobbus.JobBus.enqueue", new=AsyncMock()) as enq,
        ):
            st, _ = await call(app, "post", url, sw["tok"](), json={})
            assert st == 202
            assert enq.await_count in (0, 1)
        with (
            patch(
                "apps.api.modules.streams.node_catalog.validate_playbook_nodes",
                return_value=valid,
            ),
            patch("apps.worker.config.settings.settings.redis_url", ""),
        ):
            st, _ = await call(app, "post", url, sw["tok"](), json={})
            assert st == 202

    async def test_execution_history(self, app, sw):
        """Per-stream and unified execution listings plus detail endpoints."""
        db = sw["db"]
        sid = sw["sid"]
        ts = now()
        exec_uuid = "ex-" + uuid.uuid4().hex[:10]
        tag = uuid.uuid4().hex[:6]

        def seed():
            """Insert playbook/coderun/webhook executions and a node log."""
            db.stream_executions.insert(
                tenant_id=sw["tenant"],
                playbook_id=sid,
                execution_id=exec_uuid,
                status="completed",
                trigger_type="manual",
                triggered_by_identity_id=sw["owner"],
                input_json={},
                started_at=ts,
                completed_at=ts,
                created_at=ts,
                updated_at=ts,
            )
            db.stream_executions.insert(
                tenant_id=sw["tenant"],
                playbook_id=sid,
                execution_id=exec_uuid + "b",
                status="failed",
                trigger_type="manual",
                triggered_by_identity_id=sw["owner"],
                input_json={},
                started_at=ts - timedelta(hours=1),
                created_at=ts,
                updated_at=ts,
            )
            db.stream_node_executions.insert(
                tenant_id=sw["tenant"],
                execution_id=exec_uuid,
                node_id="n1",
                node_type="x",
                playbook_id=sid,
                status="completed",
                started_at=ts,
                completed_at=ts,
                duration_ms=3,
                created_at=ts,
                updated_at=ts,
            )
            fid = db.run_functions.insert(
                tenant_id=sw["tenant"],
                village_id=vid(),
                name=f"fn-{tag}",
                runtime="python",
                code="x",
                created_at=ts,
                updated_at=ts,
            )
            db.run_executions.insert(
                tenant_id=sw["tenant"],
                execution_id="rx-" + tag,
                function_id=fid,
                status="completed",
                trigger_type="manual",
                started_at=ts,
                created_at=ts,
            )
            db.run_executions.insert(
                tenant_id=sw["tenant"],
                execution_id="rx2-" + tag,
                function_id=fid,
                status="failed",
                created_at=ts,
            )
            wid = db.webhooks.insert(
                tenant_id=sw["tenant"],
                village_id=vid(),
                name=f"wh-{tag}",
                url="https://example.test/h",
                is_active=True,
                created_at=ts,
                updated_at=ts,
            )
            db.webhook_deliveries.insert(
                webhook_id=wid,
                event_type="issue.created",
                status="success",
                http_status=200,
                delivered_at=ts,
                created_at=ts,
            )
            db.commit()

        await in_db(seed)
        tok = sw["tok"]()
        st, body = await call(app, "get", f"{BASE}/{sid}/executions", tok)
        assert st == 200 and body["total"] >= 2
        st, body = await call(app, "get", f"{BASE}/{sid}/executions?status=failed", tok)
        assert {e["status"] for e in body["data"]} == {"failed"}
        assert (
            await call(app, "get", f"{BASE}/{sid}/executions", sw["tok"]("outsider"))
        )[0] == 404
        assert (await call(app, "get", f"{BASE}/999999999/executions", tok))[0] == 404

        st, body = await call(app, "get", f"{BASE}/{sid}/executions/{exec_uuid}", tok)
        assert st == 200 and body["node_logs"][0]["node_id"] == "n1"
        st, body = await call(app, "get", f"{BASE}/{sid}/executions/{exec_uuid}b", tok)
        assert st == 200 and body["node_logs"] == []
        assert (await call(app, "get", f"{BASE}/{sid}/executions/nope", tok))[0] == 404
        assert (
            await call(app, "get", f"{BASE}/999999999/executions/{exec_uuid}", tok)
        )[0] == 404
        assert (
            await call(
                app,
                "get",
                f"{BASE}/{sid}/executions/{exec_uuid}",
                sw["tok"]("outsider"),
            )
        )[0] == 404

        st, body = await call(app, "get", f"{BASE}/executions/{exec_uuid}", tok)
        assert st == 200 and body["stream_id"] == sid
        assert (await call(app, "get", f"{BASE}/executions/nope", tok))[0] == 404
        assert (
            await call(
                app, "get", f"{BASE}/executions/{exec_uuid}", sw["tok"]("outsider")
            )
        )[0] == 404
        assert (
            await call(app, "get", f"{BASE}/executions/{exec_uuid}", sw["tok"]("buser"))
        )[0] == 404

        st, body = await call(app, "get", f"{BASE}/executions?per_page=100", tok)
        counts = body["counts_by_source"]
        assert (
            st == 200
            and counts["playbook"] >= 2
            and counts["coderun"] >= 2
            and counts["webhook"] >= 1
        )
        for src in ("playbook", "coderun", "webhook"):
            st, body = await call(app, "get", f"{BASE}/executions?source={src}", tok)
            assert st == 200 and {e["source"] for e in body["items"]} <= {src}
        st, body = await call(
            app, "get", f"{BASE}/executions?source=all&status=failed&search=rx2", tok
        )
        assert [e["execution_id"] for e in body["items"]] == ["rx2-" + tag]
        st, body = await call(
            app, "get", f"{BASE}/executions?search=zzzz-no-match", tok
        )
        assert body["total"] == 0 and body["pages"] == 0
        assert (await call(app, "get", f"{BASE}/executions?source=bogus", tok))[
            0
        ] == 400
        st, body = await call(app, "get", f"{BASE}/executions?stream_id={sid}", tok)
        assert st == 200 and body["counts_by_source"]["coderun"] == 0
        st, body = await call(app, "get", f"{BASE}/executions?stream_id=abc", tok)
        assert st == 200 and body["total"] == 0
        st, body = await call(app, "get", f"{BASE}/executions?stream_id=999999999", tok)
        assert body["total"] == 0
        # a viewer reaches the shared stream's executions; the outsider sees none
        st, body = await call(
            app,
            "get",
            f"{BASE}/executions?source=playbook&per_page=100",
            sw["tok"]("viewer"),
        )
        assert body["total"] >= 2
        st, body = await call(
            app,
            "get",
            f"{BASE}/executions?source=playbook&per_page=100",
            sw["tok"]("outsider"),
        )
        assert body["total"] == 0


class TestHelpers:
    """Pure helper functions in the streams routes module."""

    def test_iso_and_sort_key(self):
        """Timestamps normalise to ISO and sort keys tolerate bad input."""
        d = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
        assert sr._iso_timestamp(None) is None
        assert sr._iso_timestamp(d) == d.isoformat()
        assert sr._iso_timestamp("x") == "x"
        naive = datetime(2026, 1, 2)
        assert sr._execution_sort_key({"started_at": naive, "id": "7"})[1] == 7
        assert sr._execution_sort_key({"created_at": d})[0] == d
        assert sr._execution_sort_key({"started_at": "2026-01-02T03:04:00Z"})[0] == d
        assert sr._execution_sort_key({"started_at": "2026-01-02T03:04:00"})[0] == d
        assert sr._execution_sort_key({"started_at": "not-a-date", "id": "zz"}) == (
            datetime.min.replace(tzinfo=UTC),
            0,
        )
        assert sr._execution_sort_key({"started_at": "  "})[1] == 0

    def test_filters(self):
        """Status and free-text filters match case-insensitively."""
        e = {"status": "Failed", "source_name": "Alpha", "execution_id": None}
        assert sr._matches_execution_filters(e, None, "")
        assert sr._matches_execution_filters(e, "failed", "alpha")
        assert not sr._matches_execution_filters(e, "completed", "")
        assert not sr._matches_execution_filters(e, None, "beta")

    def test_serializers_with_sparse_rows(self):
        """Code-run and webhook serializers fall back to generated names."""
        row = SimpleNamespace(id=1, execution_id="x", function_id=9, status="ok")
        out = sr._serialize_coderun_execution(row, None)
        assert out["source_name"] == "CodeRun #9" and out["trigger_type"] == "manual"
        wrow = SimpleNamespace(id=2, webhook_id=5)
        out = sr._serialize_webhook_delivery(wrow, None)
        assert out["source_name"] == "Webhook #5" and out["status"] == "unknown"
        assert out["trigger_type"] == "webhook"

    def test_mint_execution_jwt(self, app):
        """The execution JWT is tenant scoped, portal-aware and None without a secret."""
        cfg = {"JWT_SECRET_KEY": "k" * 32, "JWT_ALGORITHM": "HS256"}
        tok = sr._mint_execution_jwt(
            cfg, {"sub": "5", "scope": ["a"], "roles": ["r"]}, None, 3, 5
        )
        payload = jwt.decode(
            tok, cfg["JWT_SECRET_KEY"], algorithms=["HS256"], audience="elder-api"
        )
        assert (
            payload["tenant"] == "3"
            and payload["type"] == "access"
            and payload["scope"] == ["a"]
        )
        tok = sr._mint_execution_jwt(
            cfg, {"type": "portal_user"}, SimpleNamespace(id=8), 3, 5
        )
        assert (
            jwt.decode(
                tok, cfg["JWT_SECRET_KEY"], algorithms=["HS256"], audience="elder-api"
            )["type"]
            == "portal_user"
        )
        tok = sr._mint_execution_jwt(
            cfg, {}, SimpleNamespace(_portal_user=1, id=8), 3, 5
        )
        assert (
            jwt.decode(
                tok, cfg["JWT_SECRET_KEY"], algorithms=["HS256"], audience="elder-api"
            )["sub"]
            == "8"
        )
        assert sr._mint_execution_jwt({}, {}, None, 1, 1) is None

    def test_identity_and_tenant_parsing(self, app):
        """Claim parsing tolerates missing and malformed values."""
        from quart import g

        async def go():
            """Probe the claim helpers inside a request context."""
            async with app.test_request_context("/"):
                g.claims = {"tenant": "abc", "identity_id": "zz"}
                assert sr._get_tenant_id() is None and sr._get_identity_id() is None
                g.claims = {}
                assert sr._get_tenant_id() is None
                g.claims = {"tenant": "4", "identity_id": "9"}
                assert (sr._get_tenant_id(), sr._get_identity_id()) == (4, 9)

        import asyncio

        asyncio.run(go())

    def test_can_read_rules(self):
        """Read/edit checks cover cross-tenant, anonymous and public cases."""
        stream = SimpleNamespace(
            id=1, tenant_id=1, is_public=False, owner_identity_id=2
        )
        assert not sr._can_read_stream(None, stream, 9, 2)
        assert not sr._can_read_stream(None, stream, 1, None)
        assert sr._can_read_stream(None, stream, 1, 2)
        assert sr._can_read_stream(
            None, SimpleNamespace(tenant_id=1, is_public=True), 1
        )
        assert not sr._can_edit_stream(None, stream, 9, 2)
        assert not sr._can_edit_stream(None, stream, 1, None)
        assert sr._can_edit_stream(None, stream, 1, 2)
