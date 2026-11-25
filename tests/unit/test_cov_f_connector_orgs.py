"""Organization lookup/creation contract shared by every worker connector."""

import importlib
from typing import Any

import pytest

import tests.unit.conftest_worker_stubs  # noqa: F401
from apps.worker.config.settings import settings
from tests.unit._cov_f_helpers import make_elder

CONNECTORS = [
    ("iboss_connector", "IBossConnector"),
    ("vcenter_connector", "VCenterConnector"),
    ("k8s_connector", "KubernetesConnector"),
    ("gcp_connector", "GCPConnector"),
    ("aws_connector", "AWSConnector"),
    ("lxd_connector", "LXDConnector"),
]


@pytest.fixture(params=CONNECTORS, ids=[name for name, _ in CONNECTORS])
def connector(request: pytest.FixtureRequest) -> Any:
    """Instantiate each connector with an Elder double and nothing else connected."""
    module_name, class_name = request.param
    module = importlib.import_module(f"apps.worker.connectors.{module_name}")
    instance = getattr(module, class_name)()
    instance.elder_client = make_elder()
    return instance


@pytest.mark.asyncio
async def test_existing_org_matched_on_name_and_parent(connector: Any) -> None:
    """Only an org with the same name AND parent is reused; the hit is cached."""
    connector.elder_client = make_elder(
        orgs=[
            {"id": 1, "name": "Acme", "parent_id": 99},
            {"id": 2, "name": "Other", "parent_id": 7},
            {"id": 3, "name": "Acme", "parent_id": 7},
        ]
    )
    assert await connector._get_or_create_organization("Acme", "d", parent_id=7) == 3
    assert await connector._get_or_create_organization("Acme", "d", parent_id=7) == 3
    connector.elder_client.list_organizations.assert_awaited_once_with(per_page=1000)
    connector.elder_client.create_organization.assert_not_awaited()
    assert connector.organization_cache == {"7:Acme": 3}


@pytest.mark.asyncio
async def test_missing_org_created_when_enabled(
    connector: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With auto-create on, a missing root org is created once and cached."""
    monkeypatch.setattr(settings, "create_missing_organizations", True)
    assert await connector._get_or_create_organization("Acme", "desc") == 501
    assert await connector._get_or_create_organization("Acme", "desc") == 501
    connector.elder_client.create_organization.assert_awaited_once()
    payload = connector.elder_client.create_organization.call_args.args[0]
    assert (payload.name, payload.description, payload.parent_id) == (
        "Acme",
        "desc",
        None,
    )
    assert connector.organization_cache == {"root:Acme": 501}


@pytest.mark.asyncio
async def test_default_org_used_when_creation_disabled(
    connector: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With auto-create off the configured default organization id is returned."""
    monkeypatch.setattr(settings, "create_missing_organizations", False)
    monkeypatch.setattr(settings, "default_organization_id", 42)
    assert await connector._get_or_create_organization("Acme", "d") == 42
    connector.elder_client.create_organization.assert_not_awaited()


@pytest.mark.asyncio
async def test_error_when_no_org_and_no_default(
    connector: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With auto-create off and no default, the lookup fails loudly."""
    monkeypatch.setattr(settings, "create_missing_organizations", False)
    monkeypatch.setattr(settings, "default_organization_id", None)
    with pytest.raises(ValueError, match="Acme.*auto-creation disabled"):
        await connector._get_or_create_organization("Acme", "d")
