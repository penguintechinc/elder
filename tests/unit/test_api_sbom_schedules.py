"""
Unit tests for SBOM Schedule API endpoints.

These tests use mocked authentication and database connections.
No external network calls or real database required.
"""

import datetime
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestSBOMSchedulesAPI:
    """Test SBOM Schedules API endpoints."""

    @patch("apps.api.auth.decorators.verify_jwt")
    @patch("apps.api.utils.validation_helpers.validate_resource_exists")
    async def test_list_schedules(self, mock_validate, mock_jwt, client, app):
        """Test GET /api/v1/sbom/schedules."""
        mock_jwt.return_value = {"user_id": 1, "username": "test"}

        response = client.get(
            "/api/v1/sbom/schedules",
            headers={"Authorization": "Bearer fake-token"},
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert "items" in data or "total" in data

    @patch("apps.api.auth.decorators.verify_jwt")
    @patch("shared.async_utils.run_in_threadpool")
    async def test_list_schedules_with_filters(self, mock_threadpool, mock_jwt, client):
        """Test GET /api/v1/sbom/schedules with filters."""
        mock_jwt.return_value = {"user_id": 1, "username": "test"}

        # Mock the database query results
        mock_threadpool.return_value = (2, [])

        response = client.get(
            "/api/v1/sbom/schedules?parent_type=service&is_active=true&page=1&per_page=50",
            headers={"Authorization": "Bearer fake-token"},
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert "items" in data

    @patch("apps.api.auth.decorators.verify_jwt")
    @patch("apps.api.auth.decorators.resource_role_required")
    @patch("apps.api.utils.validation_helpers.validate_json_body")
    @patch("apps.api.utils.validation_helpers.validate_required_fields")
    @patch("shared.async_utils.run_in_threadpool")
    async def test_create_schedule_success(
        self, mock_threadpool, mock_req_fields, mock_json, mock_role, mock_jwt, client
    ):
        """Test POST /api/v1/sbom/schedules - successful creation."""
        mock_jwt.return_value = {"user_id": 1, "username": "admin"}
        mock_role.return_value = lambda f: f
        mock_json.return_value = None
        mock_req_fields.return_value = None

        # Mock threadpool calls: validate parent, then create
        parent_record = MagicMock()
        schedule_record = MagicMock()
        schedule_record.id = 1
        schedule_record.parent_type = "service"
        schedule_record.parent_id = 1
        schedule_record.schedule_cron = "0 0 * * *"
        schedule_record.is_active = True
        schedule_record.next_run_at = datetime.datetime.now(datetime.UTC)
        schedule_record.created_at = datetime.datetime.now(datetime.UTC)
        schedule_record.updated_at = datetime.datetime.now(datetime.UTC)
        schedule_record.tenant_id = 1
        schedule_record.village_id = "a1b2-c3d4-e5f67890"

        mock_threadpool.side_effect = [parent_record, schedule_record]

        payload = {
            "parent_type": "service",
            "parent_id": 1,
            "schedule_cron": "0 0 * * *",
            "is_active": True,
        }

        with patch("apps.api.v1.sbom_schedules.request") as mock_request:
            mock_request.get_json.return_value = payload

            response = client.post(
                "/api/v1/sbom/schedules",
                data=json.dumps(payload),
                content_type="application/json",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert response.status_code in [200, 201]
        data = json.loads(response.data)
        assert data["parent_type"] == "service"

    @patch("apps.api.auth.decorators.verify_jwt")
    @patch("apps.api.auth.decorators.resource_role_required")
    @patch("apps.api.utils.validation_helpers.validate_json_body")
    @patch("apps.api.utils.validation_helpers.validate_required_fields")
    async def test_create_schedule_missing_required(
        self, mock_req_fields, mock_json, mock_role, mock_jwt, client
    ):
        """Test POST /api/v1/sbom/schedules - missing required fields."""
        mock_jwt.return_value = {"user_id": 1, "username": "admin"}
        mock_role.return_value = lambda f: f
        mock_json.return_value = None
        mock_req_fields.return_value = (
            {"error": "Missing required fields", "status": 400},
            400,
        )

        payload = {"parent_type": "service"}

        response = client.post(
            "/api/v1/sbom/schedules",
            data=json.dumps(payload),
            content_type="application/json",
            headers={"Authorization": "Bearer fake-token"},
        )

        assert response.status_code in [400, 422]

    @patch("apps.api.auth.decorators.verify_jwt")
    @patch("apps.api.auth.decorators.resource_role_required")
    @patch("apps.api.utils.validation_helpers.validate_json_body")
    @patch("apps.api.utils.validation_helpers.validate_required_fields")
    async def test_create_schedule_invalid_cron(
        self, mock_req_fields, mock_json, mock_role, mock_jwt, client
    ):
        """Test POST /api/v1/sbom/schedules - invalid cron expression."""
        mock_jwt.return_value = {"user_id": 1, "username": "admin"}
        mock_role.return_value = lambda f: f
        mock_json.return_value = None
        mock_req_fields.return_value = None

        payload = {
            "parent_type": "service",
            "parent_id": 1,
            "schedule_cron": "INVALID",
        }

        with patch("apps.api.v1.sbom_schedules.request") as mock_request:
            mock_request.get_json.return_value = payload

            response = client.post(
                "/api/v1/sbom/schedules",
                data=json.dumps(payload),
                content_type="application/json",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert response.status_code in [400, 422]

    @patch("apps.api.auth.decorators.verify_jwt")
    @patch("apps.api.utils.validation_helpers.validate_resource_exists")
    async def test_get_schedule_success(self, mock_validate, mock_jwt, client):
        """Test GET /api/v1/sbom/schedules/:id - success."""
        mock_jwt.return_value = {"user_id": 1, "username": "test"}

        schedule_record = MagicMock()
        schedule_record.id = 1
        schedule_record.parent_type = "service"
        schedule_record.parent_id = 1
        schedule_record.schedule_cron = "0 0 * * *"
        schedule_record.is_active = True
        schedule_record.created_at = datetime.datetime.now(datetime.UTC)
        schedule_record.updated_at = datetime.datetime.now(datetime.UTC)
        schedule_record.next_run_at = datetime.datetime.now(datetime.UTC)
        schedule_record.tenant_id = 1
        schedule_record.village_id = "a1b2-c3d4-e5f67890"

        mock_validate.return_value = (schedule_record, None)

        response = client.get(
            "/api/v1/sbom/schedules/1",
            headers={"Authorization": "Bearer fake-token"},
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["id"] == 1

    @patch("apps.api.auth.decorators.verify_jwt")
    @patch("apps.api.utils.validation_helpers.validate_resource_exists")
    async def test_get_schedule_not_found(self, mock_validate, mock_jwt, client):
        """Test GET /api/v1/sbom/schedules/:id - not found."""
        mock_jwt.return_value = {"user_id": 1, "username": "test"}

        mock_validate.return_value = (
            None,
            ({"error": "Not found", "status": 404}, 404),
        )

        response = client.get(
            "/api/v1/sbom/schedules/999",
            headers={"Authorization": "Bearer fake-token"},
        )

        assert response.status_code == 404

    @patch("apps.api.auth.decorators.verify_jwt")
    @patch("apps.api.auth.decorators.resource_role_required")
    @patch("apps.api.utils.validation_helpers.validate_json_body")
    @patch("apps.api.utils.validation_helpers.validate_resource_exists")
    @patch("shared.async_utils.run_in_threadpool")
    async def test_update_schedule_success(
        self, mock_threadpool, mock_validate, mock_json, mock_role, mock_jwt, client
    ):
        """Test PUT /api/v1/sbom/schedules/:id - successful update."""
        mock_jwt.return_value = {"user_id": 1, "username": "admin"}
        mock_role.return_value = lambda f: f
        mock_json.return_value = None

        schedule_record = MagicMock()
        schedule_record.id = 1
        schedule_record.parent_type = "service"
        schedule_record.parent_id = 1
        schedule_record.schedule_cron = "0 6 * * *"
        schedule_record.is_active = False
        schedule_record.created_at = datetime.datetime.now(datetime.UTC)
        schedule_record.updated_at = datetime.datetime.now(datetime.UTC)
        schedule_record.next_run_at = datetime.datetime.now(datetime.UTC)
        schedule_record.tenant_id = 1
        schedule_record.village_id = "a1b2-c3d4-e5f67890"

        mock_validate.return_value = (schedule_record, None)
        mock_threadpool.return_value = schedule_record

        payload = {"is_active": False}

        with patch("apps.api.v1.sbom_schedules.request") as mock_request:
            mock_request.get_json.return_value = payload

            response = client.put(
                "/api/v1/sbom/schedules/1",
                data=json.dumps(payload),
                content_type="application/json",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert response.status_code == 200

    @patch("apps.api.auth.decorators.verify_jwt")
    @patch("apps.api.auth.decorators.resource_role_required")
    @patch("apps.api.utils.validation_helpers.validate_json_body")
    @patch("apps.api.utils.validation_helpers.validate_resource_exists")
    async def test_update_schedule_not_found(
        self, mock_validate, mock_json, mock_role, mock_jwt, client
    ):
        """Test PUT /api/v1/sbom/schedules/:id - not found."""
        mock_jwt.return_value = {"user_id": 1, "username": "admin"}
        mock_role.return_value = lambda f: f
        mock_json.return_value = None

        mock_validate.return_value = (
            None,
            ({"error": "Not found", "status": 404}, 404),
        )

        payload = {"is_active": False}

        response = client.put(
            "/api/v1/sbom/schedules/999",
            data=json.dumps(payload),
            content_type="application/json",
            headers={"Authorization": "Bearer fake-token"},
        )

        assert response.status_code == 404

    @patch("apps.api.auth.decorators.verify_jwt")
    @patch("apps.api.auth.decorators.resource_role_required")
    @patch("apps.api.utils.validation_helpers.validate_resource_exists")
    @patch("shared.async_utils.run_in_threadpool")
    async def test_delete_schedule_success(
        self, mock_threadpool, mock_validate, mock_role, mock_jwt, client
    ):
        """Test DELETE /api/v1/sbom/schedules/:id - successful deletion."""
        mock_jwt.return_value = {"user_id": 1, "username": "admin"}
        mock_role.return_value = lambda f: f

        schedule_record = MagicMock()
        schedule_record.id = 1

        mock_validate.return_value = (schedule_record, None)
        mock_threadpool.return_value = None

        response = client.delete(
            "/api/v1/sbom/schedules/1",
            headers={"Authorization": "Bearer fake-token"},
        )

        assert response.status_code == 204

    @patch("apps.api.auth.decorators.verify_jwt")
    @patch("apps.api.auth.decorators.resource_role_required")
    @patch("apps.api.utils.validation_helpers.validate_resource_exists")
    async def test_delete_schedule_not_found(
        self, mock_validate, mock_role, mock_jwt, client
    ):
        """Test DELETE /api/v1/sbom/schedules/:id - not found."""
        mock_jwt.return_value = {"user_id": 1, "username": "admin"}
        mock_role.return_value = lambda f: f

        mock_validate.return_value = (
            None,
            ({"error": "Not found", "status": 404}, 404),
        )

        response = client.delete(
            "/api/v1/sbom/schedules/999",
            headers={"Authorization": "Bearer fake-token"},
        )

        assert response.status_code == 404

    @patch("apps.api.auth.decorators.verify_jwt")
    @patch("shared.async_utils.run_in_threadpool")
    async def test_get_due_schedules_empty(self, mock_threadpool, mock_jwt, client):
        """Test GET /api/v1/sbom/schedules/due - empty list."""
        mock_jwt.return_value = {"user_id": 1, "username": "worker"}

        mock_threadpool.return_value = []

        response = client.get(
            "/api/v1/sbom/schedules/due",
            headers={"Authorization": "Bearer fake-token"},
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert isinstance(data, list)
        assert len(data) == 0

    @patch("apps.api.auth.decorators.verify_jwt")
    @patch("shared.async_utils.run_in_threadpool")
    async def test_get_due_schedules_with_results(
        self, mock_threadpool, mock_jwt, client
    ):
        """Test GET /api/v1/sbom/schedules/due - with results."""
        mock_jwt.return_value = {"user_id": 1, "username": "worker"}

        schedule_record = MagicMock()
        schedule_record.id = 1
        schedule_record.parent_type = "service"
        schedule_record.parent_id = 1
        schedule_record.schedule_cron = "0 0 * * *"
        schedule_record.is_active = True
        schedule_record.created_at = datetime.datetime.now(datetime.UTC)
        schedule_record.updated_at = datetime.datetime.now(datetime.UTC)
        schedule_record.next_run_at = datetime.datetime.now(datetime.UTC)
        schedule_record.tenant_id = 1
        schedule_record.village_id = "a1b2-c3d4-e5f67890"

        mock_threadpool.return_value = [schedule_record]

        response = client.get(
            "/api/v1/sbom/schedules/due",
            headers={"Authorization": "Bearer fake-token"},
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert isinstance(data, list)
        assert len(data) > 0

    @patch("apps.api.auth.decorators.verify_jwt")
    @patch("apps.api.auth.decorators.resource_role_required")
    @patch("apps.api.utils.validation_helpers.validate_json_body")
    @patch("apps.api.utils.validation_helpers.validate_required_fields")
    @patch("shared.async_utils.run_in_threadpool")
    async def test_create_schedule_with_credentials(
        self, mock_threadpool, mock_req_fields, mock_json, mock_role, mock_jwt, client
    ):
        """Test POST /api/v1/sbom/schedules - with credentials."""
        mock_jwt.return_value = {"user_id": 1, "username": "admin"}
        mock_role.return_value = lambda f: f
        mock_json.return_value = None
        mock_req_fields.return_value = None

        parent_record = MagicMock()
        schedule_record = MagicMock()
        schedule_record.id = 1
        schedule_record.parent_type = "service"
        schedule_record.parent_id = 1
        schedule_record.schedule_cron = "0 0 * * *"
        schedule_record.is_active = True
        schedule_record.credential_type = "vault"
        schedule_record.credential_id = 5
        schedule_record.next_run_at = datetime.datetime.now(datetime.UTC)
        schedule_record.created_at = datetime.datetime.now(datetime.UTC)
        schedule_record.updated_at = datetime.datetime.now(datetime.UTC)
        schedule_record.tenant_id = 1
        schedule_record.village_id = "a1b2-c3d4-e5f67890"

        mock_threadpool.side_effect = [parent_record, schedule_record]

        payload = {
            "parent_type": "service",
            "parent_id": 1,
            "schedule_cron": "0 0 * * *",
            "is_active": True,
            "credential_type": "vault",
            "credential_id": 5,
        }

        with patch("apps.api.v1.sbom_schedules.request") as mock_request:
            mock_request.get_json.return_value = payload

            response = client.post(
                "/api/v1/sbom/schedules",
                data=json.dumps(payload),
                content_type="application/json",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert response.status_code in [200, 201]

    @patch("apps.api.auth.decorators.verify_jwt")
    @patch("apps.api.auth.decorators.resource_role_required")
    @patch("apps.api.utils.validation_helpers.validate_json_body")
    @patch("apps.api.utils.validation_helpers.validate_resource_exists")
    @patch("shared.async_utils.run_in_threadpool")
    async def test_update_schedule_with_cron(
        self, mock_threadpool, mock_validate, mock_json, mock_role, mock_jwt, client
    ):
        """Test PUT /api/v1/sbom/schedules/:id - update cron expression."""
        mock_jwt.return_value = {"user_id": 1, "username": "admin"}
        mock_role.return_value = lambda f: f
        mock_json.return_value = None

        schedule_record = MagicMock()
        schedule_record.id = 1
        schedule_record.parent_type = "service"
        schedule_record.parent_id = 1
        schedule_record.schedule_cron = "0 6 * * *"
        schedule_record.is_active = True
        schedule_record.created_at = datetime.datetime.now(datetime.UTC)
        schedule_record.updated_at = datetime.datetime.now(datetime.UTC)
        schedule_record.next_run_at = datetime.datetime.now(datetime.UTC)
        schedule_record.tenant_id = 1
        schedule_record.village_id = "a1b2-c3d4-e5f67890"

        mock_validate.return_value = (schedule_record, None)
        mock_threadpool.return_value = schedule_record

        payload = {"schedule_cron": "0 6 * * *"}

        with patch("apps.api.v1.sbom_schedules.request") as mock_request:
            mock_request.get_json.return_value = payload

            response = client.put(
                "/api/v1/sbom/schedules/1",
                data=json.dumps(payload),
                content_type="application/json",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert response.status_code == 200

    @patch("apps.api.auth.decorators.verify_jwt")
    async def test_list_schedules_unauthorized(self, mock_jwt, client):
        """Test GET /api/v1/sbom/schedules - unauthorized."""
        mock_jwt.side_effect = Exception("Unauthorized")

        response = client.get(
            "/api/v1/sbom/schedules",
            headers={"Authorization": "Bearer invalid-token"},
        )

        assert response.status_code == 401

    @patch("apps.api.auth.decorators.verify_jwt")
    @patch("apps.api.auth.decorators.resource_role_required")
    async def test_create_schedule_insufficient_permissions(
        self, mock_role, mock_jwt, client
    ):
        """Test POST /api/v1/sbom/schedules - insufficient permissions."""
        mock_jwt.return_value = {"user_id": 1, "username": "guest"}
        mock_role.side_effect = Exception("Insufficient permissions")

        payload = {
            "parent_type": "service",
            "parent_id": 1,
            "schedule_cron": "0 0 * * *",
        }

        response = client.post(
            "/api/v1/sbom/schedules",
            data=json.dumps(payload),
            content_type="application/json",
            headers={"Authorization": "Bearer fake-token"},
        )

        assert response.status_code == 403
