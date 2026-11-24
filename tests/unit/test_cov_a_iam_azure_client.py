"""Unit tests for the Azure AD (Entra ID) IAM client.

The Microsoft Graph / azure-identity SDKs are not installed in the harness, so
they are replaced at the module boundary (``GraphServiceClient`` /
``ClientSecretCredential``) and the lazily-imported ``msgraph.generated.models``
modules are stubbed through ``sys.modules``.
"""

import sys
from datetime import datetime
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from apps.api.services.iam import azure_client
from apps.api.services.iam.azure_client import AzureADClient

MODULE = "apps.api.services.iam.azure_client"
CFG = {"tenant_id": "t-1", "client_id": "c-1", "client_secret": "-".join(["fake", "s"])}


class _Model(SimpleNamespace):
    """Attribute bag standing in for generated msgraph model classes."""

    def __init__(self, **kw):
        super().__init__(**kw)


def _graph_models():
    """Build fake ``msgraph.generated.models.*`` modules."""
    names = {
        "password_profile": "PasswordProfile",
        "user": "User",
        "group": "Group",
        "reference_create": "ReferenceCreate",
    }
    mods = {}
    for mod, cls in names.items():
        m = ModuleType(f"msgraph.generated.models.{mod}")
        setattr(m, cls, _Model)
        mods[f"msgraph.generated.models.{mod}"] = m
    for pkg in ("msgraph", "msgraph.generated", "msgraph.generated.models"):
        mods[pkg] = ModuleType(pkg)
    return mods


@pytest.fixture
def az():
    """Return an AzureADClient whose Graph client is a MagicMock."""
    with (
        patch(f"{MODULE}.GraphServiceClient") as graph,
        patch(f"{MODULE}.ClientSecretCredential", create=True) as cred,
        patch.dict(sys.modules, _graph_models()),
        # The class leaves several abstract IAM methods unimplemented (see the
        # xfail test below); clear them so the rest of the surface is testable.
        patch.object(AzureADClient, "__abstractmethods__", frozenset()),
    ):
        graph.return_value = MagicMock(name="graph")
        client = AzureADClient(dict(CFG))
        client._cred_cls = cred
        yield client


def _user(**kw):
    base = {
        "id": "u1",
        "user_principal_name": "a@x.com",
        "display_name": "A",
        "mail": "a@mail.com",
        "account_enabled": True,
        "created_date_time": datetime(2026, 1, 2, 3, 4, 5),
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _group(**kw):
    base = {
        "id": "g1",
        "display_name": "G",
        "description": "d",
        "created_date_time": datetime(2026, 1, 2),
    }
    base.update(kw)
    return SimpleNamespace(**base)


class TestInit:
    """Constructor behaviour."""

    def test_missing_sdk(self):
        """Without the Graph SDK the constructor raises ImportError."""
        with (
            patch(f"{MODULE}.GraphServiceClient", None),
            patch.object(AzureADClient, "__abstractmethods__", frozenset()),
        ):
            with pytest.raises(ImportError, match="Microsoft Graph SDK"):
                AzureADClient(dict(CFG))

    @pytest.mark.parametrize("missing", ["tenant_id", "client_id", "client_secret"])
    def test_missing_credentials_rejected(self, missing):
        """Each of tenant/client/secret is mandatory."""
        cfg = {k: v for k, v in CFG.items() if k != missing}
        with (
            patch(f"{MODULE}.GraphServiceClient"),
            patch.object(AzureADClient, "__abstractmethods__", frozenset()),
        ):
            with pytest.raises(ValueError, match="requires tenant_id"):
                AzureADClient(cfg)

    def test_builds_credential_and_client(self, az):
        """The client secret credential is built from config and fed to Graph."""
        az._cred_cls.assert_called_once_with(
            tenant_id="t-1", client_id="c-1", client_secret=CFG["client_secret"]
        )
        assert az.tenant_id == "t-1"

    @pytest.mark.xfail(
        strict=True,
        reason="bug: azure_client.py:19 AzureADClient never implements the abstract "
        "policy/access-key/sync methods of BaseIAMProvider, so it cannot be "
        "instantiated (TypeError)",
    )
    def test_class_is_instantiable(self):
        """AzureADClient should be constructible with valid config."""
        with (
            patch(f"{MODULE}.GraphServiceClient"),
            patch(f"{MODULE}.ClientSecretCredential", create=True),
        ):
            AzureADClient(dict(CFG))


class TestUsers:
    """User operations."""

    def test_list_users_applies_paging(self, az):
        """list_users clamps top to 999, forwards the skip token and returns next link."""
        req = SimpleNamespace(
            value=[_user(), _user(id="u2", mail=None, created_date_time=None)],
            odata_next_link="next-link",
        )
        az.client.users.get.return_value = req
        out = az.list_users(limit=5000, next_token="skip")
        assert req.top == 999 and req.skip_token == "skip"
        assert out["next_token"] == "next-link"
        assert out["users"][0]["created_at"] == "2026-01-02T03:04:05"
        assert out["users"][1]["email"] == "a@x.com"
        assert out["users"][1]["created_at"] is None
        assert out["users"][0]["provider"] == "azure_ad"

    def test_list_users_defaults(self, az):
        """Without limit/token nothing is set and a missing value list is empty."""
        req = SimpleNamespace(value=None)
        az.client.users.get.return_value = req
        out = az.list_users()
        assert out == {"users": [], "next_token": None}
        assert not hasattr(req, "top") and not hasattr(req, "skip_token")

    def test_list_users_error(self, az):
        """Graph errors are wrapped."""
        az.client.users.get.side_effect = RuntimeError("throttled")
        with pytest.raises(Exception, match="Azure AD list users error: throttled"):
            az.list_users()

    def test_get_user(self, az):
        """get_user normalises the Graph user."""
        az.client.users.by_user_id.return_value.get.return_value = _user()
        out = az.get_user("u1")
        az.client.users.by_user_id.assert_called_once_with("u1")
        assert out["id"] == "u1" and out["enabled"] is True

    def test_get_user_not_found(self, az):
        """A not-found from Graph is wrapped."""
        az.client.users.by_user_id.return_value.get.side_effect = KeyError("404")
        with pytest.raises(Exception, match="Azure AD get user error"):
            az.get_user("nope")

    def test_create_user_defaults(self, az):
        """create_user posts a user with defaults and a forced password change."""
        az.client.users.post.return_value = _user()
        az.create_user("bob@x.com")
        posted = az.client.users.post.call_args.args[0]
        assert posted.user_principal_name == "bob@x.com"
        assert posted.display_name == "bob@x.com"
        assert posted.mail_nickname == "bob"
        assert posted.account_enabled is True
        assert posted.password_profile.force_change_password_next_sign_in is True

    def test_create_user_overrides(self, az):
        """Explicit display name, password and enabled flag are honoured."""
        az.client.users.post.return_value = _user()
        pw = "-".join(["pw", "x"])
        az.create_user("bob@x.com", display_name="Bob", password=pw, enabled=False)
        posted = az.client.users.post.call_args.args[0]
        assert posted.display_name == "Bob"
        assert posted.password_profile.password == pw
        assert posted.account_enabled is False

    def test_create_user_error(self, az):
        """Graph failures during creation are wrapped."""
        az.client.users.post.side_effect = RuntimeError("exists")
        with pytest.raises(Exception, match="Azure AD create user error: exists"):
            az.create_user("bob@x.com")

    def test_delete_user(self, az):
        """delete_user deletes by id and reports success."""
        assert az.delete_user("u1") == {"success": True, "user_id": "u1"}
        az.client.users.by_user_id.return_value.delete.assert_called_once()

    def test_delete_user_error(self, az):
        """Delete failures are wrapped."""
        az.client.users.by_user_id.return_value.delete.side_effect = RuntimeError("x")
        with pytest.raises(Exception, match="Azure AD delete user error"):
            az.delete_user("u1")

    @pytest.mark.parametrize(
        "kwargs,attr,value",
        [
            ({"display_name": "New"}, "display_name", "New"),
            ({"enabled": False}, "account_enabled", False),
        ],
    )
    def test_update_user_sets_only_given_fields(self, az, kwargs, attr, value):
        """update_user patches just the provided fields then re-reads the user."""
        by_id = az.client.users.by_user_id.return_value
        by_id.get.return_value = _user()
        az.update_user("u1", **kwargs)
        patched = by_id.patch.call_args.args[0]
        assert getattr(patched, attr) == value
        assert len(vars(patched)) == 1
        by_id.get.assert_called_once()

    def test_update_user_error(self, az):
        """Patch failures are wrapped."""
        az.client.users.by_user_id.return_value.patch.side_effect = RuntimeError("x")
        with pytest.raises(Exception, match="Azure AD update user error"):
            az.update_user("u1", display_name="n")


class TestRoles:
    """Groups-as-roles operations."""

    def test_list_roles_paging(self, az):
        """list_roles clamps the page size and normalises groups."""
        req = SimpleNamespace(
            value=[_group(), _group(id="g2", description=None, created_date_time=None)],
            odata_next_link="n",
        )
        az.client.groups.get.return_value = req
        out = az.list_roles(limit=10, next_token="tk")
        assert req.top == 10 and req.skip_token == "tk"
        assert out["next_token"] == "n"
        assert out["roles"][1]["description"] == ""
        assert out["roles"][1]["created_at"] is None
        assert out["roles"][0]["created_at"] == "2026-01-02T00:00:00"

    def test_list_roles_defaults(self, az):
        """An empty/None group list yields no roles."""
        az.client.groups.get.return_value = SimpleNamespace(value=None)
        assert az.list_roles() == {"roles": [], "next_token": None}

    def test_list_roles_error(self, az):
        """list_roles failures are wrapped."""
        az.client.groups.get.side_effect = RuntimeError("x")
        with pytest.raises(Exception, match="Azure AD list roles error"):
            az.list_roles()

    def test_get_role(self, az):
        """get_role fetches by group id."""
        az.client.groups.by_group_id.return_value.get.return_value = _group()
        assert az.get_role("g1")["name"] == "G"

    def test_get_role_error(self, az):
        """get_role failures are wrapped."""
        az.client.groups.by_group_id.return_value.get.side_effect = RuntimeError("x")
        with pytest.raises(Exception, match="Azure AD get role error"):
            az.get_role("g1")

    def test_create_role(self, az):
        """create_role posts a security-enabled, non-mail group."""
        az.client.groups.post.return_value = _group()
        az.create_role("Team Admins", description="desc")
        posted = az.client.groups.post.call_args.args[0]
        assert posted.display_name == "Team Admins"
        assert posted.mail_nickname == "team_admins"
        assert posted.mail_enabled is False and posted.security_enabled is True
        assert posted.description == "desc"

    def test_create_role_error(self, az):
        """create_role failures are wrapped."""
        az.client.groups.post.side_effect = RuntimeError("x")
        with pytest.raises(Exception, match="Azure AD create role error"):
            az.create_role("r")

    def test_delete_role(self, az):
        """delete_role removes the group and reports success."""
        assert az.delete_role("g1") == {"success": True, "role_id": "g1"}

    def test_delete_role_error(self, az):
        """delete_role failures are wrapped."""
        az.client.groups.by_group_id.return_value.delete.side_effect = RuntimeError("x")
        with pytest.raises(Exception, match="Azure AD delete role error"):
            az.delete_role("g1")


class TestAssignments:
    """Group membership operations."""

    def test_assign_role_to_user(self, az):
        """assign posts a directory-object reference to the group's members."""
        out = az.assign_role_to_user("u1", "g1")
        az.client.groups.by_group_id.assert_called_once_with("g1")
        ref = az.client.groups.by_group_id.return_value.members.ref.post.call_args.args[
            0
        ]
        assert ref.odata_id.endswith("/users/u1")
        assert out == {"success": True, "user_id": "u1", "role_id": "g1"}

    def test_assign_role_error(self, az):
        """assign failures are wrapped."""
        members = az.client.groups.by_group_id.return_value.members
        members.ref.post.side_effect = RuntimeError("x")
        with pytest.raises(Exception, match="Azure AD assign role error"):
            az.assign_role_to_user("u1", "g1")

    def test_remove_role_from_user(self, az):
        """remove deletes the membership reference."""
        out = az.remove_role_from_user("u1", "g1")
        members = az.client.groups.by_group_id.return_value.members
        members.by_directory_object_id.assert_called_once_with("u1")
        members.by_directory_object_id.return_value.ref.delete.assert_called_once()
        assert out["success"] is True

    def test_remove_role_error(self, az):
        """remove failures are wrapped."""
        members = az.client.groups.by_group_id.return_value.members
        members.by_directory_object_id.side_effect = RuntimeError("x")
        with pytest.raises(Exception, match="Azure AD remove role error"):
            az.remove_role_from_user("u1", "g1")

    def test_list_user_roles(self, az):
        """list_user_roles normalises member_of groups."""
        az.client.users.by_user_id.return_value.member_of.get.return_value = (
            SimpleNamespace(value=[_group()])
        )
        assert az.list_user_roles("u1")["roles"][0]["id"] == "g1"

    def test_list_user_roles_empty_and_error(self, az):
        """No memberships gives an empty list; Graph errors are wrapped."""
        by_id = az.client.users.by_user_id.return_value
        by_id.member_of.get.return_value = SimpleNamespace(value=None)
        assert az.list_user_roles("u1") == {"roles": []}
        by_id.member_of.get.side_effect = RuntimeError("x")
        with pytest.raises(Exception, match="Azure AD list user roles error"):
            az.list_user_roles("u1")


class TestConnection:
    """Connectivity probe."""

    def test_connection_ok(self, az):
        """test_connection is True when a 1-item list succeeds."""
        az.client.users.get.return_value = SimpleNamespace(value=[])
        assert az.test_connection() is True
        assert az.client.users.get.return_value.top == 1

    def test_connection_failure(self, az):
        """test_connection returns False instead of raising on failure."""
        az.client.users.get.side_effect = RuntimeError("down")
        assert az.test_connection() is False

    def test_module_level_logger_name(self):
        """The module logs under its own dotted name for operator filtering."""
        assert azure_client.logger.name == MODULE
