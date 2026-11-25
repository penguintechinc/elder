"""Authentication and tenant isolation tests for the Elder gRPC server.

A real in-process gRPC server (built the same way production builds it) is
driven over a loopback channel against the test database with two tenants.
Covers: unauthenticated and malformed-token calls rejected for every
protected RPC, wrong-tenant list/get/update/delete/create denied per RPC,
platform (system-tenant) service identities refused, per-call scope checks,
and the page-size / graph-depth / batch-size caps.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
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

try:
    from apps.api.grpc.server import create_server
except ImportError:  # pre-fix tree: no hardened server factory exists

    def create_server(app, max_workers: int = 4):
        """Build the server the way the pre-fix ``serve()`` did."""
        from concurrent import futures

        from apps.api.grpc.servicers.elder_servicer import ElderServicer

        server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
        elder_pb2_grpc.add_ElderServiceServicer_to_server(ElderServicer(app), server)
        return server


SERVICE = elder_pb2.DESCRIPTOR.services_by_name["ElderService"]
PUBLIC_RPCS = {"Login", "RefreshToken", "HealthCheck"}
PROTECTED_RPCS = sorted(m.name for m in SERVICE.methods if m.name not in PUBLIC_RPCS)
PASSWORD = "Grpc-Test-Passw0rd!"


# --------------------------------------------------------------------------
# Seeding
# --------------------------------------------------------------------------


def _tenant(db, label: str) -> int:
    """Create a fresh tenant."""
    tid = db.tenants.insert(
        name=f"gRPC {label}", slug=f"grpc-{label}-{uuid4().hex[:8]}", is_active=True
    )
    db.commit()
    return tid


def _identity(db, tenant_id: int, role: str, superuser: bool = False) -> int:
    """Create an active local identity in ``tenant_id`` with ``role``."""
    name = f"grpc-{role}-{uuid4().hex[:8]}"
    iid = db.identities.insert(
        username=name,
        email=f"{name}@example.test",
        identity_type="human",
        auth_provider="local",
        password_hash=generate_password_hash(PASSWORD),
        is_active=True,
        is_superuser=superuser,
        mfa_enabled=False,
        must_change_password=False,
        portal_role=role,
        tenant_id=tenant_id,
    )
    db.commit()
    return iid


def _org(db, tenant_id: int, name: str, parent_id: int | None = None) -> int:
    """Create an organization in ``tenant_id``."""
    now = datetime.now(UTC)
    oid = db.organizations.insert(
        name=name,
        tenant_id=tenant_id,
        parent_id=parent_id,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return oid


def _entity(db, org_id: int, name: str) -> int:
    """Create a compute entity in ``org_id``."""
    now = datetime.now(UTC)
    eid = db.entities.insert(
        name=name,
        type="compute",
        organization_id=org_id,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return eid


def _dep(db, tenant_id: int, source_id: int, target_id: int) -> int:
    """Create an entity-to-entity dependency recorded for ``tenant_id``."""
    now = datetime.now(UTC)
    did = db.dependencies.insert(
        tenant_id=tenant_id,
        source_type="entity",
        source_id=source_id,
        target_type="entity",
        target_id=target_id,
        dependency_type="depends_on",
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return did


@dataclass
class World:
    """Two seeded tenants plus the running server and tokens."""

    db: Any
    stub: Any
    channel: Any
    own_tenant: int
    foreign_tenant: int
    ids: dict[str, int]
    tokens: dict[str, str]


def _in_app(app, fn, *args):
    """Run ``fn`` inside the (async) Quart application context."""

    async def _run():
        async with app.app_context():
            return fn(*args)

    return asyncio.run(_run())


def _access_token(app, identity_id: int) -> str:
    """Issue a REST-API access token for an identity."""
    from apps.api.auth.jwt_handler import generate_token

    row = app.db.identities[identity_id]
    return _in_app(app, generate_token, row, "access")


def _service_token(app, tenant: str, scopes: list[str], sub="svc-integrations") -> str:
    """Issue a short-lived service-worker JWT as the internal issuers do."""
    now = datetime.now(UTC)
    payload = {
        "sub": sub,
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


@pytest.fixture(scope="module")
def world(app):
    """Seed two tenants and start an in-process gRPC server."""
    db = app.db
    own, foreign = _tenant(db, "own"), _tenant(db, "foreign")
    tag = uuid4().hex[:8]
    ids: dict[str, int] = {}
    for prefix, tid in (("own", own), ("foreign", foreign)):
        ids[f"{prefix}_org"] = _org(db, tid, f"{prefix}-org-{tag}")
        ids[f"{prefix}_child_org"] = _org(
            db, tid, f"{prefix}-child-{tag}", ids[f"{prefix}_org"]
        )
        ids[f"{prefix}_e1"] = _entity(db, ids[f"{prefix}_org"], f"{prefix}-e1-{tag}")
        ids[f"{prefix}_e2"] = _entity(db, ids[f"{prefix}_org"], f"{prefix}-e2-{tag}")
        ids[f"{prefix}_dep"] = _dep(db, tid, ids[f"{prefix}_e1"], ids[f"{prefix}_e2"])
        ids[f"{prefix}_admin"] = _identity(db, tid, "admin")
    ids["own_observer"] = _identity(db, own, "observer")

    tokens = {
        "admin": _access_token(app, ids["own_admin"]),
        "observer": _access_token(app, ids["own_observer"]),
        "svc_tenant": _service_token(app, str(own), ["entities:read"]),
        "svc_system": _service_token(
            app, "system", ["entities:read", "entities:write", "organizations:read"]
        ),
    }

    server = create_server(app, max_workers=4)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    stub = elder_pb2_grpc.ElderServiceStub(channel)
    try:
        yield World(db, stub, channel, own, foreign, ids, tokens)
    finally:
        channel.close()
        server.stop(0)


def _meta(token: str | None) -> tuple:
    """Build call metadata carrying ``token`` as a bearer credential."""
    return (("authorization", f"Bearer {token}"),) if token else ()


def _call(world: World, rpc: str, request, token: str | None = None):
    """Invoke ``rpc``; return ``(code, response_or_None)``."""
    try:
        resp = getattr(world.stub, rpc)(request, metadata=_meta(token), timeout=30)
        return grpc.StatusCode.OK, resp
    except grpc.RpcError as err:
        return err.code(), None


def _exists(world: World, table: str, rid: int) -> bool:
    """Return whether row ``rid`` still exists in ``table``."""
    world.db.commit()  # end any open snapshot so other threads' writes show
    return getattr(world.db, table)[rid] is not None


# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------


def _raw_call(world: World, rpc: str, token: str | None):
    """Call ``rpc`` with an empty request message (valid for every type)."""
    method = world.channel.unary_unary(
        f"/elder.ElderService/{rpc}",
        request_serializer=lambda _: b"",
        response_deserializer=lambda b: b,
    )
    try:
        method(None, metadata=_meta(token), timeout=30)
        return grpc.StatusCode.OK
    except grpc.RpcError as err:
        return err.code()


@pytest.mark.parametrize("rpc", PROTECTED_RPCS)
def test_protected_rpc_rejects_missing_token(world, rpc):
    """Every non-public RPC answers UNAUTHENTICATED without a token."""
    assert _raw_call(world, rpc, None) == grpc.StatusCode.UNAUTHENTICATED


def _bad_tokens(app, world: World) -> dict[str, str]:
    """Tokens that must never authenticate a gRPC call."""
    from apps.api.auth.jwt_handler import generate_token

    now = datetime.now(UTC)
    base = {
        "sub": str(world.ids["own_admin"]),
        "type": "access",
        "tenant": str(world.own_tenant),
        "scope": ["infrastructure:read"],
        "iss": app.config.get("JWT_ISSUER", "elder-api"),
        "aud": app.config.get("JWT_AUDIENCE", "elder-api"),
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    secret = app.config["JWT_SECRET_KEY"] or app.config["SECRET_KEY"]
    alg = app.config["JWT_ALGORITHM"]

    def signed(**changes):
        payload = {**base, **changes}
        payload = {k: v for k, v in payload.items() if v is not None}
        return jwt.encode(payload, secret, algorithm=alg)

    refresh = _in_app(
        app, generate_token, app.db.identities[world.ids["own_admin"]], "refresh"
    )
    return {
        "garbage": "not-a-jwt",
        "wrong_key": jwt.encode(base, "x" * 64, algorithm=alg),
        "expired": signed(iat=now - timedelta(hours=2), exp=now - timedelta(hours=1)),
        "refresh_type": refresh,
        "untyped": signed(type=None),
        "no_tenant": signed(tenant=None),
        "no_issuer": signed(iss=None),
        "no_audience": signed(aud=None),
        "tenant_mismatch": signed(tenant=str(world.foreign_tenant)),
    }


@pytest.mark.parametrize(
    "kind",
    [
        "garbage",
        "wrong_key",
        "expired",
        "refresh_type",
        "untyped",
        "no_tenant",
        "no_issuer",
        "no_audience",
        "tenant_mismatch",
    ],
)
def test_invalid_token_rejected(app, world, kind):
    """Malformed, expired, mistyped or mis-claimed tokens are UNAUTHENTICATED."""
    token = _bad_tokens(app, world)[kind]
    code, _ = _call(world, "ListEntities", entity_pb2.ListEntitiesRequest(), token)
    assert code == grpc.StatusCode.UNAUTHENTICATED


def test_health_check_is_public(world):
    """The liveness probe stays reachable without credentials."""
    code, resp = _call(world, "HealthCheck", common_pb2.Empty())
    assert code == grpc.StatusCode.OK and resp.success


def test_login_token_authenticates_follow_up_calls(world):
    """Login issues a token the server itself accepts (round trip)."""
    username = world.db.identities[world.ids["own_admin"]].username
    code, login = _call(
        world,
        "Login",
        auth_pb2.LoginRequest(username=username, password=PASSWORD),
    )
    assert code == grpc.StatusCode.OK and login.access_token
    claims = jwt.decode(login.access_token, options={"verify_signature": False})
    assert isinstance(claims.get("auth_time"), int)
    code, resp = _call(
        world,
        "GetEntity",
        entity_pb2.GetEntityRequest(id=world.ids["own_e1"]),
        login.access_token,
    )
    assert code == grpc.StatusCode.OK
    assert resp.entity.id == world.ids["own_e1"]


# --------------------------------------------------------------------------
# Tenant isolation, per RPC
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    """One wrong-tenant probe: request builder, expected code, survivor row."""

    rpc: str
    build: Callable[[dict[str, int]], Any]
    code: grpc.StatusCode = grpc.StatusCode.NOT_FOUND
    survives: tuple[str, str] | None = None  # (table, ids key) left intact


NF = grpc.StatusCode.NOT_FOUND
CROSS_TENANT_CASES = [
    Case(
        "GetOrganization",
        lambda i: organization_pb2.GetOrganizationRequest(id=i["foreign_org"]),
    ),
    Case(
        "GetOrganizationChildren",
        lambda i: organization_pb2.GetOrganizationChildrenRequest(id=i["foreign_org"]),
    ),
    Case(
        "GetOrganizationHierarchy",
        lambda i: organization_pb2.GetOrganizationHierarchyRequest(
            id=i["foreign_child_org"]
        ),
    ),
    Case(
        "UpdateOrganization",
        lambda i: organization_pb2.UpdateOrganizationRequest(
            id=i["foreign_org"], name="pwned"
        ),
        survives=("organizations", "foreign_org"),
    ),
    Case(
        "DeleteOrganization",
        lambda i: organization_pb2.DeleteOrganizationRequest(id=i["foreign_child_org"]),
        survives=("organizations", "foreign_child_org"),
    ),
    Case(
        "CreateOrganization",
        lambda i: organization_pb2.CreateOrganizationRequest(
            name="x", parent_id=i["foreign_org"]
        ),
        code=grpc.StatusCode.INVALID_ARGUMENT,
    ),
    Case("GetEntity", lambda i: entity_pb2.GetEntityRequest(id=i["foreign_e1"])),
    Case(
        "UpdateEntity",
        lambda i: entity_pb2.UpdateEntityRequest(id=i["foreign_e1"], name="pwned"),
        survives=("entities", "foreign_e1"),
    ),
    Case(
        "UpdateEntity",
        lambda i: entity_pb2.UpdateEntityRequest(
            id=i["own_e2"], organization_id=i["foreign_org"]
        ),
    ),
    Case(
        "DeleteEntity",
        lambda i: entity_pb2.DeleteEntityRequest(id=i["foreign_e2"]),
        survives=("entities", "foreign_e2"),
    ),
    Case(
        "CreateEntity",
        lambda i: entity_pb2.CreateEntityRequest(
            name="x", organization_id=i["foreign_org"]
        ),
    ),
    Case(
        "GetEntityDependencies",
        lambda i: entity_pb2.GetEntityDependenciesRequest(id=i["foreign_e1"]),
    ),
    Case(
        "GetDependency",
        lambda i: dependency_pb2.GetDependencyRequest(id=i["foreign_dep"]),
    ),
    Case(
        "UpdateDependency",
        lambda i: dependency_pb2.UpdateDependencyRequest(
            id=i["foreign_dep"], metadata={"k": "v"}
        ),
        survives=("dependencies", "foreign_dep"),
    ),
    Case(
        "DeleteDependency",
        lambda i: dependency_pb2.DeleteDependencyRequest(id=i["foreign_dep"]),
        survives=("dependencies", "foreign_dep"),
    ),
    Case(
        "CreateDependency",
        lambda i: dependency_pb2.CreateDependencyRequest(
            source_entity_id=i["own_e1"], target_entity_id=i["foreign_e1"]
        ),
    ),
    Case("GetIdentity", lambda i: auth_pb2.GetIdentityRequest(id=i["foreign_admin"])),
    Case(
        "UpdateIdentity",
        lambda i: auth_pb2.UpdateIdentityRequest(
            id=i["foreign_admin"], is_active=False
        ),
        survives=("identities", "foreign_admin"),
    ),
    Case(
        "DeleteIdentity",
        lambda i: auth_pb2.DeleteIdentityRequest(id=i["foreign_admin"]),
        survives=("identities", "foreign_admin"),
    ),
    Case(
        "GetDependencyGraph",
        lambda i: graph_pb2.GetDependencyGraphRequest(entity_id=i["foreign_e1"]),
    ),
    Case(
        "GetDependencyGraph",
        lambda i: graph_pb2.GetDependencyGraphRequest(organization_id=i["foreign_org"]),
    ),
    Case(
        "AnalyzeGraph",
        lambda i: graph_pb2.AnalyzeGraphRequest(organization_id=i["foreign_org"]),
    ),
    Case(
        "FindPath",
        lambda i: graph_pb2.FindPathRequest(
            source_entity_id=i["foreign_e1"], target_entity_id=i["foreign_e2"]
        ),
    ),
    Case(
        "GetEntityImpact",
        lambda i: graph_pb2.GetEntityImpactRequest(entity_id=i["foreign_e1"]),
    ),
]


@pytest.mark.parametrize(
    "case",
    CROSS_TENANT_CASES,
    ids=[f"{c.rpc}-{n}" for n, c in enumerate(CROSS_TENANT_CASES)],
)
def test_cross_tenant_rpc_denied(world, case):
    """A foreign id is indistinguishable from a missing one and is never mutated."""
    code, _ = _call(world, case.rpc, case.build(world.ids), world.tokens["admin"])
    assert code == case.code
    if case.survives:
        table, key = case.survives
        assert _exists(world, table, world.ids[key])


def test_cross_tenant_update_leaves_values_unchanged(world):
    """Foreign rows keep their original values after rejected updates."""
    _call(
        world,
        "UpdateIdentity",
        auth_pb2.UpdateIdentityRequest(id=world.ids["foreign_admin"], is_active=False),
        world.tokens["admin"],
    )
    world.db.commit()
    assert world.db.identities[world.ids["foreign_admin"]].is_active is True


LIST_CASES = [
    (
        "ListOrganizations",
        organization_pb2.ListOrganizationsRequest(),
        "organizations",
        "org",
    ),
    ("ListEntities", entity_pb2.ListEntitiesRequest(), "entities", "e1"),
    (
        "ListDependencies",
        dependency_pb2.ListDependenciesRequest(),
        "dependencies",
        "dep",
    ),
    ("ListIdentities", auth_pb2.ListIdentitiesRequest(), "identities", "admin"),
]


@pytest.mark.parametrize(
    "rpc,request_msg,field,key", LIST_CASES, ids=[c[0] for c in LIST_CASES]
)
def test_list_rpc_is_tenant_filtered(world, rpc, request_msg, field, key):
    """Lists include the caller's rows and never another tenant's."""
    request_msg.pagination.per_page = 200
    code, resp = _call(world, rpc, request_msg, world.tokens["admin"])
    assert code == grpc.StatusCode.OK
    listed = {row.id for row in getattr(resp, field)}
    assert world.ids[f"own_{key}"] in listed
    assert world.ids[f"foreign_{key}"] not in listed


def test_list_entities_foreign_org_filter_is_empty(world):
    """Filtering by another tenant's organization returns nothing."""
    code, resp = _call(
        world,
        "ListEntities",
        entity_pb2.ListEntitiesRequest(organization_id=world.ids["foreign_org"]),
        world.tokens["admin"],
    )
    assert code == grpc.StatusCode.OK
    assert list(resp.entities) == []


def test_bulk_rpcs_skip_foreign_rows(world):
    """Bulk create/delete never touch or reference another tenant's rows."""
    i = world.ids
    db = world.db
    victim = _dep(db, world.foreign_tenant, i["foreign_e2"], i["foreign_e1"])
    foreign_entities = db(db.entities.organization_id == i["foreign_org"]).count()
    bridges = db(
        (db.dependencies.source_id == i["foreign_e1"])
        & (db.dependencies.target_id == i["own_e1"])
    ).count()
    db.commit()

    code, resp = _call(
        world,
        "BulkDeleteDependencies",
        dependency_pb2.BulkDeleteDependenciesRequest(ids=[victim]),
        world.tokens["admin"],
    )
    assert code == grpc.StatusCode.OK and resp.deleted_count == 0
    assert _exists(world, "dependencies", victim)

    code, resp = _call(
        world,
        "BatchCreateEntities",
        entity_pb2.BatchCreateEntitiesRequest(
            entities=[
                entity_pb2.CreateEntityRequest(
                    name="x", organization_id=i["foreign_org"]
                )
            ]
        ),
        world.tokens["admin"],
    )
    assert code == grpc.StatusCode.OK and len(resp.entities) == 0 and resp.errors

    code, resp = _call(
        world,
        "BulkCreateDependencies",
        dependency_pb2.BulkCreateDependenciesRequest(
            dependencies=[
                dependency_pb2.CreateDependencyRequest(
                    source_entity_id=i["foreign_e1"], target_entity_id=i["own_e1"]
                )
            ]
        ),
        world.tokens["admin"],
    )
    assert code == grpc.StatusCode.OK and len(resp.dependencies) == 0 and resp.errors

    db.commit()
    assert (
        db(db.entities.organization_id == i["foreign_org"]).count() == foreign_entities
    )
    assert (
        db(
            (db.dependencies.source_id == i["foreign_e1"])
            & (db.dependencies.target_id == i["own_e1"])
        ).count()
        == bridges
    )


def test_in_tenant_create_records_caller_tenant(world):
    """Creates inside the caller's tenant work and are recorded for it."""
    i = world.ids
    code, resp = _call(
        world,
        "CreateEntity",
        entity_pb2.CreateEntityRequest(
            name=f"new-{uuid4().hex[:6]}", organization_id=i["own_org"]
        ),
        world.tokens["admin"],
    )
    assert code == grpc.StatusCode.OK and resp.entity.id
    code, resp = _call(
        world,
        "CreateDependency",
        dependency_pb2.CreateDependencyRequest(
            source_entity_id=resp.entity.id, target_entity_id=i["own_e1"]
        ),
        world.tokens["admin"],
    )
    assert code == grpc.StatusCode.OK
    world.db.commit()
    assert world.db.dependencies[resp.dependency.id].tenant_id == world.own_tenant


def test_find_path_ignores_cross_tenant_edges(world):
    """A bridge edge into another tenant is never traversed."""
    i = world.ids
    _dep(world.db, world.foreign_tenant, i["own_e2"], i["foreign_e1"])
    code, resp = _call(
        world,
        "GetEntityImpact",
        graph_pb2.GetEntityImpactRequest(entity_id=i["own_e1"]),
        world.tokens["admin"],
    )
    assert code == grpc.StatusCode.OK
    assert i["foreign_e1"] not in {e.id for e in resp.impact.affected_entities}


# --------------------------------------------------------------------------
# Identities: system tenant, scopes, privilege fields
# --------------------------------------------------------------------------


def test_tenant_service_identity_is_scoped(world):
    """A tenant-bound service worker sees only its tenant's entities."""
    code, resp = _call(
        world,
        "ListEntities",
        entity_pb2.ListEntitiesRequest(
            pagination=common_pb2.PaginationRequest(per_page=200)
        ),
        world.tokens["svc_tenant"],
    )
    assert code == grpc.StatusCode.OK
    listed = {e.id for e in resp.entities}
    assert world.ids["own_e1"] in listed and world.ids["foreign_e1"] not in listed


@pytest.mark.parametrize(
    "rpc,request_msg",
    [
        ("ListEntities", entity_pb2.ListEntitiesRequest()),
        ("ListOrganizations", organization_pb2.ListOrganizationsRequest()),
    ],
)
def test_system_service_identity_gets_no_tenant_data(world, rpc, request_msg):
    """A system-tenant platform identity is refused on tenant-owned RPCs."""
    code, _ = _call(world, rpc, request_msg, world.tokens["svc_system"])
    assert code == grpc.StatusCode.PERMISSION_DENIED


@pytest.mark.parametrize(
    "token_key,rpc,build",
    [
        (
            "svc_tenant",
            "DeleteEntity",
            lambda i: entity_pb2.DeleteEntityRequest(id=i["own_e2"]),
        ),
        (
            "svc_tenant",
            "ListDependencies",
            lambda i: dependency_pb2.ListDependenciesRequest(),
        ),
        (
            "observer",
            "UpdateEntity",
            lambda i: entity_pb2.UpdateEntityRequest(id=i["own_e1"], name="x"),
        ),
        ("observer", "ListIdentities", lambda i: auth_pb2.ListIdentitiesRequest()),
        (
            "observer",
            "RegisterIdentity",
            lambda i: auth_pb2.RegisterIdentityRequest(
                username="u", email="u@x.test", password="p"
            ),
        ),
    ],
)
def test_missing_scope_is_permission_denied(world, token_key, rpc, build):
    """Each RPC enforces its resource:action scope like the REST route."""
    code, _ = _call(world, rpc, build(world.ids), world.tokens[token_key])
    assert code == grpc.StatusCode.PERMISSION_DENIED
    assert _exists(world, "entities", world.ids["own_e2"])


def test_non_superuser_cannot_grant_superuser(world):
    """is_superuser can only be changed by a superuser caller."""
    code, _ = _call(
        world,
        "UpdateIdentity",
        auth_pb2.UpdateIdentityRequest(id=world.ids["own_observer"], is_superuser=True),
        world.tokens["admin"],
    )
    assert code == grpc.StatusCode.PERMISSION_DENIED
    world.db.commit()
    assert world.db.identities[world.ids["own_observer"]].is_superuser is False


# --------------------------------------------------------------------------
# Bounds
# --------------------------------------------------------------------------


def test_page_size_is_capped(world):
    """per_page above 200 is clamped to 200."""
    code, resp = _call(
        world,
        "ListEntities",
        entity_pb2.ListEntitiesRequest(
            pagination=common_pb2.PaginationRequest(per_page=100000)
        ),
        world.tokens["admin"],
    )
    assert code == grpc.StatusCode.OK
    assert resp.pagination.per_page == 200


def test_graph_depth_is_capped(world):
    """An excessive traversal depth is clamped and reported as truncated."""
    code, resp = _call(
        world,
        "GetDependencyGraph",
        graph_pb2.GetDependencyGraphRequest(
            entity_id=world.ids["own_e1"], depth=100000
        ),
        world.tokens["admin"],
    )
    assert code == grpc.StatusCode.OK
    assert resp.graph.metadata["truncated"] == "true"
    assert world.ids["foreign_e1"] not in {n.id for n in resp.graph.nodes}


def test_bulk_size_is_capped(world):
    """Batch RPCs reject more than 200 items."""
    code, _ = _call(
        world,
        "BulkDeleteDependencies",
        dependency_pb2.BulkDeleteDependenciesRequest(ids=list(range(1, 202))),
        world.tokens["admin"],
    )
    assert code == grpc.StatusCode.INVALID_ARGUMENT
