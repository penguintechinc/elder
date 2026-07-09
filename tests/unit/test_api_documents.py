"""Integration tests for the documents module.

Covers CRUD, bleach sanitization of rendered markdown, and the role-based
visibility ACL (_can_read_document). Uses real JWT tokens (sub = real identity
id) and a real database.

regression: scope-retrofit
"""

# flake8: noqa: E501

import json
import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
import pytest_asyncio


@pytest.mark.integration
class TestDocumentsAPI:
    """Documents CRUD + security tests."""

    @pytest_asyncio.fixture(autouse=True)
    async def setup_db(self, app, test_database_url):
        if not test_database_url:
            pytest.skip("DATABASE_URL not set")

        db = app.db

        def _setup():
            now = datetime.now(timezone.utc)
            tenant_id = db.tenants.insert(
                name="Doc Tenant",
                slug=f"doc-tenant-{uuid.uuid4().hex[:8]}",
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            email = f"author-{uuid.uuid4().hex[:8]}@test.local"
            author_id = db.identities.insert(
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
            return {"tenant_id": tenant_id, "author_id": author_id}

        from apps.api.utils.async_utils import run_in_threadpool

        self.fixtures = await run_in_threadpool(_setup)

    def _token(self, app, tenant_id, identity_id, scopes, roles=None):
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
    async def test_create_document_draft(self, app):
        client = app.test_client()
        t, a = self.fixtures["tenant_id"], self.fixtures["author_id"]
        token = self._token(app, t, a, ["documents:write"])
        resp = await client.post(
            "/api/v1/documents",
            json={
                "title": "Getting Started",
                "body": "# Welcome\n\nThis is a guide.",
                "category": "intro",
                "tags": ["beginner"],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201
        data = json.loads(await resp.get_data())
        assert data["title"] == "Getting Started"
        assert data["slug"] == "getting-started"

    @pytest.mark.asyncio
    async def test_bleach_strips_script(self, app):
        client = app.test_client()
        t, a = self.fixtures["tenant_id"], self.fixtures["author_id"]
        token = self._token(app, t, a, ["documents:write", "documents:read"])
        resp = await client.post(
            "/api/v1/documents",
            json={
                "title": "XSS Doc",
                "body": "Safe text <script>alert('xss')</script>",
                "status": "published",
                "visibility": "public",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201
        slug = json.loads(await resp.get_data())["slug"]
        got = await client.get(
            f"/api/v1/documents/{slug}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert got.status_code == 200
        body_html = json.loads(await got.get_data()).get("body_html", "")
        assert "<script>" not in body_html

    @pytest.mark.asyncio
    async def test_roles_visibility_enforced(self, app):
        """Role-based visibility: only callers holding a listed role can read."""
        client = app.test_client()
        t, a = self.fixtures["tenant_id"], self.fixtures["author_id"]
        writer = self._token(app, t, a, ["documents:write"], roles=["editor"])
        resp = await client.post(
            "/api/v1/documents",
            json={
                "title": "Editors Only",
                "body": "secret content",
                "status": "published",
                "visibility": "roles",
                "visibility_roles": ["editor"],
            },
            headers={"Authorization": f"Bearer {writer}"},
        )
        assert resp.status_code == 201
        slug = json.loads(await resp.get_data())["slug"]

        # Reader WITHOUT the 'editor' role -> denied.
        reader = self._token(app, t, a, ["documents:read"], roles=["viewer"])
        denied = await client.get(
            f"/api/v1/documents/{slug}",
            headers={"Authorization": f"Bearer {reader}"},
        )
        assert denied.status_code in (403, 404)

        # Reader WITH the 'editor' role -> allowed.
        ok = self._token(app, t, a, ["documents:read"], roles=["editor"])
        allowed = await client.get(
            f"/api/v1/documents/{slug}",
            headers={"Authorization": f"Bearer {ok}"},
        )
        assert allowed.status_code == 200

    @pytest.mark.asyncio
    async def test_write_blocked_when_not_readable(self, app):
        """IDOR regression: a documents:write caller who cannot READ a
        role-restricted doc must not be able to update or delete it.

        regression: security-review-idor-documents-write
        """
        client = app.test_client()
        t, a = self.fixtures["tenant_id"], self.fixtures["author_id"]
        # Author (editor) creates a doc restricted to the 'editor' role.
        writer = self._token(
            app, t, a, ["documents:write", "documents:read"], roles=["editor"]
        )
        resp = await client.post(
            "/api/v1/documents",
            json={
                "title": "Editors Only Mutable",
                "body": "secret content",
                "status": "published",
                "visibility": "roles",
                "visibility_roles": ["editor"],
            },
            headers={"Authorization": f"Bearer {writer}"},
        )
        assert resp.status_code == 201
        slug = json.loads(await resp.get_data())["slug"]
        # Editor reads it to learn the numeric id (attacker would guess it).
        got = await client.get(
            f"/api/v1/documents/{slug}",
            headers={"Authorization": f"Bearer {writer}"},
        )
        doc_id = json.loads(await got.get_data())["id"]

        # Attacker: documents:write but WITHOUT the 'editor' role.
        attacker = self._token(app, t, a, ["documents:write"], roles=["viewer"])
        patched = await client.patch(
            f"/api/v1/documents/{doc_id}",
            json={"title": "Hijacked"},
            headers={"Authorization": f"Bearer {attacker}"},
        )
        assert patched.status_code in (403, 404)
        deleted = await client.delete(
            f"/api/v1/documents/{doc_id}",
            headers={"Authorization": f"Bearer {attacker}"},
        )
        assert deleted.status_code in (403, 404)

    @pytest.mark.asyncio
    async def test_public_list_no_auth(self, app):
        client = app.test_client()
        resp = await client.get("/api/v1/documents/public")
        assert resp.status_code == 200
        data = json.loads(await resp.get_data())
        assert "items" in data
        assert "pagination" in data
