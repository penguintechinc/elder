"""Route tests for the IAM provider API (``/api/v1/iam``) through the real app.

Real JWT verification, the real portal-role/scope decorators and the real
Postgres schema are used.  The cloud-facing ``IAMService`` operations are
patched per route (the true external boundary) so that every route's success,
tenant-claim, not-found and generic-error branches can be driven.
"""

# flake8: noqa: E501

from unittest.mock import patch

import pytest
import pytest_asyncio

from apps.api.services.iam.service import (
    IAMProviderNotFoundError,
    IAMService,
    IAMTenantRequiredError,
)
from tests.unit._cov_d_helpers import call, in_db, world  # noqa: F401

BASE = "/api/v1/iam/providers"
SCOPES = ["discovery:read", "discovery:write", "discovery:admin"]
TENANT_PATCH = "apps.api.modules.discovery.routes.iam.get_current_tenant_id"


@pytest_asyncio.fixture
async def admin_world(world):  # noqa: F811
    """Promote the tenant-A identity to the portal admin role and mint tokens."""

    def _promote():
        """Set portal_role=admin for the tenant-A user."""
        db = world["db"]
        db(db.identities.id == world["a"]["user"]).update(portal_role="admin")
        db.commit()

    await in_db(_promote)
    world["admin"] = world["tok"]("a", SCOPES)
    world["reader"] = world["tok"]("a", ["discovery:read"])
    return world


# (name, http method, url suffix, body, IAMService method, generic-not-found,
#  generic error status, success status)
ROUTES = [
    ("list_providers", "get", "", None, "list_providers", False, 500, 200),
    ("get_provider", "get", "/7", None, "get_provider", True, 500, 200),
    (
        "create_provider",
        "post",
        "",
        {"name": "n", "provider_type": "aws_iam", "config": {}},
        "create_provider",
        False,
        400,
        201,
    ),
    ("update_provider", "put", "/7", {"name": "n"}, "update_provider", True, 400, 200),
    ("delete_provider", "delete", "/7", None, "delete_provider", True, 500, 200),
    ("test_provider", "post", "/7/test", None, "test_provider", True, 500, 200),
    ("sync_provider", "post", "/7/sync", None, "sync_provider", True, 500, 200),
    ("list_users", "get", "/7/users", None, "list_users", True, 500, 200),
    ("get_user", "get", "/7/users/u1", None, "get_user", True, 500, 200),
    (
        "create_user",
        "post",
        "/7/users",
        {"username": "u"},
        "create_user",
        True,
        400,
        201,
    ),
    (
        "update_user",
        "put",
        "/7/users/u1",
        {"display_name": "x"},
        "update_user",
        True,
        400,
        200,
    ),
    ("delete_user", "delete", "/7/users/u1", None, "delete_user", True, 500, 200),
    ("list_roles", "get", "/7/roles", None, "list_roles", True, 500, 200),
    ("get_role", "get", "/7/roles/r1", None, "get_role", True, 500, 200),
    (
        "create_role",
        "post",
        "/7/roles",
        {"role_name": "r"},
        "create_role",
        True,
        400,
        201,
    ),
    (
        "update_role",
        "put",
        "/7/roles/r1",
        {"description": "x"},
        "update_role",
        True,
        400,
        200,
    ),
    ("delete_role", "delete", "/7/roles/r1", None, "delete_role", True, 500, 200),
    ("list_policies", "get", "/7/policies", None, "list_policies", True, 500, 200),
    ("get_policy", "get", "/7/policies/p1", None, "get_policy", True, 500, 200),
    (
        "create_policy",
        "post",
        "/7/policies",
        {"policy_name": "p", "policy_document": {}},
        "create_policy",
        True,
        400,
        201,
    ),
    (
        "delete_policy",
        "delete",
        "/7/policies/p1",
        None,
        "delete_policy",
        True,
        500,
        200,
    ),
    (
        "attach_user_policy",
        "post",
        "/7/users/u1/policies/p1",
        None,
        "attach_policy_to_user",
        True,
        400,
        200,
    ),
    (
        "detach_user_policy",
        "delete",
        "/7/users/u1/policies/p1",
        None,
        "detach_policy_from_user",
        True,
        400,
        200,
    ),
    (
        "attach_role_policy",
        "post",
        "/7/roles/r1/policies/p1",
        None,
        "attach_policy_to_role",
        True,
        400,
        200,
    ),
    (
        "detach_role_policy",
        "delete",
        "/7/roles/r1/policies/p1",
        None,
        "detach_policy_from_role",
        True,
        400,
        200,
    ),
    (
        "list_user_policies",
        "get",
        "/7/users/u1/policies",
        None,
        "list_user_policies",
        True,
        500,
        200,
    ),
    (
        "list_role_policies",
        "get",
        "/7/roles/r1/policies",
        None,
        "list_role_policies",
        True,
        500,
        200,
    ),
    (
        "create_access_key",
        "post",
        "/7/users/u1/access-keys",
        None,
        "create_access_key",
        True,
        400,
        201,
    ),
    (
        "list_access_keys",
        "get",
        "/7/users/u1/access-keys",
        None,
        "list_access_keys",
        True,
        500,
        200,
    ),
    (
        "delete_access_key",
        "delete",
        "/7/users/u1/access-keys/k1",
        None,
        "delete_access_key",
        True,
        500,
        200,
    ),
    ("list_groups", "get", "/7/groups", None, "list_groups", True, 500, 200),
    (
        "create_group",
        "post",
        "/7/groups",
        {"group_name": "g"},
        "create_group",
        True,
        400,
        201,
    ),
    ("delete_group", "delete", "/7/groups/g1", None, "delete_group", True, 500, 200),
    (
        "add_user_to_group",
        "post",
        "/7/groups/g1/users/u1",
        None,
        "add_user_to_group",
        True,
        400,
        200,
    ),
    (
        "remove_user_from_group",
        "delete",
        "/7/groups/g1/users/u1",
        None,
        "remove_user_from_group",
        True,
        400,
        200,
    ),
]
IDS = [r[0] for r in ROUTES]


def _kw(body):
    """Return request kwargs carrying the JSON body when one is defined."""
    return {"json": body} if body is not None else {}


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES, ids=IDS)
async def test_route_success_and_error_mapping(app, admin_world, route):
    """Each route returns its success status and maps service errors to HTTP codes."""
    _, method, suffix, body, svc, generic_nf, generic_code, ok = route
    token = admin_world["admin"]
    url = BASE + suffix
    with patch.object(IAMService, svc, return_value={"ok": True}) as m:
        status, data = await call(app, method, url, token, **_kw(body))
        assert status == ok
        assert "ok" in str(data)
        assert m.call_args.args[0] == admin_world["a"]["tenant"]

    with patch.object(IAMService, svc, side_effect=IAMProviderNotFoundError("gone")):
        status, _ = await call(app, method, url, token, **_kw(body))
        assert status == 404

    with patch.object(IAMService, svc, side_effect=Exception("Resource not found")):
        status, _ = await call(app, method, url, token, **_kw(body))
        assert status == (404 if generic_nf else generic_code)

    with patch.object(IAMService, svc, side_effect=Exception("kaboom")):
        status, data = await call(app, method, url, token, **_kw(body))
        assert status == generic_code
        assert "kaboom" not in str(data)

    with patch.object(IAMService, svc, side_effect=IAMTenantRequiredError("x")):
        status, _ = await call(app, method, url, token, **_kw(body))
        assert status == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES, ids=IDS)
async def test_route_missing_tenant_claim_is_403(app, admin_world, route):
    """A token whose tenant cannot be resolved is refused before the service runs."""
    _, method, suffix, body, svc, *_ = route
    with (
        patch(TENANT_PATCH, return_value=None),
        patch.object(IAMService, svc, return_value={"ok": True}) as m,
    ):
        status, data = await call(
            app, method, BASE + suffix, admin_world["admin"], **_kw(body)
        )
    assert status == 403
    assert "tenant" in data["error"].lower()
    m.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES, ids=IDS)
async def test_route_requires_scope_and_role(app, admin_world, route):
    """A read-only scope is rejected on every route needing write/admin access."""
    _, method, suffix, body, svc, *_ = route
    with patch.object(IAMService, svc, return_value={"ok": True}):
        status, _ = await call(
            app, method, BASE + suffix, admin_world["reader"], **_kw(body)
        )
        anon, _ = await call(app, method, BASE + suffix, None, **_kw(body))
    if method == "get":
        assert status == 200
    else:
        assert status == 403
    assert anon == 401


@pytest.mark.asyncio
async def test_non_admin_role_forbidden_even_with_scope(app, world):  # noqa: F811
    """An observer portal role is refused on admin-only routes despite the scope."""
    token = world["tok"]("a", SCOPES)
    status, data = await call(app, "delete", BASE + "/7", token)
    assert status == 403
    assert data["your_role"] == "observer"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,suffix,body,fragment",
    [
        ("post", "", {}, "Request body required"),
        ("post", "", {"name": "n"}, "Missing required fields"),
        ("put", "/7", {}, "Request body required"),
        ("post", "/7/users", {}, "Request body required"),
        ("post", "/7/users", {"display_name": "x"}, "username"),
        ("put", "/7/users/u1", {}, "Request body required"),
        ("post", "/7/roles", {}, "Request body required"),
        ("post", "/7/roles", {"description": "x"}, "role_name"),
        ("put", "/7/roles/r1", {}, "Request body required"),
        ("post", "/7/policies", {}, "Request body required"),
        ("post", "/7/policies", {"policy_name": "p"}, "policy_document"),
        ("post", "/7/groups", {}, "Request body required"),
        ("post", "/7/groups", {"description": "x"}, "group_name"),
    ],
)
async def test_validation_errors(app, admin_world, method, suffix, body, fragment):
    """Missing or incomplete JSON bodies are rejected with 400 and a clear message."""
    status, data = await call(
        app, method, BASE + suffix, admin_world["admin"], json=body
    )
    assert status == 400
    assert fragment in data["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "suffix,svc",
    [
        ("/7/users", "list_users"),
        ("/7/roles", "list_roles"),
        ("/7/policies", "list_policies"),
        ("/7/groups", "list_groups"),
    ],
)
async def test_list_pagination_params_forwarded(app, admin_world, suffix, svc):
    """limit/next_token query params are clamped and forwarded to the service."""
    with patch.object(IAMService, svc, return_value={"items": []}) as m:
        status, _ = await call(
            app, "get", BASE + suffix + "?limit=5&next_token=abc", admin_world["admin"]
        )
        assert status == 200
        assert m.call_args.kwargs == {"limit": 5, "next_token": "abc"}
        status, _ = await call(app, "get", BASE + suffix, admin_world["admin"])
        assert status == 200
        assert m.call_args.kwargs == {}


@pytest.mark.asyncio
async def test_delete_provider_with_resources_is_409(app, admin_world):
    """A 'cannot delete' service error maps to 409 conflict."""
    with patch.object(
        IAMService, "delete_provider", side_effect=Exception("Cannot delete: has users")
    ):
        status, _ = await call(app, "delete", BASE + "/7", admin_world["admin"])
    assert status == 409


@pytest.mark.asyncio
async def test_list_providers_filters_forwarded(app, admin_world):
    """enabled_only and organization_id query params reach the service."""
    with patch.object(IAMService, "list_providers", return_value=[1, 2]) as m:
        status, data = await call(
            app,
            "get",
            BASE + "?enabled_only=true&organization_id=9",
            admin_world["admin"],
        )
    assert status == 200
    assert data == {"providers": [1, 2], "count": 2}
    assert m.call_args.kwargs == {"enabled_only": True, "organization_id": 9}


@pytest.mark.asyncio
async def test_provider_crud_through_real_service(app, admin_world):
    """Create, read, update, list and delete a provider against the real database."""
    tok = admin_world["admin"]
    org = admin_world["a"]["org"]
    status, created = await call(
        app,
        "post",
        BASE,
        tok,
        json={
            "name": "cd-prov",
            "provider_type": "aws_iam",
            "config": {"region": "us-east-1"},
            "description": "d",
            "organization_id": org,
        },
    )
    assert status == 201, created
    pid = created["id"]
    status, got = await call(app, "get", f"{BASE}/{pid}", tok)
    assert status == 200 and got["name"] == "cd-prov"
    status, upd = await call(
        app, "put", f"{BASE}/{pid}", tok, json={"description": "new", "enabled": False}
    )
    assert status == 200
    status, listed = await call(app, "get", BASE + "?enabled_only=false", tok)
    assert pid in {p["id"] for p in listed["providers"]}
    status, _ = await call(app, "delete", f"{BASE}/{pid}", tok)
    assert status == 200
    status, _ = await call(app, "get", f"{BASE}/{pid}", tok)
    assert status == 404
