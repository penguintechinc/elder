"""Diagram comments, templates, and shape libraries API tests.

regression: diagrams-comments-templates-libraries-phase4b3
"""

import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
import pytest_asyncio


@pytest.mark.integration
class TestDiagramComments:
    """Diagram comments and replies tests."""

    @pytest_asyncio.fixture(autouse=True)
    async def setup_db(self, app, test_database_url):
        """Set up test database with tenant, identities, and sample diagram."""
        if not test_database_url:
            pytest.skip("DATABASE_URL not set")

        db = app.db

        def _setup():
            now = datetime.now(timezone.utc)
            tenant_id = db.tenants.insert(
                name="Comments Tenant",
                slug=f"cmt-{uuid.uuid4().hex[:8]}",
                is_active=True,
                created_at=now,
                updated_at=now,
            )

            # Primary user (diagram owner)
            email1 = f"cmt-user-{uuid.uuid4().hex[:8]}@test.local"
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

            # Secondary user (commenter/reader)
            email2 = f"cmt-user-{uuid.uuid4().hex[:8]}@test.local"
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

            # Create sample diagram (public for comment tests)
            diagram_id = db.dg_diagrams.insert(
                tenant_id=tenant_id,
                village_id=uuid.uuid4().hex[:24],
                title="Test Diagram for Comments",
                owner_identity_id=identity_id_1,
                created_by_identity_id=identity_id_1,
                updated_by_identity_id=identity_id_1,
                status="draft",
                is_public=True,
                created_at=now,
                updated_at=now,
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
    async def test_create_comment(self, app):
        """Test creating a comment on a diagram."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id_1 = self.fixtures["identity_id_1"]
        diagram_id = self.fixtures["diagram_id"]
        token = self._token(app, tenant_id, identity_id_1)

        client = app.test_client()
        response = await client.post(
            f"/api/v1/diagrams/{diagram_id}/comments",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "text_content": "This needs fixing",
                "x_position": 100,
                "y_position": 200,
            },
        )

        assert response.status_code == 201
        data = await response.get_json()
        assert data["text_content"] == "This needs fixing"
        assert data["author_identity_id"] == identity_id_1
        assert data["x_position"] == 100
        assert data["y_position"] == 200
        assert data["is_resolved"] is False
        assert data["replies"] == []

    @pytest.mark.asyncio
    async def test_list_comments(self, app):
        """Test listing comments for a diagram."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id_1 = self.fixtures["identity_id_1"]
        diagram_id = self.fixtures["diagram_id"]
        token = self._token(app, tenant_id, identity_id_1)

        client = app.test_client()

        # Create two comments
        await client.post(
            f"/api/v1/diagrams/{diagram_id}/comments",
            headers={"Authorization": f"Bearer {token}"},
            json={"text_content": "Comment 1"},
        )
        await client.post(
            f"/api/v1/diagrams/{diagram_id}/comments",
            headers={"Authorization": f"Bearer {token}"},
            json={"text_content": "Comment 2"},
        )

        # List comments
        response = await client.get(
            f"/api/v1/diagrams/{diagram_id}/comments",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = await response.get_json()
        assert len(data["items"]) == 2
        assert data["pagination"]["total"] == 2

    @pytest.mark.asyncio
    async def test_add_reply(self, app):
        """Test adding a reply to a comment."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id_1 = self.fixtures["identity_id_1"]
        identity_id_2 = self.fixtures["identity_id_2"]
        diagram_id = self.fixtures["diagram_id"]
        token1 = self._token(app, tenant_id, identity_id_1)
        token2 = self._token(app, tenant_id, identity_id_2)

        client = app.test_client()

        # Create comment
        resp = await client.post(
            f"/api/v1/diagrams/{diagram_id}/comments",
            headers={"Authorization": f"Bearer {token1}"},
            json={"text_content": "Parent comment"},
        )
        comment_id = (await resp.get_json())["id"]

        # Add reply
        reply_resp = await client.post(
            f"/api/v1/diagrams/{diagram_id}/comments/{comment_id}/replies",
            headers={"Authorization": f"Bearer {token2}"},
            json={"text_content": "I agree"},
        )

        assert reply_resp.status_code == 201
        reply_data = await reply_resp.get_json()
        assert reply_data["text_content"] == "I agree"
        assert reply_data["author_identity_id"] == identity_id_2

    @pytest.mark.asyncio
    async def test_update_comment_by_author(self, app):
        """Test updating a comment by the author."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id_1 = self.fixtures["identity_id_1"]
        diagram_id = self.fixtures["diagram_id"]
        token = self._token(app, tenant_id, identity_id_1)

        client = app.test_client()

        # Create comment
        resp = await client.post(
            f"/api/v1/diagrams/{diagram_id}/comments",
            headers={"Authorization": f"Bearer {token}"},
            json={"text_content": "Original text"},
        )
        comment_id = (await resp.get_json())["id"]

        # Update it
        update_resp = await client.patch(
            f"/api/v1/diagrams/{diagram_id}/comments/{comment_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"text_content": "Updated text", "is_resolved": True},
        )

        assert update_resp.status_code == 200
        data = await update_resp.get_json()
        assert data["text_content"] == "Updated text"
        assert data["is_resolved"] is True

    @pytest.mark.asyncio
    async def test_delete_comment_by_author(self, app):
        """Test deleting a comment by the author."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id_1 = self.fixtures["identity_id_1"]
        diagram_id = self.fixtures["diagram_id"]
        token = self._token(app, tenant_id, identity_id_1)

        client = app.test_client()

        # Create comment
        resp = await client.post(
            f"/api/v1/diagrams/{diagram_id}/comments",
            headers={"Authorization": f"Bearer {token}"},
            json={"text_content": "To delete"},
        )
        comment_id = (await resp.get_json())["id"]

        # Delete it
        del_resp = await client.delete(
            f"/api/v1/diagrams/{diagram_id}/comments/{comment_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert del_resp.status_code == 204

        # Verify it's gone
        list_resp = await client.get(
            f"/api/v1/diagrams/{diagram_id}/comments",
            headers={"Authorization": f"Bearer {token}"},
        )
        data = await list_resp.get_json()
        assert len(data["items"]) == 0

    @pytest.mark.asyncio
    async def test_non_author_cannot_delete_comment(self, app):
        """Test that a non-author cannot delete someone else's comment."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id_1 = self.fixtures["identity_id_1"]
        identity_id_2 = self.fixtures["identity_id_2"]
        diagram_id = self.fixtures["diagram_id"]
        token1 = self._token(app, tenant_id, identity_id_1)
        token2 = self._token(app, tenant_id, identity_id_2)

        client = app.test_client()

        # User 1 creates comment
        resp = await client.post(
            f"/api/v1/diagrams/{diagram_id}/comments",
            headers={"Authorization": f"Bearer {token1}"},
            json={"text_content": "User 1 comment"},
        )
        comment_id = (await resp.get_json())["id"]

        # User 2 tries to delete it
        del_resp = await client.delete(
            f"/api/v1/diagrams/{diagram_id}/comments/{comment_id}",
            headers={"Authorization": f"Bearer {token2}"},
        )

        # Should fail (403 or 404)
        assert del_resp.status_code in (403, 404)


@pytest.mark.integration
class TestDiagramTemplates:
    """Diagram templates tests."""

    @pytest_asyncio.fixture(autouse=True)
    async def setup_db(self, app, test_database_url):
        """Set up test database with tenant and identities."""
        if not test_database_url:
            pytest.skip("DATABASE_URL not set")

        db = app.db

        def _setup():
            now = datetime.now(timezone.utc)
            tenant_id = db.tenants.insert(
                name="Templates Tenant",
                slug=f"tpl-{uuid.uuid4().hex[:8]}",
                is_active=True,
                created_at=now,
                updated_at=now,
            )

            email1 = f"tpl-user-{uuid.uuid4().hex[:8]}@test.local"
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

            email2 = f"tpl-user-{uuid.uuid4().hex[:8]}@test.local"
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

            db.commit()
            return {
                "tenant_id": tenant_id,
                "identity_id_1": identity_id_1,
                "identity_id_2": identity_id_2,
            }

        from apps.api.utils.async_utils import run_in_threadpool

        self.fixtures = await run_in_threadpool(_setup)

    def _token(self, app, tenant_id, identity_id, scopes=None, roles=None):
        """Create a test JWT token."""
        scopes = scopes or ["diagrams:read", "diagrams:write"]
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
    async def test_create_template(self, app):
        """Test creating a diagram template."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id_1"]
        token = self._token(app, tenant_id, identity_id)

        client = app.test_client()
        response = await client.post(
            "/api/v1/diagram-templates",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "Flowchart",
                "content": {"nodes": [], "edges": []},
                "category": "flowchart",
                "is_public": True,
            },
        )

        assert response.status_code == 201
        data = await response.get_json()
        assert data["name"] == "Flowchart"
        assert data["category"] == "flowchart"
        assert data["is_public"] is True
        assert data["created_by_identity_id"] == identity_id

    @pytest.mark.asyncio
    async def test_list_templates(self, app):
        """Test listing templates (public + creator's)."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id_1 = self.fixtures["identity_id_1"]
        identity_id_2 = self.fixtures["identity_id_2"]
        token1 = self._token(app, tenant_id, identity_id_1)
        token2 = self._token(app, tenant_id, identity_id_2)

        client = app.test_client()

        # User 1 creates public and private templates
        await client.post(
            "/api/v1/diagram-templates",
            headers={"Authorization": f"Bearer {token1}"},
            json={
                "name": "Public Template",
                "content": {},
                "is_public": True,
            },
        )
        await client.post(
            "/api/v1/diagram-templates",
            headers={"Authorization": f"Bearer {token1}"},
            json={
                "name": "Private Template",
                "content": {},
                "is_public": False,
            },
        )

        # User 2 lists templates - should see only public
        response = await client.get(
            "/api/v1/diagram-templates",
            headers={"Authorization": f"Bearer {token2}"},
        )

        assert response.status_code == 200
        data = await response.get_json()
        assert len(data["items"]) == 1
        assert data["items"][0]["name"] == "Public Template"

        # User 1 lists - should see both
        response = await client.get(
            "/api/v1/diagram-templates",
            headers={"Authorization": f"Bearer {token1}"},
        )
        data = await response.get_json()
        assert len(data["items"]) == 2

    @pytest.mark.asyncio
    async def test_get_template_private_forbidden(self, app):
        """Test getting a private template not owned by caller returns 404."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id_1 = self.fixtures["identity_id_1"]
        identity_id_2 = self.fixtures["identity_id_2"]
        token1 = self._token(app, tenant_id, identity_id_1)
        token2 = self._token(app, tenant_id, identity_id_2)

        client = app.test_client()

        # User 1 creates private template
        resp = await client.post(
            "/api/v1/diagram-templates",
            headers={"Authorization": f"Bearer {token1}"},
            json={
                "name": "Private",
                "content": {},
                "is_public": False,
            },
        )
        template_id = (await resp.get_json())["id"]

        # User 2 tries to get it
        get_resp = await client.get(
            f"/api/v1/diagram-templates/{template_id}",
            headers={"Authorization": f"Bearer {token2}"},
        )

        assert get_resp.status_code == 403

    @pytest.mark.asyncio
    async def test_update_template_creator_only(self, app):
        """Test updating a template - creator only."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id_1 = self.fixtures["identity_id_1"]
        identity_id_2 = self.fixtures["identity_id_2"]
        token1 = self._token(app, tenant_id, identity_id_1)
        token2 = self._token(app, tenant_id, identity_id_2)

        client = app.test_client()

        # User 1 creates template
        resp = await client.post(
            "/api/v1/diagram-templates",
            headers={"Authorization": f"Bearer {token1}"},
            json={"name": "Original", "content": {}, "is_public": True},
        )
        template_id = (await resp.get_json())["id"]

        # User 2 tries to update
        update_resp = await client.patch(
            f"/api/v1/diagram-templates/{template_id}",
            headers={"Authorization": f"Bearer {token2}"},
            json={"name": "Hijacked"},
        )

        assert update_resp.status_code == 403

        # User 1 updates successfully
        update_resp = await client.patch(
            f"/api/v1/diagram-templates/{template_id}",
            headers={"Authorization": f"Bearer {token1}"},
            json={"name": "Updated"},
        )

        assert update_resp.status_code == 200
        data = await update_resp.get_json()
        assert data["name"] == "Updated"

    @pytest.mark.asyncio
    async def test_delete_template_creator_only(self, app):
        """Test deleting a template - creator only."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id_1 = self.fixtures["identity_id_1"]
        identity_id_2 = self.fixtures["identity_id_2"]
        token1 = self._token(app, tenant_id, identity_id_1)
        token2 = self._token(app, tenant_id, identity_id_2)

        client = app.test_client()

        # User 1 creates template
        resp = await client.post(
            "/api/v1/diagram-templates",
            headers={"Authorization": f"Bearer {token1}"},
            json={"name": "To Delete", "content": {}},
        )
        template_id = (await resp.get_json())["id"]

        # User 2 tries to delete
        del_resp = await client.delete(
            f"/api/v1/diagram-templates/{template_id}",
            headers={"Authorization": f"Bearer {token2}"},
        )

        assert del_resp.status_code == 403


@pytest.mark.integration
class TestDiagramLibraries:
    """Shape libraries tests."""

    @pytest_asyncio.fixture(autouse=True)
    async def setup_db(self, app, test_database_url):
        """Set up test database with tenant and identities."""
        if not test_database_url:
            pytest.skip("DATABASE_URL not set")

        db = app.db

        def _setup():
            now = datetime.now(timezone.utc)
            tenant_id = db.tenants.insert(
                name="Libraries Tenant",
                slug=f"lib-{uuid.uuid4().hex[:8]}",
                is_active=True,
                created_at=now,
                updated_at=now,
            )

            email1 = f"lib-user-{uuid.uuid4().hex[:8]}@test.local"
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

            email2 = f"lib-user-{uuid.uuid4().hex[:8]}@test.local"
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

            db.commit()
            return {
                "tenant_id": tenant_id,
                "identity_id_1": identity_id_1,
                "identity_id_2": identity_id_2,
            }

        from apps.api.utils.async_utils import run_in_threadpool

        self.fixtures = await run_in_threadpool(_setup)

    def _token(self, app, tenant_id, identity_id, scopes=None, roles=None):
        """Create a test JWT token."""
        scopes = scopes or ["diagrams:read", "diagrams:write"]
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
    async def test_create_library(self, app):
        """Test creating a shape library."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id_1"]
        token = self._token(app, tenant_id, identity_id)

        client = app.test_client()
        response = await client.post(
            "/api/v1/diagram-libraries",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "Basic Shapes",
                "description": "Common shapes",
                "is_public": True,
            },
        )

        assert response.status_code == 201
        data = await response.get_json()
        assert data["name"] == "Basic Shapes"
        assert data["owner_identity_id"] == identity_id

    @pytest.mark.asyncio
    async def test_add_shape_to_library(self, app):
        """Test adding shapes to a library."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id_1"]
        token = self._token(app, tenant_id, identity_id)

        client = app.test_client()

        # Create library
        lib_resp = await client.post(
            "/api/v1/diagram-libraries",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "My Shapes"},
        )
        library_id = (await lib_resp.get_json())["id"]

        # Add shape
        shape_resp = await client.post(
            f"/api/v1/diagram-libraries/{library_id}/shapes",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "Box",
                "shape_type": "rectangle",
                "default_width": 100,
                "default_height": 50,
                "svg_content": "<rect/>",
            },
        )

        assert shape_resp.status_code == 201
        shape_data = await shape_resp.get_json()
        assert shape_data["name"] == "Box"

    @pytest.mark.asyncio
    async def test_get_library_with_shapes(self, app):
        """Test getting a library with nested shapes."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id_1"]
        token = self._token(app, tenant_id, identity_id)

        client = app.test_client()

        # Create library
        lib_resp = await client.post(
            "/api/v1/diagram-libraries",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "Lib"},
        )
        library_id = (await lib_resp.get_json())["id"]

        # Add two shapes
        await client.post(
            f"/api/v1/diagram-libraries/{library_id}/shapes",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "Shape1"},
        )
        await client.post(
            f"/api/v1/diagram-libraries/{library_id}/shapes",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "Shape2"},
        )

        # Get library
        get_resp = await client.get(
            f"/api/v1/diagram-libraries/{library_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert get_resp.status_code == 200
        data = await get_resp.get_json()
        assert len(data["shapes"]) == 2
        assert data["shapes"][0]["name"] in ("Shape1", "Shape2")

    @pytest.mark.asyncio
    async def test_remove_shape_from_library(self, app):
        """Test removing a shape from a library."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id_1"]
        token = self._token(app, tenant_id, identity_id)

        client = app.test_client()

        # Create library and add shape
        lib_resp = await client.post(
            "/api/v1/diagram-libraries",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "Lib"},
        )
        library_id = (await lib_resp.get_json())["id"]

        shape_resp = await client.post(
            f"/api/v1/diagram-libraries/{library_id}/shapes",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "Shape"},
        )
        shape_id = (await shape_resp.get_json())["id"]

        # Remove shape
        del_resp = await client.delete(
            f"/api/v1/diagram-libraries/{library_id}/shapes/{shape_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert del_resp.status_code == 204

        # Verify it's gone
        get_resp = await client.get(
            f"/api/v1/diagram-libraries/{library_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        data = await get_resp.get_json()
        assert len(data["shapes"]) == 0

    @pytest.mark.asyncio
    async def test_non_owner_cannot_delete_library(self, app):
        """Test that non-owner cannot delete a library."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id_1 = self.fixtures["identity_id_1"]
        identity_id_2 = self.fixtures["identity_id_2"]
        token1 = self._token(app, tenant_id, identity_id_1)
        token2 = self._token(app, tenant_id, identity_id_2)

        client = app.test_client()

        # User 1 creates library
        lib_resp = await client.post(
            "/api/v1/diagram-libraries",
            headers={"Authorization": f"Bearer {token1}"},
            json={"name": "Lib", "is_public": True},
        )
        library_id = (await lib_resp.get_json())["id"]

        # User 2 tries to delete
        del_resp = await client.delete(
            f"/api/v1/diagram-libraries/{library_id}",
            headers={"Authorization": f"Bearer {token2}"},
        )

        assert del_resp.status_code == 403

    @pytest.mark.asyncio
    async def test_delete_library_cascades_shapes(self, app):
        """Test that deleting a library cascades shape deletion."""
        tenant_id = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id_1"]
        token = self._token(app, tenant_id, identity_id)

        client = app.test_client()

        # Create library with shapes
        lib_resp = await client.post(
            "/api/v1/diagram-libraries",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "Lib"},
        )
        library_id = (await lib_resp.get_json())["id"]

        await client.post(
            f"/api/v1/diagram-libraries/{library_id}/shapes",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "Shape"},
        )

        # Delete library
        await client.delete(
            f"/api/v1/diagram-libraries/{library_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        # Verify it's gone
        get_resp = await client.get(
            f"/api/v1/diagram-libraries/{library_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert get_resp.status_code == 404
