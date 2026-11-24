"""Unit tests for the GCP IAM client and the BaseIAMProvider default behaviours.

The google-cloud SDK modules referenced by ``gcp_client`` are replaced with
MagicMocks at the module boundary; the real ``google.api_core`` exception
classes are used so provider-error translation is exercised faithfully.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("google.api_core")
from google.api_core import exceptions as google_exceptions  # noqa: E402

from apps.api.services.iam.gcp_client import GCPIAMClient  # noqa: E402

MODULE = "apps.api.services.iam.gcp_client"
PROJECT = "proj-1"


@pytest.fixture
def sdk():
    """Patch GCP SDK module handles and return (client, iam_mock, rm_mock)."""
    with (
        patch(f"{MODULE}.iam_admin_v1") as iam,
        patch(f"{MODULE}.resourcemanager_v3") as rm,
        patch(f"{MODULE}.service_account"),
    ):
        iam.IAMClient.return_value = MagicMock(name="iam_client")
        rm.ProjectsClient.return_value = MagicMock(name="rm_client")
        client = GCPIAMClient({"project_id": PROJECT, "organization_id": "org-9"})
        yield client, iam, rm


def _sa(email="svc@proj-1.iam.gserviceaccount.com", **kw):
    return SimpleNamespace(
        unique_id=kw.get("unique_id", "uid-1"),
        email=email,
        display_name=kw.get("display_name", "Svc"),
        name=kw.get("name", f"projects/{PROJECT}/serviceAccounts/{email}"),
        project_id=PROJECT,
        disabled=False,
        description="d",
    )


def _role(name=f"projects/{PROJECT}/roles/custom", stage=None, perms=("a.b",)):
    return SimpleNamespace(
        name=name,
        title="Custom",
        description="desc",
        included_permissions=list(perms),
        stage=stage,
        deleted=False,
    )


def _binding(role, members, condition=None):
    return SimpleNamespace(role=role, members=list(members), condition=condition)


class TestInit:
    """Constructor behaviour."""

    def test_missing_sdk_raises_import_error(self):
        """Without google-cloud-iam installed the constructor refuses to build."""
        with patch(f"{MODULE}.iam_admin_v1", None):
            with pytest.raises(ImportError, match="google-cloud-iam"):
                GCPIAMClient({"project_id": PROJECT})

    @pytest.mark.parametrize("as_str", [True, False])
    def test_credentials_json_str_or_dict(self, as_str):
        """credentials_json is accepted as JSON text or dict and passed to the SDK."""
        info = {"type": "service_account", "project_id": PROJECT}
        raw = json.dumps(info) if as_str else info
        with (
            patch(f"{MODULE}.iam_admin_v1") as iam,
            patch(f"{MODULE}.resourcemanager_v3") as rm,
            patch(f"{MODULE}.service_account") as sa_mod,
        ):
            creds = object()
            sa_mod.Credentials.from_service_account_info.return_value = creds
            c = GCPIAMClient({"project_id": PROJECT, "credentials_json": raw})
        sa_mod.Credentials.from_service_account_info.assert_called_once_with(info)
        iam.IAMClient.assert_called_once_with(credentials=creds)
        rm.ProjectsClient.assert_called_once_with(credentials=creds)
        assert c.project_id == PROJECT

    def test_no_resource_manager_module(self):
        """A missing resourcemanager module leaves resource_manager as None."""
        with (
            patch(f"{MODULE}.iam_admin_v1"),
            patch(f"{MODULE}.resourcemanager_v3", None),
        ):
            c = GCPIAMClient({"project_id": PROJECT})
        assert c.resource_manager is None


class TestUsers:
    """Service-account (user) operations."""

    def test_list_users_paginates(self, sdk):
        """list_users normalises accounts and exposes the next page token."""
        c, iam, _ = sdk
        c.iam_client.list_service_accounts.return_value = SimpleNamespace(
            service_accounts=[_sa()], next_page_token="tok2"
        )
        out = c.list_users(limit=5, next_token="tok1")
        assert out["users"][0]["email"] == "svc@proj-1.iam.gserviceaccount.com"
        assert out["next_token"] == "tok2" and out["truncated"] is True
        kw = iam.ListServiceAccountsRequest.call_args.kwargs
        assert kw == {
            "name": f"projects/{PROJECT}",
            "page_size": 5,
            "page_token": "tok1",
        }

    def test_list_users_last_page(self, sdk):
        """An empty next token yields next_token None and truncated False."""
        c, iam, _ = sdk
        c.iam_client.list_service_accounts.return_value = SimpleNamespace(
            service_accounts=[], next_page_token=""
        )
        out = c.list_users()
        assert out == {"users": [], "next_token": None, "truncated": False}
        assert iam.ListServiceAccountsRequest.call_args.kwargs["page_size"] == 100

    @pytest.mark.parametrize(
        "exc", [google_exceptions.PermissionDenied("nope"), RuntimeError("boom")]
    )
    def test_list_users_wraps_errors(self, sdk, exc):
        """Both Google API errors and generic errors surface as wrapped Exceptions."""
        c, _, _ = sdk
        c.iam_client.list_service_accounts.side_effect = exc
        with pytest.raises(Exception, match="GCP IAM list users error"):
            c.list_users()

    @pytest.mark.parametrize(
        "ident,expected",
        [
            ("svc", f"projects/{PROJECT}/serviceAccounts/svc"),
            ("projects/x/serviceAccounts/y", "projects/x/serviceAccounts/y"),
        ],
    )
    def test_get_user_resource_name(self, sdk, ident, expected):
        """get_user expands short ids to full resource names and normalises."""
        c, iam, _ = sdk
        c.iam_client.get_service_account.return_value = _sa()
        out = c.get_user(ident)
        iam.GetServiceAccountRequest.assert_called_once_with(name=expected)
        assert out["id"] == "uid-1"

    def test_get_user_not_found(self, sdk):
        """A provider NotFound is wrapped with the operation name."""
        c, _, _ = sdk
        c.iam_client.get_service_account.side_effect = google_exceptions.NotFound("x")
        with pytest.raises(Exception, match="GCP IAM get user error"):
            c.get_user("svc")

    def test_get_user_generic_error(self, sdk):
        """A non-Google error from get_user is wrapped too."""
        c, _, _ = sdk
        c.iam_client.get_service_account.side_effect = ValueError("bad")
        with pytest.raises(Exception, match="GCP IAM get user error: bad"):
            c.get_user("svc")

    @pytest.mark.parametrize(
        "username,display,expected_id,expected_display",
        [
            ("svc@proj.iam.gserviceaccount.com", None, "svc", "svc"),
            ("plain", "Pretty", "plain", "Pretty"),
        ],
    )
    def test_create_user(self, sdk, username, display, expected_id, expected_display):
        """create_user strips the email domain and defaults display_name to the id."""
        c, iam, _ = sdk
        c.iam_client.create_service_account.return_value = _sa()
        out = c.create_user(username, display_name=display, description="why")
        assert out["username"].startswith("svc@")
        assert iam.CreateServiceAccountRequest.call_args.kwargs["account_id"] == (
            expected_id
        )
        sa_kwargs = iam.ServiceAccount.call_args.kwargs
        assert sa_kwargs == {"display_name": expected_display, "description": "why"}

    def test_create_user_error(self, sdk):
        """create_user wraps provider failures."""
        c, _, _ = sdk
        c.iam_client.create_service_account.side_effect = google_exceptions.Conflict(
            "dup"
        )
        with pytest.raises(Exception, match="GCP IAM create user error"):
            c.create_user("x")

    def test_create_user_generic_error(self, sdk):
        """create_user wraps non-Google failures."""
        c, _, _ = sdk
        c.iam_client.create_service_account.side_effect = RuntimeError("x")
        with pytest.raises(Exception, match="GCP IAM create user error: x"):
            c.create_user("x")

    @pytest.mark.parametrize(
        "ident,expected",
        [
            ("svc", f"projects/{PROJECT}/serviceAccounts/svc"),
            ("projects/p/serviceAccounts/s", "projects/p/serviceAccounts/s"),
        ],
    )
    def test_delete_user(self, sdk, ident, expected):
        """delete_user issues the delete for the expanded name and reports it."""
        c, iam, _ = sdk
        out = c.delete_user(ident)
        iam.DeleteServiceAccountRequest.assert_called_once_with(name=expected)
        c.iam_client.delete_service_account.assert_called_once()
        assert expected in out["message"]

    @pytest.mark.parametrize(
        "exc", [google_exceptions.NotFound("gone"), RuntimeError("z")]
    )
    def test_delete_user_error(self, sdk, exc):
        """delete_user wraps provider failures."""
        c, _, _ = sdk
        c.iam_client.delete_service_account.side_effect = exc
        with pytest.raises(Exception, match="GCP IAM delete user error"):
            c.delete_user("svc")

    def test_update_user_sets_fields(self, sdk):
        """update_user applies display_name and description then patches."""
        c, iam, _ = sdk
        sa = _sa()
        c.iam_client.get_service_account.return_value = sa
        c.iam_client.patch_service_account.return_value = sa
        c.update_user("svc", display_name="New", description="newdesc")
        assert sa.display_name == "New" and sa.description == "newdesc"
        iam.PatchServiceAccountRequest.assert_called_once_with(service_account=sa)

    def test_update_user_no_changes_keeps_fields(self, sdk):
        """update_user without values leaves existing fields untouched."""
        c, _, _ = sdk
        sa = _sa(display_name="Keep")
        c.iam_client.get_service_account.return_value = sa
        c.iam_client.patch_service_account.return_value = sa
        c.update_user("projects/p/serviceAccounts/s")
        assert sa.display_name == "Keep" and sa.description == "d"

    @pytest.mark.parametrize(
        "exc", [google_exceptions.NotFound("gone"), RuntimeError("z")]
    )
    def test_update_user_error(self, sdk, exc):
        """update_user wraps provider failures."""
        c, _, _ = sdk
        c.iam_client.get_service_account.side_effect = exc
        with pytest.raises(Exception, match="GCP IAM update user error"):
            c.update_user("svc", display_name="x")


class TestRoles:
    """Custom role operations."""

    def test_list_roles(self, sdk):
        """list_roles normalises roles and keeps the pagination token."""
        c, iam, _ = sdk
        c.iam_client.list_roles.return_value = SimpleNamespace(
            roles=[_role()], next_page_token="n"
        )
        out = c.list_roles(limit=2, next_token="t")
        assert out["roles"][0]["name"] == "custom"
        assert out["next_token"] == "n" and out["truncated"] is True
        assert iam.ListRolesRequest.call_args.kwargs["page_size"] == 2

    def test_list_roles_last_page(self, sdk):
        """No token means a complete listing."""
        c, _, _ = sdk
        c.iam_client.list_roles.return_value = SimpleNamespace(
            roles=[], next_page_token=""
        )
        assert c.list_roles()["truncated"] is False

    @pytest.mark.parametrize(
        "exc", [google_exceptions.Forbidden("f"), RuntimeError("z")]
    )
    def test_list_roles_error(self, sdk, exc):
        """list_roles wraps provider failures."""
        c, _, _ = sdk
        c.iam_client.list_roles.side_effect = exc
        with pytest.raises(Exception, match="GCP IAM list roles error"):
            c.list_roles()

    @pytest.mark.parametrize(
        "ident,expected",
        [
            ("custom", f"projects/{PROJECT}/roles/custom"),
            ("projects/a/roles/b", "projects/a/roles/b"),
            ("organizations/1/roles/b", "organizations/1/roles/b"),
        ],
    )
    def test_get_role_resource_name(self, sdk, ident, expected):
        """get_role leaves project/org names alone and expands bare ids."""
        c, iam, _ = sdk
        c.iam_client.get_role.return_value = _role(stage=SimpleNamespace(name="GA"))
        out = c.get_role(ident)
        iam.GetRoleRequest.assert_called_once_with(name=expected)
        assert out["id"] == f"projects/{PROJECT}/roles/custom"

    @pytest.mark.parametrize(
        "exc", [google_exceptions.NotFound("n"), RuntimeError("z")]
    )
    def test_get_role_error(self, sdk, exc):
        """get_role wraps provider failures."""
        c, _, _ = sdk
        c.iam_client.get_role.side_effect = exc
        with pytest.raises(Exception, match="GCP IAM get role error"):
            c.get_role("x")

    def test_create_role_builds_role_id(self, sdk):
        """create_role derives a safe role_id and forwards permissions."""
        c, iam, _ = sdk
        c.iam_client.create_role.return_value = _role()
        c.create_role("My Fancy-Role", permissions=["x.y.z"])
        assert iam.CreateRoleRequest.call_args.kwargs["role_id"] == "my_fancy_role"
        role_kw = iam.Role.call_args.kwargs
        assert role_kw["included_permissions"] == ["x.y.z"]
        assert role_kw["description"] == "Custom role: My Fancy-Role"

    def test_create_role_explicit_description(self, sdk):
        """An explicit description overrides the default text."""
        c, iam, _ = sdk
        c.iam_client.create_role.return_value = _role()
        c.create_role("r", description="mine")
        assert iam.Role.call_args.kwargs["description"] == "mine"

    @pytest.mark.parametrize(
        "exc", [google_exceptions.AlreadyExists("a"), RuntimeError("z")]
    )
    def test_create_role_error(self, sdk, exc):
        """create_role wraps provider failures."""
        c, _, _ = sdk
        c.iam_client.create_role.side_effect = exc
        with pytest.raises(Exception, match="GCP IAM create role error"):
            c.create_role("r")

    @pytest.mark.parametrize(
        "ident,expected",
        [
            ("custom", f"projects/{PROJECT}/roles/custom"),
            ("projects/a/roles/b", "projects/a/roles/b"),
        ],
    )
    def test_delete_role(self, sdk, ident, expected):
        """delete_role expands ids and reports the removal."""
        c, iam, _ = sdk
        out = c.delete_role(ident)
        iam.DeleteRoleRequest.assert_called_once_with(name=expected)
        assert expected in out["message"]

    @pytest.mark.parametrize(
        "exc", [google_exceptions.NotFound("n"), RuntimeError("z")]
    )
    def test_delete_role_error(self, sdk, exc):
        """delete_role wraps provider failures."""
        c, _, _ = sdk
        c.iam_client.delete_role.side_effect = exc
        with pytest.raises(Exception, match="GCP IAM delete role error"):
            c.delete_role("r")

    def test_update_role_applies_fields(self, sdk):
        """update_role writes description and permissions before updating."""
        c, iam, _ = sdk
        role = _role()
        c.iam_client.get_role.return_value = role
        c.iam_client.update_role.return_value = role
        c.update_role("r", description="new", permissions=["p.q"])
        assert role.description == "new" and role.included_permissions == ["p.q"]
        iam.UpdateRoleRequest.assert_called_once()

    def test_update_role_without_changes(self, sdk):
        """update_role with nothing to change keeps the existing role content."""
        c, _, _ = sdk
        role = _role()
        c.iam_client.get_role.return_value = role
        c.iam_client.update_role.return_value = role
        c.update_role("projects/a/roles/b")
        assert role.description == "desc" and role.included_permissions == ["a.b"]

    @pytest.mark.parametrize(
        "exc", [google_exceptions.NotFound("n"), RuntimeError("z")]
    )
    def test_update_role_error(self, sdk, exc):
        """update_role wraps provider failures."""
        c, _, _ = sdk
        c.iam_client.get_role.side_effect = exc
        with pytest.raises(Exception, match="GCP IAM update role error"):
            c.update_role("r")


class TestPolicies:
    """Project IAM policy bindings."""

    def test_list_policies(self, sdk):
        """Bindings become policy dicts, with conditions rendered as expressions."""
        c, _, _ = sdk
        c.resource_manager.get_iam_policy.return_value = SimpleNamespace(
            bindings=[
                _binding("roles/viewer", ["user:a"]),
                _binding(
                    "roles/editor", ["user:b"], SimpleNamespace(expression="x > 1")
                ),
            ]
        )
        out = c.list_policies()
        assert [p["role"] for p in out["policies"]] == ["roles/viewer", "roles/editor"]
        assert out["policies"][0]["condition"] is None
        assert out["policies"][1]["condition"] == "x > 1"
        assert out["policies"][0]["id"] == f"projects/{PROJECT}/bindings/roles/viewer"

    def test_list_policies_without_resource_manager(self, sdk):
        """Without a resource manager the listing is empty with an explanation."""
        c, _, _ = sdk
        c.resource_manager = None
        out = c.list_policies()
        assert out["policies"] == [] and "not available" in out["message"]

    @pytest.mark.parametrize(
        "exc", [google_exceptions.PermissionDenied("p"), RuntimeError("z")]
    )
    def test_list_policies_error(self, sdk, exc):
        """list_policies wraps provider failures."""
        c, _, _ = sdk
        c.resource_manager.get_iam_policy.side_effect = exc
        with pytest.raises(Exception, match="GCP IAM list policies error"):
            c.list_policies()

    def test_get_policy_found(self, sdk):
        """get_policy returns the matching binding with members."""
        c, _, _ = sdk
        c.resource_manager.get_iam_policy.return_value = SimpleNamespace(
            bindings=[
                _binding("roles/a", ["u1"]),
                _binding("roles/b", ["u2"], SimpleNamespace(expression="e")),
            ]
        )
        out = c.get_policy("roles/b")
        assert out["members"] == ["u2"] and out["condition"] == "e"

    def test_get_policy_without_condition(self, sdk):
        """A binding without a condition reports condition None."""
        c, _, _ = sdk
        c.resource_manager.get_iam_policy.return_value = SimpleNamespace(
            bindings=[_binding("roles/a", ["u1"])]
        )
        assert c.get_policy("roles/a")["condition"] is None

    def test_get_policy_missing_binding(self, sdk):
        """An unknown role raises a not-found style error."""
        c, _, _ = sdk
        c.resource_manager.get_iam_policy.return_value = SimpleNamespace(bindings=[])
        with pytest.raises(Exception, match="roles/zzz not found"):
            c.get_policy("roles/zzz")

    def test_get_policy_without_resource_manager(self, sdk):
        """Missing resource manager is a hard error for get_policy."""
        c, _, _ = sdk
        c.resource_manager = None
        with pytest.raises(Exception, match="Resource Manager client not available"):
            c.get_policy("roles/a")

    def test_get_policy_google_error(self, sdk):
        """Google API errors from get_policy are wrapped."""
        c, _, _ = sdk
        c.resource_manager.get_iam_policy.side_effect = google_exceptions.Forbidden("f")
        with pytest.raises(Exception, match="GCP IAM get policy error"):
            c.get_policy("roles/a")

    def test_create_policy_appends_binding(self, sdk):
        """create_policy appends a binding and writes the policy back."""
        c, _, rm = sdk
        policy = SimpleNamespace(bindings=[])
        c.resource_manager.get_iam_policy.return_value = policy
        out = c.create_policy(
            "roles/viewer", {"members": ["user:a"], "condition": None}
        )
        assert len(policy.bindings) == 1
        rm.Binding.assert_called_once_with(
            role="roles/viewer", members=["user:a"], condition=None
        )
        rm.SetIamPolicyRequest.assert_called_once()
        c.resource_manager.set_iam_policy.assert_called_once()
        assert out["members"] == ["user:a"]
        assert out["message"].startswith("Policy binding created")

    def test_create_policy_without_resource_manager(self, sdk):
        """create_policy requires a resource manager."""
        c, _, _ = sdk
        c.resource_manager = None
        with pytest.raises(Exception, match="GCP IAM create policy error"):
            c.create_policy("roles/x", {})

    def test_create_policy_google_error(self, sdk):
        """Google API errors from set_iam_policy are wrapped."""
        c, _, _ = sdk
        c.resource_manager.get_iam_policy.return_value = SimpleNamespace(bindings=[])
        c.resource_manager.set_iam_policy.side_effect = google_exceptions.Aborted("a")
        with pytest.raises(Exception, match="GCP IAM create policy error"):
            c.create_policy("roles/x", {"members": []})

    def test_delete_policy_removes_role_bindings(self, sdk):
        """delete_policy drops every binding of the role and keeps the others."""
        c, rm_, _ = sdk[0], sdk[1], sdk[2]
        policy = SimpleNamespace(
            bindings=[_binding("roles/a", ["u"]), _binding("roles/b", ["v"])]
        )
        c.resource_manager.get_iam_policy.return_value = policy
        out = c.delete_policy("roles/a")
        assert [b.role for b in policy.bindings] == ["roles/b"]
        c.resource_manager.set_iam_policy.assert_called_once()
        assert "roles/a" in out["message"]

    def test_delete_policy_without_resource_manager(self, sdk):
        """delete_policy requires a resource manager."""
        c, _, _ = sdk
        c.resource_manager = None
        with pytest.raises(Exception, match="GCP IAM delete policy error"):
            c.delete_policy("roles/a")

    def test_delete_policy_google_error(self, sdk):
        """Google API errors from delete_policy are wrapped."""
        c, _, _ = sdk
        c.resource_manager.get_iam_policy.side_effect = google_exceptions.NotFound("n")
        with pytest.raises(Exception, match="GCP IAM delete policy error"):
            c.delete_policy("roles/a")


class TestAttachments:
    """Role/member attachment through policy bindings."""

    @pytest.mark.parametrize(
        "ident,expected",
        [
            ("svc", f"serviceAccount:svc@{PROJECT}.iam.gserviceaccount.com"),
            ("svc@x.com", "serviceAccount:svc@x.com"),
            ("serviceAccount:svc@x.com", "serviceAccount:svc@x.com"),
        ],
    )
    def test_attach_creates_binding_when_absent(self, sdk, ident, expected):
        """attach adds a new binding for the normalised member id."""
        c, rm, _ = sdk[0], sdk[2], None
        policy = SimpleNamespace(bindings=[_binding("roles/other", ["u"])])
        c.resource_manager.get_iam_policy.return_value = policy
        out = c.attach_policy_to_user(ident, "roles/new")
        assert len(policy.bindings) == 2
        rm.Binding.assert_called_with(role="roles/new", members=[expected])
        assert expected in out["message"]

    def test_attach_adds_member_to_existing_binding(self, sdk):
        """attach appends the member to an existing binding for the role."""
        c, _, _ = sdk
        member = f"serviceAccount:s@{PROJECT}.iam.gserviceaccount.com"
        b = _binding("roles/a", ["user:x"])
        c.resource_manager.get_iam_policy.return_value = SimpleNamespace(bindings=[b])
        c.attach_policy_to_user("s", "roles/a")
        assert b.members == ["user:x", member]

    def test_attach_existing_member_is_idempotent(self, sdk):
        """attach does not duplicate a member already bound."""
        c, _, _ = sdk
        member = "serviceAccount:s@x.com"
        b = _binding("roles/a", [member])
        c.resource_manager.get_iam_policy.return_value = SimpleNamespace(bindings=[b])
        c.attach_policy_to_user(member, "roles/a")
        assert b.members == [member]

    def test_attach_without_resource_manager(self, sdk):
        """attach requires a resource manager."""
        c, _, _ = sdk
        c.resource_manager = None
        with pytest.raises(Exception, match="attach policy to user error"):
            c.attach_policy_to_user("s", "roles/a")

    def test_attach_google_error(self, sdk):
        """Google API errors from attach are wrapped."""
        c, _, _ = sdk
        c.resource_manager.get_iam_policy.side_effect = google_exceptions.Forbidden("f")
        with pytest.raises(Exception, match="attach policy to user error"):
            c.attach_policy_to_user("s", "roles/a")

    @pytest.mark.parametrize(
        "ident,expected",
        [
            ("svc", f"serviceAccount:svc@{PROJECT}.iam.gserviceaccount.com"),
            ("svc@x.com", "serviceAccount:svc@x.com"),
            ("serviceAccount:svc@x.com", "serviceAccount:svc@x.com"),
        ],
    )
    def test_detach_removes_member(self, sdk, ident, expected):
        """detach removes the member from the matching binding only."""
        c, _, _ = sdk
        b = _binding("roles/a", [expected, "user:keep"])
        other = _binding("roles/b", [expected])
        c.resource_manager.get_iam_policy.return_value = SimpleNamespace(
            bindings=[b, other]
        )
        c.detach_policy_from_user(ident, "roles/a")
        assert b.members == ["user:keep"] and other.members == [expected]
        c.resource_manager.set_iam_policy.assert_called_once()

    def test_detach_member_not_bound(self, sdk):
        """detach is a no-op for a member that is not in the binding."""
        c, _, _ = sdk
        b = _binding("roles/a", ["user:keep"])
        c.resource_manager.get_iam_policy.return_value = SimpleNamespace(bindings=[b])
        out = c.detach_policy_from_user("s@x.com", "roles/a")
        assert b.members == ["user:keep"] and "detached" in out["message"]

    def test_detach_without_resource_manager(self, sdk):
        """detach requires a resource manager."""
        c, _, _ = sdk
        c.resource_manager = None
        with pytest.raises(Exception, match="detach policy from user error"):
            c.detach_policy_from_user("s", "roles/a")

    def test_detach_google_error(self, sdk):
        """Google API errors from detach are wrapped."""
        c, _, _ = sdk
        c.resource_manager.get_iam_policy.side_effect = google_exceptions.Forbidden("f")
        with pytest.raises(Exception, match="detach policy from user error"):
            c.detach_policy_from_user("s", "roles/a")

    def test_role_policy_attachment_not_supported(self, sdk):
        """Attaching/detaching policies to roles is unsupported in GCP."""
        c, _, _ = sdk
        with pytest.raises(NotImplementedError):
            c.attach_policy_to_role("r", "p")
        with pytest.raises(NotImplementedError):
            c.detach_policy_from_role("r", "p")
        assert c.list_role_policies("r") == []

    def test_list_user_policies(self, sdk):
        """list_user_policies returns only the bindings that include the member."""
        c, _, _ = sdk
        member = f"serviceAccount:s@{PROJECT}.iam.gserviceaccount.com"
        c.resource_manager.get_iam_policy.return_value = SimpleNamespace(
            bindings=[_binding("roles/a", [member]), _binding("roles/b", ["user:z"])]
        )
        out = c.list_user_policies("s")
        assert out == [{"name": "roles/a", "role": "roles/a", "arn": "roles/a"}]

    @pytest.mark.parametrize("ident", ["s@x.com", "serviceAccount:s@x.com"])
    def test_list_user_policies_member_forms(self, sdk, ident):
        """Email and fully-qualified member forms both resolve."""
        c, _, _ = sdk
        c.resource_manager.get_iam_policy.return_value = SimpleNamespace(
            bindings=[_binding("roles/a", ["serviceAccount:s@x.com"])]
        )
        assert len(c.list_user_policies(ident)) == 1

    def test_list_user_policies_without_resource_manager(self, sdk):
        """Without a resource manager the list is empty."""
        c, _, _ = sdk
        c.resource_manager = None
        assert c.list_user_policies("s") == []

    def test_list_user_policies_google_error(self, sdk):
        """Google API errors are wrapped."""
        c, _, _ = sdk
        c.resource_manager.get_iam_policy.side_effect = google_exceptions.Forbidden("f")
        with pytest.raises(Exception, match="list user policies error"):
            c.list_user_policies("s")


class TestAccessKeys:
    """Service-account key lifecycle."""

    def test_create_access_key(self, sdk):
        """create_access_key decodes the key payload and returns metadata."""
        c, iam, _ = sdk
        payload = json.dumps({"client_email": "e"}).encode()
        key = SimpleNamespace(
            name="projects/p/serviceAccounts/s/keys/k123",
            private_key_data=payload,
            valid_after_time=SimpleNamespace(isoformat=lambda: "2026-01-01T00:00:00"),
        )
        c.iam_client.create_service_account_key.return_value = key
        out = c.create_access_key("svc")
        assert out["key_id"] == "k123"
        assert out["key_data"] == {"client_email": "e"}
        assert out["user"] == f"projects/{PROJECT}/serviceAccounts/svc"
        assert (
            iam.CreateServiceAccountKeyRequest.call_args.kwargs["name"] == (out["user"])
        )

    def test_create_access_key_full_name(self, sdk):
        """A full resource name is passed through unchanged."""
        c, _, _ = sdk
        key = SimpleNamespace(
            name="a/keys/k",
            private_key_data=b"{}",
            valid_after_time=SimpleNamespace(isoformat=lambda: "t"),
        )
        c.iam_client.create_service_account_key.return_value = key
        assert c.create_access_key("projects/p/serviceAccounts/s")["user"] == (
            "projects/p/serviceAccounts/s"
        )

    def test_create_access_key_bad_payload(self, sdk):
        """Undecodable key data is wrapped as an error rather than returned."""
        c, _, _ = sdk
        c.iam_client.create_service_account_key.return_value = SimpleNamespace(
            name="a/keys/k", private_key_data=b"not-json", valid_after_time=None
        )
        with pytest.raises(Exception, match="GCP IAM create access key error"):
            c.create_access_key("svc")

    def test_create_access_key_google_error(self, sdk):
        """Google API errors are wrapped."""
        c, _, _ = sdk
        c.iam_client.create_service_account_key.side_effect = (
            google_exceptions.ResourceExhausted("quota")
        )
        with pytest.raises(Exception, match="GCP IAM create access key error"):
            c.create_access_key("svc")

    def test_list_access_keys(self, sdk):
        """list_access_keys reports type/algorithm and optional validity times."""
        c, _, _ = sdk
        ts = SimpleNamespace(isoformat=lambda: "2026-02-02T00:00:00")
        k1 = SimpleNamespace(
            name="x/keys/k1",
            key_type=SimpleNamespace(name="USER_MANAGED"),
            key_algorithm=SimpleNamespace(name="KEY_ALG_RSA_2048"),
            valid_after_time=ts,
            valid_before_time=ts,
        )
        k2 = SimpleNamespace(
            name="x/keys/k2",
            key_type=SimpleNamespace(name="SYSTEM_MANAGED"),
            key_algorithm=SimpleNamespace(name="A"),
            valid_after_time=None,
            valid_before_time=None,
        )
        c.iam_client.list_service_account_keys.return_value = SimpleNamespace(
            keys=[k1, k2]
        )
        out = c.list_access_keys("svc")
        assert out[0]["key_id"] == "k1" and out[0]["valid_after"].startswith("2026")
        assert out[1]["valid_after"] is None and out[1]["valid_before"] is None

    @pytest.mark.parametrize(
        "exc", [google_exceptions.NotFound("n"), RuntimeError("z")]
    )
    def test_list_access_keys_error(self, sdk, exc):
        """list_access_keys wraps provider failures."""
        c, _, _ = sdk
        c.iam_client.list_service_account_keys.side_effect = exc
        with pytest.raises(Exception, match="GCP IAM list access keys error"):
            c.list_access_keys("svc")

    @pytest.mark.parametrize(
        "ident,prefix",
        [
            ("svc", f"projects/{PROJECT}/serviceAccounts/svc"),
            ("projects/p/serviceAccounts/s", "projects/p/serviceAccounts/s"),
        ],
    )
    def test_delete_access_key(self, sdk, ident, prefix):
        """delete_access_key targets the key under the expanded account name."""
        c, iam, _ = sdk
        out = c.delete_access_key(ident, "k9")
        iam.DeleteServiceAccountKeyRequest.assert_called_once_with(
            name=f"{prefix}/keys/k9"
        )
        assert "k9" in out["message"]

    @pytest.mark.parametrize(
        "exc", [google_exceptions.NotFound("n"), RuntimeError("z")]
    )
    def test_delete_access_key_error(self, sdk, exc):
        """delete_access_key wraps provider failures."""
        c, _, _ = sdk
        c.iam_client.delete_service_account_key.side_effect = exc
        with pytest.raises(Exception, match="GCP IAM delete access key error"):
            c.delete_access_key("svc", "k")


class TestUtilityAndBase:
    """Connectivity, sync, helpers and inherited BaseIAMProvider defaults."""

    def test_test_connection_true(self, sdk):
        """test_connection returns True when the probe call succeeds."""
        c, iam, _ = sdk
        assert c.test_connection() is True
        assert iam.ListServiceAccountsRequest.call_args.kwargs["page_size"] == 1

    def test_test_connection_false_on_error(self, sdk):
        """test_connection returns False instead of raising on provider failure."""
        c, _, _ = sdk
        c.iam_client.list_service_accounts.side_effect = RuntimeError("down")
        assert c.test_connection() is False

    def test_sync_counts(self, sdk):
        """sync_from_provider reports counts for each resource kind."""
        c, _, _ = sdk
        c.iam_client.list_service_accounts.return_value = SimpleNamespace(
            service_accounts=[_sa(), _sa()], next_page_token=""
        )
        c.iam_client.list_roles.return_value = SimpleNamespace(
            roles=[_role()], next_page_token=""
        )
        c.resource_manager.get_iam_policy.return_value = SimpleNamespace(
            bindings=[_binding("roles/a", [])]
        )
        out = c.sync_from_provider()
        assert out == {
            "users_synced": 2,
            "roles_synced": 1,
            "policies_synced": 1,
            "errors": [],
        }

    def test_sync_collects_errors_per_section(self, sdk):
        """A failure in one section is recorded without aborting the others."""
        c, _, _ = sdk
        c.iam_client.list_service_accounts.side_effect = RuntimeError("a")
        c.iam_client.list_roles.side_effect = RuntimeError("b")
        c.resource_manager.get_iam_policy.side_effect = RuntimeError("c")
        out = c.sync_from_provider()
        assert len(out["errors"]) == 3
        assert out["users_synced"] == out["roles_synced"] == 0
        assert "service accounts" in out["errors"][0]
        assert "roles" in out["errors"][1] and "policy bindings" in out["errors"][2]

    def test_sync_general_error(self, sdk):
        """An unexpected failure outside the section guards is reported."""
        c, _, _ = sdk
        with patch.object(c, "list_users", side_effect=RuntimeError("x")):
            with (
                patch.object(c, "list_roles", return_value={"roles": []}),
                patch.object(c, "list_policies", return_value={"policies": []}),
            ):
                out = c.sync_from_provider()
        assert out["errors"] == ["Error syncing service accounts: x"]

    def test_role_to_dict_without_slash(self, sdk):
        """_role_to_dict keeps names without a slash and maps a missing stage."""
        c, _, _ = sdk
        d = c._role_to_dict(_role(name="plain", stage=None))
        assert d["name"] == "plain" and d["stage"] is None

    def test_base_group_defaults(self, sdk):
        """Providers without group support return an empty list and raise on writes."""
        c, _, _ = sdk
        assert c.list_groups() == {"groups": [], "next_token": None, "supported": False}
        for call in (
            lambda: c.create_group("g"),
            lambda: c.delete_group("g"),
            lambda: c.add_user_to_group("u", "g"),
            lambda: c.remove_user_from_group("u", "g"),
        ):
            with pytest.raises(NotImplementedError, match="Groups not supported"):
                call()

    def test_base_normalize_policy_aliases(self, sdk):
        """_normalize_policy accepts both generic and AWS-style key spellings."""
        c, _, _ = sdk
        out = c._normalize_policy(
            {"PolicyId": "p1", "PolicyName": "n", "Description": "d", "Arn": "a"}
        )
        assert out == {
            "id": "p1",
            "name": "n",
            "description": "d",
            "created_at": None,
            "arn": "a",
        }
