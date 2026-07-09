"""Diagrams API tests (CRUD + version management).

regression: diagrams-crud-phase4b1
"""

import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
import pytest_asyncio


@pytest.mark.integration
class TestDiagrams:
    """Diagram CRUD and versioning tests."""

    @pytest_asyncio.fixture(autouse=True)
    async def setup_db(self, app, test_database_url):
        """Set up test database with tenant and identity."""
        if not test_database_url:
            pytest.skip("DATABASE_URL not set")

        db = app.db

        def _setup():
            now = datetime.now(timezone.utc)
            tenant_id = db.tenants.insert(
                name="Diagram Tenant",
                slug=f"dgm-{uuid.uuid4().hex[:8]}",
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            email = f"dg-user-{uuid.uuid4().hex[:8]}@test.local"
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
            db.commit()
            return {"tenant_id": tenant_id, "identity_id": identity_id}

        from apps.api.utils.async_utils import run_in_threadpool

        self.fixtures = await run_in_threadpool(_setup)

    def _token(self, app, tenant_id, identity_id, scopes=None, roles=None):
        """Create a test JWT token."""
        scopes = scopes or ["diagrams:read", "diagrams:write"]
        # Elder's require_scope reads g.claims["scope"] as a LIST (set()-ified);
        # a space-joined string would be split into characters. Pass the list.
        now = datetime.now(timezone.utc)
        payload = {
            "sub": str(identity_id),
            "iat": now,
            "exp": now + timedelta(hours=1),
            "scope": scopes,
            "tenant": str(tenant_id),
            "identity_id": identity_id,
            "roles": roles if roles is not None else ["admin"],
        }
        secret = app.config.get("JWT_SECRET_KEY") or app.config.get("SECRET_KEY")
        return jwt.encode(payload, secret, algorithm="HS256")

    @pytest.mark.asyncio
    async def test_public_diagram_reader_cannot_edit(self, app):
        """Priv-esc regression: a non-owner who can READ a public diagram must
        NOT be able to update/delete/save it (read-ability != edit-ability).

        regression: security-review-diagrams-write-auth
        """
        from apps.api.utils.async_utils import run_in_threadpool

        db = app.db
        t = self.fixtures["tenant_id"]
        owner = self.fixtures["identity_id"]

        def _mk_attacker():
            now = datetime.now(timezone.utc)
            email = f"dg-attacker-{uuid.uuid4().hex[:8]}@test.local"
            aid = db.identities.insert(
                tenant_id=t,
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
            db.commit()
            return aid

        attacker_id = await run_in_threadpool(_mk_attacker)

        client = app.test_client()
        owner_token = self._token(app, t, owner)
        resp = await client.post(
            "/api/v1/diagrams",
            json={"title": "Public Diagram", "is_public": True},
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert resp.status_code == 201
        diagram_id = (await resp.get_json())["id"]

        attacker = self._token(app, t, attacker_id, ["diagrams:read", "diagrams:write"])
        # Attacker CAN read the public diagram...
        got = await client.get(
            f"/api/v1/diagrams/{diagram_id}",
            headers={"Authorization": f"Bearer {attacker}"},
        )
        assert got.status_code == 200
        # ...but must NOT be able to modify it.
        patched = await client.patch(
            f"/api/v1/diagrams/{diagram_id}",
            json={"title": "Hijacked"},
            headers={"Authorization": f"Bearer {attacker}"},
        )
        assert patched.status_code in (403, 404)
        deleted = await client.delete(
            f"/api/v1/diagrams/{diagram_id}",
            headers={"Authorization": f"Bearer {attacker}"},
        )
        assert deleted.status_code in (403, 404)
        saved = await client.post(
            f"/api/v1/diagrams/{diagram_id}/versions",
            json={"content": {"nodes": [], "edges": []}},
            headers={"Authorization": f"Bearer {attacker}"},
        )
        assert saved.status_code in (403, 404)

    @pytest.mark.asyncio
    async def test_create_diagram_draft(self, app):
        """Test creating a diagram in draft status."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id"]
        token = self._token(app, tenant_id, identity_id)

        client = app.test_client()
        response = await client.post(
            "/api/v1/diagrams",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "title": "My Diagram",
                "description": "A test diagram",
                "tags": ["test", "example"],
            },
        )

        assert response.status_code == 201
        data = await response.get_json()
        assert data["title"] == "My Diagram"
        assert data["status"] == "draft"
        assert data["is_public"] is False
        assert data["description"] == "A test diagram"

    @pytest.mark.asyncio
    async def test_create_diagram_with_content(self, app):
        """Test creating a diagram with initial content."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id"]
        token = self._token(app, tenant_id, identity_id)

        content = {"nodes": [{"id": "1", "label": "Node 1"}], "edges": []}

        client = app.test_client()
        response = await client.post(
            "/api/v1/diagrams",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "title": "Diagram with Content",
                "content": content,
            },
        )

        assert response.status_code == 201
        data = await response.get_json()
        diagram_id = data["id"]

        # Verify content was saved in version
        response = await client.get(
            f"/api/v1/diagrams/{diagram_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        diagram_data = await response.get_json()
        assert diagram_data["content"] == content

    @pytest.mark.asyncio
    async def test_list_diagrams(self, app):
        """Test listing diagrams."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id"]
        token = self._token(app, tenant_id, identity_id)
        db = app.db

        # Create multiple diagrams
        def create_diagrams():
            now = datetime.now(timezone.utc)
            for i in range(3):
                db.dg_diagrams.insert(
                    tenant_id=tenant_id,
                    village_id=f"village-{i}-{uuid.uuid4().hex[:12]}",
                    title=f"Diagram {i}",
                    owner_identity_id=identity_id,
                    created_by_identity_id=identity_id,
                    updated_by_identity_id=identity_id,
                    status="draft",
                    created_at=now,
                    updated_at=now,
                )
            db.commit()

        from apps.api.utils.async_utils import run_in_threadpool

        await run_in_threadpool(create_diagrams)

        client = app.test_client()
        response = await client.get(
            "/api/v1/diagrams",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = await response.get_json()
        assert len(data["items"]) == 3
        assert data["pagination"]["total"] == 3

    @pytest.mark.asyncio
    async def test_list_diagrams_with_status_filter(self, app):
        """Test listing diagrams with status filter."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id"]
        token = self._token(app, tenant_id, identity_id)
        db = app.db

        def create_diagrams():
            now = datetime.now(timezone.utc)
            db.dg_diagrams.insert(
                tenant_id=tenant_id,
                village_id=uuid.uuid4().hex[:24],
                title="Draft Diagram",
                owner_identity_id=identity_id,
                created_by_identity_id=identity_id,
                updated_by_identity_id=identity_id,
                status="draft",
                created_at=now,
                updated_at=now,
            )
            db.dg_diagrams.insert(
                tenant_id=tenant_id,
                village_id=uuid.uuid4().hex[:24],
                title="Active Diagram",
                owner_identity_id=identity_id,
                created_by_identity_id=identity_id,
                updated_by_identity_id=identity_id,
                status="active",
                created_at=now,
                updated_at=now,
            )
            db.commit()

        from apps.api.utils.async_utils import run_in_threadpool

        await run_in_threadpool(create_diagrams)

        client = app.test_client()
        # Filter for draft only
        response = await client.get(
            "/api/v1/diagrams?status=draft",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = await response.get_json()
        assert len(data["items"]) == 1
        assert data["items"][0]["status"] == "draft"

    @pytest.mark.asyncio
    async def test_get_diagram(self, app):
        """Test getting a diagram by ID."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id"]
        token = self._token(app, tenant_id, identity_id)
        db = app.db

        diagram_id = None

        def create_diagram():
            nonlocal diagram_id
            now = datetime.now(timezone.utc)
            diagram_id = db.dg_diagrams.insert(
                tenant_id=tenant_id,
                village_id=uuid.uuid4().hex[:24],
                title="Test Diagram",
                owner_identity_id=identity_id,
                created_by_identity_id=identity_id,
                updated_by_identity_id=identity_id,
                status="draft",
                created_at=now,
                updated_at=now,
            )
            db.commit()

        from apps.api.utils.async_utils import run_in_threadpool

        await run_in_threadpool(create_diagram)

        client = app.test_client()
        response = await client.get(
            f"/api/v1/diagrams/{diagram_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = await response.get_json()
        assert data["id"] == diagram_id
        assert data["title"] == "Test Diagram"

    @pytest.mark.asyncio
    async def test_get_nonexistent_diagram(self, app):
        """Test getting a diagram that doesn't exist."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id"]
        token = self._token(app, tenant_id, identity_id)

        client = app.test_client()
        response = await client.get(
            "/api/v1/diagrams/9999",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_update_diagram(self, app):
        """Test updating a diagram."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id"]
        token = self._token(app, tenant_id, identity_id)
        db = app.db

        diagram_id = None

        def create_diagram():
            nonlocal diagram_id
            now = datetime.now(timezone.utc)
            diagram_id = db.dg_diagrams.insert(
                tenant_id=tenant_id,
                village_id=uuid.uuid4().hex[:24],
                title="Original Title",
                description="Original description",
                owner_identity_id=identity_id,
                created_by_identity_id=identity_id,
                updated_by_identity_id=identity_id,
                status="draft",
                created_at=now,
                updated_at=now,
            )
            db.commit()

        from apps.api.utils.async_utils import run_in_threadpool

        await run_in_threadpool(create_diagram)

        client = app.test_client()
        response = await client.patch(
            f"/api/v1/diagrams/{diagram_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "title": "Updated Title",
                "status": "active",
            },
        )

        assert response.status_code == 200
        data = await response.get_json()
        assert data["title"] == "Updated Title"
        assert data["status"] == "active"

    @pytest.mark.asyncio
    async def test_delete_diagram(self, app):
        """Test deleting a diagram."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id"]
        token = self._token(app, tenant_id, identity_id)
        db = app.db

        diagram_id = None

        def create_diagram():
            nonlocal diagram_id
            now = datetime.now(timezone.utc)
            diagram_id = db.dg_diagrams.insert(
                tenant_id=tenant_id,
                village_id=uuid.uuid4().hex[:24],
                title="To Delete",
                owner_identity_id=identity_id,
                created_by_identity_id=identity_id,
                updated_by_identity_id=identity_id,
                status="draft",
                created_at=now,
                updated_at=now,
            )
            db.commit()

        from apps.api.utils.async_utils import run_in_threadpool

        await run_in_threadpool(create_diagram)

        client = app.test_client()
        response = await client.delete(
            f"/api/v1/diagrams/{diagram_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 204

        # Verify it's deleted
        response = await client.get(
            f"/api/v1/diagrams/{diagram_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_save_version(self, app):
        """Test saving a new version."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id"]
        token = self._token(app, tenant_id, identity_id)

        client = app.test_client()

        # Create diagram first
        response = await client.post(
            "/api/v1/diagrams",
            headers={"Authorization": f"Bearer {token}"},
            json={"title": "Diagram for Versioning"},
        )
        assert response.status_code == 201
        diagram_data = await response.get_json()
        diagram_id = diagram_data["id"]

        # Save a version
        content = {
            "nodes": [{"id": "1", "label": "Node 1"}],
            "edges": [],
        }
        response = await client.post(
            f"/api/v1/diagrams/{diagram_id}/versions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "content": content,
                "change_summary": "First version",
            },
        )

        assert response.status_code == 201
        version_data = await response.get_json()
        assert version_data["version_number"] == 1
        assert version_data["content"] == content
        assert version_data["change_summary"] == "First version"

    @pytest.mark.asyncio
    async def test_list_versions(self, app):
        """Test listing diagram versions."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id"]
        token = self._token(app, tenant_id, identity_id)

        client = app.test_client()

        # Create diagram
        response = await client.post(
            "/api/v1/diagrams",
            headers={"Authorization": f"Bearer {token}"},
            json={"title": "Versioned Diagram"},
        )
        diagram_id = (await response.get_json())["id"]

        # Save multiple versions
        for i in range(3):
            await client.post(
                f"/api/v1/diagrams/{diagram_id}/versions",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "content": {"nodes": [{"id": str(i)}], "edges": []},
                    "change_summary": f"Version {i}",
                },
            )

        # List versions
        response = await client.get(
            f"/api/v1/diagrams/{diagram_id}/versions",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = await response.get_json()
        assert len(data["versions"]) == 3
        assert data["pagination"]["total"] == 3

    @pytest.mark.asyncio
    async def test_get_specific_version(self, app):
        """Test getting a specific version."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id"]
        token = self._token(app, tenant_id, identity_id)

        client = app.test_client()

        # Create diagram
        response = await client.post(
            "/api/v1/diagrams",
            headers={"Authorization": f"Bearer {token}"},
            json={"title": "Diagram"},
        )
        diagram_id = (await response.get_json())["id"]

        # Save version
        content = {"nodes": [{"id": "test"}], "edges": []}
        await client.post(
            f"/api/v1/diagrams/{diagram_id}/versions",
            headers={"Authorization": f"Bearer {token}"},
            json={"content": content},
        )

        # Get version
        response = await client.get(
            f"/api/v1/diagrams/{diagram_id}/versions/1",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = await response.get_json()
        assert data["version_number"] == 1
        assert data["content"] == content

    @pytest.mark.asyncio
    async def test_restore_version(self, app):
        """Test restoring from a version."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id"]
        token = self._token(app, tenant_id, identity_id)

        client = app.test_client()

        # Create diagram
        response = await client.post(
            "/api/v1/diagrams",
            headers={"Authorization": f"Bearer {token}"},
            json={"title": "Diagram"},
        )
        diagram_id = (await response.get_json())["id"]

        # Save version 1
        content1 = {"nodes": [{"id": "1"}], "edges": []}
        await client.post(
            f"/api/v1/diagrams/{diagram_id}/versions",
            headers={"Authorization": f"Bearer {token}"},
            json={"content": content1},
        )

        # Save version 2
        content2 = {"nodes": [{"id": "2"}], "edges": []}
        await client.post(
            f"/api/v1/diagrams/{diagram_id}/versions",
            headers={"Authorization": f"Bearer {token}"},
            json={"content": content2},
        )

        # Restore to version 1
        response = await client.post(
            f"/api/v1/diagrams/{diagram_id}/versions/1/restore",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = await response.get_json()
        assert data["restored_from_version"] == 1
        assert data["new_version_number"] == 3

        # Verify new version has version 1's content
        response = await client.get(
            f"/api/v1/diagrams/{diagram_id}/versions/3",
            headers={"Authorization": f"Bearer {token}"},
        )
        version3 = await response.get_json()
        assert version3["content"] == content1

    @pytest.mark.asyncio
    async def test_missing_scope_returns_403(self, app):
        """Test that missing required scope returns 403."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id"]
        token = self._token(app, tenant_id, identity_id, scopes=["other:read"])

        client = app.test_client()
        response = await client.post(
            "/api/v1/diagrams",
            headers={"Authorization": f"Bearer {token}"},
            json={"title": "Test"},
        )

        assert response.status_code == 403
