"""Route tests for the Google Workspace API (``/api/v1/google-workspace``).

Requests go through the real Quart app with real JWT verification and the
real portal-role/scope decorators.  ``GoogleWorkspaceService`` operations are
the external boundary (they call Google Directory APIs) and are patched per
route to drive every success and error-mapping branch.
"""

# flake8: noqa: E501

from unittest.mock import patch

import pytest
import pytest_asyncio

from apps.api.services.google_workspace import GoogleWorkspaceService
from tests.unit._cov_d_helpers import call, fake_secret, in_db, world  # noqa: F401

BASE = "/api/v1/google-workspace/providers"
SCOPES = ["discovery:read", "discovery:write", "discovery:admin"]
PROV_BODY = {
    "name": "n",
    "organization_id": 1,
    "customer_id": "C1",
    "admin_email": "a@example.com",
    "service_account_json": {"k": "v"},
}
USER_BODY = {
    "primary_email": "u@example.com",
    "given_name": "G",
    "family_name": "F",
    "password": "pw",
}

# (name, method, url suffix, body, service method, generic-NF-mapped,
#  generic error status, success status)
ROUTES = [
    ("list_providers", "get", "", None, "list_providers", False, 500, 200),
    ("create_provider", "post", "", PROV_BODY, "create_provider", False, 400, 201),
    ("get_provider", "get", "/7", None, "get_provider", True, 500, 200),
    ("update_provider", "put", "/7", {"name": "x"}, "update_provider", True, 400, 200),
    ("delete_provider", "delete", "/7", None, "delete_provider", True, 500, 200),
    ("test_provider", "post", "/7/test", None, "test_provider", True, 500, 200),
    (
        "list_users",
        "get",
        "/7/users?domain=d.com&limit=3",
        None,
        "list_users",
        True,
        500,
        200,
    ),
    ("get_user", "get", "/7/users/u@example.com", None, "get_user", True, 500, 200),
    ("create_user", "post", "/7/users", USER_BODY, "create_user", True, 400, 201),
    (
        "update_user",
        "put",
        "/7/users/u@example.com",
        {"suspended": True},
        "update_user",
        True,
        400,
        200,
    ),
    (
        "delete_user",
        "delete",
        "/7/users/u@example.com",
        None,
        "delete_user",
        True,
        500,
        200,
    ),
    (
        "list_groups",
        "get",
        "/7/groups?domain=d.com",
        None,
        "list_groups",
        True,
        500,
        200,
    ),
    ("get_group", "get", "/7/groups/g@example.com", None, "get_group", True, 500, 200),
    (
        "create_group",
        "post",
        "/7/groups",
        {"email": "g@example.com", "name": "G"},
        "create_group",
        True,
        400,
        201,
    ),
    (
        "delete_group",
        "delete",
        "/7/groups/g@example.com",
        None,
        "delete_group",
        True,
        500,
        200,
    ),
    (
        "list_group_members",
        "get",
        "/7/groups/g@example.com/members",
        None,
        "list_group_members",
        True,
        500,
        200,
    ),
    (
        "add_group_member",
        "post",
        "/7/groups/g@example.com/members",
        {"member_email": "m@example.com"},
        "add_group_member",
        True,
        400,
        201,
    ),
    (
        "remove_group_member",
        "delete",
        "/7/groups/g@example.com/members/m@example.com",
        None,
        "remove_group_member",
        True,
        500,
        200,
    ),
]
IDS = [r[0] for r in ROUTES]


@pytest_asyncio.fixture
async def gworld(world):  # noqa: F811
    """Promote the tenant-A identity to portal admin and mint tokens."""

    def _promote():
        """Set portal_role=admin for the tenant-A user."""
        db = world["db"]
        db(db.identities.id == world["a"]["user"]).update(portal_role="admin")
        db.commit()

    await in_db(_promote)
    world["admin"] = world["tok"]("a", SCOPES)
    world["reader"] = world["tok"]("a", ["discovery:read"])
    return world


def _kw(body):
    """Return request kwargs carrying the JSON body when one is defined."""
    return {"json": body} if body is not None else {}


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES, ids=IDS)
async def test_route_success_and_error_mapping(app, gworld, route):
    """Each route returns its success status and maps errors to its HTTP codes."""
    _, method, suffix, body, svc, generic_nf, generic_code, ok = route
    tok = gworld["admin"]
    url = BASE + suffix
    result = [1, 2] if svc == "list_providers" else {"success": True, "ok": True}
    with patch.object(GoogleWorkspaceService, svc, return_value=result) as m:
        status, data = await call(app, method, url, tok, **_kw(body))
    assert status == ok, data
    assert m.called

    with patch.object(
        GoogleWorkspaceService, svc, side_effect=Exception("Item not found")
    ):
        status, _ = await call(app, method, url, tok, **_kw(body))
    assert status == (404 if generic_nf else generic_code)

    with patch.object(GoogleWorkspaceService, svc, side_effect=Exception("kaboom")):
        status, data = await call(app, method, url, tok, **_kw(body))
    assert status == generic_code
    assert "kaboom" not in str(data)


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES, ids=IDS)
async def test_route_auth_enforced(app, gworld, route):
    """Anonymous callers get 401 and a read-only scope cannot use write routes."""
    _, method, suffix, body, svc, *_ = route
    with patch.object(GoogleWorkspaceService, svc, return_value={"success": True}):
        anon, _ = await call(app, method, BASE + suffix, None, **_kw(body))
        status, _ = await call(
            app, method, BASE + suffix, gworld["reader"], **_kw(body)
        )
    assert anon == 401
    assert status == (200 if method == "get" else 403)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,suffix,body,fragment",
    [
        ("post", "", {}, "Request body required"),
        ("post", "", {"name": "n"}, "Missing required fields"),
        ("put", "/7", {}, "Request body required"),
        ("post", "/7/users", {}, "Request body required"),
        (
            "post",
            "/7/users",
            {"primary_email": "u@example.com"},
            "Missing required fields",
        ),
        ("put", "/7/users/u@example.com", {}, "Request body required"),
        ("post", "/7/groups", {}, "Request body required"),
        ("post", "/7/groups", {"email": "g@example.com"}, "Missing required fields"),
        ("post", "/7/groups/g@example.com/members", {}, "member_email"),
    ],
)
async def test_validation_errors(app, gworld, method, suffix, body, fragment):
    """Missing or incomplete bodies are rejected with 400."""
    status, data = await call(app, method, BASE + suffix, gworld["admin"], json=body)
    assert status == 400
    assert fragment in data["error"]


@pytest.mark.asyncio
async def test_test_provider_failure_result_is_400(app, gworld):
    """A provider test reporting success=False yields HTTP 400."""
    with patch.object(
        GoogleWorkspaceService, "test_provider", return_value={"success": False}
    ):
        status, data = await call(app, "post", BASE + "/7/test", gworld["admin"])
    assert status == 400 and data == {"success": False}


@pytest.mark.asyncio
async def test_arguments_forwarded_to_service(app, gworld):
    """Query/body fields (limit, domain, optional org unit, role) reach the service."""
    tok = gworld["admin"]
    with patch.object(GoogleWorkspaceService, "list_users", return_value={}) as m:
        await call(app, "get", BASE + "/7/users?domain=d.com&limit=3", tok)
    assert m.call_args.kwargs == {"provider_id": 7, "domain": "d.com", "limit": 3}
    with patch.object(GoogleWorkspaceService, "add_group_member", return_value={}) as m:
        await call(
            app,
            "post",
            BASE + "/7/groups/g@example.com/members",
            tok,
            json={"member_email": "m@example.com", "role": "OWNER"},
        )
    assert m.call_args.kwargs["role"] == "OWNER"
    with patch.object(GoogleWorkspaceService, "create_user", return_value={}) as m:
        await call(app, "post", BASE + "/7/users", tok, json=USER_BODY)
    assert m.call_args.kwargs["org_unit_path"] == "/"


@pytest.mark.asyncio
async def test_provider_crud_through_real_service(app, gworld):
    """Create, read, list, update and delete a provider against the real database."""
    tok = gworld["admin"]
    org = gworld["a"]["org"]
    status, _ = await call(app, "get", BASE + "/99999999", tok)
    assert status == 404
    sa_json = {
        "type": "service_account",
        "project_id": "proj-1",
        "private_key": fake_secret("pk"),
        "client_email": "svc@example.com",
    }
    status, made = await call(
        app,
        "post",
        BASE,
        tok,
        json={
            "name": "cd-gws",
            "organization_id": org,
            "customer_id": "C0123",
            "admin_email": "admin@example.com",
            "service_account_json": sa_json,
            "description": "d",
        },
    )
    assert status == 201, made
    pid = made["id"]
    status, got = await call(app, "get", f"{BASE}/{pid}", tok)
    assert status == 200 and got["name"] == "cd-gws"
    status, listed = await call(app, "get", f"{BASE}?organization_id={org}", tok)
    assert status == 200 and pid in {p["id"] for p in listed["providers"]}
    status, _ = await call(
        app, "put", f"{BASE}/{pid}", tok, json={"description": "n", "enabled": False}
    )
    assert status == 200
    status, _ = await call(app, "post", BASE, tok, json={**PROV_BODY, "name": " "})
    assert status == 400
    status, _ = await call(app, "delete", f"{BASE}/{pid}", tok)
    assert status == 200
    status, _ = await call(app, "get", f"{BASE}/{pid}", tok)
    assert status == 404
