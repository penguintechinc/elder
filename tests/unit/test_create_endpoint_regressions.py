"""Regression tests for create-path 500s (branch: fix/create-endpoint-500s).

Covers 4 root-cause bugs that caused CREATE endpoints to return 500 instead
of 201:

1. POST /api/v1/identities — insert omitted `is_superuser` (NOT NULL, no
   DB default) -> IntegrityError.
2. POST /api/v1/services — auto-created sbom_scans row passed `updated_at`,
   not a real column -> CompileError.
3. POST /api/v1/dependencies — `@validated_request` came from
   penguin_libs.pydantic (Flask/werkzeug request-context based), plus a
   leftover `from flask import g` in the handler body -> both raise
   RuntimeError under Quart (no Flask app/request context).
4. POST /api/v1/data-stores — `size_bytes` was a 32-bit Integer column in
   the SQLAlchemy model while the live schema is BIGINT -> DataError on
   values > 2**31.

Each test hits the real Quart route (async_client) against the real test
Postgres database — not mocks — so the assertions fail if any of these
regress.
"""

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from quart import current_app


def _get_or_create_tenant(db, slug: str, name: str) -> int:
    """Get or create a tenant row by slug, returning its id."""
    tenant = db(db.tenants.slug == slug).select().first()
    if tenant:
        return tenant.id
    tenant_id = db.tenants.insert(name=name, slug=slug, is_active=True)
    db.commit()
    return tenant_id


def _create_org(db, tenant_id: int, name: str) -> int:
    """Create an organization scoped to the given tenant, returning its id."""
    now = datetime.now(timezone.utc)
    org_id = db.organizations.insert(
        name=name, tenant_id=tenant_id, created_at=now, updated_at=now
    )
    db.commit()
    return org_id


def _superuser_mock(tenant_id: int) -> MagicMock:
    """Build a mock current_user that bypasses permission/scope checks."""
    user = MagicMock()
    user.id = 1
    user.username = "test-admin"
    user.is_superuser = True
    user.tenant_id = tenant_id
    return user


class TestCreateIdentityRegression:
    """regression: create-endpoint-500s — identities insert missing is_superuser."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_identity_returns_201(self, mock_get_user, async_client, app):
        async with app.app_context():
            db = current_app.db
            tenant_id = _get_or_create_tenant(
                db, "create-regress-identities", "Create Regress Identities"
            )

        mock_get_user.return_value = _superuser_mock(tenant_id)

        username = f"regress-user-create-{uuid.uuid4().hex[:8]}"
        payload = {
            "username": username,
            "identity_type": "human",
            "auth_provider": "local",
            "password": "SuperSecret123!",
            "email": "regress-create@example.com",
            "tenant_id": tenant_id,
        }

        response = await async_client.post(
            "/api/v1/identities",
            json=payload,
            headers={"Authorization": "Bearer fake-token"},
        )

        body = await response.get_data()
        assert response.status_code == 201, (
            f"expected 201, got {response.status_code}: {body}"
        )
        data = json.loads(body)
        assert data["username"] == username

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_identity_defaults_is_superuser_false(
        self, mock_get_user, async_client, app
    ):
        async with app.app_context():
            db = current_app.db
            tenant_id = _get_or_create_tenant(
                db, "create-regress-identities-2", "Create Regress Identities 2"
            )

        mock_get_user.return_value = _superuser_mock(tenant_id)

        username = f"regress-user-default-su-{uuid.uuid4().hex[:8]}"
        payload = {
            "username": username,
            "identity_type": "service_account",
            "auth_provider": "local",
            "password": "SuperSecret123!",
            "tenant_id": tenant_id,
        }

        response = await async_client.post(
            "/api/v1/identities",
            json=payload,
            headers={"Authorization": "Bearer fake-token"},
        )

        assert response.status_code == 201
        async with app.app_context():
            db = current_app.db
            row = db(db.identities.username == username).select().first()
            assert row is not None
            assert row.is_superuser is False


class TestCreateServiceRegression:
    """regression: create-endpoint-500s — sbom_scans insert passed non-column `updated_at`."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_service_with_repository_url_returns_201(
        self, mock_get_user, async_client, app
    ):
        async with app.app_context():
            db = current_app.db
            tenant_id = _get_or_create_tenant(
                db, "create-regress-services", "Create Regress Services"
            )
            org_id = _create_org(db, tenant_id, "Regress Services Org")

        mock_get_user.return_value = _superuser_mock(tenant_id)

        payload = {
            "name": "regress-service",
            "organization_id": org_id,
            "repository_url": "https://example.com/regress/service.git",
        }

        response = await async_client.post(
            "/api/v1/services",
            json=payload,
            headers={"Authorization": "Bearer fake-token"},
        )

        body = await response.get_data()
        assert response.status_code == 201, (
            f"expected 201, got {response.status_code}: {body}"
        )
        data = json.loads(body)
        assert data["name"] == "regress-service"

        # The repository_url triggers an auto-created sbom_scans row; verify
        # it actually landed (the CompileError previously happened inside
        # this insert and rolled back the whole request).
        async with app.app_context():
            db = current_app.db
            scan = (
                db(
                    (db.sbom_scans.parent_type == "service")
                    & (db.sbom_scans.status == "pending")
                )
                .select(orderby=~db.sbom_scans.id)
                .first()
            )
            assert scan is not None
            assert scan.repository_url == "https://example.com/regress/service.git"


class TestCreateDependencyRegression:
    """regression: create-endpoint-500s — Flask-based validated_request/g under Quart."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_dependency_returns_201(self, mock_get_user, async_client, app):
        async with app.app_context():
            db = current_app.db
            tenant_id = _get_or_create_tenant(
                db, "create-regress-deps", "Create Regress Deps"
            )
            source_org_id = _create_org(db, tenant_id, "Regress Dep Source Org")
            target_org_id = _create_org(db, tenant_id, "Regress Dep Target Org")

        mock_get_user.return_value = _superuser_mock(tenant_id)

        payload = {
            "source_type": "organization",
            "source_id": source_org_id,
            "target_type": "organization",
            "target_id": target_org_id,
            "dependency_type": "depends_on",
        }

        response = await async_client.post(
            "/api/v1/dependencies",
            json=payload,
            headers={"Authorization": "Bearer fake-token"},
        )

        body = await response.get_data()
        assert response.status_code == 201, (
            f"expected 201, got {response.status_code}: {body}"
        )
        data = json.loads(body)
        assert data["source_id"] == source_org_id
        assert data["target_id"] == target_org_id


class TestCreateDataStoreRegression:
    """regression: create-endpoint-500s — size_bytes was 32-bit Integer vs BIGINT schema."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_data_store_with_large_size_bytes_returns_201(
        self, mock_get_user, async_client, app
    ):
        async with app.app_context():
            db = current_app.db
            tenant_id = _get_or_create_tenant(
                db, "create-regress-datastores", "Create Regress Data Stores"
            )
            org_id = _create_org(db, tenant_id, "Regress Data Store Org")

        mock_get_user.return_value = _superuser_mock(tenant_id)

        large_size_bytes = 2**31 + 1_000_000  # overflows 32-bit signed Integer

        payload = {
            "name": "regress-data-store",
            "organization_id": org_id,
            "storage_type": "blob_storage",
            "size_bytes": large_size_bytes,
        }

        response = await async_client.post(
            "/api/v1/data-stores",
            json=payload,
            headers={"Authorization": "Bearer fake-token"},
        )

        body = await response.get_data()
        assert response.status_code == 201, (
            f"expected 201, got {response.status_code}: {body}"
        )
        data = json.loads(body)
        assert data["size_bytes"] == large_size_bytes
