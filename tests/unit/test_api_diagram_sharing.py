"""Diagram sharing and collections API tests.

regression: diagrams-sharing-collections-phase4b2
"""

import uuid
from datetime import UTC, datetime, timedelta, timezone

import jwt
import pytest
import pytest_asyncio


@pytest.mark.integration
class TestDiagramSharing:
    """Diagram sharing and public access tests."""

    @pytest_asyncio.fixture(autouse=True)
    async def setup_db(self, app, test_database_url):
        """Set up test database with tenant, identity, and sample diagrams."""
        if not test_database_url:
            pytest.skip("DATABASE_URL not set")

        db = app.db

        def _setup():
            now = datetime.now(UTC)
            tenant_id = db.tenants.insert(
                name="Sharing Tenant",
                slug=f"shr-{uuid.uuid4().hex[:8]}",
                is_active=True,
                created_at=now,
                updated_at=now,
            )

            # Create primary user
            email1 = f"shr-user-{uuid.uuid4().hex[:8]}@test.local"
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

            # Create secondary user (for sharing tests)
            email2 = f"shr-user-{uuid.uuid4().hex[:8]}@test.local"
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

            # Create sample diagram
            diagram_id = db.dg_diagrams.insert(
                tenant_id=tenant_id,
                village_id=uuid.uuid4().hex[:24],
                title="Test Diagram for Sharing",
                owner_identity_id=identity_id_1,
                created_by_identity_id=identity_id_1,
                updated_by_identity_id=identity_id_1,
                status="draft",
                is_public=False,
                created_at=now,
                updated_at=now,
            )

            # Create version for the diagram
            db.dg_diagram_versions.insert(
                diagram_id=diagram_id,
                tenant_id=tenant_id,
                version_number=1,
                created_by_identity_id=identity_id_1,
                content_json={"nodes": [{"id": "1"}], "edges": []},
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
    async def test_public_diagram_reader_cannot_create_share(self, app):
        """Priv-esc regression: a non-owner who can READ a public diagram must
        NOT be able to create shares on it — granting others access is share
        management and requires edit rights, not mere read-ability.

        regression: security-review-diagrams-share-management-auth
        """
        from apps.api.utils.async_utils import run_in_threadpool

        db = app.db
        t = self.fixtures["tenant_id"]
        owner = self.fixtures["identity_id_1"]
        reader = self.fixtures["identity_id_2"]

        def _mk_public():
            now = datetime.now(UTC)
            did = db.dg_diagrams.insert(
                tenant_id=t,
                village_id=uuid.uuid4().hex[:24],
                title="Public Diagram",
                owner_identity_id=owner,
                created_by_identity_id=owner,
                updated_by_identity_id=owner,
                status="draft",
                is_public=True,
                created_at=now,
                updated_at=now,
            )
            db.commit()
            return did

        pub_id = await run_in_threadpool(_mk_public)

        client = app.test_client()
        reader_token = self._token(app, t, reader, ["diagrams:read", "diagrams:write"])
        # Reader CAN read the public diagram...
        got = await client.get(
            f"/api/v1/diagrams/{pub_id}",
            headers={"Authorization": f"Bearer {reader_token}"},
        )
        assert got.status_code == 200
        # ...but must NOT be able to create a share on it (escalation).
        resp = await client.post(
            f"/api/v1/diagrams/{pub_id}/shares",
            json={"shared_with_identity_id": reader, "permission": "editor"},
            headers={"Authorization": f"Bearer {reader_token}"},
        )
        assert resp.status_code in (403, 404)

    @pytest.mark.asyncio
    async def test_create_share_private(self, app):
        """Test creating a private share to another identity."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id_1 = self.fixtures["identity_id_1"]
        identity_id_2 = self.fixtures["identity_id_2"]
        diagram_id = self.fixtures["diagram_id"]
        token = self._token(app, tenant_id, identity_id_1)

        client = app.test_client()
        response = await client.post(
            f"/api/v1/diagrams/{diagram_id}/shares",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "shared_with_identity_id": identity_id_2,
                "permission": "viewer",
            },
        )

        assert response.status_code == 201
        data = await response.get_json()
        assert data["shared_with_identity_id"] == identity_id_2
        assert data["permission"] == "viewer"
        assert data["is_public"] is False

    @pytest.mark.asyncio
    async def test_create_share_public(self, app):
        """Test creating a public share with a share token."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id_1 = self.fixtures["identity_id_1"]
        diagram_id = self.fixtures["diagram_id"]
        token = self._token(app, tenant_id, identity_id_1)

        client = app.test_client()
        response = await client.post(
            f"/api/v1/diagrams/{diagram_id}/shares",
            headers={"Authorization": f"Bearer {token}"},
            json={"is_public": True, "permission": "editor"},
        )

        assert response.status_code == 201
        data = await response.get_json()
        assert data["is_public"] is True
        assert data["share_token"] is not None
        self.public_share_token = data["share_token"]

    @pytest.mark.asyncio
    async def test_list_shares(self, app):
        """Test listing shares for a diagram."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id_1 = self.fixtures["identity_id_1"]
        identity_id_2 = self.fixtures["identity_id_2"]
        diagram_id = self.fixtures["diagram_id"]
        token = self._token(app, tenant_id, identity_id_1)

        client = app.test_client()

        # Create a share first
        await client.post(
            f"/api/v1/diagrams/{diagram_id}/shares",
            headers={"Authorization": f"Bearer {token}"},
            json={"shared_with_identity_id": identity_id_2, "permission": "viewer"},
        )

        # List shares
        response = await client.get(
            f"/api/v1/diagrams/{diagram_id}/shares",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = await response.get_json()
        assert len(data["items"]) >= 1
        assert any(s["shared_with_identity_id"] == identity_id_2 for s in data["items"])

    @pytest.mark.asyncio
    async def test_revoke_share(self, app):
        """Test revoking a share."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id_1 = self.fixtures["identity_id_1"]
        identity_id_2 = self.fixtures["identity_id_2"]
        diagram_id = self.fixtures["diagram_id"]
        token = self._token(app, tenant_id, identity_id_1)

        client = app.test_client()

        # Create a share
        create_response = await client.post(
            f"/api/v1/diagrams/{diagram_id}/shares",
            headers={"Authorization": f"Bearer {token}"},
            json={"shared_with_identity_id": identity_id_2, "permission": "viewer"},
        )
        share_id = (await create_response.get_json())["id"]

        # Revoke it
        response = await client.delete(
            f"/api/v1/diagrams/{diagram_id}/shares/{share_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 204

        # Verify it's gone
        list_response = await client.get(
            f"/api/v1/diagrams/{diagram_id}/shares",
            headers={"Authorization": f"Bearer {token}"},
        )
        list_data = await list_response.get_json()
        assert not any(s["id"] == share_id for s in list_data["items"])

    @pytest.mark.asyncio
    async def test_get_shared_diagram_public_token(self, app):
        """Test accessing a diagram via public share token (no auth)."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id_1 = self.fixtures["identity_id_1"]
        diagram_id = self.fixtures["diagram_id"]
        token = self._token(app, tenant_id, identity_id_1)

        client = app.test_client()

        # Create public share
        create_response = await client.post(
            f"/api/v1/diagrams/{diagram_id}/shares",
            headers={"Authorization": f"Bearer {token}"},
            json={"is_public": True},
        )
        share_data = await create_response.get_json()
        share_token = share_data["share_token"]

        # Access via token (no auth required)
        response = await client.get(f"/api/v1/diagrams/shared/{share_token}")

        assert response.status_code == 200
        data = await response.get_json()
        assert data["id"] == diagram_id
        assert data["title"] == "Test Diagram for Sharing"
        assert "content" in data

    @pytest.mark.asyncio
    async def test_get_shared_diagram_expired_token(self, app):
        """Test that expired share tokens return 404."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id_1 = self.fixtures["identity_id_1"]
        diagram_id = self.fixtures["diagram_id"]
        token = self._token(app, tenant_id, identity_id_1)

        db = app.db

        client = app.test_client()

        # Create public share with past expiration
        def create_expired_share():
            import secrets

            now = datetime.now(UTC)
            expired_token = f"expired-{secrets.token_urlsafe(32)}"
            share_id = db.dg_shares.insert(
                tenant_id=tenant_id,
                diagram_id=diagram_id,
                shared_by_identity_id=identity_id_1,
                permission="viewer",
                is_public=True,
                share_token=expired_token,
                expires_at=now - timedelta(hours=1),  # Past expiration
                created_at=now,
                updated_at=now,
            )
            db.commit()
            return expired_token

        from apps.api.utils.async_utils import run_in_threadpool

        expired_token = await run_in_threadpool(create_expired_share)

        # Try to access via expired token
        response = await client.get(f"/api/v1/diagrams/shared/{expired_token}")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_share_nonexistent_diagram_returns_404(self, app):
        """Test that sharing a nonexistent diagram returns 404."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id_1 = self.fixtures["identity_id_1"]
        token = self._token(app, tenant_id, identity_id_1)

        client = app.test_client()
        response = await client.post(
            "/api/v1/diagrams/99999/shares",
            headers={"Authorization": f"Bearer {token}"},
            json={"is_public": True},
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_missing_scope_returns_403(self, app):
        """Test that missing diagrams:write scope returns 403."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id_1 = self.fixtures["identity_id_1"]
        diagram_id = self.fixtures["diagram_id"]

        # Token with only read scope
        token = self._token(app, tenant_id, identity_id_1, scopes=["diagrams:read"])

        client = app.test_client()
        response = await client.post(
            f"/api/v1/diagrams/{diagram_id}/shares",
            headers={"Authorization": f"Bearer {token}"},
            json={"is_public": True},
        )

        assert response.status_code == 403


@pytest.mark.integration
class TestDiagramCollections:
    """Diagram collections CRUD and item management tests."""

    @pytest_asyncio.fixture(autouse=True)
    async def setup_db(self, app, test_database_url):
        """Set up test database with tenant, identity, and sample diagrams."""
        if not test_database_url:
            pytest.skip("DATABASE_URL not set")

        db = app.db

        def _setup():
            now = datetime.now(UTC)
            tenant_id = db.tenants.insert(
                name="Collections Tenant",
                slug=f"col-{uuid.uuid4().hex[:8]}",
                is_active=True,
                created_at=now,
                updated_at=now,
            )

            # Create primary user
            email1 = f"col-user-{uuid.uuid4().hex[:8]}@test.local"
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
            email2 = f"col-user-{uuid.uuid4().hex[:8]}@test.local"
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

            # Create sample diagrams
            diagram_ids = []
            for i in range(3):
                diagram_id = db.dg_diagrams.insert(
                    tenant_id=tenant_id,
                    village_id=uuid.uuid4().hex[:24],
                    title=f"Collection Test Diagram {i}",
                    owner_identity_id=identity_id_1,
                    created_by_identity_id=identity_id_1,
                    updated_by_identity_id=identity_id_1,
                    status="draft",
                    is_public=False,
                    created_at=now,
                    updated_at=now,
                )
                diagram_ids.append(diagram_id)

            db.commit()
            return {
                "tenant_id": tenant_id,
                "identity_id_1": identity_id_1,
                "identity_id_2": identity_id_2,
                "diagram_ids": diagram_ids,
            }

        from apps.api.utils.async_utils import run_in_threadpool

        self.fixtures = await run_in_threadpool(_setup)

    def _token(self, app, tenant_id, identity_id, scopes=None, roles=None):
        """Create a test JWT token."""
        scopes = scopes or ["diagrams:read", "diagrams:write"]
        # CRITICAL: scope must be a LIST
        now = datetime.now(UTC)
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
    async def test_create_collection(self, app):
        """Test creating a collection."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id_1"]
        token = self._token(app, tenant_id, identity_id)

        client = app.test_client()
        response = await client.post(
            "/api/v1/diagram-collections",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "Test Collection",
                "is_public": False,
                "share_mode": "private",
            },
        )

        assert response.status_code == 201
        data = await response.get_json()
        assert data["name"] == "Test Collection"
        assert data["is_public"] is False
        assert data["share_mode"] == "private"
        assert data["owner_identity_id"] == identity_id
        self.collection_id = data["id"]

    @pytest.mark.asyncio
    async def test_list_collections(self, app):
        """Test listing collections."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id_1"]
        token = self._token(app, tenant_id, identity_id)

        client = app.test_client()

        # Create a collection first
        await client.post(
            "/api/v1/diagram-collections",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "Collection 1"},
        )

        # List collections
        response = await client.get(
            "/api/v1/diagram-collections",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = await response.get_json()
        assert len(data["items"]) >= 1
        assert any(c["name"] == "Collection 1" for c in data["items"])

    @pytest.mark.asyncio
    async def test_get_collection_with_items(self, app):
        """Test getting a collection with its items."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id_1"]
        diagram_ids = self.fixtures["diagram_ids"]
        token = self._token(app, tenant_id, identity_id)

        client = app.test_client()

        # Create collection
        create_response = await client.post(
            "/api/v1/diagram-collections",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "Test Collection with Items"},
        )
        collection_data = await create_response.get_json()
        collection_id = collection_data["id"]

        # Add items
        for diagram_id in diagram_ids[:2]:
            await client.post(
                f"/api/v1/diagram-collections/{collection_id}/items",
                headers={"Authorization": f"Bearer {token}"},
                json={"diagram_id": diagram_id},
            )

        # Get collection
        response = await client.get(
            f"/api/v1/diagram-collections/{collection_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = await response.get_json()
        assert data["name"] == "Test Collection with Items"
        assert len(data["items"]) == 2

    @pytest.mark.asyncio
    async def test_update_collection_owner_only(self, app):
        """Test that only owner can update collection."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id_1 = self.fixtures["identity_id_1"]
        identity_id_2 = self.fixtures["identity_id_2"]
        token1 = self._token(app, tenant_id, identity_id_1)
        token2 = self._token(app, tenant_id, identity_id_2)

        client = app.test_client()

        # Create collection as user 1
        create_response = await client.post(
            "/api/v1/diagram-collections",
            headers={"Authorization": f"Bearer {token1}"},
            json={"name": "Collection by User 1"},
        )
        collection_id = (await create_response.get_json())["id"]

        # Try to update as user 2 (should 404)
        response = await client.patch(
            f"/api/v1/diagram-collections/{collection_id}",
            headers={"Authorization": f"Bearer {token2}"},
            json={"name": "Updated by User 2"},
        )

        assert response.status_code == 404

        # Update as owner (should succeed)
        response = await client.patch(
            f"/api/v1/diagram-collections/{collection_id}",
            headers={"Authorization": f"Bearer {token1}"},
            json={"name": "Updated by Owner"},
        )

        assert response.status_code == 200
        data = await response.get_json()
        assert data["name"] == "Updated by Owner"

    @pytest.mark.asyncio
    async def test_delete_collection_owner_only(self, app):
        """Test that only owner can delete collection."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id_1 = self.fixtures["identity_id_1"]
        identity_id_2 = self.fixtures["identity_id_2"]
        token1 = self._token(app, tenant_id, identity_id_1)
        token2 = self._token(app, tenant_id, identity_id_2)

        client = app.test_client()

        # Create collection
        create_response = await client.post(
            "/api/v1/diagram-collections",
            headers={"Authorization": f"Bearer {token1}"},
            json={"name": "Collection to Delete"},
        )
        collection_id = (await create_response.get_json())["id"]

        # Try to delete as non-owner (should 404)
        response = await client.delete(
            f"/api/v1/diagram-collections/{collection_id}",
            headers={"Authorization": f"Bearer {token2}"},
        )

        assert response.status_code == 404

        # Delete as owner (should succeed)
        response = await client.delete(
            f"/api/v1/diagram-collections/{collection_id}",
            headers={"Authorization": f"Bearer {token1}"},
        )

        assert response.status_code == 204

    @pytest.mark.asyncio
    async def test_add_item_to_collection(self, app):
        """Test adding a diagram to a collection."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id_1"]
        diagram_id = self.fixtures["diagram_ids"][0]
        token = self._token(app, tenant_id, identity_id)

        client = app.test_client()

        # Create collection
        create_response = await client.post(
            "/api/v1/diagram-collections",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "Test Add Item"},
        )
        collection_id = (await create_response.get_json())["id"]

        # Add item
        response = await client.post(
            f"/api/v1/diagram-collections/{collection_id}/items",
            headers={"Authorization": f"Bearer {token}"},
            json={"diagram_id": diagram_id},
        )

        assert response.status_code == 201
        data = await response.get_json()
        assert data["diagram_id"] == diagram_id
        assert data["order_index"] == 0

    @pytest.mark.asyncio
    async def test_remove_item_from_collection(self, app):
        """Test removing a diagram from a collection."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id_1"]
        diagram_id = self.fixtures["diagram_ids"][0]
        token = self._token(app, tenant_id, identity_id)

        client = app.test_client()

        # Create collection and add item
        create_response = await client.post(
            "/api/v1/diagram-collections",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "Test Remove Item"},
        )
        collection_id = (await create_response.get_json())["id"]

        await client.post(
            f"/api/v1/diagram-collections/{collection_id}/items",
            headers={"Authorization": f"Bearer {token}"},
            json={"diagram_id": diagram_id},
        )

        # Remove item
        response = await client.delete(
            f"/api/v1/diagram-collections/{collection_id}/items/{diagram_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 204

        # Verify it's gone
        get_response = await client.get(
            f"/api/v1/diagram-collections/{collection_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        get_data = await get_response.get_json()
        assert len(get_data["items"]) == 0

    @pytest.mark.asyncio
    async def test_cross_tenant_collection_access_returns_404(
        self, app, test_database_url
    ):
        """Test that cross-tenant collection access returns 404."""
        db = app.db

        if not test_database_url:
            pytest.skip("DATABASE_URL not set")

        # Create a different tenant with a collection
        def create_other_tenant_collection():
            now = datetime.now(UTC)
            other_tenant_id = db.tenants.insert(
                name="Other Tenant",
                slug=f"oth-{uuid.uuid4().hex[:8]}",
                is_active=True,
                created_at=now,
                updated_at=now,
            )

            other_identity_id = db.identities.insert(
                tenant_id=other_tenant_id,
                username=f"other-user-{uuid.uuid4().hex[:8]}@test.local",
                email=f"other-user-{uuid.uuid4().hex[:8]}@test.local",
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

            collection_id = db.dg_collections.insert(
                tenant_id=other_tenant_id,
                village_id=uuid.uuid4().hex[:24],
                name="Other Tenant Collection",
                owner_identity_id=other_identity_id,
                created_at=now,
                updated_at=now,
            )
            db.commit()
            return collection_id

        from apps.api.utils.async_utils import run_in_threadpool

        other_collection_id = await run_in_threadpool(create_other_tenant_collection)

        # Try to access from different tenant
        tenant_id = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id_1"]
        token = self._token(app, tenant_id, identity_id)

        client = app.test_client()
        response = await client.get(
            f"/api/v1/diagram-collections/{other_collection_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 404
