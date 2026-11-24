"""Additional unit tests for KubernetesRBACClient: error translation, create/update branches, bindings, tokens.

Only the kubernetes API handles are mocked (the client boundary); the real V1* model classes are used.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("kubernetes")
from kubernetes import client as k8s  # noqa: E402
from kubernetes.client.rest import ApiException  # noqa: E402

from apps.api.services.iam.k8s_client import KubernetesRBACClient  # noqa: E402

MODULE = "apps.api.services.iam.k8s_client"
NOW = datetime(2024, 5, 6, 7, 8, 9, tzinfo=timezone.utc)


def _make(namespace="team-a"):
    """Return a KubernetesRBACClient with mocked API handles."""
    with patch(f"{MODULE}.config"):
        c = KubernetesRBACClient({"namespace": namespace})
    c.core_v1 = MagicMock()
    c.rbac_v1 = MagicMock()
    return c


def _meta(
    name="n", uid="u1", labels=None, annotations=None, ts=None, namespace="team-a"
):
    """Build a k8s-like metadata namespace."""
    return SimpleNamespace(
        name=name,
        uid=uid,
        namespace=namespace,
        creation_timestamp=ts,
        labels=labels,
        annotations=annotations,
    )


def _sa(**kw):
    """Build a service-account-like object."""
    return SimpleNamespace(metadata=_meta(**kw), secrets=None)


def _listing(items, cont=None):
    """Build a list response with optional continue token."""
    md = SimpleNamespace()
    if cont is not None:
        md._continue = cont
    return SimpleNamespace(items=items, metadata=md)


def _rb(name="rb", role="viewer", subjects=None, ts=None):
    """Build a role-binding-like object."""
    return SimpleNamespace(
        metadata=_meta(name=name, ts=ts),
        role_ref=SimpleNamespace(
            kind="Role", name=role, api_group="rbac.authorization.k8s.io"
        ),
        subjects=subjects,
    )


def _api_exc(reason="Forbidden"):
    """Create an ApiException with a reason."""
    return ApiException(status=403, reason=reason)


def test_init_without_kubernetes_raises_import_error():
    """Constructor raises ImportError when the kubernetes package is missing."""
    with patch(f"{MODULE}.client", None):
        with pytest.raises(ImportError, match="kubernetes is required"):
            KubernetesRBACClient({})


def test_init_defaults_namespace_and_loads_kubeconfig():
    """Default namespace is 'default' and kubeconfig loading is attempted."""
    with patch(f"{MODULE}.config") as cfg:
        c = KubernetesRBACClient({})
    assert c.namespace == "default"
    cfg.load_kube_config.assert_called_once_with(config_file=None, context=None)


ERROR_CASES = [
    ("list_users", (), "core_v1", "list_namespaced_service_account", "list users"),
    ("get_user", ("u",), "core_v1", "read_namespaced_service_account", "get user"),
    (
        "create_user",
        ("u",),
        "core_v1",
        "create_namespaced_service_account",
        "create user",
    ),
    (
        "delete_user",
        ("u",),
        "core_v1",
        "delete_namespaced_service_account",
        "delete user",
    ),
    (
        "update_user",
        ("u",),
        "core_v1",
        "read_namespaced_service_account",
        "update user",
    ),
    ("list_roles", (), "rbac_v1", "list_namespaced_role", "list roles"),
    ("get_role", ("r",), "rbac_v1", "read_namespaced_role", "get role"),
    ("create_role", ("r",), "rbac_v1", "create_namespaced_role", "create role"),
    ("delete_role", ("r",), "rbac_v1", "delete_namespaced_role", "delete role"),
    ("update_role", ("r",), "rbac_v1", "read_namespaced_role", "update role"),
    ("list_policies", (), "rbac_v1", "list_namespaced_role_binding", "list policies"),
    ("get_policy", ("p",), "rbac_v1", "read_namespaced_role_binding", "get policy"),
    (
        "create_policy",
        ("p", {"role_ref": {"name": "r"}}),
        "rbac_v1",
        "create_namespaced_role_binding",
        "create policy",
    ),
    (
        "delete_policy",
        ("p",),
        "rbac_v1",
        "delete_namespaced_role_binding",
        "delete policy",
    ),
    (
        "detach_policy_from_user",
        ("u", "r"),
        "rbac_v1",
        "delete_namespaced_role_binding",
        "detach policy from user",
    ),
    (
        "list_user_policies",
        ("u",),
        "rbac_v1",
        "list_namespaced_role_binding",
        "list user policies",
    ),
    (
        "create_access_key",
        ("u",),
        "core_v1",
        "create_namespaced_service_account_token",
        "create access key",
    ),
    (
        "list_access_keys",
        ("u",),
        "core_v1",
        "read_namespaced_service_account",
        "list access keys",
    ),
    (
        "delete_access_key",
        ("u", "k"),
        "core_v1",
        "delete_namespaced_secret",
        "delete access key",
    ),
]


@pytest.mark.parametrize("method,args,api,fn,prefix", ERROR_CASES)
def test_api_exception_translated_with_reason(method, args, api, fn, prefix):
    """ApiException surfaces as Exception that carries the API reason."""
    c = _make()
    getattr(getattr(c, api), fn).side_effect = _api_exc("quota exceeded")
    with pytest.raises(Exception, match=f"K8s RBAC {prefix} error: quota exceeded"):
        getattr(c, method)(*args)


@pytest.mark.parametrize("method,args,api,fn,prefix", ERROR_CASES)
def test_generic_exception_wrapped(method, args, api, fn, prefix):
    """A non-API exception is wrapped with the operation prefix."""
    c = _make()
    getattr(getattr(c, api), fn).side_effect = RuntimeError("kaput")
    with pytest.raises(Exception, match=f"K8s RBAC {prefix} error: kaput"):
        getattr(c, method)(*args)


@pytest.mark.parametrize(
    "method,api,fn",
    [
        ("list_users", "core_v1", "list_namespaced_service_account"),
        ("list_roles", "rbac_v1", "list_namespaced_role"),
        ("list_policies", "rbac_v1", "list_namespaced_role_binding"),
    ],
)
def test_list_reports_continue_token_and_truncation(method, api, fn):
    """A continue token on the metadata becomes next_token and truncated=True."""
    c = _make()
    getattr(getattr(c, api), fn).return_value = _listing([], cont="tok-2")
    out = getattr(c, method)(limit=10)
    assert out["next_token"] == "tok-2"
    assert out["truncated"] is True
    assert getattr(getattr(c, api), fn).call_args.kwargs["limit"] == 10
    assert getattr(getattr(c, api), fn).call_args.kwargs["namespace"] == "team-a"


@pytest.mark.parametrize(
    "method,api,fn",
    [
        ("list_users", "core_v1", "list_namespaced_service_account"),
        ("list_roles", "rbac_v1", "list_namespaced_role"),
        ("list_policies", "rbac_v1", "list_namespaced_role_binding"),
    ],
)
def test_list_without_continue_attribute_is_not_truncated(method, api, fn):
    """Metadata lacking _continue yields next_token None and truncated False."""
    c = _make()
    getattr(getattr(c, api), fn).return_value = _listing([])
    out = getattr(c, method)()
    assert out["next_token"] is None
    assert out["truncated"] is False


def test_get_user_normalizes_service_account():
    """get_user maps the service account onto the normalized user shape."""
    c = _make()
    c.core_v1.read_namespaced_service_account.return_value = _sa(
        name="bot", uid="U9", ts=NOW
    )
    out = c.get_user("bot")
    assert out["id"] == "U9"
    assert out["username"] == "bot"
    assert out["created_at"] == NOW.isoformat()
    c.core_v1.read_namespaced_service_account.assert_called_once_with(
        name="bot", namespace="team-a"
    )


def test_create_user_builds_service_account_with_labels_and_description():
    """create_user sends labels and a description annotation in the chosen namespace."""
    c = _make()
    c.core_v1.create_namespaced_service_account.return_value = _sa(name="bot")
    c.create_user("bot", display_name="A bot", tags={"app": "x"})
    body = c.core_v1.create_namespaced_service_account.call_args.kwargs["body"]
    assert body.metadata.name == "bot"
    assert body.metadata.labels == {"app": "x"}
    assert body.metadata.annotations == {"description": "A bot"}
    assert (
        c.core_v1.create_namespaced_service_account.call_args.kwargs["namespace"]
        == "team-a"
    )


def test_create_user_defaults_description_to_username():
    """Without display_name or tags the description falls back to the username."""
    c = _make()
    c.core_v1.create_namespaced_service_account.return_value = _sa(name="bot")
    c.create_user("bot")
    body = c.core_v1.create_namespaced_service_account.call_args.kwargs["body"]
    assert body.metadata.annotations == {"description": "bot"}
    assert body.metadata.labels == {}


def test_delete_user_message_and_call():
    """delete_user deletes within the client's namespace and confirms."""
    c = _make()
    out = c.delete_user("bot")
    c.core_v1.delete_namespaced_service_account.assert_called_once_with(
        name="bot", namespace="team-a"
    )
    assert "bot deleted successfully" in out["message"]


def test_update_user_sets_labels_and_creates_annotations():
    """update_user replaces labels and creates the annotation map when absent."""
    c = _make()
    sa = _sa(name="bot", labels=None, annotations=None)
    c.core_v1.read_namespaced_service_account.return_value = sa
    c.core_v1.patch_namespaced_service_account.return_value = sa
    c.update_user("bot", display_name="New", tags={"a": "b"})
    assert sa.metadata.labels == {"a": "b"}
    assert sa.metadata.annotations == {"description": "New"}
    c.core_v1.patch_namespaced_service_account.assert_called_once()


def test_update_user_preserves_existing_annotations():
    """An existing annotation map is updated in place, other keys kept."""
    c = _make()
    sa = _sa(annotations={"keep": "1"})
    c.core_v1.read_namespaced_service_account.return_value = sa
    c.core_v1.patch_namespaced_service_account.return_value = sa
    c.update_user("n", display_name="D")
    assert sa.metadata.annotations == {"keep": "1", "description": "D"}


def test_update_user_without_changes_leaves_metadata_untouched():
    """No tags/display_name means the metadata is patched unchanged."""
    c = _make()
    sa = _sa(labels={"x": "y"})
    c.core_v1.read_namespaced_service_account.return_value = sa
    c.core_v1.patch_namespaced_service_account.return_value = sa
    c.update_user("n")
    assert sa.metadata.labels == {"x": "y"}
    assert sa.metadata.annotations is None


def _role(rules=None, **kw):
    """Build a role-like object."""
    return SimpleNamespace(metadata=_meta(**kw), rules=rules)


def _rule(**kw):
    """Build a policy-rule-like object."""
    base = {
        "api_groups": ["apps"],
        "resources": ["pods"],
        "verbs": ["get"],
        "resource_names": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def test_get_role_converts_rules():
    """get_role reports rules with empty defaults for missing fields."""
    c = _make()
    c.rbac_v1.read_namespaced_role.return_value = _role(
        rules=[_rule(api_groups=None, resource_names=None)], name="viewer"
    )
    out = c.get_role("viewer")
    assert out["name"] == "viewer"
    assert out["id"] == "u1"
    c.rbac_v1.read_namespaced_role.assert_called_once_with(
        name="viewer", namespace="team-a"
    )


def test_create_role_converts_rules_to_policy_rules():
    """create_role turns rule dicts into V1PolicyRule objects with defaults."""
    c = _make()
    c.rbac_v1.create_namespaced_role.return_value = _role(name="r")
    c.create_role(
        "r",
        description="desc",
        tags={"t": "1"},
        rules=[
            {"resources": ["pods"], "verbs": ["get", "list"], "resource_names": ["p1"]},
            {"api_groups": ["apps"], "resources": ["deployments"], "verbs": ["*"]},
        ],
    )
    body = c.rbac_v1.create_namespaced_role.call_args.kwargs["body"]
    assert isinstance(body, k8s.V1Role)
    assert body.metadata.labels == {"t": "1"}
    assert body.metadata.annotations == {"description": "desc"}
    assert body.rules[0].api_groups == [""]
    assert body.rules[0].verbs == ["get", "list"]
    assert body.rules[0].resource_names == ["p1"]
    assert body.rules[1].api_groups == ["apps"]


def test_create_role_without_rules_has_empty_rule_list():
    """create_role with no rules sends an empty rules list and name-based description."""
    c = _make()
    c.rbac_v1.create_namespaced_role.return_value = _role(name="r")
    c.create_role("r")
    body = c.rbac_v1.create_namespaced_role.call_args.kwargs["body"]
    assert body.rules == []
    assert body.metadata.annotations == {"description": "r"}


def test_delete_role_message_and_call():
    """delete_role deletes in the client's namespace and confirms."""
    c = _make()
    out = c.delete_role("r")
    c.rbac_v1.delete_namespaced_role.assert_called_once_with(
        name="r", namespace="team-a"
    )
    assert "Role r deleted successfully" == out["message"]


def test_update_role_metadata_and_rules():
    """update_role replaces labels, description and rules then patches."""
    c = _make()
    role = _role(labels=None, annotations=None)
    c.rbac_v1.read_namespaced_role.return_value = role
    c.rbac_v1.patch_namespaced_role.return_value = role
    c.update_role(
        "r",
        description="d",
        tags={"a": "b"},
        rules=[{"resources": ["x"], "verbs": ["get"]}],
    )
    assert role.metadata.labels == {"a": "b"}
    assert role.metadata.annotations == {"description": "d"}
    assert role.rules[0].resources == ["x"]
    assert role.rules[0].api_groups == [""]


def test_update_role_existing_annotations_kept_and_no_rules_change():
    """Existing annotations are preserved and rules untouched when not supplied."""
    c = _make()
    original = [_rule()]
    role = _role(rules=original, annotations={"keep": "1"})
    c.rbac_v1.read_namespaced_role.return_value = role
    c.rbac_v1.patch_namespaced_role.return_value = role
    c.update_role("r", description="d")
    assert role.metadata.annotations == {"keep": "1", "description": "d"}
    assert role.rules is original


def test_update_role_no_changes():
    """With no fields the role is patched as read."""
    c = _make()
    role = _role(labels={"x": "y"})
    c.rbac_v1.read_namespaced_role.return_value = role
    c.rbac_v1.patch_namespaced_role.return_value = role
    c.update_role("r")
    assert role.metadata.labels == {"x": "y"}


def test_get_policy_converts_role_binding_with_subjects_and_timestamp():
    """get_policy returns the role ref, subjects and ISO creation time."""
    c = _make()
    subj = SimpleNamespace(kind="ServiceAccount", name="bot", namespace="team-a")
    c.rbac_v1.read_namespaced_role_binding.return_value = _rb(
        name="rb1", subjects=[subj], ts=NOW
    )
    out = c.get_policy("rb1")
    assert out["role_ref"] == {
        "kind": "Role",
        "name": "viewer",
        "api_group": "rbac.authorization.k8s.io",
    }
    assert out["subjects"] == [
        {"kind": "ServiceAccount", "name": "bot", "namespace": "team-a"}
    ]
    assert out["created_at"] == NOW.isoformat()


def test_list_policies_includes_bindings_without_subjects():
    """A binding with subjects=None is listed with an empty subject list."""
    c = _make()
    c.rbac_v1.list_namespaced_role_binding.return_value = _listing([_rb(subjects=None)])
    out = c.list_policies()
    assert out["policies"][0]["subjects"] == []
    assert out["policies"][0]["created_at"] is None


def test_create_policy_builds_role_binding_from_document():
    """create_policy converts role_ref and subjects into V1 objects with defaults."""
    c = _make()
    c.rbac_v1.create_namespaced_role_binding.return_value = _rb(name="rb")
    c.create_policy(
        "rb",
        {
            "role_ref": {"name": "viewer"},
            "subjects": [
                {"name": "bot"},
                {"kind": "User", "name": "alice", "namespace": "other"},
            ],
        },
        description="d",
        tags={"k": "v"},
    )
    body = c.rbac_v1.create_namespaced_role_binding.call_args.kwargs["body"]
    assert body.role_ref.kind == "Role"
    assert body.role_ref.name == "viewer"
    assert body.role_ref.api_group == "rbac.authorization.k8s.io"
    assert body.subjects[0].kind == "ServiceAccount"
    assert body.subjects[0].namespace == "team-a"
    assert body.subjects[1].kind == "User"
    assert body.subjects[1].namespace == "other"
    assert body.metadata.annotations == {"description": "d"}
    assert body.metadata.labels == {"k": "v"}


def test_create_policy_without_subjects_defaults():
    """Missing subjects produce an empty list and the name as description."""
    c = _make()
    c.rbac_v1.create_namespaced_role_binding.return_value = _rb(name="rb")
    c.create_policy("rb", {"role_ref": {"name": "r", "kind": "ClusterRole"}})
    body = c.rbac_v1.create_namespaced_role_binding.call_args.kwargs["body"]
    assert body.subjects == []
    assert body.role_ref.kind == "ClusterRole"
    assert body.metadata.annotations == {"description": "rb"}


def test_delete_policy_message():
    """delete_policy deletes the binding and confirms."""
    c = _make()
    out = c.delete_policy("rb")
    c.rbac_v1.delete_namespaced_role_binding.assert_called_once_with(
        name="rb", namespace="team-a"
    )
    assert "Role binding rb deleted successfully" == out["message"]


def test_attach_policy_to_user_existing_binding_is_idempotent():
    """An existing binding short-circuits without creating a new one."""
    c = _make()
    out = c.attach_policy_to_user("bot", "viewer")
    c.rbac_v1.read_namespaced_role_binding.assert_called_once_with(
        name="bot-viewer", namespace="team-a"
    )
    c.rbac_v1.create_namespaced_role_binding.assert_not_called()
    assert "already attached" in out["message"]


def test_attach_policy_to_user_creates_binding_when_absent():
    """When the binding is not found a role binding for the service account is created."""
    c = _make()
    c.rbac_v1.read_namespaced_role_binding.side_effect = ApiException(
        status=404, reason="Not Found"
    )
    out = c.attach_policy_to_user("bot", "viewer")
    body = c.rbac_v1.create_namespaced_role_binding.call_args.kwargs["body"]
    assert body.metadata.name == "bot-viewer"
    assert body.role_ref.name == "viewer"
    assert body.subjects[0].name == "bot"
    assert body.subjects[0].namespace == "team-a"
    assert "attached to service account bot" in out["message"]


def test_attach_policy_to_user_create_failure_translated():
    """A create failure after not-found is translated to an Exception."""
    c = _make()
    c.rbac_v1.read_namespaced_role_binding.side_effect = ApiException(
        status=404, reason="Not Found"
    )
    c.rbac_v1.create_namespaced_role_binding.side_effect = _api_exc("denied")
    with pytest.raises(Exception, match="attach policy to user error: denied"):
        c.attach_policy_to_user("bot", "viewer")


def test_attach_policy_to_user_create_generic_failure_wrapped():
    """A non-API create failure is wrapped too."""
    c = _make()
    c.rbac_v1.read_namespaced_role_binding.side_effect = ApiException(
        status=404, reason="Not Found"
    )
    c.rbac_v1.create_namespaced_role_binding.side_effect = RuntimeError("net")
    with pytest.raises(Exception, match="attach policy to user error: net"):
        c.attach_policy_to_user("bot", "viewer")


def test_detach_policy_from_user_deletes_conventional_binding():
    """detach_policy_from_user deletes the '<user>-<role>' binding."""
    c = _make()
    out = c.detach_policy_from_user("bot", "viewer")
    c.rbac_v1.delete_namespaced_role_binding.assert_called_once_with(
        name="bot-viewer", namespace="team-a"
    )
    assert "detached from service account bot" in out["message"]


@pytest.mark.parametrize("method", ["attach_policy_to_role", "detach_policy_from_role"])
def test_role_policy_attachment_not_supported(method):
    """Attaching/detaching policies on roles is unsupported in K8s."""
    with pytest.raises(NotImplementedError):
        getattr(_make(), method)("r", "p")


def test_list_role_policies_always_empty():
    """Roles carry no attached policies in K8s."""
    assert _make().list_role_policies("r") == []


def test_list_user_policies_filters_bindings_by_service_account():
    """Only bindings naming the service account (kind ServiceAccount) are returned, once each."""
    c = _make()
    sa = SimpleNamespace(kind="ServiceAccount", name="bot", namespace="team-a")
    other = SimpleNamespace(kind="ServiceAccount", name="other", namespace="team-a")
    user_kind = SimpleNamespace(kind="User", name="bot", namespace=None)
    c.rbac_v1.list_namespaced_role_binding.return_value = _listing(
        [
            _rb(name="b1", role="admin", subjects=[other, sa, sa]),
            _rb(name="b2", role="viewer", subjects=[user_kind]),
            _rb(name="b3", role="x", subjects=None),
        ]
    )
    assert c.list_user_policies("bot") == [
        {"name": "admin", "role": "admin", "binding_name": "b1"}
    ]


def test_create_access_key_returns_token_with_expiration():
    """create_access_key issues a token request and returns token/expiration."""
    c = _make()
    token = "-".join(["fake", "token"])
    c.core_v1.create_namespaced_service_account_token.return_value = SimpleNamespace(
        status=SimpleNamespace(token=token, expiration_timestamp=NOW)
    )
    out = c.create_access_key("bot")
    assert out == {"token": token, "expiration": NOW.isoformat(), "user": "bot"}
    kwargs = c.core_v1.create_namespaced_service_account_token.call_args.kwargs
    assert kwargs["name"] == "bot"
    assert kwargs["body"].spec.audiences == ["https://kubernetes.default.svc"]


def test_create_access_key_without_expiration():
    """A token without an expiration timestamp reports expiration None."""
    c = _make()
    c.core_v1.create_namespaced_service_account_token.return_value = SimpleNamespace(
        status=SimpleNamespace(token="t", expiration_timestamp=None)
    )
    assert c.create_access_key("bot")["expiration"] is None


def test_list_access_keys_uses_secret_refs_and_namespace_fallback():
    """list_access_keys reports secret refs, defaulting namespace to the client's."""
    c = _make()
    sa = _sa()
    sa.secrets = [
        SimpleNamespace(name="s1", namespace=None),
        SimpleNamespace(name="s2", namespace="ns2"),
    ]
    c.core_v1.read_namespaced_service_account.return_value = sa
    assert c.list_access_keys("bot") == [
        {"secret_name": "s1", "namespace": "team-a"},
        {"secret_name": "s2", "namespace": "ns2"},
    ]


def test_list_access_keys_none_secrets_is_empty():
    """A service account without secrets has no access keys."""
    c = _make()
    c.core_v1.read_namespaced_service_account.return_value = _sa()
    assert c.list_access_keys("bot") == []


def test_delete_access_key_deletes_secret():
    """delete_access_key removes the named token secret."""
    c = _make()
    out = c.delete_access_key("bot", "tok-secret")
    c.core_v1.delete_namespaced_secret.assert_called_once_with(
        name="tok-secret", namespace="team-a"
    )
    assert "tok-secret deleted successfully" in out["message"]


def test_sync_records_each_failing_resource():
    """sync_from_provider reports per-resource failures and counts successes."""
    c = _make()
    c.core_v1.list_namespaced_service_account.side_effect = RuntimeError("sa")
    c.rbac_v1.list_namespaced_role.return_value = _listing([_role(name="r")])
    c.rbac_v1.list_namespaced_role_binding.side_effect = RuntimeError("rb")
    out = c.sync_from_provider()
    assert (out["users_synced"], out["roles_synced"], out["policies_synced"]) == (
        0,
        1,
        0,
    )
    assert len(out["errors"]) == 2
    assert "service accounts" in out["errors"][0]
    assert "role bindings" in out["errors"][1]


def test_sync_roles_failure_recorded():
    """A failing role listing is recorded while others succeed."""
    c = _make()
    c.core_v1.list_namespaced_service_account.return_value = _listing([_sa()])
    c.rbac_v1.list_namespaced_role.side_effect = RuntimeError("roles")
    c.rbac_v1.list_namespaced_role_binding.return_value = _listing([_rb()])
    out = c.sync_from_provider()
    assert (out["users_synced"], out["policies_synced"]) == (1, 1)
    assert any("roles" in e for e in out["errors"])


def test_connection_true_and_false():
    """test_connection reports True on success and False on failure."""
    c = _make()
    assert c.test_connection() is True
    c.core_v1.list_namespace.side_effect = _api_exc()
    assert c.test_connection() is False


def test_sync_outer_error_is_recorded():
    """An unexpected failure outside the per-resource handlers lands in errors."""
    c = _make()
    with patch.object(c, "list_users", return_value=SimpleNamespace(get=None)):
        out = c.sync_from_provider()
    assert any("Error syncing service accounts" in e for e in out["errors"])


def test_in_cluster_config_branch_loaded():
    """in_cluster=True loads the in-cluster config instead of a kubeconfig."""
    with patch(f"{MODULE}.config") as cfg:
        KubernetesRBACClient({"in_cluster": True})
    cfg.load_incluster_config.assert_called_once()
    cfg.load_kube_config.assert_not_called()


def test_create_policy_with_subjects_works_with_real_kubernetes_models():
    """create_policy with subjects should build a role binding using the real kubernetes models."""
    c = _make()
    c.rbac_v1.create_namespaced_role_binding.return_value = _rb(name="rb")
    c.create_policy("rb", {"role_ref": {"name": "r"}, "subjects": [{"name": "bot"}]})
    c.rbac_v1.create_namespaced_role_binding.assert_called_once()


def test_attach_policy_creates_binding_with_real_kubernetes_models():
    """attach_policy_to_user should create a binding when none exists, using the real models."""
    c = _make()
    c.rbac_v1.read_namespaced_role_binding.side_effect = ApiException(
        status=404, reason="nf"
    )
    c.attach_policy_to_user("bot", "viewer")
    c.rbac_v1.create_namespaced_role_binding.assert_called_once()


def test_create_access_key_works_with_real_kubernetes_models():
    """create_access_key should issue a token request using the real kubernetes models."""
    c = _make()
    c.core_v1.create_namespaced_service_account_token.return_value = SimpleNamespace(
        status=SimpleNamespace(token="t", expiration_timestamp=None)
    )
    assert c.create_access_key("bot")["token"] == "t"
