"""Unit tests for tenant module management API endpoints.

Uses real async client, JWT token generation, and mocked auth for proper scope/tenant testing.
"""

# flake8: noqa: E501


import json
from unittest.mock import MagicMock, patch

import pytest


class TestTenantModulesAPI:
    """Test tenant module management API endpoints."""

    @pytest.mark.asyncio
    async def test_list_tenant_modules_no_auth(self, async_client):
        """GET /api/v1/tenants/<id>/modules without token should fail."""
        response = await async_client.get("/api/v1/tenants/1/modules")
        # 401 from missing g.current_user (login_required fails)
        assert response.status_code == 401

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_list_tenant_modules_no_admin_scope(
        self, mock_get_user, async_client, generate_token
    ):
        """GET /api/v1/tenants/<id>/modules without admin:write scope should fail."""
        # Mock user without is_superuser
        mock_user = MagicMock()
        mock_user.is_superuser = False
        mock_get_user.return_value = mock_user

        # Generate token with tenant=1, only "user:read" scope (no admin:write)
        token = generate_token(tenant_id=1, scopes=["user:read"])

        response = await async_client.get(
            "/api/v1/tenants/1/modules",
            headers={"Authorization": f"Bearer {token}"},
        )

        # 403 from missing admin:write scope
        assert response.status_code == 403
        data = json.loads(await response.get_data())
        assert "admin:write" in data.get("error", "")

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_list_tenant_modules_cross_tenant_isolated(
        self, mock_get_user, async_client, generate_token
    ):
        """GET /api/v1/tenants/<id>/modules: tenant=2 user cannot access tenant=1."""
        # Mock user (tenant=2) without superuser
        mock_user = MagicMock()
        mock_user.is_superuser = False
        mock_get_user.return_value = mock_user

        # Generate token for tenant=2 with admin:write scope
        token = generate_token(tenant_id=2, scopes=["admin:write"])

        # Try to access tenant=1
        response = await async_client.get(
            "/api/v1/tenants/1/modules",
            headers={"Authorization": f"Bearer {token}"},
        )

        # 403 from tenant isolation check
        assert response.status_code == 403
        data = json.loads(await response.get_data())
        assert "other tenant" in data.get("error", "").lower()

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_list_tenant_modules_success(
        self, mock_get_user, async_client, generate_token, app
    ):
        """GET /api/v1/tenants/<id>/modules with valid auth should return module list."""
        # Mock user (tenant=1, superuser)
        mock_user = MagicMock()
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        # Generate valid token
        token = generate_token(tenant_id=1, scopes=["admin:write"])

        response = await async_client.get(
            "/api/v1/tenants/1/modules",
            headers={"Authorization": f"Bearer {token}"},
        )

        # Should succeed
        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert data["status"] == "success"
        assert "data" in data
        # Should return all registered modules (at least some)
        assert len(data["data"]) > 0

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_set_tenant_module_no_auth(self, mock_get_user, async_client):
        """PUT /api/v1/tenants/<id>/modules without token should fail."""
        mock_get_user.return_value = None

        response = await async_client.put(
            "/api/v1/tenants/1/modules",
            json={"module_name": "infrastructure", "enabled": True},
        )

        assert response.status_code == 401

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_set_tenant_module_no_admin_scope(
        self, mock_get_user, async_client, generate_token
    ):
        """PUT /api/v1/tenants/<id>/modules without admin:write scope should fail."""
        mock_user = MagicMock()
        mock_user.is_superuser = False
        mock_get_user.return_value = mock_user

        # Token with only user:read, no admin:write
        token = generate_token(tenant_id=1, scopes=["user:read"])

        response = await async_client.put(
            "/api/v1/tenants/1/modules",
            json={"module_name": "infrastructure", "enabled": True},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_set_tenant_module_cross_tenant_isolated(
        self, mock_get_user, async_client, generate_token
    ):
        """PUT /api/v1/tenants/<id>/modules: tenant=2 user cannot modify tenant=1."""
        mock_user = MagicMock()
        mock_user.is_superuser = False
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=2, scopes=["admin:write"])

        response = await async_client.put(
            "/api/v1/tenants/1/modules",
            json={"module_name": "infrastructure", "enabled": True},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_set_tenant_module_invalid_body(
        self, mock_get_user, async_client, generate_token
    ):
        """PUT /api/v1/tenants/<id>/modules with invalid request should fail."""
        mock_user = MagicMock()
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["admin:write"])

        # Empty module_name fails validation
        response = await async_client.put(
            "/api/v1/tenants/1/modules",
            json={"module_name": "", "enabled": True},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 400

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    @patch("apps.api.common.modules.tenant_toggle.set_module_enabled")
    async def test_set_tenant_module_success(
        self, mock_set, mock_get_user, async_client, generate_token
    ):
        """PUT /api/v1/tenants/<id>/modules with valid auth should update module."""
        mock_user = MagicMock()
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        # Mock the service call (doesn't raise exception)
        mock_set.return_value = None

        token = generate_token(tenant_id=1, scopes=["admin:write"])

        response = await async_client.put(
            "/api/v1/tenants/1/modules",
            json={"module_name": "infrastructure", "enabled": False, "settings": {"key": "val"}},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert data["status"] == "success"
        assert data["data"]["module_name"] == "infrastructure"
        assert data["data"]["enabled"] is False

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    @patch("apps.api.common.modules.tenant_toggle.set_module_enabled")
    async def test_set_tenant_module_superuser_cross_tenant(
        self, mock_set, mock_get_user, async_client, generate_token
    ):
        """Superusers can bypass tenant isolation and manage any tenant."""
        mock_user = MagicMock()
        mock_user.is_superuser = True  # Superuser
        mock_get_user.return_value = mock_user

        # Superuser token for tenant=2, trying to manage tenant=1
        token = generate_token(tenant_id=2, scopes=["admin:write"])

        mock_set.return_value = None

        response = await async_client.put(
            "/api/v1/tenants/1/modules",
            json={"module_name": "infrastructure", "enabled": True},
            headers={"Authorization": f"Bearer {token}"},
        )

        # Superuser should succeed (no 403)
        assert response.status_code == 200
