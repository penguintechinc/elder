"""Coverage tests for apps.api.auth.decorators (authN/authZ decorators).

Exercises 401/403/400 denial paths, superuser/service-worker bypasses, scope and
role claims checks, and resource-role lookups against the real Postgres.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from quart import g

from apps.api.auth import decorators as dec

GCU = "apps.api.auth.decorators.get_current_user"
XF = pytest.mark.xfail(
    strict=True,
    reason="bug: decorators.py:413 compares lowercase resource_type to uppercase "
    "Postgres enum; DataError swallowed -> always 403",
)


def _user(**kw):
    """Build a lightweight identity stand-in with sane defaults."""
    base = {
        "id": 7,
        "is_superuser": False,
        "portal_role": "observer",
        "token_type": None,
    }
    base.update(kw)
    ns = SimpleNamespace(**base)
    ns.get = lambda key, default=None: getattr(ns, key, default)
    return ns


async def _ok(*args, **kwargs):
    """Async handler that records it ran."""
    return "async-ok"


def _sync_ok(*args, **kwargs):
    """Sync handler that records it ran."""
    return "sync-ok"


def _status(resp):
    """Return (body, status) normalizing jsonify tuples."""
    return resp if isinstance(resp, tuple) else (resp, 200)


class TestLoginRequired:
    """login_required denies anonymous callers and runs sync/async handlers."""

    @pytest.mark.asyncio
    async def test_no_user_returns_401(self, app):
        """Anonymous request is rejected with 401."""
        async with app.test_request_context("/x"):
            with patch(GCU, return_value=None):
                _, code = _status(await dec.login_required(_ok)())
        assert code == 401

    @pytest.mark.asyncio
    @pytest.mark.parametrize("fn,expected", [(_ok, "async-ok"), (_sync_ok, "sync-ok")])
    async def test_authenticated_runs_handler(self, app, fn, expected):
        """Authenticated request runs sync and async handlers and sets g.current_user."""
        u = _user()
        async with app.test_request_context("/x"):
            with patch(GCU, return_value=u):
                out = await dec.login_required(fn)()
            assert g.current_user is u
        assert out == expected


class TestPermissionRequired:
    """permission_required: 401, 403, superuser bypass, granted."""

    @pytest.mark.asyncio
    async def test_unauthenticated(self, app):
        """No user -> 401."""
        async with app.test_request_context("/x"):
            with patch(GCU, return_value=None):
                _, code = _status(await dec.permission_required("p")(_ok)())
        assert code == 401

    @pytest.mark.asyncio
    @pytest.mark.parametrize("fn,expected", [(_ok, "async-ok"), (_sync_ok, "sync-ok")])
    async def test_superuser_bypasses(self, app, fn, expected):
        """Superuser skips the permission check entirely."""
        async with app.test_request_context("/x"):
            with (
                patch(GCU, return_value=_user(is_superuser=True)),
                patch.object(dec, "_check_user_permission") as chk,
            ):
                out = await dec.permission_required("p")(fn)()
        assert out == expected
        chk.assert_not_called()

    @pytest.mark.asyncio
    async def test_denied_returns_403_with_required_permission(self, app):
        """Missing permission -> 403 naming the required permission."""
        async with app.test_request_context("/x"):
            with (
                patch(GCU, return_value=_user()),
                patch.object(dec, "_check_user_permission", return_value=False),
            ):
                resp, code = await dec.permission_required("entities:write")(_ok)()
                body = await resp.get_json()
        assert code == 403
        assert body["required_permission"] == "entities:write"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("fn,expected", [(_ok, "async-ok"), (_sync_ok, "sync-ok")])
    async def test_granted_runs_handler(self, app, fn, expected):
        """Granted permission runs the handler."""
        async with app.test_request_context("/x"):
            with (
                patch(GCU, return_value=_user()),
                patch.object(dec, "_check_user_permission", return_value=True),
            ):
                out = await dec.permission_required("p")(fn)()
        assert out == expected


class TestPermissionsRequired:
    """permissions_required: all/any semantics."""

    @pytest.mark.asyncio
    async def test_unauthenticated(self, app):
        """No user -> 401."""
        async with app.test_request_context("/x"):
            with patch(GCU, return_value=None):
                _, code = _status(await dec.permissions_required(["a"])(_ok)())
        assert code == 401

    @pytest.mark.asyncio
    @pytest.mark.parametrize("fn,expected", [(_ok, "async-ok"), (_sync_ok, "sync-ok")])
    async def test_superuser_bypasses(self, app, fn, expected):
        """Superuser skips permission checks."""
        async with app.test_request_context("/x"):
            with patch(GCU, return_value=_user(is_superuser=True)):
                out = await dec.permissions_required(["a", "b"])(fn)()
        assert out == expected

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "require_all,results,allowed",
        [
            (True, [True, True], True),
            (True, [True, False], False),
            (False, [False, True], True),
            (False, [False, False], False),
        ],
    )
    @pytest.mark.parametrize("fn", [_ok, _sync_ok])
    async def test_all_vs_any(self, app, require_all, results, allowed, fn):
        """require_all demands every permission; otherwise any one suffices."""
        async with app.test_request_context("/x"):
            with (
                patch(GCU, return_value=_user()),
                patch.object(dec, "_check_user_permission", side_effect=results),
            ):
                out = await dec.permissions_required(["a", "b"], require_all)(fn)()
        if allowed:
            assert out in ("async-ok", "sync-ok")
        else:
            resp, code = out
            assert code == 403
            assert (await resp.get_json())["require_all"] is require_all


class TestOrgPermissionRequired:
    """org_permission_required: org id param, 400, 403."""

    @pytest.mark.asyncio
    async def test_unauthenticated(self, app):
        """No user -> 401."""
        async with app.test_request_context("/x"):
            with patch(GCU, return_value=None):
                _, code = _status(await dec.org_permission_required("p")(_ok)(id=1))
        assert code == 401

    @pytest.mark.asyncio
    @pytest.mark.parametrize("fn,expected", [(_ok, "async-ok"), (_sync_ok, "sync-ok")])
    async def test_superuser_bypasses(self, app, fn, expected):
        """Superuser bypasses org checks even without an org id."""
        async with app.test_request_context("/x"):
            with patch(GCU, return_value=_user(is_superuser=True)):
                out = await dec.org_permission_required("p")(fn)()
        assert out == expected

    @pytest.mark.asyncio
    async def test_missing_org_id_is_400(self, app):
        """Non-superuser without the org id route param -> 400."""
        async with app.test_request_context("/x"):
            with patch(GCU, return_value=_user()):
                _, code = await dec.org_permission_required("p")(_ok)()
        assert code == 400

    @pytest.mark.asyncio
    async def test_denied_is_403_with_org_id(self, app):
        """Missing org permission -> 403 echoing the organization id."""
        async with app.test_request_context("/x"):
            with (
                patch(GCU, return_value=_user()),
                patch.object(dec, "_check_org_permission", return_value=False),
            ):
                resp, code = await dec.org_permission_required("p", "oid")(_ok)(oid=9)
                body = await resp.get_json()
        assert code == 403
        assert body["organization_id"] == 9

    @pytest.mark.asyncio
    @pytest.mark.parametrize("fn,expected", [(_ok, "async-ok"), (_sync_ok, "sync-ok")])
    async def test_granted(self, app, fn, expected):
        """Granted org permission runs the handler."""
        async with app.test_request_context("/x"):
            with (
                patch(GCU, return_value=_user()),
                patch.object(dec, "_check_org_permission", return_value=True) as chk,
            ):
                out = await dec.org_permission_required("p")(fn)(id=3)
        assert out == expected
        chk.assert_called_once()
        assert chk.call_args.args[1:] == ("p", 3)


def test_check_helpers_delegate_to_rbac():
    """_check_user_permission/_check_org_permission delegate to the rbac module."""
    u = _user()
    with patch.object(dec, "check_permission", return_value=True) as cp:
        assert dec._check_user_permission(u, "x") is True
        cp.assert_called_once_with(u, "x")
    with patch.object(dec, "check_org_permission", return_value=False) as cop:
        assert dec._check_org_permission(u, "x", 4) is False
        cop.assert_called_once_with(u, "x", 4)


class TestScopeAndRole:
    """require_scope / require_role: claims-based denial and superuser bypass."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "maker,key,missing_msg",
        [
            (dec.require_scope, "scope", "Missing required scope"),
            (dec.require_role, "roles", "Missing required role"),
        ],
    )
    async def test_wrong_claim_is_403(self, app, maker, key, missing_msg):
        """A token lacking the required scope/role is denied with 403."""
        async with app.test_request_context("/x"):
            g.claims = {key: ["other:thing"]}
            resp, code = await maker("needed:thing")(_ok)()
            body = await resp.get_json()
        assert code == 403
        assert missing_msg in body["error"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "maker,key", [(dec.require_scope, "scope"), (dec.require_role, "roles")]
    )
    async def test_missing_claims_is_403(self, app, maker, key):
        """With no claims at all the request is denied."""
        async with app.test_request_context("/x"):
            _, code = await maker("needed")(_ok)()
        assert code == 403

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "maker,key", [(dec.require_scope, "scope"), (dec.require_role, "roles")]
    )
    @pytest.mark.parametrize("fn,expected", [(_ok, "async-ok"), (_sync_ok, "sync-ok")])
    async def test_granted(self, app, maker, key, fn, expected):
        """Present scope/role lets the handler run (sync and async)."""
        async with app.test_request_context("/x"):
            g.claims = {key: ["needed"]}
            out = await maker("needed")(fn)()
        assert out == expected

    @pytest.mark.asyncio
    @pytest.mark.parametrize("maker", [dec.require_scope, dec.require_role])
    async def test_superuser_bypass(self, app, maker):
        """Superuser bypasses scope/role claim checks."""
        async with app.test_request_context("/x"):
            g.claims = {}
            g.current_user = _user(is_superuser=True)
            out = await maker("needed")(_ok)()
        assert out == "async-ok"


def _mk_tenant(db):
    """Insert a tenant and return its id."""
    tid = db.tenants.insert(
        name="CovA Tenant", slug=f"cova-{uuid4().hex[:8]}", is_active=True
    )
    db.commit()
    return tid


def _mk_identity(db, tenant_id):
    """Insert an identity and return its id."""
    u = f"cova{uuid4().hex[:8]}"
    iid = db.identities.insert(
        identity_type="human",
        username=u,
        email=f"{u}@example.com",
        tenant_id=tenant_id,
        auth_provider="local",
        is_active=True,
        is_superuser=False,
        mfa_enabled=False,
        must_change_password=False,
        portal_role="observer",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.commit()
    return iid


def _mk_role(db, identity_id, rtype, rid, role):
    """Insert a resource_roles row (enum members are stored uppercase)."""
    db.resource_roles.insert(
        identity_id=identity_id,
        resource_type=rtype.upper(),
        resource_id=rid,
        role=role,
        role_type=role.upper(),
    )
    db.commit()


class TestResourceRoleRequired:
    """resource_role_required against real resource_roles rows."""

    @pytest.mark.asyncio
    async def test_unauthenticated(self, app):
        """No user -> 401."""
        async with app.test_request_context("/entities/1"):
            with patch(GCU, return_value=None):
                _, code = _status(await dec.resource_role_required("viewer")(_ok)(id=1))
        assert code == 401

    @pytest.mark.asyncio
    @pytest.mark.parametrize("fn,expected", [(_ok, "async-ok"), (_sync_ok, "sync-ok")])
    async def test_superuser_bypass(self, app, fn, expected):
        """Superuser skips the resource role lookup."""
        async with app.test_request_context("/entities/1"):
            with patch(GCU, return_value=_user(is_superuser=True)):
                out = await dec.resource_role_required("viewer")(fn)(id=1)
        assert out == expected

    @pytest.mark.asyncio
    async def test_service_worker_bypass_requires_matching_claim(self, app):
        """A service_worker identity bypasses only when claims also say service_worker."""
        sw = _user(
            token_type="service_worker", username="svc-runworker", tenant_id=None
        )
        async with app.test_request_context("/entities/1"):
            g.claims = {"type": "service_worker", "tenant": "system"}
            g.current_user = sw
            with patch(GCU, return_value=sw):
                out = await dec.resource_role_required("viewer")(_ok)(id=1)
        assert out == "async-ok"
        # A tenant-scoped worker never gets the platform bypass.
        tenant_sw = _user(
            token_type="service_worker", username="svc-runworker", tenant_id=5
        )
        async with app.test_request_context("/entities/1"):
            g.claims = {"type": "service_worker", "tenant": "acme"}
            g.current_user = tenant_sw
            with patch(GCU, return_value=tenant_sw):
                _, code = await dec.resource_role_required("viewer")(_ok)(id=1)
        assert code == 403
        async with app.test_request_context("/entities/1"):
            g.claims = {"type": "access"}
            with patch(GCU, return_value=sw):
                _, code = await dec.resource_role_required("viewer")(_ok)(id=1)
        assert code == 403

    @pytest.mark.asyncio
    async def test_service_worker_scope_bypass_refused_for_tenant_worker(self, app):
        """Scope bypass is limited to platform (system-tenant) workers."""
        sw = _user(
            token_type="service_worker",
            portal_role="service_worker",
            username="svc-runworker",
            tenant_id=5,
        )
        deco = dec.role_required("admin", allow_service_worker_scopes=("runs:read",))
        async with app.test_request_context("/x"):
            g.claims = {
                "type": "service_worker",
                "scope": ["runs:read"],
                "tenant": "acme",
            }
            g.current_user = sw
            with patch(GCU, return_value=sw):
                out = await deco(_ok)()
        assert out[1] == 403

    @pytest.mark.asyncio
    async def test_route_param_unknown_path_is_400(self, app):
        """Resource id from route but path neither entities nor organizations -> 400."""
        async with app.test_request_context("/widgets/1"):
            with patch(GCU, return_value=_user()):
                _, code = await dec.resource_role_required("viewer")(_ok)(id=1)
        assert code == 400

    @pytest.mark.asyncio
    async def test_no_resource_id_non_json_is_400(self, app):
        """No route id and no JSON body -> 400."""
        async with app.test_request_context("/entities"):
            with patch(GCU, return_value=_user()):
                _, code = await dec.resource_role_required("viewer")(_ok)()
        assert code == 400

    @pytest.mark.asyncio
    async def test_json_body_without_resource_is_400(self, app):
        """JSON body lacking entity_id/organization_id -> 400."""
        async with app.test_request_context(
            "/issues", method="POST", json={"title": "t"}
        ):
            with patch(GCU, return_value=_user()):
                _, code = await dec.resource_role_required("viewer")(_ok)()
        assert code == 400

    @pytest.mark.asyncio
    async def test_invalid_required_role_is_500(self, app):
        """Unknown required role string is a server misconfiguration -> 500."""
        async with app.test_request_context("/entities/1"):
            with patch(GCU, return_value=_user()):
                _, code = await dec.resource_role_required("godmode")(_ok)(id=1)
        assert code == 500

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "path,rtype",
        [("/api/v1/entities/", "entity"), ("/api/v1/organizations/", "organization")],
    )
    @pytest.mark.parametrize(
        "held,required,allowed",
        [
            pytest.param("maintainer", "viewer", True, marks=XF),
            pytest.param("operator", "operator", True, marks=XF),
            ("viewer", "maintainer", False),
            ("viewer", "operator", False),
        ],
    )
    async def test_role_hierarchy_for_route_param(
        self, app, path, rtype, held, required, allowed
    ):
        """Held role must meet/exceed the required level; others are denied (403)."""
        async with app.app_context():
            db = app.db
            iid = _mk_identity(db, _mk_tenant(db))
            rid = 880000 + int(uuid4().int % 90000)
            _mk_role(db, iid, rtype, rid, held)
        async with app.test_request_context(f"{path}{rid}"):
            with patch(GCU, return_value=_user(id=iid)):
                out = await dec.resource_role_required(required)(_ok)(id=rid)
        if allowed:
            assert out == "async-ok"
        else:
            resp, code = out
            assert code == 403
            assert (await resp.get_json())["resource_type"] == rtype

    @pytest.mark.asyncio
    async def test_other_identity_denied_on_same_resource(self, app):
        """Cross-identity denial: a role held by A does not authorize B."""
        async with app.app_context():
            db = app.db
            tid = _mk_tenant(db)
            owner, other = _mk_identity(db, tid), _mk_identity(db, tid)
            rid = 890000 + int(uuid4().int % 90000)
            _mk_role(db, owner, "entity", rid, "maintainer")
        async with app.test_request_context(f"/api/v1/entities/{rid}"):
            with patch(GCU, return_value=_user(id=other)):
                _, code = await dec.resource_role_required("viewer")(_ok)(id=rid)
        assert code == 403

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "body,rtype",
        [
            pytest.param({"entity_id": 0}, None, marks=XF),
            pytest.param({"organization_id": 0}, None, marks=XF),
        ],
    )
    async def test_json_body_resource_resolution(self, app, body, rtype):
        """entity_id/organization_id in the JSON body resolves the resource type."""
        key = next(iter(body))
        async with app.app_context():
            db = app.db
            iid = _mk_identity(db, _mk_tenant(db))
            rid = 870000 + int(uuid4().int % 90000)
            _mk_role(db, iid, key.split("_")[0], rid, "viewer")
        async with app.test_request_context("/issues", method="POST", json={key: rid}):
            with patch(GCU, return_value=_user(id=iid)):
                out = await dec.resource_role_required("viewer")(_ok)()
        assert out == "async-ok"

    @pytest.mark.asyncio
    async def test_db_failure_denies_gracefully(self, app):
        """A failing roles query denies access (403) instead of 500."""
        broken = MagicMock(side_effect=RuntimeError("db down"))
        async with app.test_request_context("/api/v1/entities/5"):
            with (
                patch(GCU, return_value=_user()),
                patch.object(app, "db", broken),
            ):
                _, code = await dec.resource_role_required("viewer")(_ok)(id=5)
        assert code == 403


class TestRoleRequired:
    """role_required / admin_required portal role gating."""

    @pytest.mark.asyncio
    async def test_unauthenticated(self, app):
        """No user -> 401."""
        async with app.test_request_context("/x"):
            with patch(GCU, return_value=None):
                _, code = _status(await dec.role_required("admin")(_ok)())
        assert code == 401

    @pytest.mark.asyncio
    @pytest.mark.parametrize("fn,expected", [(_ok, "async-ok"), (_sync_ok, "sync-ok")])
    async def test_superuser_bypass(self, app, fn, expected):
        """Superuser bypasses role checks."""
        async with app.test_request_context("/x"):
            with patch(GCU, return_value=_user(is_superuser=True)):
                out = await dec.role_required("admin")(fn)()
        assert out == expected

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "role,allowed_roles,ok",
        [
            ("admin", "admin", True),
            ("editor", ["admin", "editor"], True),
            ("observer", "admin", False),
            ("observer", ["admin", "editor"], False),
        ],
    )
    @pytest.mark.parametrize("fn", [_ok, _sync_ok])
    async def test_portal_role_matrix(self, app, role, allowed_roles, ok, fn):
        """Only listed portal roles pass; others get 403 with their role echoed."""
        async with app.test_request_context("/x"):
            with patch(GCU, return_value=_user(portal_role=role)):
                out = await dec.role_required(allowed_roles)(fn)()
        if ok:
            assert out in ("async-ok", "sync-ok")
        else:
            resp, code = out
            assert code == 403
            assert (await resp.get_json())["your_role"] == role

    @pytest.mark.asyncio
    async def test_admin_required_alias(self, app):
        """admin_required denies an observer and admits an admin portal role."""
        async with app.test_request_context("/x"):
            with patch(GCU, return_value=_user(portal_role="observer")):
                _, code = await dec.admin_required(_ok)()
            assert code == 403
            with patch(GCU, return_value=_user(portal_role="admin")):
                assert await dec.admin_required(_ok)() == "async-ok"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "claims_type,scopes,ok",
        [
            ("service_worker", ["runs:read", "runs:write"], True),
            ("service_worker", ["runs:read"], False),
            ("access", ["runs:read", "runs:write"], False),
        ],
    )
    @pytest.mark.parametrize("fn", [_ok, _sync_ok])
    async def test_service_worker_scope_pass(self, app, claims_type, scopes, ok, fn):
        """service_worker bypass needs matching claim type AND all listed scopes."""
        sw = _user(
            token_type="service_worker",
            portal_role="service_worker",
            username="svc-runworker",
            tenant_id=None,
        )
        deco = dec.role_required(
            "admin", allow_service_worker_scopes=("runs:read", "runs:write")
        )
        async with app.test_request_context("/x"):
            g.claims = {"type": claims_type, "scope": scopes, "tenant": "system"}
            g.current_user = sw
            with patch(GCU, return_value=sw):
                out = await deco(fn)()
        if ok:
            assert out in ("async-ok", "sync-ok")
        else:
            assert out[1] == 403
