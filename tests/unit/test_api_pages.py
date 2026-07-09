"""Unit tests for pages module API endpoints.

Tests CRUD operations, visibility ACL, per-tenant slug uniqueness, bleach sanitization,
wiki-link reference creation, backlinks, cross-tenant isolation.

Uses real database (integration test), real tokens, no mocks.
"""

# flake8: noqa: E501

import json
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio


@pytest.mark.integration
class TestPagesAPI:
    """Pages module CRUD and security tests."""

    @pytest_asyncio.fixture(autouse=True)
    async def setup_db(self, app, test_database_url):
        """Setup: initialize database and fixtures."""
        if not test_database_url:
            pytest.skip("DATABASE_URL not set")

        # Create test tenant and identities
        db = app.db

        def _setup():
            now = datetime.now(timezone.utc)

            # Create tenant 1
            tenant1_id = db.tenants.insert(
                name="Test Tenant 1",
                slug=f"test-tenant-1-{uuid.uuid4().hex[:8]}",
                is_active=True,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )

            # Create tenant 2 (for cross-tenant tests)
            tenant2_id = db.tenants.insert(
                name="Test Tenant 2",
                slug=f"test-tenant-2-{uuid.uuid4().hex[:8]}",
                is_active=True,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )

            # Create identity for tenant 1 (author)
            email1 = f"author1-{uuid.uuid4().hex[:8]}@test.local"
            author1_id = db.identities.insert(
                tenant_id=tenant1_id,
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

            # Create identity for tenant 2
            email2 = f"author2-{uuid.uuid4().hex[:8]}@test.local"
            author2_id = db.identities.insert(
                tenant_id=tenant2_id,
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
                "tenant1_id": tenant1_id,
                "tenant2_id": tenant2_id,
                "author1_id": author1_id,
                "author2_id": author2_id,
            }

        from apps.api.utils.async_utils import run_in_threadpool

        self.fixtures = await run_in_threadpool(_setup)

    def generate_token(
        self, app, tenant_id: int, user_id: int, scopes=None, roles=None
    ):
        """Generate a test JWT token."""
        from datetime import datetime, timedelta, timezone

        import jwt

        if scopes is None:
            scopes = ["pages:read", "pages:write", "pages:admin"]

        payload = {
            "sub": str(user_id),
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
            "scope": scopes,
            "tenant": str(tenant_id),
            "user_identity_id": user_id,
            "roles": ["admin"] if roles is None else roles,
            "teams": [],
        }

        secret = app.config.get("JWT_SECRET_KEY") or app.config.get("SECRET_KEY")
        token = jwt.encode(payload, secret, algorithm="HS256")
        return token

    @pytest.mark.asyncio
    async def test_create_page(self, app):
        """Test page creation."""
        client = app.test_client()
        tenant_id = self.fixtures["tenant1_id"]
        author_id = self.fixtures["author1_id"]
        token = self.generate_token(app, tenant_id, author_id)

        response = await client.post(
            "/api/v1/pages",
            json={
                "title": "Test Page",
                "body_html": "<p>This is a test page</p>",
                "status": "draft",
                "visibility": "authenticated",
                "is_public": False,
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 201
        data = json.loads(await response.get_data())
        assert data["title"] == "Test Page"
        assert data["slug"] == "test-page"
        assert data["village_id"] is not None

    @pytest.mark.asyncio
    async def test_create_page_with_slug_conflict(self, app):
        """Test per-tenant slug uniqueness (409)."""
        client = app.test_client()
        tenant_id = self.fixtures["tenant1_id"]
        author_id = self.fixtures["author1_id"]
        token = self.generate_token(app, tenant_id, author_id)

        # Create first page
        await client.post(
            "/api/v1/pages",
            json={
                "title": "Unique Page",
                "body_html": "<p>First</p>",
                "status": "draft",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        # Create second page with same title (same slug) — should 409
        response = await client.post(
            "/api/v1/pages",
            json={
                "title": "Unique Page",
                "body_html": "<p>Second</p>",
                "status": "draft",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_list_pages_paginated(self, app):
        """Test list pages with pagination."""
        client = app.test_client()
        tenant_id = self.fixtures["tenant1_id"]
        author_id = self.fixtures["author1_id"]
        token = self.generate_token(app, tenant_id, author_id)

        # Create 3 pages
        for i in range(3):
            await client.post(
                "/api/v1/pages",
                json={
                    "title": f"Page {i}",
                    "body_html": f"<p>Content {i}</p>",
                },
                headers={"Authorization": f"Bearer {token}"},
            )

        # List with pagination
        response = await client.get(
            "/api/v1/pages?page=1&per_page=2",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert len(data["items"]) == 2
        assert data["pagination"]["total"] == 3
        assert data["pagination"]["pages"] == 2

    @pytest.mark.asyncio
    async def test_get_page_by_slug(self, app):
        """Test get page by slug."""
        client = app.test_client()
        tenant_id = self.fixtures["tenant1_id"]
        author_id = self.fixtures["author1_id"]
        token = self.generate_token(app, tenant_id, author_id)

        # Create page
        create_response = await client.post(
            "/api/v1/pages",
            json={
                "title": "Get Test",
                "body_html": "<p>Content</p>",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        page_data = json.loads(await create_response.get_data())
        slug = page_data["slug"]

        # Get page
        response = await client.get(
            f"/api/v1/pages/{slug}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert data["slug"] == slug
        assert data["title"] == "Get Test"

    @pytest.mark.asyncio
    async def test_get_page_not_found(self, app):
        """Test get nonexistent page (404)."""
        client = app.test_client()
        tenant_id = self.fixtures["tenant1_id"]
        author_id = self.fixtures["author1_id"]
        token = self.generate_token(app, tenant_id, author_id)

        response = await client.get(
            "/api/v1/pages/nonexistent",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_visibility_authenticated(self, app):
        """Test visibility: authenticated mode (blocks unauthenticated access)."""
        client = app.test_client()
        tenant_id = self.fixtures["tenant1_id"]
        author_id = self.fixtures["author1_id"]
        token = self.generate_token(app, tenant_id, author_id)

        # Create authenticated-visibility page
        create_response = await client.post(
            "/api/v1/pages",
            json={
                "title": "Auth Only",
                "body_html": "<p>Authenticated users only</p>",
                "visibility": "authenticated",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        page_data = json.loads(await create_response.get_data())
        slug = page_data["slug"]

        # Access without token (no auth header) — should 403
        response = await client.get(f"/api/v1/pages/{slug}")
        # NOTE: In practice, @login_required will 401 before visibility check.
        # This test verifies the visibility check logic when called with user_id=None.
        # For integration, @login_required handles auth; our visibility ACL is defense-in-depth.
        assert response.status_code in [401, 403]

    @pytest.mark.asyncio
    async def test_visibility_users(self, app):
        """Test visibility: users mode (allowlist)."""
        client = app.test_client()
        tenant_id = self.fixtures["tenant1_id"]
        author_id = self.fixtures["author1_id"]
        other_user_id = self.fixtures[
            "author2_id"
        ]  # Use other identity as allowed user
        token = self.generate_token(app, tenant_id, author_id)
        other_token = self.generate_token(app, tenant_id, other_user_id)

        # Create users-visibility page (allow specific user)
        create_response = await client.post(
            "/api/v1/pages",
            json={
                "title": "Users Only",
                "body_html": "<p>Specific users only</p>",
                "visibility": "users",
                "visibility_users": [other_user_id],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        page_data = json.loads(await create_response.get_data())
        slug = page_data["slug"]

        # Access as allowed user — should 200
        response = await client.get(
            f"/api/v1/pages/{slug}",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert response.status_code == 200

        # Access as non-allowed user (author_id not in visibility_users) — should 403
        response = await client.get(
            f"/api/v1/pages/{slug}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_write_blocked_when_not_readable(self, app):
        """IDOR regression: a pages:write/admin caller who cannot READ a
        role-restricted page must not be able to update or delete it.

        regression: security-review-idor-pages-write
        """
        client = app.test_client()
        tenant_id = self.fixtures["tenant1_id"]
        author_id = self.fixtures["author1_id"]
        attacker_id = self.fixtures["author2_id"]

        # Author (admin) creates a page restricted to the 'editor' role.
        owner_token = self.generate_token(app, tenant_id, author_id)
        create_response = await client.post(
            "/api/v1/pages",
            json={
                "title": "Editors Only Page",
                "body_html": "<p>secret</p>",
                "visibility": "roles",
                "visibility_roles": ["editor"],
            },
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert create_response.status_code == 201
        slug = json.loads(await create_response.get_data())["slug"]

        # Attacker holds write+admin scope but only the 'viewer' role.
        attacker = self.generate_token(
            app,
            tenant_id,
            attacker_id,
            scopes=["pages:read", "pages:write", "pages:admin"],
            roles=["viewer"],
        )
        put = await client.put(
            f"/api/v1/pages/{slug}",
            json={"title": "Hijacked"},
            headers={"Authorization": f"Bearer {attacker}"},
        )
        assert put.status_code in (403, 404)
        deleted = await client.delete(
            f"/api/v1/pages/{slug}",
            headers={"Authorization": f"Bearer {attacker}"},
        )
        assert deleted.status_code in (403, 404)

    @pytest.mark.asyncio
    async def test_bleach_strips_script(self, app):
        """Test bleach sanitization strips <script> tags."""
        client = app.test_client()
        tenant_id = self.fixtures["tenant1_id"]
        author_id = self.fixtures["author1_id"]
        token = self.generate_token(app, tenant_id, author_id)

        # Create page with <script> tag
        response = await client.post(
            "/api/v1/pages",
            json={
                "title": "Bleach Test",
                "body_html": "<p>Safe</p><script>alert('xss')</script>",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 201
        page_data = json.loads(await response.get_data())

        # Verify script was stripped
        assert "<script>" not in page_data.get("body_html", "")

    @pytest.mark.asyncio
    async def test_bleach_strips_onerror(self, app):
        """Test bleach sanitization strips event handlers."""
        client = app.test_client()
        tenant_id = self.fixtures["tenant1_id"]
        author_id = self.fixtures["author1_id"]
        token = self.generate_token(app, tenant_id, author_id)

        # Create page with onerror handler
        response = await client.post(
            "/api/v1/pages",
            json={
                "title": "Event Handler Test",
                "body_html": '<img src="x" onerror="alert(\'xss\')">',
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 201
        page_data = json.loads(await response.get_data())

        # Verify event handler was stripped
        assert "onerror=" not in page_data.get("body_html", "")

    @pytest.mark.asyncio
    async def test_wiki_links_create_references(self, app):
        """Test wiki-links in body_html create references."""
        client = app.test_client()
        db = app.db
        tenant_id = self.fixtures["tenant1_id"]
        author_id = self.fixtures["author1_id"]
        token = self.generate_token(app, tenant_id, author_id)

        # Create page with wiki-link [[page:other-page]]
        response = await client.post(
            "/api/v1/pages",
            json={
                "title": "Referencing Page",
                "body_html": "<p>See [[page:other-page]] for details</p>",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 201
        page_data = json.loads(await response.get_data())
        slug = page_data["slug"]

        # Verify reference was created in db
        def check_reference():
            refs = db(
                (db.references.source_module == "pages")
                & (db.references.source_type == "page")
                & (db.references.source_id == slug)
                & (db.references.target_type == "page")
                & (db.references.target_id == "other-page")
            ).select()
            return len(refs) > 0

        from apps.api.utils.async_utils import run_in_threadpool

        has_ref = await run_in_threadpool(check_reference)
        assert has_ref, "Wiki-link reference was not created"

    @pytest.mark.asyncio
    async def test_backlinks(self, app):
        """Test backlinks endpoint."""
        client = app.test_client()
        tenant_id = self.fixtures["tenant1_id"]
        author_id = self.fixtures["author1_id"]
        token = self.generate_token(app, tenant_id, author_id)

        # Create page A
        response_a = await client.post(
            "/api/v1/pages",
            json={
                "title": "Target Page",
                "body_html": "<p>This is the target</p>",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        page_a_data = json.loads(await response_a.get_data())
        slug_a = page_a_data["slug"]

        # Create page B that references A
        response_b = await client.post(
            "/api/v1/pages",
            json={
                "title": "Referring Page",
                "body_html": f"<p>See [[page:{slug_a}]] for details</p>",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        # Get backlinks for A
        response = await client.get(
            f"/api/v1/pages/{slug_a}/backlinks",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert len(data["backlinks"]) > 0
        assert data["backlinks"][0]["source_type"] == "page"

    @pytest.mark.asyncio
    async def test_cross_tenant_isolation(self, app):
        """Test pages from different tenants are isolated."""
        client = app.test_client()
        tenant1_id = self.fixtures["tenant1_id"]
        tenant2_id = self.fixtures["tenant2_id"]
        author1_id = self.fixtures["author1_id"]
        author2_id = self.fixtures["author2_id"]
        token1 = self.generate_token(app, tenant1_id, author1_id)
        token2 = self.generate_token(app, tenant2_id, author2_id)

        # Create page in tenant 1
        response1 = await client.post(
            "/api/v1/pages",
            json={
                "title": "Tenant 1 Page",
                "body_html": "<p>T1 content</p>",
            },
            headers={"Authorization": f"Bearer {token1}"},
        )
        page1_data = json.loads(await response1.get_data())

        # Create page in tenant 2 with same title (different slug namespaces)
        response2 = await client.post(
            "/api/v1/pages",
            json={
                "title": "Tenant 1 Page",  # Same title, same slug, different tenant
                "body_html": "<p>T2 content</p>",
            },
            headers={"Authorization": f"Bearer {token2}"},
        )
        page2_data = json.loads(await response2.get_data())

        # Both should succeed (per-tenant slug uniqueness)
        assert response1.status_code == 201
        assert response2.status_code == 201

        # Tenant 1 user should see only their page when listing
        response = await client.get(
            "/api/v1/pages",
            headers={"Authorization": f"Bearer {token1}"},
        )
        data = json.loads(await response.get_data())
        assert len([p for p in data["items"] if p["title"] == "Tenant 1 Page"]) == 1

        # Both tenants share the slug; tenant 2 requesting it gets its OWN page,
        # never tenant 1's — proving cross-tenant isolation.
        response = await client.get(
            f"/api/v1/pages/{page1_data['slug']}",
            headers={"Authorization": f"Bearer {token2}"},
        )
        assert response.status_code == 200
        seen = json.loads(await response.get_data())
        assert seen["village_id"] == page2_data["village_id"]
        assert seen["village_id"] != page1_data["village_id"]

    @pytest.mark.asyncio
    async def test_update_page(self, app):
        """Test page update."""
        client = app.test_client()
        tenant_id = self.fixtures["tenant1_id"]
        author_id = self.fixtures["author1_id"]
        token = self.generate_token(app, tenant_id, author_id)

        # Create page
        create_response = await client.post(
            "/api/v1/pages",
            json={
                "title": "Original Title",
                "body_html": "<p>Original</p>",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        page_data = json.loads(await create_response.get_data())
        original_slug = page_data["slug"]

        # Update page
        update_response = await client.put(
            f"/api/v1/pages/{original_slug}",
            json={
                "title": "Updated Title",
                "body_html": "<p>Updated</p>",
                "status": "published",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert update_response.status_code == 200
        updated = json.loads(await update_response.get_data())
        assert updated["title"] == "Updated Title"
        assert updated["status"] == "published"
        assert updated["published_at"] is not None

    @pytest.mark.asyncio
    async def test_delete_page(self, app):
        """Test page deletion (admin scope)."""
        client = app.test_client()
        tenant_id = self.fixtures["tenant1_id"]
        author_id = self.fixtures["author1_id"]
        token = self.generate_token(app, tenant_id, author_id)

        # Create page
        create_response = await client.post(
            "/api/v1/pages",
            json={
                "title": "Delete Me",
                "body_html": "<p>To be deleted</p>",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        page_data = json.loads(await create_response.get_data())
        slug = page_data["slug"]

        # Delete page
        delete_response = await client.delete(
            f"/api/v1/pages/{slug}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert delete_response.status_code == 204

        # Verify page is gone
        response = await client.get(
            f"/api/v1/pages/{slug}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404
