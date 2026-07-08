"""Unit tests for NVD sync API endpoints.

These tests use mocked authentication and NVDSyncService.
No external network calls or real database required.
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestNVDSyncAPI:
    """Test NVD sync API endpoints."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    @patch("apps.api.services.sbom.vulnerability.nvd_sync.NVDSyncService")
    async def test_trigger_nvd_sync_success(self, mock_service_class, mock_get_user, async_client):
        """Test POST /api/v1/vulnerabilities/nvd-sync with successful sync."""
        # Mock current user
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.username = "admin"
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        # Setup mock service
        mock_service = AsyncMock()
        mock_service.sync_vulnerabilities = AsyncMock(
            return_value={
                "processed": 5,
                "updated": 3,
                "skipped": 1,
                "errors": 0,
            }
        )
        mock_service_class.return_value = mock_service

        payload = {"max_vulns": 100, "force_refresh": False}

        response = await async_client.post(
            "/api/v1/vulnerabilities/nvd-sync",
            json=payload,
            headers={"Authorization": "Bearer fake-token"},
        )

        assert response.status_code == 202
        data = json.loads(await response.get_data())
        assert data["message"] == "NVD sync completed"
        assert "stats" in data
        assert data["stats"]["processed"] == 5
        assert data["stats"]["updated"] == 3

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    @patch("apps.api.services.sbom.vulnerability.nvd_sync.NVDSyncService")
    async def test_trigger_nvd_sync_with_defaults(self, mock_service_class, mock_get_user, async_client):
        """Test POST /api/v1/vulnerabilities/nvd-sync with default parameters."""
        # Mock current user
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.username = "admin"
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        # Setup mock service
        mock_service = AsyncMock()
        mock_service.sync_vulnerabilities = AsyncMock(
            return_value={
                "processed": 0,
                "updated": 0,
                "skipped": 0,
                "errors": 0,
            }
        )
        mock_service_class.return_value = mock_service

        # Empty body - should use defaults
        response = await async_client.post(
            "/api/v1/vulnerabilities/nvd-sync",
            json={},
            headers={"Authorization": "Bearer fake-token"},
        )

        assert response.status_code == 202
        data = json.loads(await response.get_data())
        assert "message" in data
        assert "stats" in data

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    @patch("apps.api.services.sbom.vulnerability.nvd_sync.NVDSyncService")
    async def test_trigger_nvd_sync_force_refresh(self, mock_service_class, mock_get_user, async_client):
        """Test POST /api/v1/vulnerabilities/nvd-sync with force_refresh."""
        # Mock current user
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.username = "admin"
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        # Setup mock service
        mock_service = AsyncMock()
        mock_service.sync_vulnerabilities = AsyncMock(
            return_value={
                "processed": 10,
                "updated": 8,
                "skipped": 0,
                "errors": 2,
            }
        )
        mock_service_class.return_value = mock_service

        payload = {"max_vulns": 200, "force_refresh": True}

        response = await async_client.post(
            "/api/v1/vulnerabilities/nvd-sync",
            json=payload,
            headers={"Authorization": "Bearer fake-token"},
        )

        assert response.status_code == 202
        data = json.loads(await response.get_data())
        assert data["stats"]["processed"] == 10
        assert data["stats"]["updated"] == 8
        assert data["stats"]["errors"] == 2

    @pytest.mark.asyncio
    async def test_trigger_nvd_sync_unauthorized(self, async_client):
        """Test unauthorized access to POST /api/v1/vulnerabilities/nvd-sync."""
        response = await async_client.post(
            "/api/v1/vulnerabilities/nvd-sync",
            json={},
        )

        assert response.status_code in [401, 403]

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_trigger_nvd_sync_insufficient_permissions(self, mock_get_user, async_client):
        """Test POST /api/v1/vulnerabilities/nvd-sync with insufficient permissions."""
        # Mock a user without superuser status (will fail resource_role_required)
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.username = "viewer"
        mock_user.is_superuser = False
        mock_get_user.return_value = mock_user

        response = await async_client.post(
            "/api/v1/vulnerabilities/nvd-sync",
            json={},
            headers={"Authorization": "Bearer fake-token"},
        )

        # Should fail due to resource_role_required decorator
        assert response.status_code == 403

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_get_nvd_sync_status_success(self, mock_get_user, async_client, app):
        """Test GET /api/v1/vulnerabilities/nvd-sync/status."""
        # Mock current user
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.username = "admin"
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        response = await async_client.get(
            "/api/v1/vulnerabilities/nvd-sync/status",
            headers={"Authorization": "Bearer fake-token"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert "total_cves" in data
        assert "never_synced" in data
        assert "stale_sync" in data
        assert "recently_synced" in data
        assert "needs_sync" in data
        assert isinstance(data["total_cves"], int)
        assert isinstance(data["never_synced"], int)

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_get_nvd_sync_status_counts(self, mock_get_user, async_client, app):
        """Test GET /api/v1/vulnerabilities/nvd-sync/status returns correct counts."""
        # Mock current user
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.username = "admin"
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        response = await async_client.get(
            "/api/v1/vulnerabilities/nvd-sync/status",
            headers={"Authorization": "Bearer fake-token"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        # Verify needs_sync is sum of never_synced and stale_sync
        expected_needs = data["never_synced"] + data["stale_sync"]
        assert data["needs_sync"] == expected_needs

    @pytest.mark.asyncio
    async def test_get_nvd_sync_status_unauthorized(self, async_client):
        """Test unauthorized access to GET /api/v1/vulnerabilities/nvd-sync/status."""
        response = await async_client.get("/api/v1/vulnerabilities/nvd-sync/status")

        assert response.status_code in [401, 403]

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    @patch("apps.api.services.sbom.vulnerability.nvd_sync.NVDSyncService")
    async def test_trigger_nvd_sync_error_handling(self, mock_service_class, mock_get_user, async_client):
        """Test POST /api/v1/vulnerabilities/nvd-sync with service error."""
        # Mock current user
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.username = "admin"
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        # Setup mock service to raise error
        mock_service = AsyncMock()
        mock_service.sync_vulnerabilities = AsyncMock(
            side_effect=Exception("NVD API error")
        )
        mock_service_class.return_value = mock_service

        response = await async_client.post(
            "/api/v1/vulnerabilities/nvd-sync",
            json={},
            headers={"Authorization": "Bearer fake-token"},
        )

        # Should handle error gracefully
        assert response.status_code in [500, 202]  # Either 500 or graceful 202

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    @patch("apps.api.services.sbom.vulnerability.nvd_sync.NVDSyncService")
    async def test_trigger_nvd_sync_no_vulns(self, mock_service_class, mock_get_user, async_client):
        """Test POST /api/v1/vulnerabilities/nvd-sync with no vulnerabilities to sync."""
        # Mock current user
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.username = "admin"
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        # Setup mock service with empty stats
        mock_service = AsyncMock()
        mock_service.sync_vulnerabilities = AsyncMock(
            return_value={
                "processed": 0,
                "updated": 0,
                "skipped": 0,
                "errors": 0,
            }
        )
        mock_service_class.return_value = mock_service

        response = await async_client.post(
            "/api/v1/vulnerabilities/nvd-sync",
            json={},
            headers={"Authorization": "Bearer fake-token"},
        )

        assert response.status_code == 202
        data = json.loads(await response.get_data())
        assert data["stats"]["processed"] == 0
