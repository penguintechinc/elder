"""Streams API tests (CRUD + execution + webhooks).

regression: streams-crud-phase4b2
"""

import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
import pytest_asyncio


@pytest.mark.integration
class TestStreams:
    """Streams CRUD and execution tests."""

    @pytest_asyncio.fixture(autouse=True)
    async def setup_db(self, app, test_database_url):
        """Set up test database with tenant and identity."""
        if not test_database_url:
            pytest.skip("DATABASE_URL not set")

        db = app.db

        def _setup():
            now = datetime.now(timezone.utc)
            tenant_id = db.tenants.insert(
                name="Streams Tenant",
                slug=f"str-{uuid.uuid4().hex[:8]}",
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            email = f"streams-user-{uuid.uuid4().hex[:8]}@test.local"
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
                full_name="Test User",
                created_at=now,
                updated_at=now,
            )
            db.commit()
            return {"tenant_id": tenant_id, "identity_id": identity_id}

        from apps.api.utils.async_utils import run_in_threadpool

        self.fixtures = await run_in_threadpool(_setup)

    def _token(self, app, tenant_id, identity_id, scopes=None, roles=None):
        """Create a test JWT token."""
        scopes = scopes or ["streams:read", "streams:write", "streams:execute"]
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
    async def test_create_stream_happy_path(self, app, client):
        """Test creating a stream with happy path."""
        t = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id"]
        token = self._token(app, t, identity_id)

        response = await client.post(
            "/api/v1/streams",
            json={
                "name": "Test Stream",
                "description": "A test stream",
                "trigger_type": "manual",
                "tags": ["test"],
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 201
        data = await response.get_json()
        assert data["name"] == "Test Stream"
        assert data["description"] == "A test stream"
        assert data["owner_identity_id"] == identity_id

    @pytest.mark.asyncio
    async def test_list_streams_happy_path(self, app, client):
        """Test listing streams."""
        db = app.db
        t = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id"]
        token = self._token(app, t, identity_id)

        # Create a stream first
        def _create():
            now = datetime.now(timezone.utc)
            stream_id = db.stream_playbooks.insert(
                tenant_id=t,
                village_id=f"test-{uuid.uuid4().hex[:24]}",
                name="Test Stream",
                description="Test",
                owner_identity_id=identity_id,
                created_by_identity_id=identity_id,
                trigger_type="manual",
                is_public=False,
                is_template=False,
                is_enabled=False,
                tags=["test"],
                status="draft",
                execution_count=0,
                success_count=0,
                failure_count=0,
                created_at=now,
                updated_at=now,
            )
            db.commit()
            return stream_id

        from apps.api.utils.async_utils import run_in_threadpool

        stream_id = await run_in_threadpool(_create)

        response = await client.get(
            "/api/v1/streams",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = await response.get_json()
        assert len(data["data"]) > 0
        assert any(s["id"] == stream_id for s in data["data"])

    @pytest.mark.asyncio
    async def test_get_stream_happy_path(self, app, client):
        """Test getting a specific stream."""
        db = app.db
        t = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id"]
        token = self._token(app, t, identity_id)

        # Create a stream first
        def _create():
            now = datetime.now(timezone.utc)
            stream_id = db.stream_playbooks.insert(
                tenant_id=t,
                village_id=f"test-{uuid.uuid4().hex[:24]}",
                name="Test Stream",
                description="Test",
                owner_identity_id=identity_id,
                created_by_identity_id=identity_id,
                trigger_type="manual",
                is_public=False,
                is_template=False,
                is_enabled=False,
                tags=[],
                status="draft",
                execution_count=0,
                success_count=0,
                failure_count=0,
                created_at=now,
                updated_at=now,
            )
            db.commit()
            return stream_id

        from apps.api.utils.async_utils import run_in_threadpool

        stream_id = await run_in_threadpool(_create)

        response = await client.get(
            f"/api/v1/streams/{stream_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = await response.get_json()
        assert data["id"] == stream_id
        assert data["name"] == "Test Stream"

    @pytest.mark.asyncio
    async def test_update_stream_happy_path(self, app, client):
        """Test updating a stream."""
        db = app.db
        t = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id"]
        token = self._token(app, t, identity_id)

        # Create a stream first
        def _create():
            now = datetime.now(timezone.utc)
            stream_id = db.stream_playbooks.insert(
                tenant_id=t,
                village_id=f"test-{uuid.uuid4().hex[:24]}",
                name="Original Name",
                description="Original",
                owner_identity_id=identity_id,
                created_by_identity_id=identity_id,
                trigger_type="manual",
                is_public=False,
                is_template=False,
                is_enabled=False,
                tags=[],
                status="draft",
                execution_count=0,
                success_count=0,
                failure_count=0,
                created_at=now,
                updated_at=now,
            )
            db.commit()
            return stream_id

        from apps.api.utils.async_utils import run_in_threadpool

        stream_id = await run_in_threadpool(_create)

        response = await client.put(
            f"/api/v1/streams/{stream_id}",
            json={
                "name": "Updated Name",
                "description": "Updated description",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = await response.get_json()
        assert data["name"] == "Updated Name"
        assert data["description"] == "Updated description"

    @pytest.mark.asyncio
    async def test_execute_stream_happy_path(self, app, client):
        """Test executing a stream (creates queued execution)."""
        db = app.db
        t = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id"]
        token = self._token(app, t, identity_id)

        # Create a stream first
        def _create():
            now = datetime.now(timezone.utc)
            stream_id = db.stream_playbooks.insert(
                tenant_id=t,
                village_id=f"test-{uuid.uuid4().hex[:24]}",
                name="Executable Stream",
                description="",
                owner_identity_id=identity_id,
                created_by_identity_id=identity_id,
                trigger_type="manual",
                is_public=False,
                is_template=False,
                is_enabled=False,
                tags=[],
                status="draft",
                execution_count=0,
                success_count=0,
                failure_count=0,
                created_at=now,
                updated_at=now,
            )
            db.commit()
            return stream_id

        from apps.api.utils.async_utils import run_in_threadpool

        stream_id = await run_in_threadpool(_create)

        response = await client.post(
            f"/api/v1/streams/{stream_id}/execute",
            json={"input_data": {"key": "value"}},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 202
        data = await response.get_json()
        assert data["status"] == "queued"

    @pytest.mark.asyncio
    async def test_webhook_create_happy_path(self, app, client):
        """Test creating a webhook."""
        db = app.db
        t = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id"]
        token = self._token(app, t, identity_id)

        # Create a stream first
        def _create():
            now = datetime.now(timezone.utc)
            stream_id = db.stream_playbooks.insert(
                tenant_id=t,
                village_id=f"test-{uuid.uuid4().hex[:24]}",
                name="Stream With Webhook",
                description="",
                owner_identity_id=identity_id,
                created_by_identity_id=identity_id,
                trigger_type="manual",
                is_public=False,
                is_template=False,
                is_enabled=False,
                tags=[],
                status="draft",
                execution_count=0,
                success_count=0,
                failure_count=0,
                created_at=now,
                updated_at=now,
            )
            db.commit()
            return stream_id

        from apps.api.utils.async_utils import run_in_threadpool

        stream_id = await run_in_threadpool(_create)

        response = await client.post(
            f"/api/v1/streams/{stream_id}/webhooks",
            json={
                "name": "Test Webhook",
                "allowed_methods": ["POST"],
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 201
        data = await response.get_json()
        assert data["name"] == "Test Webhook"
        assert data["token"]

    @pytest.mark.asyncio
    async def test_update_node_metadata_happy_path(self, app, client):
        """Test updating node metadata."""
        db = app.db
        t = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id"]
        token = self._token(app, t, identity_id)

        # Create a stream first
        def _create():
            now = datetime.now(timezone.utc)
            stream_id = db.stream_playbooks.insert(
                tenant_id=t,
                village_id=f"test-{uuid.uuid4().hex[:24]}",
                name="Stream With Nodes",
                description="",
                owner_identity_id=identity_id,
                created_by_identity_id=identity_id,
                trigger_type="manual",
                is_public=False,
                is_template=False,
                is_enabled=False,
                tags=[],
                status="draft",
                execution_count=0,
                success_count=0,
                failure_count=0,
                created_at=now,
                updated_at=now,
            )
            db.commit()
            return stream_id

        from apps.api.utils.async_utils import run_in_threadpool

        stream_id = await run_in_threadpool(_create)
        node_id = "node-123"

        response = await client.put(
            f"/api/v1/streams/{stream_id}/nodes/{node_id}/metadata",
            json={
                "comments": "This is a test node",
                "metadata": {"key": "value"},
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = await response.get_json()
        assert data["comments"] == "This is a test node"

    # ------------------------------------------------------------------
    # Security / negative cases
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_get_stream_cross_tenant_404(self, app, client):
        """A stream owned by another tenant is indistinguishable from missing."""
        db = app.db
        from apps.api.utils.async_utils import run_in_threadpool

        def _seed_other_tenant():
            now = datetime.now(timezone.utc)
            other_tenant = db.tenants.insert(
                name="Other Tenant",
                slug=f"oth-{uuid.uuid4().hex[:8]}",
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            other_ident = db.identities.insert(
                tenant_id=other_tenant,
                username=f"other-{uuid.uuid4().hex[:8]}@test.local",
                email=f"other-{uuid.uuid4().hex[:8]}@test.local",
                identity_type="human",
                auth_provider="local",
                is_active=True,
                is_superuser=False,
                mfa_enabled=False,
                must_change_password=False,
                portal_role="viewer",
                full_name="Other User",
                created_at=now,
                updated_at=now,
            )
            stream_id = db.stream_playbooks.insert(
                tenant_id=other_tenant,
                village_id=f"test-{uuid.uuid4().hex[:24]}",
                name="Other Tenant Stream",
                description="",
                owner_identity_id=other_ident,
                created_by_identity_id=other_ident,
                trigger_type="manual",
                is_public=False,
                is_template=False,
                is_enabled=False,
                tags=[],
                status="draft",
                execution_count=0,
                success_count=0,
                failure_count=0,
                created_at=now,
                updated_at=now,
            )
            db.commit()
            return stream_id

        other_stream_id = await run_in_threadpool(_seed_other_tenant)

        # Caller authenticated for the ORIGINAL tenant must not see it.
        token = self._token(
            app, self.fixtures["tenant_id"], self.fixtures["identity_id"]
        )
        response = await client.get(
            f"/api/v1/streams/{other_stream_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_viewer_share_cannot_edit_or_execute(self, app, client):
        """A viewer-permission share grants read but NOT write/execute."""
        db = app.db
        t = self.fixtures["tenant_id"]
        owner_id = self.fixtures["identity_id"]
        from apps.api.utils.async_utils import run_in_threadpool

        def _seed():
            now = datetime.now(timezone.utc)
            viewer_id = db.identities.insert(
                tenant_id=t,
                username=f"viewer-{uuid.uuid4().hex[:8]}@test.local",
                email=f"viewer-{uuid.uuid4().hex[:8]}@test.local",
                identity_type="human",
                auth_provider="local",
                is_active=True,
                is_superuser=False,
                mfa_enabled=False,
                must_change_password=False,
                portal_role="viewer",
                full_name="Viewer User",
                created_at=now,
                updated_at=now,
            )
            stream_id = db.stream_playbooks.insert(
                tenant_id=t,
                village_id=f"test-{uuid.uuid4().hex[:24]}",
                name="Owned Stream",
                description="",
                owner_identity_id=owner_id,
                created_by_identity_id=owner_id,
                trigger_type="manual",
                is_public=False,
                is_template=False,
                is_enabled=False,
                tags=[],
                status="draft",
                execution_count=0,
                success_count=0,
                failure_count=0,
                created_at=now,
                updated_at=now,
            )
            db.stream_shares.insert(
                tenant_id=t,
                playbook_id=stream_id,
                shared_with_identity_id=viewer_id,
                shared_by_identity_id=owner_id,
                permission="viewer",
                created_at=now,
                updated_at=now,
            )
            db.commit()
            return viewer_id, stream_id

        viewer_id, stream_id = await run_in_threadpool(_seed)
        viewer_token = self._token(app, t, viewer_id)
        auth = {"Authorization": f"Bearer {viewer_token}"}

        # Viewer CAN read.
        read = await client.get(f"/api/v1/streams/{stream_id}", headers=auth)
        assert read.status_code == 200

        # Viewer CANNOT update (denied — 403 forbidden or 404 existence-hiding).
        upd = await client.put(
            f"/api/v1/streams/{stream_id}",
            json={"name": "Hijacked"},
            headers=auth,
        )
        assert upd.status_code in (403, 404)

        # Viewer CANNOT execute.
        ex = await client.post(
            f"/api/v1/streams/{stream_id}/execute",
            json={"trigger_type": "manual"},
            headers=auth,
        )
        assert ex.status_code in (403, 404)

    @pytest.mark.asyncio
    async def test_execute_creates_queued_execution(self, app, client):
        """Execute returns 202 and the queued execution appears in the list."""
        db = app.db
        t = self.fixtures["tenant_id"]
        owner_id = self.fixtures["identity_id"]
        token = self._token(app, t, owner_id)
        from apps.api.utils.async_utils import run_in_threadpool

        def _create():
            now = datetime.now(timezone.utc)
            stream_id = db.stream_playbooks.insert(
                tenant_id=t,
                village_id=f"test-{uuid.uuid4().hex[:24]}",
                name="Executable Stream",
                description="",
                owner_identity_id=owner_id,
                created_by_identity_id=owner_id,
                trigger_type="manual",
                is_public=False,
                is_template=False,
                is_enabled=False,
                tags=[],
                status="draft",
                execution_count=0,
                success_count=0,
                failure_count=0,
                created_at=now,
                updated_at=now,
            )
            db.commit()
            return stream_id

        stream_id = await run_in_threadpool(_create)

        ex = await client.post(
            f"/api/v1/streams/{stream_id}/execute",
            json={"trigger_type": "manual"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert ex.status_code == 202
        ex_data = await ex.get_json()
        assert ex_data["status"] == "queued"

        listed = await client.get(
            f"/api/v1/streams/{stream_id}/executions",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert listed.status_code == 200
        body = await listed.get_json()
        rows = body.get("items", body) if isinstance(body, dict) else body
        assert len(rows) >= 1
