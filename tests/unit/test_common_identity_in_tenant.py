"""Task 6: identity_in_tenant relocated to apps.api.common.identity."""

import importlib
import inspect

import pytest


def test_identity_in_tenant_accessible_from_common_identity():
    """Verify identity_in_tenant can be imported from apps.api.common.identity."""
    from apps.api.common.identity import identity_in_tenant

    # Verify the function has the correct signature
    sig = inspect.signature(identity_in_tenant)
    params = list(sig.parameters.keys())
    assert params == ["db", "identity_id", "tenant_id"]


def test_issues_module_does_not_import_helpdesk():
    """Verify issues module does not import helpdesk (no cross-module dep)."""
    # Import the issues routes module
    issues_routes = importlib.import_module("apps.api.modules.issues.routes.issues")
    source = inspect.getsource(issues_routes)

    # Check that 'helpdesk' is not imported
    assert (
        "from apps.api.modules.helpdesk" not in source
    ), "Issues module should not import from helpdesk"


def test_webhooks_uses_common_identity():
    """Verify webhooks_alerting imports from apps.api.common.identity."""
    webhooks_routes = importlib.import_module(
        "apps.api.modules.webhooks_alerting.routes.webhooks"
    )
    source = inspect.getsource(webhooks_routes)

    # Check that it doesn't import from helpdesk anymore
    assert (
        "from apps.api.modules.helpdesk" not in source
    ), "Webhooks should not import from helpdesk"


def test_documents_uses_common_identity():
    """Verify documents common.py imports from apps.api.common.identity."""
    docs_common = importlib.import_module("apps.api.modules.documents.common")
    source = inspect.getsource(docs_common)

    # Should import from common.identity
    assert (
        "from apps.api.common.identity import identity_in_tenant" in source
    ), "Documents common.py should import identity_in_tenant from common.identity"


def test_helpdesk_common_deleted():
    """Verify apps.api.modules.helpdesk.common no longer exists."""
    with pytest.raises(ImportError):
        importlib.import_module("apps.api.modules.helpdesk.common")
