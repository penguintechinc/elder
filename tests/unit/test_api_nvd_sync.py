"""Unit tests for NVD sync API endpoints.

These tests use mocked authentication and NVDSyncService.
No external network calls or real database required.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch


class TestNVDSyncAPI:
    """Test NVD sync API endpoints."""

    @patch("apps.api.auth.decorators.verify_jwt")
    @patch("apps.api.auth.decorators.check_resource_role")
    @patch("apps.api.services.sbom.vulnerability.nvd_sync.NVDSyncService")
    def test_trigger_nvd_sync_success(self, mock_service_class, mock_check_role, mock_jwt, client):
        """Test POST /api/v1/vulnerabilities/nvd-sync with successful sync."""
        mock_jwt.return_value = {"user_id": 1, "username": "admin"}
        mock_check_role.return_value = None

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

        response = client.post(
            "/api/v1/vulnerabilities/nvd-sync",
            data=json.dumps(payload),
            content_type="application/json",
            headers={"Authorization": "Bearer fake-token"},
        )

        assert response.status_code == 202
        data = json.loads(response.data)
        assert data["message"] == "NVD sync completed"
        assert "stats" in data
        assert data["stats"]["processed"] == 5
        assert data["stats"]["updated"] == 3

    @patch("apps.api.auth.decorators.verify_jwt")
    @patch("apps.api.auth.decorators.check_resource_role")
    @patch("apps.api.services.sbom.vulnerability.nvd_sync.NVDSyncService")
    def test_trigger_nvd_sync_with_defaults(self, mock_service_class, mock_check_role, mock_jwt, client):
        """Test POST /api/v1/vulnerabilities/nvd-sync with default parameters."""
        mock_jwt.return_value = {"user_id": 1, "username": "admin"}
        mock_check_role.return_value = None

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
        response = client.post(
            "/api/v1/vulnerabilities/nvd-sync",
            data=json.dumps({}),
            content_type="application/json",
            headers={"Authorization": "Bearer fake-token"},
        )

        assert response.status_code == 202
        data = json.loads(response.data)
        assert "message" in data
        assert "stats" in data

    @patch("apps.api.auth.decorators.verify_jwt")
    @patch("apps.api.auth.decorators.check_resource_role")
    @patch("apps.api.services.sbom.vulnerability.nvd_sync.NVDSyncService")
    def test_trigger_nvd_sync_force_refresh(self, mock_service_class, mock_check_role, mock_jwt, client):
        """Test POST /api/v1/vulnerabilities/nvd-sync with force_refresh."""
        mock_jwt.return_value = {"user_id": 1, "username": "admin"}
        mock_check_role.return_value = None

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

        response = client.post(
            "/api/v1/vulnerabilities/nvd-sync",
            data=json.dumps(payload),
            content_type="application/json",
            headers={"Authorization": "Bearer fake-token"},
        )

        assert response.status_code == 202
        data = json.loads(response.data)
        assert data["stats"]["processed"] == 10
        assert data["stats"]["updated"] == 8
        assert data["stats"]["errors"] == 2

    def test_trigger_nvd_sync_unauthorized(self, client):
        """Test unauthorized access to POST /api/v1/vulnerabilities/nvd-sync."""
        response = client.post(
            "/api/v1/vulnerabilities/nvd-sync",
            data=json.dumps({}),
            content_type="application/json",
        )

        assert response.status_code in [401, 403]

    @patch("apps.api.auth.decorators.verify_jwt")
    def test_trigger_nvd_sync_insufficient_permissions(self, mock_jwt, client):
        """Test POST /api/v1/vulnerabilities/nvd-sync with insufficient permissions."""
        mock_jwt.return_value = {"user_id": 1, "username": "viewer"}

        response = client.post(
            "/api/v1/vulnerabilities/nvd-sync",
            data=json.dumps({}),
            content_type="application/json",
            headers={"Authorization": "Bearer fake-token"},
        )

        # Should fail due to resource_role_required decorator
        assert response.status_code == 403

    @patch("apps.api.auth.decorators.verify_jwt")
    def test_get_nvd_sync_status_success(self, mock_jwt, client, app):
        """Test GET /api/v1/vulnerabilities/nvd-sync/status."""
        mock_jwt.return_value = {"user_id": 1, "username": "admin"}

        response = client.get(
            "/api/v1/vulnerabilities/nvd-sync/status",
            headers={"Authorization": "Bearer fake-token"},
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert "total_cves" in data
        assert "never_synced" in data
        assert "stale_sync" in data
        assert "recently_synced" in data
        assert "needs_sync" in data
        assert isinstance(data["total_cves"], int)
        assert isinstance(data["never_synced"], int)

    @patch("apps.api.auth.decorators.verify_jwt")
    def test_get_nvd_sync_status_counts(self, mock_jwt, client, app):
        """Test GET /api/v1/vulnerabilities/nvd-sync/status returns correct counts."""
        mock_jwt.return_value = {"user_id": 1, "username": "admin"}

        response = client.get(
            "/api/v1/vulnerabilities/nvd-sync/status",
            headers={"Authorization": "Bearer fake-token"},
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        # Verify needs_sync is sum of never_synced and stale_sync
        expected_needs = data["never_synced"] + data["stale_sync"]
        assert data["needs_sync"] == expected_needs

    def test_get_nvd_sync_status_unauthorized(self, client):
        """Test unauthorized access to GET /api/v1/vulnerabilities/nvd-sync/status."""
        response = client.get("/api/v1/vulnerabilities/nvd-sync/status")

        assert response.status_code in [401, 403]

    @patch("apps.api.auth.decorators.verify_jwt")
    @patch("apps.api.auth.decorators.check_resource_role")
    @patch("apps.api.services.sbom.vulnerability.nvd_sync.NVDSyncService")
    def test_trigger_nvd_sync_error_handling(self, mock_service_class, mock_check_role, mock_jwt, client):
        """Test POST /api/v1/vulnerabilities/nvd-sync with service error."""
        mock_jwt.return_value = {"user_id": 1, "username": "admin"}
        mock_check_role.return_value = None

        # Setup mock service to raise error
        mock_service = AsyncMock()
        mock_service.sync_vulnerabilities = AsyncMock(
            side_effect=Exception("NVD API error")
        )
        mock_service_class.return_value = mock_service

        response = client.post(
            "/api/v1/vulnerabilities/nvd-sync",
            data=json.dumps({}),
            content_type="application/json",
            headers={"Authorization": "Bearer fake-token"},
        )

        # Should handle error gracefully
        assert response.status_code in [500, 202]  # Either 500 or graceful 202

    @patch("apps.api.auth.decorators.verify_jwt")
    @patch("apps.api.auth.decorators.check_resource_role")
    @patch("apps.api.services.sbom.vulnerability.nvd_sync.NVDSyncService")
    def test_trigger_nvd_sync_no_vulns(self, mock_service_class, mock_check_role, mock_jwt, client):
        """Test POST /api/v1/vulnerabilities/nvd-sync with no vulnerabilities to sync."""
        mock_jwt.return_value = {"user_id": 1, "username": "admin"}
        mock_check_role.return_value = None

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

        response = client.post(
            "/api/v1/vulnerabilities/nvd-sync",
            data=json.dumps({}),
            content_type="application/json",
            headers={"Authorization": "Bearer fake-token"},
        )

        assert response.status_code == 202
        data = json.loads(response.data)
        assert data["stats"]["processed"] == 0
