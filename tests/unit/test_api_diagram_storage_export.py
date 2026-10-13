"""Diagram storage provider and export API tests.

regression: diagrams-storage-providers-export-phase4b4
"""

import json
import uuid
from datetime import UTC, datetime, timedelta, timezone

import jwt
import pytest
import pytest_asyncio


@pytest.mark.integration
class TestDiagramStorageProviders:
    """Diagram storage provider CRUD tests."""

    @pytest_asyncio.fixture(autouse=True)
    async def setup_db(self, app, test_database_url):
        """Set up test database with tenant, identity, and sample diagram."""
        if not test_database_url:
            pytest.skip("DATABASE_URL not set")

        db = app.db

        def _setup():
            now = datetime.now(UTC)
            tenant_id = db.tenants.insert(
                name="Storage Tenant",
                slug=f"stor-{uuid.uuid4().hex[:8]}",
                is_active=True,
                created_at=now,
                updated_at=now,
            )

            # Create primary user
            email1 = f"stor-user-{uuid.uuid4().hex[:8]}@test.local"
            identity_id_1 = db.identities.insert(
                tenant_id=tenant_id,
                username=email1,
                email=email1,
                identity_type="human",
                auth_provider="local",
                is_active=True,
                is_superuser=False,
                mfa_enabled=False,
                must_change_password=False,
                portal_role="viewer",
                created_at=now,
                updated_at=now,
            )

            # Create secondary user
            email2 = f"stor-user-{uuid.uuid4().hex[:8]}@test.local"
            identity_id_2 = db.identities.insert(
                tenant_id=tenant_id,
                username=email2,
                email=email2,
                identity_type="human",
                auth_provider="local",
                is_active=True,
                is_superuser=False,
                mfa_enabled=False,
                must_change_password=False,
                portal_role="viewer",
                created_at=now,
                updated_at=now,
            )

            # Create sample diagram with version
            diagram_id = db.dg_diagrams.insert(
                tenant_id=tenant_id,
                village_id=uuid.uuid4().hex[:24],
                title="Test Diagram for Export",
                owner_identity_id=identity_id_1,
                created_by_identity_id=identity_id_1,
                updated_by_identity_id=identity_id_1,
                status="draft",
                is_public=False,
                created_at=now,
                updated_at=now,
            )

            # Create version with content
            db.dg_diagram_versions.insert(
                diagram_id=diagram_id,
                tenant_id=tenant_id,
                version_number=1,
                created_by_identity_id=identity_id_1,
                content_json={
                    "nodes": [
                        {
                            "id": "n1",
                            "x": 10,
                            "y": 20,
                            "width": 100,
                            "height": 40,
                            "label": "Web",
                        }
                    ],
                    "edges": [],
                },
                created_at=now,
            )

            db.commit()
            return {
                "tenant_id": tenant_id,
                "identity_id_1": identity_id_1,
                "identity_id_2": identity_id_2,
                "diagram_id": diagram_id,
            }

        from apps.api.utils.async_utils import run_in_threadpool

        self.fixtures = await run_in_threadpool(_setup)

    def _token(self, app, tenant_id, identity_id, scopes=None, roles=None):
        """Create a test JWT token."""
        scopes = scopes or ["diagrams:read", "diagrams:write"]
        # CRITICAL: scope must be a LIST, not a space-joined string
        now = datetime.now(UTC)
        payload = {
            "sub": str(identity_id),
            "iat": now,
            "exp": now + timedelta(hours=1),
            "scope": scopes,
            "tenant": str(self.fixtures["tenant_id"]),
            "identity_id": identity_id,
            "roles": roles if roles is not None else ["admin"],
        }
        secret = app.config.get("JWT_SECRET_KEY") or app.config.get("SECRET_KEY")
        return jwt.encode(payload, secret, algorithm="HS256")

    @pytest.mark.asyncio
    async def test_create_storage_provider_s3(self, app):
        """Test creating an S3 storage provider."""
        client = app.test_client()
        token = self._token(
            app, self.fixtures["tenant_id"], self.fixtures["identity_id_1"]
        )

        payload = {
            "name": "My S3 Bucket",
            "provider_type": "s3",
            "config_json": {
                "endpoint": "https://s3.amazonaws.com",
                "bucket": "my-bucket",
                "access_key": "AKIAIOSFODNN7EXAMPLE",
                "secret_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            },
        }

        response = await client.post(
            "/api/v1/diagram-storage",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 201
        data = await response.get_json()
        assert data["name"] == "My S3 Bucket"
        assert data["provider_type"] == "s3"
        # Secrets should be redacted
        assert data["config_json"]["secret_key"] == "***"

    @pytest.mark.asyncio
    async def test_create_storage_provider_invalid_type(self, app):
        """Test creating provider with invalid type returns 422."""
        client = app.test_client()
        token = self._token(
            app, self.fixtures["tenant_id"], self.fixtures["identity_id_1"]
        )

        payload = {
            "name": "Bad Provider",
            "provider_type": "invalid_type",
            "config_json": {"key": "value"},
        }

        response = await client.post(
            "/api/v1/diagram-storage",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_list_providers_redacts_secrets(self, app):
        """Test listing providers redacts secrets in responses."""
        from apps.api.utils.async_utils import run_in_threadpool

        db = app.db
        now = datetime.now(UTC)

        def _insert_provider():
            provider_id = db.dg_storage_providers.insert(
                tenant_id=self.fixtures["tenant_id"],
                name="Test Provider",
                provider_type="minio",
                config_json={
                    "endpoint": "http://minio.local:9000",
                    "bucket": "diagrams",
                    "access_key": "minioadmin",
                    "secret_key": "minioadmin_secret_password",
                },
                storage_config={"storage_token": "secret123"},
                owner_identity_id=self.fixtures["identity_id_1"],
                is_active=True,
                is_system_default=False,
                created_at=now,
                updated_at=now,
            )
            db.commit()
            return provider_id

        provider_id = await run_in_threadpool(_insert_provider)

        client = app.test_client()
        token = self._token(
            app, self.fixtures["tenant_id"], self.fixtures["identity_id_1"]
        )

        response = await client.get(
            "/api/v1/diagram-storage",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = await response.get_json()
        assert len(data["items"]) > 0

        # Find our provider
        provider = next(p for p in data["items"] if p["id"] == provider_id)
        assert provider["config_json"]["secret_key"] == "***"
        assert provider["storage_config"]["storage_token"] == "***"

    @pytest.mark.asyncio
    async def test_get_provider_not_owner_returns_403(self, app):
        """Test non-owner cannot see another user's provider."""
        from apps.api.utils.async_utils import run_in_threadpool

        db = app.db
        now = datetime.now(UTC)

        def _insert_provider():
            provider_id = db.dg_storage_providers.insert(
                tenant_id=self.fixtures["tenant_id"],
                name="Private Provider",
                provider_type="s3",
                config_json={"endpoint": "https://s3.example.com", "bucket": "b"},
                owner_identity_id=self.fixtures["identity_id_1"],  # Owner is user 1
                is_active=True,
                is_system_default=False,
                created_at=now,
                updated_at=now,
            )
            db.commit()
            return provider_id

        provider_id = await run_in_threadpool(_insert_provider)

        client = app.test_client()
        # Request as user 2 (not owner)
        token = self._token(
            app, self.fixtures["tenant_id"], self.fixtures["identity_id_2"]
        )

        response = await client.get(
            f"/api/v1/diagram-storage/{provider_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_system_default_provider_visible_to_all(self, app):
        """Test system_default providers are visible to all users in tenant."""
        from apps.api.utils.async_utils import run_in_threadpool

        db = app.db
        now = datetime.now(UTC)

        def _insert_system_provider():
            provider_id = db.dg_storage_providers.insert(
                tenant_id=self.fixtures["tenant_id"],
                name="System Default Storage",
                provider_type="s3",
                config_json={"endpoint": "https://s3.example.com", "bucket": "shared"},
                owner_identity_id=self.fixtures["identity_id_1"],
                is_active=True,
                is_system_default=True,
                created_at=now,
                updated_at=now,
            )
            db.commit()
            return provider_id

        provider_id = await run_in_threadpool(_insert_system_provider)

        client = app.test_client()
        # Request as user 2
        token = self._token(
            app, self.fixtures["tenant_id"], self.fixtures["identity_id_2"]
        )

        response = await client.get(
            f"/api/v1/diagram-storage/{provider_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_update_provider_owner_only(self, app):
        """Test only owner can update provider."""
        from apps.api.utils.async_utils import run_in_threadpool

        db = app.db
        now = datetime.now(UTC)

        def _insert_provider():
            provider_id = db.dg_storage_providers.insert(
                tenant_id=self.fixtures["tenant_id"],
                name="Original Name",
                provider_type="s3",
                config_json={"endpoint": "https://s3.example.com", "bucket": "b"},
                owner_identity_id=self.fixtures["identity_id_1"],
                is_active=True,
                is_system_default=False,
                created_at=now,
                updated_at=now,
            )
            db.commit()
            return provider_id

        provider_id = await run_in_threadpool(_insert_provider)

        client = app.test_client()
        # Try to update as user 2 (not owner)
        token = self._token(
            app, self.fixtures["tenant_id"], self.fixtures["identity_id_2"]
        )

        response = await client.patch(
            f"/api/v1/diagram-storage/{provider_id}",
            json={"name": "New Name"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_delete_provider_owner_only(self, app):
        """Test only owner can delete provider."""
        from apps.api.utils.async_utils import run_in_threadpool

        db = app.db
        now = datetime.now(UTC)

        def _insert_provider():
            provider_id = db.dg_storage_providers.insert(
                tenant_id=self.fixtures["tenant_id"],
                name="To Delete",
                provider_type="s3",
                config_json={"endpoint": "https://s3.example.com", "bucket": "b"},
                owner_identity_id=self.fixtures["identity_id_1"],
                is_active=True,
                is_system_default=False,
                created_at=now,
                updated_at=now,
            )
            db.commit()
            return provider_id

        provider_id = await run_in_threadpool(_insert_provider)

        client = app.test_client()
        # Try to delete as user 2 (not owner)
        token = self._token(
            app, self.fixtures["tenant_id"], self.fixtures["identity_id_2"]
        )

        response = await client.delete(
            f"/api/v1/diagram-storage/{provider_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_test_provider_endpoint_validates_config(self, app):
        """Test provider validation endpoint checks required keys."""
        from apps.api.utils.async_utils import run_in_threadpool

        db = app.db
        now = datetime.now(UTC)

        def _insert_incomplete_provider():
            # Provider missing required keys
            provider_id = db.dg_storage_providers.insert(
                tenant_id=self.fixtures["tenant_id"],
                name="Incomplete S3",
                provider_type="s3",
                config_json={
                    "endpoint": "https://s3.example.com",
                    # Missing: bucket, access_key, secret_key
                },
                owner_identity_id=self.fixtures["identity_id_1"],
                is_active=True,
                is_system_default=False,
                created_at=now,
                updated_at=now,
            )
            db.commit()
            return provider_id

        provider_id = await run_in_threadpool(_insert_incomplete_provider)

        client = app.test_client()
        token = self._token(
            app, self.fixtures["tenant_id"], self.fixtures["identity_id_1"]
        )

        response = await client.post(
            f"/api/v1/diagram-storage/{provider_id}/test",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = await response.get_json()
        assert data["ok"] is False
        assert "bucket" in data["missing"]
        assert "access_key" in data["missing"]
        assert "secret_key" in data["missing"]

    @pytest.mark.asyncio
    async def test_test_provider_endpoint_good_config(self, app):
        """Test provider validation endpoint returns ok for complete config."""
        from apps.api.utils.async_utils import run_in_threadpool

        db = app.db
        now = datetime.now(UTC)

        def _insert_good_provider():
            provider_id = db.dg_storage_providers.insert(
                tenant_id=self.fixtures["tenant_id"],
                name="Good GCS",
                provider_type="gcs",
                config_json={
                    "bucket": "my-bucket",
                    "credentials": '{"type": "service_account"}',
                },
                owner_identity_id=self.fixtures["identity_id_1"],
                is_active=True,
                is_system_default=False,
                created_at=now,
                updated_at=now,
            )
            db.commit()
            return provider_id

        provider_id = await run_in_threadpool(_insert_good_provider)

        client = app.test_client()
        token = self._token(
            app, self.fixtures["tenant_id"], self.fixtures["identity_id_1"]
        )

        response = await client.post(
            f"/api/v1/diagram-storage/{provider_id}/test",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = await response.get_json()
        assert data["ok"] is True
        assert len(data["missing"]) == 0

    @pytest.mark.asyncio
    async def test_test_provider_non_owner_returns_403(self, app):
        """Test non-owner cannot test another's provider."""
        from apps.api.utils.async_utils import run_in_threadpool

        db = app.db
        now = datetime.now(UTC)

        def _insert_provider():
            provider_id = db.dg_storage_providers.insert(
                tenant_id=self.fixtures["tenant_id"],
                name="Private",
                provider_type="s3",
                config_json={"endpoint": "https://s3.example.com", "bucket": "b"},
                owner_identity_id=self.fixtures["identity_id_1"],
                is_active=True,
                is_system_default=False,
                created_at=now,
                updated_at=now,
            )
            db.commit()
            return provider_id

        provider_id = await run_in_threadpool(_insert_provider)

        client = app.test_client()
        token = self._token(
            app, self.fixtures["tenant_id"], self.fixtures["identity_id_2"]
        )

        response = await client.post(
            f"/api/v1/diagram-storage/{provider_id}/test",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_non_admin_cannot_set_is_system_default(self, app):
        """Test non-admin user setting is_system_default=True results in False.

        Security: is_system_default is silently forced to False for non-admins.
        """
        client = app.test_client()
        # Token as user 2 with viewer role (not admin)
        token = self._token(
            app,
            self.fixtures["tenant_id"],
            self.fixtures["identity_id_2"],
            scopes=["diagrams:read", "diagrams:write"],
            roles=["viewer"],
        )

        payload = {
            "name": "Non-Admin System Default Attempt",
            "provider_type": "s3",
            "config_json": {"endpoint": "https://s3.example.com", "bucket": "b"},
            "is_system_default": True,  # Try to set as default
        }

        response = await client.post(
            "/api/v1/diagram-storage",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 201
        data = await response.get_json()
        # is_system_default should be False despite request setting it to True
        assert data["is_system_default"] is False

    @pytest.mark.asyncio
    async def test_secrets_redacted_nested_in_config(self, app):
        """Test that nested secrets and credentials are redacted.

        Includes: credentials (dict), private_key, passphrase, etc.
        """
        from apps.api.utils.async_utils import run_in_threadpool

        db = app.db
        now = datetime.now(UTC)

        def _insert_provider_with_nested_secrets():
            provider_id = db.dg_storage_providers.insert(
                tenant_id=self.fixtures["tenant_id"],
                name="Provider with Nested Secrets",
                provider_type="gcs",
                config_json={
                    "bucket": "my-bucket",
                    "credentials": {
                        "type": "service_account",
                        "project_id": "my-project",
                        "private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA...",
                        "private_key_id": "key123",
                        "client_email": "sa@project.iam.gserviceaccount.com",
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    },
                },
                storage_config={"passphrase": "secret_passphrase"},
                owner_identity_id=self.fixtures["identity_id_1"],
                is_active=True,
                is_system_default=False,
                created_at=now,
                updated_at=now,
            )
            db.commit()
            return provider_id

        provider_id = await run_in_threadpool(_insert_provider_with_nested_secrets)

        client = app.test_client()
        token = self._token(
            app, self.fixtures["tenant_id"], self.fixtures["identity_id_1"]
        )

        response = await client.get(
            f"/api/v1/diagram-storage/{provider_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = await response.get_json()

        # Nested credentials should be redacted entirely
        assert data["config_json"]["credentials"] == "***"
        # Nested passphrase should be redacted
        assert data["storage_config"]["passphrase"] == "***"

    @pytest.mark.asyncio
    async def test_require_scope_diagrams_write(self, app):
        """Test storage endpoints require diagrams:write scope."""
        client = app.test_client()

        # Token with only diagrams:read scope
        read_token = self._token(
            app,
            self.fixtures["tenant_id"],
            self.fixtures["identity_id_1"],
            scopes=["diagrams:read"],
        )

        response = await client.post(
            "/api/v1/diagram-storage",
            json={
                "name": "Test",
                "provider_type": "s3",
                "config_json": {"endpoint": "https://s3.example.com", "bucket": "b"},
            },
            headers={"Authorization": f"Bearer {read_token}"},
        )

        assert response.status_code == 403


@pytest.mark.integration
class TestDiagramExport:
    """Diagram export endpoints (JSON, SVG, raster) tests."""

    @pytest_asyncio.fixture(autouse=True)
    async def setup_db(self, app, test_database_url):
        """Set up test database with tenant, identity, and sample diagram."""
        if not test_database_url:
            pytest.skip("DATABASE_URL not set")

        db = app.db

        def _setup():
            now = datetime.now(UTC)
            tenant_id = db.tenants.insert(
                name="Export Tenant",
                slug=f"exp-{uuid.uuid4().hex[:8]}",
                is_active=True,
                created_at=now,
                updated_at=now,
            )

            email = f"exp-user-{uuid.uuid4().hex[:8]}@test.local"
            identity_id = db.identities.insert(
                tenant_id=tenant_id,
                username=email,
                email=email,
                identity_type="human",
                auth_provider="local",
                is_active=True,
                is_superuser=False,
                mfa_enabled=False,
                must_change_password=False,
                portal_role="viewer",
                created_at=now,
                updated_at=now,
            )

            diagram_id = db.dg_diagrams.insert(
                tenant_id=tenant_id,
                village_id=uuid.uuid4().hex[:24],
                title="Export Test Diagram",
                owner_identity_id=identity_id,
                created_by_identity_id=identity_id,
                updated_by_identity_id=identity_id,
                status="draft",
                is_public=False,
                created_at=now,
                updated_at=now,
            )

            db.dg_diagram_versions.insert(
                diagram_id=diagram_id,
                tenant_id=tenant_id,
                version_number=1,
                created_by_identity_id=identity_id,
                content_json={
                    "nodes": [
                        {
                            "id": "n1",
                            "x": 10,
                            "y": 20,
                            "width": 100,
                            "height": 40,
                            "label": "Web",
                        },
                        {
                            "id": "n2",
                            "x": 200,
                            "y": 20,
                            "width": 100,
                            "height": 40,
                            "label": "DB",
                        },
                    ],
                    "edges": [{"source": "n1", "target": "n2"}],
                },
                created_at=now,
            )

            db.commit()
            return {
                "tenant_id": tenant_id,
                "identity_id": identity_id,
                "diagram_id": diagram_id,
            }

        from apps.api.utils.async_utils import run_in_threadpool

        self.fixtures = await run_in_threadpool(_setup)

    def _token(self, app, tenant_id, identity_id, scopes=None):
        """Create a test JWT token."""
        scopes = scopes or ["diagrams:read", "diagrams:write"]
        now = datetime.now(UTC)
        payload = {
            "sub": str(identity_id),
            "iat": now,
            "exp": now + timedelta(hours=1),
            "scope": scopes,
            "tenant": str(tenant_id),
            "identity_id": identity_id,
            "roles": ["admin"],
        }
        secret = app.config.get("JWT_SECRET_KEY") or app.config.get("SECRET_KEY")
        return jwt.encode(payload, secret, algorithm="HS256")

    @pytest.mark.asyncio
    async def test_export_json_returns_content(self, app):
        """Test exporting diagram as JSON returns content_json."""
        client = app.test_client()
        token = self._token(
            app, self.fixtures["tenant_id"], self.fixtures["identity_id"]
        )

        response = await client.get(
            f"/api/v1/diagrams/{self.fixtures['diagram_id']}/export/json",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        assert response.content_type == "application/json"

        # Parse JSON content
        data = json.loads(await response.get_data(as_text=True))
        assert "nodes" in data
        assert "edges" in data
        assert len(data["nodes"]) == 2

    @pytest.mark.asyncio
    async def test_export_svg_returns_svg(self, app):
        """Test exporting diagram as SVG returns SVG markup."""
        client = app.test_client()
        token = self._token(
            app, self.fixtures["tenant_id"], self.fixtures["identity_id"]
        )

        response = await client.get(
            f"/api/v1/diagrams/{self.fixtures['diagram_id']}/export/svg",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        assert "image/svg+xml" in response.content_type

        svg_content = await response.get_data(as_text=True)
        assert "<svg" in svg_content
        assert "Web" in svg_content
        assert "DB" in svg_content

    @pytest.mark.asyncio
    async def test_export_png_returns_501_when_disabled(self, app):
        """Test PNG export returns 501 when raster export flag is disabled."""
        client = app.test_client()
        token = self._token(
            app, self.fixtures["tenant_id"], self.fixtures["identity_id"]
        )

        response = await client.get(
            f"/api/v1/diagrams/{self.fixtures['diagram_id']}/export/png",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 501

    @pytest.mark.asyncio
    async def test_export_pdf_returns_501_when_disabled(self, app):
        """Test PDF export returns 501 when raster export flag is disabled."""
        client = app.test_client()
        token = self._token(
            app, self.fixtures["tenant_id"], self.fixtures["identity_id"]
        )

        response = await client.get(
            f"/api/v1/diagrams/{self.fixtures['diagram_id']}/export/pdf",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 501

    @pytest.mark.asyncio
    async def test_export_invalid_format_returns_400(self, app):
        """Test exporting with invalid format returns 400."""
        client = app.test_client()
        token = self._token(
            app, self.fixtures["tenant_id"], self.fixtures["identity_id"]
        )

        response = await client.get(
            f"/api/v1/diagrams/{self.fixtures['diagram_id']}/export/xyz",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_export_non_readable_diagram_returns_404(self, app):
        """Test exporting private diagram as non-owner returns 404."""
        from apps.api.utils.async_utils import run_in_threadpool

        db = app.db

        def _create_other_diagram():
            now = datetime.now(UTC)
            identity_id_2 = db.identities.insert(
                tenant_id=self.fixtures["tenant_id"],
                username=f"exp-other-{uuid.uuid4().hex[:8]}@test.local",
                email=f"exp-other-{uuid.uuid4().hex[:8]}@test.local",
                identity_type="human",
                auth_provider="local",
                is_active=True,
                is_superuser=False,
                mfa_enabled=False,
                must_change_password=False,
                portal_role="viewer",
                created_at=now,
                updated_at=now,
            )

            diagram_id = db.dg_diagrams.insert(
                tenant_id=self.fixtures["tenant_id"],
                village_id=uuid.uuid4().hex[:24],
                title="Private Diagram",
                owner_identity_id=identity_id_2,
                created_by_identity_id=identity_id_2,
                updated_by_identity_id=identity_id_2,
                status="draft",
                is_public=False,
                created_at=now,
                updated_at=now,
            )

            db.dg_diagram_versions.insert(
                diagram_id=diagram_id,
                tenant_id=self.fixtures["tenant_id"],
                version_number=1,
                created_by_identity_id=identity_id_2,
                content_json={"nodes": [], "edges": []},
                created_at=now,
            )

            db.commit()
            return diagram_id

        other_diagram_id = await run_in_threadpool(_create_other_diagram)

        client = app.test_client()
        # Token as first user (not owner of other_diagram)
        token = self._token(
            app, self.fixtures["tenant_id"], self.fixtures["identity_id"]
        )

        response = await client.get(
            f"/api/v1/diagrams/{other_diagram_id}/export/json",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_require_scope_diagrams_read(self, app):
        """Test export endpoints require diagrams:read scope."""
        client = app.test_client()

        # Token with no relevant scopes
        token = self._token(
            app,
            self.fixtures["tenant_id"],
            self.fixtures["identity_id"],
            scopes=["other:read"],
        )

        response = await client.get(
            f"/api/v1/diagrams/{self.fixtures['diagram_id']}/export/json",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 403
