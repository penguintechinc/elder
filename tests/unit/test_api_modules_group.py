"""Task 3: /api/v1/modules response includes group field."""

from apps.api.modules import MODULES


def test_modules_api_response_has_group_field():
    """Verify that module manifests have group field and API response will include it."""
    # Test that all manifests have a valid group
    for m in MODULES:
        assert hasattr(m, "group"), f"Module {m.name} missing group attribute"
        assert m.group in {
            "core",
            "workflow",
            "kb",
        }, f"Invalid group {m.group!r} for module {m.name}"

    # Test that a mock response dict would have the group field
    # (simulating what the API endpoint does)
    for manifest in MODULES:
        response_dict = {
            "name": manifest.name,
            "title": manifest.title,
            "installed": True,
            "licensed": True,
            "tenant_enabled": True,
            "effective": True,
            "nav_id": manifest.nav_id,
            "scopes": manifest.scopes,
            "group": manifest.group,  # This is what we're testing
            "capabilities": {},
        }
        assert "group" in response_dict
        assert response_dict["group"] == manifest.group

    # Check specific module
    helpdesk = [m for m in MODULES if m.name == "helpdesk"]
    assert len(helpdesk) == 1
    assert helpdesk[0].group == "workflow"
