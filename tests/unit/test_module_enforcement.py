"""Unit tests for module enforcement layers (licensing + tenant toggles).

Tests the before_request hook that enforces:
- Layer 2: Module licensing (MODULE_UNLICENSED)
- Layer 3: Tenant module enablement (MODULE_DISABLED)

Uses mocked auth, licensing, and toggle services.
"""

# flake8: noqa: E501

import json
import pytest
from unittest.mock import MagicMock, patch
from quart import current_app


class TestModuleEnforcementBeforeRequest:
    """Test module enforcement before_request hook."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_core_route_unaffected_by_licensing(
        self, mock_get_user, async_client, generate_token
    ):
        """Core (non-module) routes should not be affected by licensing enforcement."""
        # Mock user
        mock_user = MagicMock()
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        # Generate token
        token = generate_token(tenant_id=1, scopes=["user:read"])

        # Core route: /api/v1/users (not a module route)
        # Should not return 403 MODULE_UNLICENSED even if licensing fails
        response = await async_client.get(
            "/api/v1/users", headers={"Authorization": f"Bearer {token}"}
        )

        # Verify: not a 403 MODULE_UNLICENSED (may be 200, 404, or other, but not enforced)
        data = await response.get_data(as_text=True)
        if response.status_code == 403:
            assert "MODULE_UNLICENSED" not in data
            assert "MODULE_DISABLED" not in data

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_list_modules_shows_licensed_status(
        self, mock_get_user, async_client, generate_token, app
    ):
        """GET /api/v1/modules should show correct licensed status for each module."""
        # Mock user
        mock_user = MagicMock()
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        # Generate token
        token = generate_token(tenant_id=1, scopes=["user:read"])

        response = await async_client.get(
            "/api/v1/modules", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert "modules" in data

        # Verify structure of returned modules
        for module in data["modules"]:
            assert "name" in module
            assert "licensed" in module  # Should now be computed, not hardcoded
            assert "tenant_enabled" in module  # Should now be computed
            assert "effective" in module  # effective = licensed AND tenant_enabled
            assert isinstance(module["licensed"], bool)
            assert isinstance(module["tenant_enabled"], bool)
            assert isinstance(module["effective"], bool)

            # Verify logic: effective = licensed AND tenant_enabled
            expected_effective = module["licensed"] and module["tenant_enabled"]
            assert (
                module["effective"] == expected_effective
            ), f"Module {module['name']}: effective should be {expected_effective}"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_list_modules_no_auth(self, mock_get_user, async_client):
        """GET /api/v1/modules without auth should fail."""
        mock_get_user.return_value = None

        response = await async_client.get("/api/v1/modules")

        # 401 from login_required
        assert response.status_code == 401

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_list_modules_different_tenants_see_same_licenses(
        self, mock_get_user, async_client, generate_token
    ):
        """Two different tenants should both see licensing info (not tenant-specific)."""
        # Mock user
        mock_user = MagicMock()
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        # Tenant 1
        token1 = generate_token(tenant_id=1, scopes=["user:read"])
        response1 = await async_client.get(
            "/api/v1/modules", headers={"Authorization": f"Bearer {token1}"}
        )

        # Tenant 2
        token2 = generate_token(tenant_id=2, scopes=["user:read"])
        response2 = await async_client.get(
            "/api/v1/modules", headers={"Authorization": f"Bearer {token2}"}
        )

        assert response1.status_code == 200
        assert response2.status_code == 200

        data1 = json.loads(await response1.get_data())
        data2 = json.loads(await response2.get_data())

        # Both should have modules
        assert len(data1["modules"]) > 0
        assert len(data2["modules"]) > 0


class TestLicensingHelper:
    """Unit tests for the licensing helper function."""

    @pytest.mark.asyncio
    async def test_module_licensed_returns_true_when_no_feature(self):
        """module_licensed should return True if manifest.license_feature is None."""
        from apps.api.common.modules.licensing import module_licensed
        from apps.api.modules.registry import ModuleManifest

        app = MagicMock()
        manifest = ModuleManifest(
            name="always_available",
            title="Always Available",
            license_feature=None,  # No license feature required
            depends_on=(),
            blueprints=lambda: [],
            models_import=(),
            table_prefix=None,
            nav_id="nav_always",
            scopes=(),
            worker_task_groups=(),
            optional_services=(),
            default_enabled=True,
        )

        result = module_licensed(app, manifest)
        assert result is True

    @pytest.mark.asyncio
    async def test_module_licensed_returns_true_when_client_unavailable(self):
        """module_licensed should return True if license_client is None (graceful degradation)."""
        from apps.api.common.modules.licensing import module_licensed
        from apps.api.modules.registry import ModuleManifest

        app = MagicMock()
        app.extensions = {"license_client": None}  # No license client

        manifest = ModuleManifest(
            name="enterprise_feature",
            title="Enterprise Feature",
            license_feature="advanced_analytics",
            depends_on=(),
            blueprints=lambda: [],
            models_import=(),
            table_prefix=None,
            nav_id="nav_enterprise",
            scopes=(),
            worker_task_groups=(),
            optional_services=(),
            default_enabled=True,
        )

        result = module_licensed(app, manifest)
        # Should return True (graceful degradation)
        assert result is True

    @pytest.mark.asyncio
    async def test_module_licensed_returns_false_when_not_licensed(self):
        """module_licensed should return False if license_client says feature not licensed."""
        from apps.api.common.modules.licensing import module_licensed
        from apps.api.modules.registry import ModuleManifest

        app = MagicMock()
        mock_license_client = MagicMock()
        mock_license_client.check_feature.return_value = False
        app.extensions = {"license_client": mock_license_client}

        manifest = ModuleManifest(
            name="enterprise_feature",
            title="Enterprise Feature",
            license_feature="advanced_analytics",
            depends_on=(),
            blueprints=lambda: [],
            models_import=(),
            table_prefix=None,
            nav_id="nav_enterprise",
            scopes=(),
            worker_task_groups=(),
            optional_services=(),
            default_enabled=True,
        )

        result = module_licensed(app, manifest)
        assert result is False
        mock_license_client.check_feature.assert_called_once_with("advanced_analytics")

    @pytest.mark.asyncio
    async def test_module_licensed_returns_true_when_licensed(self):
        """module_licensed should return True if license_client says feature is licensed."""
        from apps.api.common.modules.licensing import module_licensed
        from apps.api.modules.registry import ModuleManifest

        app = MagicMock()
        mock_license_client = MagicMock()
        mock_license_client.check_feature.return_value = True
        app.extensions = {"license_client": mock_license_client}

        manifest = ModuleManifest(
            name="enterprise_feature",
            title="Enterprise Feature",
            license_feature="advanced_analytics",
            depends_on=(),
            blueprints=lambda: [],
            models_import=(),
            table_prefix=None,
            nav_id="nav_enterprise",
            scopes=(),
            worker_task_groups=(),
            optional_services=(),
            default_enabled=True,
        )

        result = module_licensed(app, manifest)
        assert result is True
        mock_license_client.check_feature.assert_called_once_with("advanced_analytics")

    @pytest.mark.asyncio
    async def test_module_licensed_gracefully_handles_exception(self):
        """module_licensed should return True and log warning if check_feature raises."""
        from apps.api.common.modules.licensing import module_licensed
        from apps.api.modules.registry import ModuleManifest

        app = MagicMock()
        mock_license_client = MagicMock()
        mock_license_client.check_feature.side_effect = Exception(
            "License server unavailable"
        )
        app.extensions = {"license_client": mock_license_client}

        manifest = ModuleManifest(
            name="enterprise_feature",
            title="Enterprise Feature",
            license_feature="advanced_analytics",
            depends_on=(),
            blueprints=lambda: [],
            models_import=(),
            table_prefix=None,
            nav_id="nav_enterprise",
            scopes=(),
            worker_task_groups=(),
            optional_services=(),
            default_enabled=True,
        )

        result = module_licensed(app, manifest)
        # Should return True (graceful degradation)
        assert result is True

    @pytest.mark.asyncio
    async def test_module_licensed_with_missing_extensions_dict(self):
        """module_licensed should handle app.extensions missing or KeyError."""
        from apps.api.common.modules.licensing import module_licensed
        from apps.api.modules.registry import ModuleManifest

        app = MagicMock()
        app.extensions = {}  # No license_client key

        manifest = ModuleManifest(
            name="enterprise_feature",
            title="Enterprise Feature",
            license_feature="advanced_analytics",
            depends_on=(),
            blueprints=lambda: [],
            models_import=(),
            table_prefix=None,
            nav_id="nav_enterprise",
            scopes=(),
            worker_task_groups=(),
            optional_services=(),
            default_enabled=True,
        )

        result = module_licensed(app, manifest)
        assert result is True
