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

    @pytest.mark.asyncio
    async def test_list_all_executions_tenant_wide(self, app, client):
        """GET /streams/executions returns executions from all streams in tenant.

        regression: streams-executions-frontend-#xyz
        """
        db = app.db
        t = self.fixtures["tenant_id"]
        owner_id = self.fixtures["identity_id"]
        token = self._token(app, t, owner_id)
        from apps.api.utils.async_utils import run_in_threadpool

        def _create():
            now = datetime.now(timezone.utc)
            # Create 2 streams
            stream_ids = []
            for i in range(2):
                stream_id = db.stream_playbooks.insert(
                    tenant_id=t,
                    village_id=f"test-{uuid.uuid4().hex[:24]}",
                    name=f"Stream {i + 1}",
                    description=f"Test stream {i + 1}",
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
                stream_ids.append(stream_id)

                # Create 2 executions per stream
                for j in range(2):
                    exec_uuid = str(uuid.uuid4())
                    db.stream_executions.insert(
                        tenant_id=t,
                        playbook_id=stream_id,
                        execution_id=exec_uuid,
                        status="completed",
                        trigger_type="manual",
                        triggered_by_identity_id=owner_id,
                        input_json={"test": f"input-{i}-{j}"},
                        output_json={"result": "success"},
                        started_at=now - timedelta(hours=i * 2 + j),
                        completed_at=now
                        - timedelta(hours=i * 2 + j)
                        + timedelta(seconds=30),
                        duration_ms=30000,
                        created_at=now - timedelta(hours=i * 2 + j),
                        updated_at=now
                        - timedelta(hours=i * 2 + j)
                        + timedelta(seconds=30),
                    )
                db.commit()
            return stream_ids

        stream_ids = await run_in_threadpool(_create)

        # List all executions in the tenant
        response = await client.get(
            "/api/v1/streams/executions",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        body = await response.get_json()
        assert "data" in body
        execs = body["data"]
        assert len(execs) >= 4  # At least 4 executions (2 streams × 2 execs)

        # Verify each execution has stream identity fields
        for exec_data in execs:
            assert "stream_id" in exec_data, "Execution must include stream_id"
            assert "stream_name" in exec_data, "Execution must include stream_name"
            assert "execution_id" in exec_data
            assert "status" in exec_data
            assert "created_at" in exec_data, "Execution must include created_at"

        # Verify both streams' executions appear
        stream_ids_in_response = {e["stream_id"] for e in execs}
        assert stream_ids[0] in stream_ids_in_response
        assert stream_ids[1] in stream_ids_in_response

    @pytest.mark.asyncio
    async def test_list_all_executions_tenant_isolation(self, app, client):
        """GET /streams/executions does not leak executions from other tenants."""
        db = app.db
        t = self.fixtures["tenant_id"]
        owner_id = self.fixtures["identity_id"]
        token = self._token(app, t, owner_id)
        from apps.api.utils.async_utils import run_in_threadpool

        def _create():
            now = datetime.now(timezone.utc)

            # Create execution in THIS tenant
            stream_id = db.stream_playbooks.insert(
                tenant_id=t,
                village_id=f"test-{uuid.uuid4().hex[:24]}",
                name="My Stream",
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
            exec_uuid = str(uuid.uuid4())
            db.stream_executions.insert(
                tenant_id=t,
                playbook_id=stream_id,
                execution_id=exec_uuid,
                status="completed",
                trigger_type="manual",
                triggered_by_identity_id=owner_id,
                started_at=now,
                completed_at=now + timedelta(seconds=10),
                duration_ms=10000,
                created_at=now,
                updated_at=now + timedelta(seconds=10),
            )

            # Create execution in OTHER tenant
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
            other_stream = db.stream_playbooks.insert(
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
            other_exec_uuid = str(uuid.uuid4())
            db.stream_executions.insert(
                tenant_id=other_tenant,
                playbook_id=other_stream,
                execution_id=other_exec_uuid,
                status="completed",
                trigger_type="manual",
                triggered_by_identity_id=other_ident,
                started_at=now,
                completed_at=now + timedelta(seconds=10),
                duration_ms=10000,
                created_at=now,
                updated_at=now + timedelta(seconds=10),
            )
            db.commit()
            return exec_uuid, other_exec_uuid

        my_exec_id, other_exec_id = await run_in_threadpool(_create)

        # List all executions for THIS tenant
        response = await client.get(
            "/api/v1/streams/executions",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        body = await response.get_json()
        exec_ids = {e["execution_id"] for e in body["data"]}

        # Should see MY execution
        assert my_exec_id in exec_ids

        # Should NOT see OTHER tenant's execution
        assert other_exec_id not in exec_ids

    @pytest.mark.asyncio
    async def test_list_all_executions_empty(self, app, client):
        """GET /streams/executions returns empty data array when no executions exist."""
        t = self.fixtures["tenant_id"]
        owner_id = self.fixtures["identity_id"]
        token = self._token(app, t, owner_id)

        # No streams/executions created yet, should return empty
        response = await client.get(
            "/api/v1/streams/executions",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        body = await response.get_json()
        assert "data" in body
        assert body["data"] == []
        assert body.get("total", 0) >= 0

    @pytest.mark.asyncio
    async def test_list_all_executions_total_access_filtered(self, app, client):
        """GET /streams/executions: total must be access-filtered, not raw count.

        regression: streams/executions total must be access-filtered
        - Create PUBLIC stream with K executions (readable by caller)
        - Create PRIVATE stream with M executions (not readable by caller)
        - Assert total == K (NOT K+M) and len(data) == K
        """
        db = app.db
        t = self.fixtures["tenant_id"]
        reader_id = self.fixtures["identity_id"]  # Will only be able to read public
        token = self._token(app, t, reader_id)
        from apps.api.utils.async_utils import run_in_threadpool

        def _create():
            now = datetime.now(timezone.utc)

            # Create a second identity (owner of private stream)
            private_owner_id = db.identities.insert(
                tenant_id=t,
                username=f"private-owner-{uuid.uuid4().hex[:8]}@test.local",
                email=f"private-owner-{uuid.uuid4().hex[:8]}@test.local",
                identity_type="human",
                auth_provider="local",
                is_active=True,
                is_superuser=False,
                mfa_enabled=False,
                must_change_password=False,
                portal_role="viewer",
                full_name="Private Owner",
                created_at=now,
                updated_at=now,
            )

            # Create PUBLIC stream with 3 executions (readable by reader_id)
            public_stream_id = db.stream_playbooks.insert(
                tenant_id=t,
                village_id=f"test-{uuid.uuid4().hex[:24]}",
                name="Public Stream",
                description="A public stream",
                owner_identity_id=reader_id,
                created_by_identity_id=reader_id,
                trigger_type="manual",
                is_public=True,
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
            for j in range(3):
                exec_uuid = str(uuid.uuid4())
                db.stream_executions.insert(
                    tenant_id=t,
                    playbook_id=public_stream_id,
                    execution_id=exec_uuid,
                    status="completed",
                    trigger_type="manual",
                    triggered_by_identity_id=reader_id,
                    input_json={"test": f"public-input-{j}"},
                    output_json={"result": "success"},
                    started_at=now - timedelta(hours=j),
                    completed_at=now - timedelta(hours=j) + timedelta(seconds=30),
                    duration_ms=30000,
                    created_at=now - timedelta(hours=j),
                    updated_at=now - timedelta(hours=j) + timedelta(seconds=30),
                )

            # Create PRIVATE stream with 2 executions (NOT readable by reader_id)
            private_stream_id = db.stream_playbooks.insert(
                tenant_id=t,
                village_id=f"test-{uuid.uuid4().hex[:24]}",
                name="Private Stream",
                description="A private stream",
                owner_identity_id=private_owner_id,
                created_by_identity_id=private_owner_id,
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
            for j in range(2):
                exec_uuid = str(uuid.uuid4())
                db.stream_executions.insert(
                    tenant_id=t,
                    playbook_id=private_stream_id,
                    execution_id=exec_uuid,
                    status="completed",
                    trigger_type="manual",
                    triggered_by_identity_id=private_owner_id,
                    input_json={"test": f"private-input-{j}"},
                    output_json={"result": "success"},
                    started_at=now - timedelta(hours=10 + j),
                    completed_at=now - timedelta(hours=10 + j) + timedelta(seconds=30),
                    duration_ms=30000,
                    created_at=now - timedelta(hours=10 + j),
                    updated_at=now - timedelta(hours=10 + j) + timedelta(seconds=30),
                )

            db.commit()

        await run_in_threadpool(_create)

        # List all executions as reader_id (can only read public)
        response = await client.get(
            "/api/v1/streams/executions",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        body = await response.get_json()
        assert "data" in body
        assert "total" in body

        # Total must be 3 (public only), NOT 5 (public + private)
        assert body["total"] == 3, f"Expected total=3, got {body['total']}"

        # Data must be 3 executions
        assert len(body["data"]) == 3, f"Expected 3 executions, got {len(body['data'])}"

        # Verify all returned executions are from public stream
        for exec_data in body["data"]:
            assert exec_data["stream_name"] == "Public Stream"
