"""Unit tests for the Kubernetes RBAC IAM provider client.

The kubernetes config loader and API clients are mocked, so no cluster is
required. Covers service-account (user), role, and role-binding (policy)
list/get, the object→dict conversion helpers, connectivity test, aggregate
sync, and the ApiException → Exception translation.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("kubernetes")

from apps.api.services.iam.k8s_client import KubernetesRBACClient  # noqa: E402

MODULE = "apps.api.services.iam.k8s_client"


def _make_client(namespace="default"):
    with patch(f"{MODULE}.config") as mock_config, patch(f"{MODULE}.client"):
        mock_config.load_kube_config.return_value = None
        c = KubernetesRBACClient({"namespace": namespace})
    # Replace API handles with fresh mocks for per-test control.
    c.core_v1 = MagicMock()
    c.rbac_v1 = MagicMock()
    return c


def _meta(name="x", uid="uid-1", namespace="default", labels=None, annotations=None):
    return SimpleNamespace(
        name=name,
        uid=uid,
        namespace=namespace,
        creation_timestamp=None,
        labels=labels,
        annotations=annotations,
        _continue=None,
    )


def _list_response(items):
    return SimpleNamespace(items=items, metadata=SimpleNamespace(_continue=None))


def _api_exception(reason="boom"):
    from kubernetes.client.rest import ApiException

    exc = ApiException(reason=reason)
    return exc


class TestInit:
    def test_in_cluster_config(self):
        with patch(f"{MODULE}.config") as mock_config, patch(f"{MODULE}.client"):
            KubernetesRBACClient({"in_cluster": True, "namespace": "ns"})
            mock_config.load_incluster_config.assert_called_once()

    def test_kubeconfig_path(self):
        with patch(f"{MODULE}.config") as mock_config, patch(f"{MODULE}.client"):
            KubernetesRBACClient({"kubeconfig_path": "/k", "context": "ctx"})
            mock_config.load_kube_config.assert_called_once_with(
                config_file="/k", context="ctx"
            )


class TestUsers:
    def test_list_users(self):
        c = _make_client()
        sa = SimpleNamespace(metadata=_meta(name="sa1", uid="u1"))
        c.core_v1.list_namespaced_service_account.return_value = _list_response([sa])
        result = c.list_users()
        assert result["users"][0]["username"] == "sa1"
        assert result["users"][0]["id"] == "u1"
        assert result["truncated"] is False

    def test_get_user(self):
        c = _make_client()
        sa = SimpleNamespace(metadata=_meta(name="sa1", uid="u1"))
        c.core_v1.read_namespaced_service_account.return_value = sa
        user = c.get_user("sa1")
        assert user["username"] == "sa1"

    def test_get_user_api_exception(self):
        c = _make_client()
        c.core_v1.read_namespaced_service_account.side_effect = _api_exception("nope")
        with pytest.raises(Exception, match="get user error"):
            c.get_user("missing")

    def test_delete_user(self):
        c = _make_client()
        result = c.delete_user("sa1")
        assert "deleted" in result["message"].lower()
        c.core_v1.delete_namespaced_service_account.assert_called_once()


class TestRoles:
    def test_list_roles_converts_rules(self):
        c = _make_client()
        rule = SimpleNamespace(
            api_groups=["rbac"],
            resources=["pods"],
            verbs=["get", "list"],
            resource_names=[],
        )
        role = SimpleNamespace(metadata=_meta(name="r1", uid="rid1"), rules=[rule])
        c.rbac_v1.list_namespaced_role.return_value = _list_response([role])
        result = c.list_roles()
        assert result["roles"][0]["name"] == "r1"

    def test_get_role(self):
        c = _make_client()
        role = SimpleNamespace(metadata=_meta(name="r1", uid="rid1"), rules=None)
        c.rbac_v1.read_namespaced_role.return_value = role
        assert c.get_role("r1")["name"] == "r1"

    def test_list_roles_api_exception(self):
        c = _make_client()
        c.rbac_v1.list_namespaced_role.side_effect = _api_exception("x")
        with pytest.raises(Exception, match="list roles error"):
            c.list_roles()


class TestPolicies:
    def test_list_policies_converts_bindings(self):
        c = _make_client()
        subj = SimpleNamespace(kind="ServiceAccount", name="sa1", namespace="default")
        role_ref = SimpleNamespace(kind="Role", name="r1", api_group="rbac")
        rb = SimpleNamespace(
            metadata=_meta(name="rb1", uid="rbid1"),
            subjects=[subj],
            role_ref=role_ref,
        )
        c.rbac_v1.list_namespaced_role_binding.return_value = _list_response([rb])
        result = c.list_policies()
        pol = result["policies"][0]
        assert pol["name"] == "rb1"
        assert pol["role_ref"]["name"] == "r1"
        assert pol["subjects"][0]["name"] == "sa1"

    def test_get_policy(self):
        c = _make_client()
        rb = SimpleNamespace(
            metadata=_meta(name="rb1", uid="rbid1"),
            subjects=None,
            role_ref=SimpleNamespace(kind="Role", name="r1", api_group="rbac"),
        )
        c.rbac_v1.read_namespaced_role_binding.return_value = rb
        assert c.get_policy("rb1")["name"] == "rb1"


class TestConnectionAndSync:
    def test_connection_success(self):
        c = _make_client()
        c.core_v1.list_namespace.return_value = _list_response([])
        assert c.test_connection() is True

    def test_connection_failure(self):
        c = _make_client()
        c.core_v1.list_namespace.side_effect = RuntimeError("unreachable")
        assert c.test_connection() is False

    def test_sync_counts_and_errors(self):
        c = _make_client()
        sa = SimpleNamespace(metadata=_meta(name="sa1", uid="u1"))
        c.core_v1.list_namespaced_service_account.return_value = _list_response([sa])
        c.rbac_v1.list_namespaced_role.side_effect = _api_exception("role fail")
        c.rbac_v1.list_namespaced_role_binding.return_value = _list_response([])
        result = c.sync_from_provider()
        assert result["users_synced"] == 1
        assert any("Error syncing roles" in e for e in result["errors"])
