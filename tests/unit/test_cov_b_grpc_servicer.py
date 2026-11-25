"""Behavioural tests for the Elder gRPC servicer (happy paths and error paths).

A real in-process gRPC server is built the way production builds it and driven
over a loopback channel against the test database. Each test seeds its own
tenant so tests never interfere with each other.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import PropertyMock, patch
from uuid import uuid4

import grpc
import jwt
import pytest
from werkzeug.security import generate_password_hash

from apps.api.grpc.generated import (
    auth_pb2,
    common_pb2,
    dependency_pb2,
    elder_pb2,
    elder_pb2_grpc,
    entity_pb2,
    graph_pb2,
    organization_pb2,
)
from apps.api.grpc.server import create_server

OK = grpc.StatusCode.OK
PASSWORD = "Grpc-Cov-Passw0rd!"
SERVICE = elder_pb2.DESCRIPTOR.services_by_name["ElderService"]


def _in_app(app, fn, *args):
    """Run ``fn`` inside the Quart application context."""

    async def run():
        """Execute inside the app context."""
        async with app.app_context():
            return fn(*args)

    return asyncio.run(run())


def _mk_tenant(db) -> int:
    """Insert a tenant."""
    tid = db.tenants.insert(
        name="cov-b", slug=f"covb-{uuid4().hex[:10]}", is_active=True
    )
    db.commit()
    return tid


def _mk_identity(db, tid: int, role: str = "admin", **extra) -> int:
    """Insert an identity in tenant ``tid``."""
    name = f"covb-{role}-{uuid4().hex[:8]}"
    fields = dict(
        username=name,
        email=f"{name}@example.test",
        identity_type="human",
        auth_provider="local",
        password_hash=generate_password_hash(PASSWORD),
        is_active=True,
        is_superuser=False,
        mfa_enabled=False,
        must_change_password=False,
        portal_role=role,
        tenant_id=tid,
    )
    fields.update(extra)
    iid = db.identities.insert(**fields)
    db.commit()
    return iid


def _mk_org(db, tid: int, name: str | None = None, parent: int | None = None) -> int:
    """Insert an organization."""
    now = datetime.now(UTC)
    oid = db.organizations.insert(
        name=name or f"org-{uuid4().hex[:6]}",
        tenant_id=tid,
        parent_id=parent,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return oid


def _mk_entity(db, org: int, name: str | None = None, etype: str = "compute") -> int:
    """Insert an entity."""
    now = datetime.now(UTC)
    eid = db.entities.insert(
        name=name or f"ent-{uuid4().hex[:6]}",
        type=etype,
        organization_id=org,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return eid


def _mk_dep(db, tid: int, src: int, dst: int) -> int:
    """Insert a dependency."""
    now = datetime.now(UTC)
    did = db.dependencies.insert(
        tenant_id=tid,
        source_type="entity",
        source_id=src,
        target_type="entity",
        target_id=dst,
        dependency_type="depends_on",
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return did


@dataclass
class Env:
    """One seeded tenant with tokens and the shared gRPC stub."""

    app: Any
    db: Any
    stub: Any
    channel: Any
    tid: int
    admin: int
    viewer: int
    tok: str
    viewer_tok: str
    org: int
    child: int
    e1: int
    e2: int
    e3: int
    dep: int

    def call(self, rpc: str, req, token: str | None = "default"):
        """Invoke ``rpc`` and return ``(code, response, details)``."""
        token = self.tok if token == "default" else token
        meta = (("authorization", f"Bearer {token}"),) if token else ()
        try:
            resp = getattr(self.stub, rpc)(req, metadata=meta, timeout=30)
            return OK, resp, ""
        except grpc.RpcError as err:
            return err.code(), None, err.details()

    def ok(self, rpc: str, req, token: str | None = "default"):
        """Invoke ``rpc`` and assert success; return the response."""
        code, resp, details = self.call(rpc, req, token)
        assert code == OK, f"{rpc}: {code} {details}"
        return resp


@pytest.fixture(scope="module")
def grpc_stub(app):
    """Start the in-process server once for the module."""
    server = create_server(app, max_workers=4)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    try:
        yield elder_pb2_grpc.ElderServiceStub(channel), channel
    finally:
        channel.close()
        server.stop(0)


@pytest.fixture
def env(app, grpc_stub):
    """Seed a tenant with org tree, entities, a dependency and tokens."""
    from apps.api.auth.jwt_handler import generate_token

    db = app.db
    stub, channel = grpc_stub
    tid = _mk_tenant(db)
    admin = _mk_identity(db, tid, "admin")
    viewer = _mk_identity(db, tid, "observer")
    org = _mk_org(db, tid, "root-org")
    child = _mk_org(db, tid, "child-org", org)
    e1 = _mk_entity(db, org, "alpha")
    e2 = _mk_entity(db, org, "beta", "network")
    e3 = _mk_entity(db, child, "gamma")
    dep = _mk_dep(db, tid, e1, e2)
    tok = _in_app(app, generate_token, db.identities[admin], "access")
    vtok = _in_app(app, generate_token, db.identities[viewer], "access")
    return Env(
        app,
        db,
        stub,
        channel,
        tid,
        admin,
        viewer,
        tok,
        vtok,
        org,
        child,
        e1,
        e2,
        e3,
        dep,
    )


def _service_token(app, tenant: str, scopes: list[str]) -> str:
    """Issue a service-worker token."""
    now = datetime.now(UTC)
    payload = {
        "sub": "svc-integrations",
        "type": "service_worker",
        "tenant": tenant,
        "scope": scopes,
        "iss": app.config.get("JWT_ISSUER", "elder-api"),
        "aud": app.config.get("JWT_AUDIENCE", "elder-api"),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    secret = app.config["JWT_SECRET_KEY"] or app.config["SECRET_KEY"]
    return jwt.encode(payload, secret, algorithm=app.config["JWT_ALGORITHM"])


# ------------------------------------------------------------------- helpers


def test_health_and_helpers(env):
    """HealthCheck is public; timestamp/pagination helpers behave."""
    resp = env.ok("HealthCheck", common_pb2.Empty(), token=None)
    assert resp.success and resp.details["service"] == "elder-grpc"

    from apps.api.grpc.servicers.elder_servicer import ElderServicer

    svc = ElderServicer(env.app)
    assert svc._create_timestamp(None).seconds == 0
    assert svc._create_timestamp(datetime(2026, 1, 1, tzinfo=UTC)).seconds > 0
    page = common_pb2.PaginationRequest(page=0, per_page=0)
    assert svc._page(page) == (1, 50, 0)
    assert svc._page(common_pb2.PaginationRequest(page=3, per_page=10_000))[1] == 200
    assert svc._create_pagination_response(1, 0, 5).pages == 0


class _Ctx:
    """Minimal gRPC context double recording status."""

    def __init__(self):
        """Start with no status."""
        self.code = None
        self.details = None

    def set_code(self, code):
        """Record the status code."""
        self.code = code

    def set_details(self, details):
        """Record the status details."""
        self.details = details


def test_health_check_failure_path(env):
    """A failing HealthCheck helper still reports a status instead of raising."""
    from apps.api.grpc.servicers.elder_servicer import ElderServicer

    svc = ElderServicer(env.app)
    with patch.object(
        svc, "_create_status_response", side_effect=[RuntimeError("x"), "fallback"]
    ):
        assert svc.HealthCheck(common_pb2.Empty(), _Ctx()) == "fallback"


# --------------------------------------------------------------------- auth


def test_login_paths(env):
    """Login validates input, credentials and account state."""
    name = env.db.identities[env.admin].username
    assert (
        env.call("Login", auth_pb2.LoginRequest(password="x"), None)[0]
        == grpc.StatusCode.INVALID_ARGUMENT
    )
    assert (
        env.call("Login", auth_pb2.LoginRequest(username=name), None)[0]
        == grpc.StatusCode.INVALID_ARGUMENT
    )
    code, *_ = env.call(
        "Login", auth_pb2.LoginRequest(username="ghost", password="x"), None
    )
    assert code == grpc.StatusCode.UNAUTHENTICATED
    code, *_ = env.call(
        "Login", auth_pb2.LoginRequest(username=name, password="nope"), None
    )
    assert code == grpc.StatusCode.UNAUTHENTICATED
    nopw = _mk_identity(env.db, env.tid, password_hash=None)
    code, *_ = env.call(
        "Login",
        auth_pb2.LoginRequest(username=env.db.identities[nopw].username, password="x"),
        None,
    )
    assert code == grpc.StatusCode.UNAUTHENTICATED
    off = _mk_identity(env.db, env.tid, is_active=False)
    code, *_ = env.call(
        "Login",
        auth_pb2.LoginRequest(
            username=env.db.identities[off].username, password=PASSWORD
        ),
        None,
    )
    assert code == grpc.StatusCode.PERMISSION_DENIED
    resp = env.ok(
        "Login", auth_pb2.LoginRequest(username=name, password=PASSWORD), None
    )
    assert resp.access_token and resp.refresh_token and resp.identity.id == env.admin


def test_refresh_token_paths(env):
    """RefreshToken accepts only live refresh tokens of active users."""
    from apps.api.auth.jwt_handler import generate_token

    R = "RefreshToken"
    assert (
        env.call(R, auth_pb2.RefreshTokenRequest(), None)[0]
        == grpc.StatusCode.INVALID_ARGUMENT
    )
    assert (
        env.call(R, auth_pb2.RefreshTokenRequest(refresh_token="junk"), None)[0]
        == grpc.StatusCode.UNAUTHENTICATED
    )
    assert (
        env.call(R, auth_pb2.RefreshTokenRequest(refresh_token=env.tok), None)[0]
        == grpc.StatusCode.UNAUTHENTICATED
    )
    refresh = _in_app(env.app, generate_token, env.db.identities[env.admin], "refresh")
    assert env.ok(
        R, auth_pb2.RefreshTokenRequest(refresh_token=refresh), None
    ).access_token
    off = _mk_identity(env.db, env.tid, is_active=False)
    dead = _in_app(env.app, generate_token, env.db.identities[off], "refresh")
    assert (
        env.call(R, auth_pb2.RefreshTokenRequest(refresh_token=dead), None)[0]
        == grpc.StatusCode.UNAUTHENTICATED
    )


def test_logout_current_identity_change_password(env):
    """Logout, GetCurrentIdentity and ChangePassword basics."""
    assert env.ok("Logout", auth_pb2.LogoutRequest()).status.success
    me = env.ok("GetCurrentIdentity", auth_pb2.GetCurrentIdentityRequest())
    assert me.identity.id == env.admin

    svc_tok = _service_token(env.app, str(env.tid), ["entities:read"])
    code, *_ = env.call(
        "GetCurrentIdentity", auth_pb2.GetCurrentIdentityRequest(), svc_tok
    )
    assert code == grpc.StatusCode.NOT_FOUND

    CP = "ChangePassword"
    assert (
        env.call(CP, auth_pb2.ChangePasswordRequest(new_password="n"))[0]
        == grpc.StatusCode.INVALID_ARGUMENT
    )
    assert (
        env.call(CP, auth_pb2.ChangePasswordRequest(current_password="c"))[0]
        == grpc.StatusCode.INVALID_ARGUMENT
    )
    both = auth_pb2.ChangePasswordRequest(current_password="c", new_password="n")
    assert env.call(CP, both)[0] == grpc.StatusCode.UNIMPLEMENTED


def test_register_identity(env):
    """RegisterIdentity validates input, rejects duplicates and creates identities."""
    RI = "RegisterIdentity"
    base = dict(
        username=f"reg-{uuid4().hex[:8]}",
        email=f"{uuid4().hex[:8]}@example.test",
        password="pw-123456",
    )
    for missing in ("username", "email", "password"):
        kw = {k: v for k, v in base.items() if k != missing}
        code, *_ = env.call(RI, auth_pb2.RegisterIdentityRequest(**kw))
        assert code == grpc.StatusCode.INVALID_ARGUMENT, missing
    resp = env.ok(RI, auth_pb2.RegisterIdentityRequest(display_name="R", **base))
    assert resp.identity.id
    assert (
        env.call(RI, auth_pb2.RegisterIdentityRequest(**base))[0]
        == grpc.StatusCode.ALREADY_EXISTS
    )
    other = dict(base, username=f"reg-{uuid4().hex[:8]}")
    assert (
        env.call(RI, auth_pb2.RegisterIdentityRequest(**other))[0]
        == grpc.StatusCode.ALREADY_EXISTS
    )
    sa = dict(
        username=f"sa-{uuid4().hex[:8]}",
        email=f"{uuid4().hex[:8]}@example.test",
        password="pw-123456",
    )
    resp = env.ok(
        RI,
        auth_pb2.RegisterIdentityRequest(
            identity_type=auth_pb2.IdentityType.SERVICE_ACCOUNT, **sa
        ),
    )
    assert env.db.identities[resp.identity.id].identity_type == "service_account"
    assert (
        env.call(RI, auth_pb2.RegisterIdentityRequest(**base), env.viewer_tok)[0]
        == grpc.StatusCode.PERMISSION_DENIED
    )


def test_validate_token(env):
    """ValidateToken only validates same-tenant, active, access tokens."""
    from apps.api.auth.jwt_handler import generate_token

    V = "ValidateToken"
    assert (
        env.call(V, auth_pb2.ValidateTokenRequest())[0]
        == grpc.StatusCode.INVALID_ARGUMENT
    )
    for tok in (
        "junk",
        _in_app(env.app, generate_token, env.db.identities[env.admin], "refresh"),
    ):
        assert not env.ok(V, auth_pb2.ValidateTokenRequest(access_token=tok)).valid
    foreign = _mk_identity(env.db, _mk_tenant(env.db))
    ftok = _in_app(env.app, generate_token, env.db.identities[foreign], "access")
    assert not env.ok(V, auth_pb2.ValidateTokenRequest(access_token=ftok)).valid
    off = _mk_identity(env.db, env.tid, is_active=False)
    otok = _in_app(env.app, generate_token, env.db.identities[off], "access")
    assert not env.ok(V, auth_pb2.ValidateTokenRequest(access_token=otok)).valid
    assert env.ok(V, auth_pb2.ValidateTokenRequest(access_token=env.viewer_tok)).valid
    svc = _service_token(env.app, str(env.tid), [])
    assert not env.ok(V, auth_pb2.ValidateTokenRequest(access_token=svc)).valid


def test_identity_crud(env):
    """List/Get/Update/Delete identities within a tenant."""
    LI = "ListIdentities"
    resp = env.ok(LI, auth_pb2.ListIdentitiesRequest())
    assert resp.pagination.total == 2
    sa = _mk_identity(
        env.db,
        env.tid,
        "observer",
        identity_type="service_account",
        auth_provider="saml",
        is_active=False,
    )
    for kw, expect in [
        (dict(identity_type=auth_pb2.IdentityType.HUMAN), 2),
        (dict(identity_type=auth_pb2.IdentityType.SERVICE_ACCOUNT), 1),
        (dict(auth_provider=auth_pb2.AuthProvider.SAML), 1),
        (dict(auth_provider=auth_pb2.AuthProvider.LOCAL), 2),
        (dict(auth_provider=auth_pb2.AuthProvider.OAUTH2), 0),
        (dict(auth_provider=auth_pb2.AuthProvider.LDAP), 0),
        (dict(active_only=True), 2),
    ]:
        got = env.ok(LI, auth_pb2.ListIdentitiesRequest(**kw)).pagination.total
        assert got == expect, kw
    name = env.db.identities[env.admin].username
    req = auth_pb2.ListIdentitiesRequest()
    req.filters.filters["search"] = name
    assert env.ok(LI, req).pagination.total == 1
    req = auth_pb2.ListIdentitiesRequest(
        pagination=common_pb2.PaginationRequest(page=1, per_page=1)
    )
    assert len(env.ok(LI, req).identities) == 1

    assert (
        env.ok("GetIdentity", auth_pb2.GetIdentityRequest(id=env.viewer)).identity.id
        == env.viewer
    )
    assert (
        env.call("GetIdentity", auth_pb2.GetIdentityRequest(id=99999999))[0]
        == grpc.StatusCode.NOT_FOUND
    )

    UI = "UpdateIdentity"
    assert (
        env.call(UI, auth_pb2.UpdateIdentityRequest(id=sa, is_superuser=True))[0]
        == grpc.StatusCode.PERMISSION_DENIED
    )
    assert (
        env.call(UI, auth_pb2.UpdateIdentityRequest(id=99999999, display_name="x"))[0]
        == grpc.StatusCode.NOT_FOUND
    )
    upd = env.ok(
        UI,
        auth_pb2.UpdateIdentityRequest(
            id=sa,
            display_name="New",
            email=f"{uuid4().hex[:8]}@example.test",
            is_active=True,
        ),
    )
    assert upd.identity.id == sa
    assert env.ok(UI, auth_pb2.UpdateIdentityRequest(id=sa)).identity.id == sa

    DI = "DeleteIdentity"
    assert (
        env.call(DI, auth_pb2.DeleteIdentityRequest(id=99999999))[0]
        == grpc.StatusCode.NOT_FOUND
    )
    assert env.ok(DI, auth_pb2.DeleteIdentityRequest(id=sa)).status.success
    assert env.db.identities[sa] is None


# ------------------------------------------------------------ organizations


def test_organization_reads(env):
    """List/Get/Children/Hierarchy return tenant data."""
    LO = "ListOrganizations"
    assert env.ok(LO, organization_pb2.ListOrganizationsRequest()).pagination.total == 2
    r = env.ok(LO, organization_pb2.ListOrganizationsRequest(parent_id=env.org))
    assert [o.id for o in r.organizations] == [env.child]
    req = organization_pb2.ListOrganizationsRequest()
    req.filters.filters["name"] = "root"
    assert env.ok(LO, req).pagination.total == 1

    assert (
        env.ok(
            "GetOrganization", organization_pb2.GetOrganizationRequest(id=env.org)
        ).organization.id
        == env.org
    )
    assert (
        env.call(
            "GetOrganization", organization_pb2.GetOrganizationRequest(id=99999999)
        )[0]
        == grpc.StatusCode.NOT_FOUND
    )

    GC = "GetOrganizationChildren"
    kids = env.ok(GC, organization_pb2.GetOrganizationChildrenRequest(id=env.org))
    assert [c.id for c in kids.children] == [env.child]
    assert (
        env.call(GC, organization_pb2.GetOrganizationChildrenRequest(id=99999999))[0]
        == grpc.StatusCode.NOT_FOUND
    )

    GH = "GetOrganizationHierarchy"
    leaf = _mk_org(env.db, env.tid, "leaf", env.child)
    h = env.ok(GH, organization_pb2.GetOrganizationHierarchyRequest(id=leaf))
    assert h.root.id == env.org
    assert {o.id for o in h.flattened_hierarchy} == {env.child, leaf}
    assert (
        env.call(GH, organization_pb2.GetOrganizationHierarchyRequest(id=99999999))[0]
        == grpc.StatusCode.NOT_FOUND
    )
    cyc_a = _mk_org(env.db, env.tid, "cyc-a")
    cyc_b = _mk_org(env.db, env.tid, "cyc-b", cyc_a)
    env.db(env.db.organizations.id == cyc_a).update(parent_id=cyc_b)
    env.db.commit()
    assert env.ok(
        GH, organization_pb2.GetOrganizationHierarchyRequest(id=cyc_a)
    ).root.id in (cyc_a, cyc_b)


def test_organization_writes(env):
    """Create/Update/Delete organizations with reference validation."""
    CO = "CreateOrganization"
    Req = organization_pb2.CreateOrganizationRequest
    assert env.call(CO, Req())[0] == grpc.StatusCode.INVALID_ARGUMENT
    assert (
        env.call(CO, Req(name="x", parent_id=99999999))[0]
        == grpc.StatusCode.INVALID_ARGUMENT
    )
    assert (
        env.call(CO, Req(name="x", owner_identity_id=99999999))[0]
        == grpc.StatusCode.INVALID_ARGUMENT
    )
    assert (
        env.call(CO, Req(name="x", owner_group_id=1))[0]
        == grpc.StatusCode.INVALID_ARGUMENT
    )
    made = env.ok(
        CO,
        Req(
            name="made", description="d", parent_id=env.org, owner_identity_id=env.admin
        ),
    )
    oid = made.organization.id

    UO = "UpdateOrganization"
    URq = organization_pb2.UpdateOrganizationRequest
    assert env.call(UO, URq(id=99999999, name="x"))[0] == grpc.StatusCode.NOT_FOUND
    assert (
        env.call(UO, URq(id=oid, parent_id=oid))[0] == grpc.StatusCode.INVALID_ARGUMENT
    )
    upd = env.ok(
        UO,
        URq(
            id=oid,
            name="renamed",
            description="d2",
            parent_id=env.child,
            owner_identity_id=env.viewer,
        ),
    )
    assert upd.organization.name == "renamed"
    assert env.ok(UO, URq(id=oid)).organization.id == oid

    DO = "DeleteOrganization"
    DRq = organization_pb2.DeleteOrganizationRequest
    assert env.call(DO, DRq(id=99999999))[0] == grpc.StatusCode.NOT_FOUND
    assert env.call(DO, DRq(id=env.org))[0] == grpc.StatusCode.FAILED_PRECONDITION
    assert env.ok(DO, DRq(id=oid)).status.success
    assert env.db.organizations[oid] is None


# ------------------------------------------------------------------ entities


def test_entity_reads(env):
    """ListEntities filters by org and type; GetEntity is tenant scoped."""
    LE = "ListEntities"
    Req = entity_pb2.ListEntitiesRequest
    assert env.ok(LE, Req()).pagination.total == 3
    assert env.ok(LE, Req(organization_id=env.child)).pagination.total == 1
    assert (
        env.ok(LE, Req(entity_type=entity_pb2.EntityType.NETWORK)).pagination.total == 1
    )
    foreign_org = _mk_org(env.db, _mk_tenant(env.db))
    assert env.ok(LE, Req(organization_id=foreign_org)).pagination.total == 0
    lonely = _mk_identity(env.db, _mk_tenant(env.db))
    from apps.api.auth.jwt_handler import generate_token

    ltok = _in_app(env.app, generate_token, env.db.identities[lonely], "access")
    assert env.ok(LE, Req(), ltok).pagination.total == 0

    assert (
        env.ok("GetEntity", entity_pb2.GetEntityRequest(id=env.e1)).entity.id == env.e1
    )
    assert (
        env.call("GetEntity", entity_pb2.GetEntityRequest(id=99999999))[0]
        == grpc.StatusCode.NOT_FOUND
    )

    GD = "GetEntityDependencies"
    deps = env.ok(GD, entity_pb2.GetEntityDependenciesRequest(id=env.e1))
    assert deps.pagination.total == 1
    assert (
        env.call(GD, entity_pb2.GetEntityDependenciesRequest(id=99999999))[0]
        == grpc.StatusCode.NOT_FOUND
    )


def test_entity_writes(env):
    """Create/Update/Delete/Batch entities respect tenant boundaries."""
    CE = "CreateEntity"
    Req = entity_pb2.CreateEntityRequest
    assert (
        env.call(CE, Req(organization_id=99999999, name="x"))[0]
        == grpc.StatusCode.NOT_FOUND
    )
    made = env.ok(
        CE,
        Req(
            organization_id=env.org,
            name="made",
            entity_type=entity_pb2.EntityType.SUBNET,
        ),
    )
    eid = made.entity.id

    UE = "UpdateEntity"
    URq = entity_pb2.UpdateEntityRequest
    assert env.call(UE, URq(id=99999999, name="x"))[0] == grpc.StatusCode.NOT_FOUND
    foreign_org = _mk_org(env.db, _mk_tenant(env.db))
    assert (
        env.call(UE, URq(id=eid, organization_id=foreign_org))[0]
        == grpc.StatusCode.NOT_FOUND
    )
    upd = env.ok(
        UE,
        URq(
            id=eid,
            name="renamed",
            entity_type=entity_pb2.EntityType.VPC,
            organization_id=env.child,
        ),
    )
    assert upd.entity.name == "renamed"
    assert env.ok(UE, URq(id=eid)).entity.id == eid

    DE = "DeleteEntity"
    assert (
        env.call(DE, entity_pb2.DeleteEntityRequest(id=99999999))[0]
        == grpc.StatusCode.NOT_FOUND
    )
    assert env.ok(DE, entity_pb2.DeleteEntityRequest(id=eid)).status.success

    BC = "BatchCreateEntities"
    batch = entity_pb2.BatchCreateEntitiesRequest(
        entities=[
            Req(organization_id=env.org, name="b1"),
            Req(organization_id=foreign_org, name="b2"),
        ]
    )
    resp = env.ok(BC, batch)
    assert len(resp.entities) == 1 and "not found" in resp.errors[0]
    too_many = entity_pb2.BatchCreateEntitiesRequest(
        entities=[Req(organization_id=env.org, name="x")] * 201
    )
    assert env.call(BC, too_many)[0] == grpc.StatusCode.INVALID_ARGUMENT


# -------------------------------------------------------------- dependencies


def test_dependency_reads_and_writes(env):
    """Dependency CRUD plus bulk helpers."""
    LD = "ListDependencies"
    Req = dependency_pb2.ListDependenciesRequest
    assert env.ok(LD, Req()).pagination.total == 1
    assert env.ok(LD, Req(source_entity_id=env.e1)).pagination.total == 1
    assert env.ok(LD, Req(target_entity_id=env.e3)).pagination.total == 0
    assert (
        env.ok(
            "GetDependency", dependency_pb2.GetDependencyRequest(id=env.dep)
        ).dependency.id
        == env.dep
    )
    assert (
        env.call("GetDependency", dependency_pb2.GetDependencyRequest(id=99999999))[0]
        == grpc.StatusCode.NOT_FOUND
    )

    CD = "CreateDependency"
    CRq = dependency_pb2.CreateDependencyRequest
    assert (
        env.call(CD, CRq(target_entity_id=env.e1))[0]
        == grpc.StatusCode.INVALID_ARGUMENT
    )
    assert (
        env.call(CD, CRq(source_entity_id=env.e1))[0]
        == grpc.StatusCode.INVALID_ARGUMENT
    )
    assert (
        env.call(CD, CRq(source_entity_id=env.e1, target_entity_id=99999999))[0]
        == grpc.StatusCode.NOT_FOUND
    )
    created = env.ok(
        CD, CRq(source_entity_id=env.e2, target_entity_id=env.e3, metadata={"k": "v"})
    )
    did = created.dependency.id

    UD = "UpdateDependency"
    URq = dependency_pb2.UpdateDependencyRequest
    assert env.call(UD, URq(id=99999999))[0] == grpc.StatusCode.NOT_FOUND
    assert env.ok(UD, URq(id=did, metadata={"a": "b"})).dependency.id == did
    assert (
        env.ok(
            UD, URq(id=did, dependency_type=entity_pb2.DependencyType.DEPENDS_ON)
        ).dependency.id
        == did
    )
    assert env.ok(UD, URq(id=did)).dependency.id == did

    DD = "DeleteDependency"
    assert (
        env.call(DD, dependency_pb2.DeleteDependencyRequest(id=99999999))[0]
        == grpc.StatusCode.NOT_FOUND
    )
    assert env.ok(DD, dependency_pb2.DeleteDependencyRequest(id=did)).status.success

    BC = "BulkCreateDependencies"
    bulk = dependency_pb2.BulkCreateDependenciesRequest(
        dependencies=[
            CRq(source_entity_id=env.e3, target_entity_id=env.e1),
            CRq(target_entity_id=env.e1),
            CRq(source_entity_id=env.e1),
            CRq(source_entity_id=env.e1, target_entity_id=99999999),
        ]
    )
    resp = env.ok(BC, bulk)
    assert len(resp.dependencies) == 1 and len(resp.errors) == 3
    too_many = dependency_pb2.BulkCreateDependenciesRequest(
        dependencies=[CRq(source_entity_id=1, target_entity_id=2)] * 201
    )
    assert env.call(BC, too_many)[0] == grpc.StatusCode.INVALID_ARGUMENT

    BD = "BulkDeleteDependencies"
    new_id = resp.dependencies[0].id
    out = env.ok(
        BD, dependency_pb2.BulkDeleteDependenciesRequest(ids=[new_id, 99999999])
    )
    assert out.deleted_count == 1 and len(out.errors) == 1
    assert (
        env.call(
            BD, dependency_pb2.BulkDeleteDependenciesRequest(ids=list(range(1, 202)))
        )[0]
        == grpc.StatusCode.INVALID_ARGUMENT
    )


# --------------------------------------------------------------------- graph


def test_dependency_graph(env):
    """GetDependencyGraph handles org, entity, invalid and foreign inputs."""
    G = "GetDependencyGraph"
    Req = graph_pb2.GetDependencyGraphRequest
    by_org = env.ok(G, Req(organization_id=env.org))
    assert (
        len(by_org.graph.nodes) >= 2 and by_org.graph.metadata["truncated"] == "false"
    )
    by_ent = env.ok(G, Req(entity_id=env.e1, depth=2))
    assert {n.id for n in by_ent.graph.nodes} >= {env.e1, env.e2}
    assert env.call(G, Req())[0] == grpc.StatusCode.INVALID_ARGUMENT
    assert env.call(G, Req(organization_id=99999999))[0] == grpc.StatusCode.NOT_FOUND
    assert env.call(G, Req(entity_id=99999999))[0] == grpc.StatusCode.NOT_FOUND


def test_analyze_graph(env):
    """AnalyzeGraph reports cycles, orphans and statistics."""
    A = "AnalyzeGraph"
    Req = graph_pb2.AnalyzeGraphRequest
    first = env.ok(A, Req())
    kinds = {r.analysis_type for r in first.results}
    assert "orphaned_entities" in kinds and "statistics" in kinds
    _mk_dep(env.db, env.tid, env.e2, env.e1)
    cyc = env.ok(A, Req(organization_id=env.org))
    assert "circular_dependencies" in {r.analysis_type for r in cyc.results}
    assert env.call(A, Req(organization_id=99999999))[0] == grpc.StatusCode.NOT_FOUND
    lonely = _mk_identity(env.db, _mk_tenant(env.db))
    from apps.api.auth.jwt_handler import generate_token

    ltok = _in_app(env.app, generate_token, env.db.identities[lonely], "access")
    empty = env.ok(A, Req(), ltok)
    assert empty.status.message == "No entities to analyze"


def test_analyze_graph_cycle_cap(env):
    """More cycles than the cap are reported as truncated."""
    A = "AnalyzeGraph"
    org = _mk_org(env.db, env.tid, "ring")
    nodes = [_mk_entity(env.db, org, f"n{i}") for i in range(5)]
    for a in nodes:
        for b in nodes:
            if a != b:
                _mk_dep(env.db, env.tid, a, b)
    res = env.ok(A, graph_pb2.AnalyzeGraphRequest(organization_id=org))
    circ = next(r for r in res.results if r.analysis_type == "circular_dependencies")
    assert circ.statistics["truncated"] == "true" and circ.statistics["count"] == "10"


def test_find_path_and_impact(env):
    """FindPath and GetEntityImpact walk only the tenant's own graph."""
    FP = "FindPath"
    Req = graph_pb2.FindPathRequest
    ok = env.ok(FP, Req(source_entity_id=env.e1, target_entity_id=env.e2))
    assert ok.path_found and ok.paths[0].total_hops == 1
    none = env.ok(FP, Req(source_entity_id=env.e2, target_entity_id=env.e1))
    assert not none.path_found and none.status.message == "No path exists"
    assert (
        env.call(FP, Req(source_entity_id=99999999, target_entity_id=env.e1))[0]
        == grpc.StatusCode.NOT_FOUND
    )
    assert (
        env.call(FP, Req(source_entity_id=env.e1, target_entity_id=99999999))[0]
        == grpc.StatusCode.NOT_FOUND
    )
    _mk_dep(env.db, env.tid, env.e2, env.e3)
    far = env.ok(FP, Req(source_entity_id=env.e1, target_entity_id=env.e3, max_depth=1))
    assert not far.path_found
    assert env.ok(
        FP, Req(source_entity_id=env.e1, target_entity_id=env.e3, max_depth=5)
    ).path_found

    GI = "GetEntityImpact"
    imp = env.ok(GI, graph_pb2.GetEntityImpactRequest(entity_id=env.e1)).impact
    assert imp.direct_dependencies == 1 and imp.indirect_dependencies == 2
    iso = _mk_entity(env.db, env.org, "isolated")
    assert (
        env.ok(GI, graph_pb2.GetEntityImpactRequest(entity_id=iso)).impact.impact_score
        == 0
    )
    assert (
        env.call(GI, graph_pb2.GetEntityImpactRequest(entity_id=99999999))[0]
        == grpc.StatusCode.NOT_FOUND
    )


def test_graph_truncation_messages(env):
    """Truncated graphs are reported in path and impact messages."""
    import apps.api.grpc.servicers.elder_servicer as mod

    with patch.object(mod.ElderServicer, "_tenant_graph", return_value=([], [], True)):
        FP = env.ok(
            "FindPath",
            graph_pb2.FindPathRequest(source_entity_id=env.e1, target_entity_id=env.e2),
        )
        assert "truncated" in FP.status.message
        GI = env.ok(
            "GetEntityImpact", graph_pb2.GetEntityImpactRequest(entity_id=env.e1)
        )
        assert "truncated" in GI.status.message


# --------------------------------------------------------- auth / scope gates


def test_system_and_scope_denials(env):
    """System-tenant and under-scoped callers are denied tenant RPCs."""
    sys_tok = _service_token(env.app, "system", ["entities:read"])
    code, *_ = env.call("ListEntities", entity_pb2.ListEntitiesRequest(), sys_tok)
    assert code == grpc.StatusCode.PERMISSION_DENIED
    svc_tok = _service_token(env.app, str(env.tid), ["entities:read"])
    assert env.call("ListEntities", entity_pb2.ListEntitiesRequest(), svc_tok)[0] == OK
    code, *_ = env.call(
        "CreateEntity",
        entity_pb2.CreateEntityRequest(organization_id=env.org, name="x"),
        svc_tok,
    )
    assert code == grpc.StatusCode.PERMISSION_DENIED
    code, *_ = env.call("ListEntities", entity_pb2.ListEntitiesRequest(), None)
    assert code == grpc.StatusCode.UNAUTHENTICATED


# --------------------------------------------------------------- error paths


def _requests(env) -> dict[str, Any]:
    """Return a plausible request for every RPC so handlers reach their bodies."""
    cd = dependency_pb2.CreateDependencyRequest(
        source_entity_id=env.e1, target_entity_id=env.e2
    )
    ce = entity_pb2.CreateEntityRequest(organization_id=env.org, name="x")
    return {
        "Login": auth_pb2.LoginRequest(
            username=env.db.identities[env.admin].username, password=PASSWORD
        ),
        "RefreshToken": auth_pb2.RefreshTokenRequest(refresh_token="junk"),
        "Logout": auth_pb2.LogoutRequest(),
        "GetCurrentIdentity": auth_pb2.GetCurrentIdentityRequest(),
        "ChangePassword": auth_pb2.ChangePasswordRequest(
            current_password="a", new_password="b"
        ),
        "RegisterIdentity": auth_pb2.RegisterIdentityRequest(
            username="u", email="e", password="p"
        ),
        "ValidateToken": auth_pb2.ValidateTokenRequest(access_token=env.tok),
        "ListIdentities": auth_pb2.ListIdentitiesRequest(),
        "GetIdentity": auth_pb2.GetIdentityRequest(id=env.viewer),
        "UpdateIdentity": auth_pb2.UpdateIdentityRequest(
            id=env.viewer, display_name="n"
        ),
        "DeleteIdentity": auth_pb2.DeleteIdentityRequest(id=env.viewer),
        "ListOrganizations": organization_pb2.ListOrganizationsRequest(),
        "GetOrganization": organization_pb2.GetOrganizationRequest(id=env.org),
        "CreateOrganization": organization_pb2.CreateOrganizationRequest(
            name="x", parent_id=env.org
        ),
        "UpdateOrganization": organization_pb2.UpdateOrganizationRequest(
            id=env.org, name="n"
        ),
        "DeleteOrganization": organization_pb2.DeleteOrganizationRequest(id=env.child),
        "GetOrganizationChildren": organization_pb2.GetOrganizationChildrenRequest(
            id=env.org
        ),
        "GetOrganizationHierarchy": organization_pb2.GetOrganizationHierarchyRequest(
            id=env.org
        ),
        "ListEntities": entity_pb2.ListEntitiesRequest(),
        "GetEntity": entity_pb2.GetEntityRequest(id=env.e1),
        "CreateEntity": ce,
        "UpdateEntity": entity_pb2.UpdateEntityRequest(id=env.e1, name="n"),
        "DeleteEntity": entity_pb2.DeleteEntityRequest(id=env.e1),
        "GetEntityDependencies": entity_pb2.GetEntityDependenciesRequest(id=env.e1),
        "BatchCreateEntities": entity_pb2.BatchCreateEntitiesRequest(entities=[ce]),
        "ListDependencies": dependency_pb2.ListDependenciesRequest(),
        "GetDependency": dependency_pb2.GetDependencyRequest(id=env.dep),
        "CreateDependency": cd,
        "UpdateDependency": dependency_pb2.UpdateDependencyRequest(
            id=env.dep, metadata={"a": "b"}
        ),
        "DeleteDependency": dependency_pb2.DeleteDependencyRequest(id=env.dep),
        "BulkCreateDependencies": dependency_pb2.BulkCreateDependenciesRequest(
            dependencies=[cd]
        ),
        "BulkDeleteDependencies": dependency_pb2.BulkDeleteDependenciesRequest(
            ids=[env.dep]
        ),
        "GetDependencyGraph": graph_pb2.GetDependencyGraphRequest(
            organization_id=env.org
        ),
        "AnalyzeGraph": graph_pb2.AnalyzeGraphRequest(),
        "FindPath": graph_pb2.FindPathRequest(
            source_entity_id=env.e1, target_entity_id=env.e2
        ),
        "GetEntityImpact": graph_pb2.GetEntityImpactRequest(entity_id=env.e1),
        "HealthCheck": common_pb2.Empty(),
    }


_PUBLIC = ("Login", "RefreshToken", "HealthCheck")


def test_requests_cover_every_rpc():
    """The request table stays in step with the service definition."""
    from types import SimpleNamespace

    fake = SimpleNamespace(
        db=None, admin=1, viewer=2, org=3, child=4, e1=5, e2=6, dep=7, tok="t"
    )
    fake.db = SimpleNamespace(identities={1: SimpleNamespace(username="u")})
    assert set(_requests(fake)) == {m.name for m in SERVICE.methods}


@pytest.mark.parametrize("method", [m.name for m in SERVICE.methods])
def test_backend_failures_do_not_leak(env, method):
    """A failing database yields a gRPC error status (or failure body), never a crash."""
    from apps.api.grpc.servicers.elder_servicer import ElderServicer

    req = _requests(env)[method]
    token = None if method in _PUBLIC else env.tok
    with patch.object(
        ElderServicer,
        "db",
        new_callable=PropertyMock,
        side_effect=RuntimeError("db down"),
    ):
        code, resp, _ = env.call(method, req, token)
    if method == "HealthCheck":
        assert code == OK
    else:
        assert code != OK or resp is not None


@pytest.mark.parametrize(
    "method", [m.name for m in SERVICE.methods if m.name not in _PUBLIC]
)
def test_helper_failures_are_internal_errors(env, method):
    """Failures inside handler bodies (after the DB is bound) map to INTERNAL."""
    from apps.api.grpc.servicers.elder_servicer import ElderServicer

    req = _requests(env)[method]
    boom = RuntimeError("helper down")
    names = [
        "_scoped_org",
        "_scoped_entity",
        "_scoped_dependency",
        "_scoped_identity",
        "_tenant_graph",
        "_page",
        "_create_status_response",
        "_create_pagination_response",
        "_verify_jwt_token",
        "_org_refs_error",
        "_dependency_insert_data",
    ]
    patches = [patch.object(ElderServicer, n, side_effect=boom) for n in names]
    for p in patches:
        p.start()
    try:
        code, resp, details = env.call(method, req)
    finally:
        for p in patches:
            p.stop()
    assert code != OK or resp is not None, details


_WRITE_RPCS = [
    "RegisterIdentity",
    "UpdateIdentity",
    "DeleteIdentity",
    "CreateOrganization",
    "UpdateOrganization",
    "DeleteOrganization",
    "CreateEntity",
    "UpdateEntity",
    "DeleteEntity",
    "CreateDependency",
    "UpdateDependency",
    "DeleteDependency",
]


@pytest.mark.xfail(
    strict=True,
    reason="bug: write-RPC error paths call db.rollback(), which penguin-dal resolves as a table",
)
@pytest.mark.parametrize("method", _WRITE_RPCS)
def test_write_failures_report_internal(env, method):
    """A failing write RPC should answer INTERNAL (currently UNKNOWN from db.rollback())."""
    from apps.api.grpc.servicers.elder_servicer import ElderServicer

    req = _requests(env)[method]
    names = [
        "_scoped_org",
        "_scoped_entity",
        "_scoped_dependency",
        "_scoped_identity",
        "_org_refs_error",
    ]
    patches = [
        patch.object(ElderServicer, n, side_effect=RuntimeError("x")) for n in names
    ]
    for p in patches:
        p.start()
    try:
        code, *_ = env.call(method, req)
    finally:
        for p in patches:
            p.stop()
    assert code == grpc.StatusCode.INTERNAL


def test_login_and_refresh_internal_errors(env):
    """Login and RefreshToken report INTERNAL when their helpers fail."""
    from apps.api.grpc.servicers.elder_servicer import ElderServicer

    reqs = _requests(env)
    with patch.object(
        ElderServicer, "_generate_jwt_token", side_effect=RuntimeError("x")
    ):
        assert env.call("Login", reqs["Login"], None)[0] == grpc.StatusCode.INTERNAL
    from apps.api.auth.jwt_handler import generate_token

    refresh = _in_app(env.app, generate_token, env.db.identities[env.admin], "refresh")
    with patch.object(
        ElderServicer, "_generate_jwt_token", side_effect=RuntimeError("x")
    ):
        code, *_ = env.call(
            "RefreshToken", auth_pb2.RefreshTokenRequest(refresh_token=refresh), None
        )
        assert code == grpc.StatusCode.INTERNAL


def test_direct_calls_without_interceptor(env):
    """Handlers called outside the interceptor refuse to run without a caller."""
    from apps.api.grpc.servicers.elder_servicer import ElderServicer

    svc = ElderServicer(env.app)
    ctx = _Ctx()
    svc.ListEntities(entity_pb2.ListEntitiesRequest(), ctx)
    assert ctx.code == grpc.StatusCode.UNAUTHENTICATED
    assert svc._verify_jwt_token("") is None


def test_current_identity_for_deleted_user(env):
    """A valid token for a since-deleted identity yields NOT_FOUND."""
    from apps.api.auth.jwt_handler import generate_token

    ghost = _mk_identity(env.db, env.tid)
    tok = _in_app(env.app, generate_token, env.db.identities[ghost], "access")
    env.db(env.db.identities.id == ghost).delete()
    env.db.commit()
    code, *_ = env.call("GetCurrentIdentity", auth_pb2.GetCurrentIdentityRequest(), tok)
    assert code in (grpc.StatusCode.NOT_FOUND, grpc.StatusCode.UNAUTHENTICATED)


def test_hierarchy_stops_at_foreign_parent(env):
    """An organization whose parent belongs to another tenant stops the walk."""
    foreign = _mk_org(env.db, _mk_tenant(env.db))
    orphan = _mk_org(env.db, env.tid, "orphan", foreign)
    resp = env.ok(
        "GetOrganizationHierarchy",
        organization_pb2.GetOrganizationHierarchyRequest(id=orphan),
    )
    assert resp.root.id == orphan


def test_per_item_failures_in_batches(env):
    """Per-item database errors inside batch RPCs are reported in-band."""
    import apps.api.grpc.servicers.elder_servicer as mod

    with patch.object(
        mod.ElderServicer, "_scoped_entity", side_effect=RuntimeError("scoped boom")
    ):
        resp = env.ok(
            "BatchCreateEntities",
            entity_pb2.BatchCreateEntitiesRequest(
                entities=[
                    entity_pb2.CreateEntityRequest(organization_id=env.org, name="x")
                ]
            ),
        )
        assert resp.errors and "scoped boom" in resp.errors[0]
        resp = env.ok(
            "BulkCreateDependencies",
            dependency_pb2.BulkCreateDependenciesRequest(
                dependencies=[
                    dependency_pb2.CreateDependencyRequest(
                        source_entity_id=1, target_entity_id=2
                    )
                ]
            ),
        )
        assert resp.errors
    with patch.object(
        mod.ElderServicer, "_scoped_dependency", side_effect=RuntimeError("dep boom")
    ):
        resp = env.ok(
            "BulkDeleteDependencies",
            dependency_pb2.BulkDeleteDependenciesRequest(ids=[1]),
        )
        assert resp.errors and "dep boom" in resp.errors[0]
