"""Streams approvals and webhook hooks API tests.

regression: streams-approvals-hooks-phase4b3c
"""

import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
import pytest_asyncio


@pytest.mark.integration
class TestStreamApprovals:
    """Stream approval gates and execution approvals tests."""

    @pytest_asyncio.fixture(autouse=True)
    async def setup_db(self, app, test_database_url):
        """Set up test database with tenant, identity, and approval fixtures."""
        if not test_database_url:
            pytest.skip("DATABASE_URL not set")

        db = app.db

        def _setup():
            now = datetime.now(timezone.utc)
            tenant_id = db.tenants.insert(
                name="Approvals Tenant",
                slug=f"apr-{uuid.uuid4().hex[:8]}",
                is_active=True,
                created_at=now,
                updated_at=now,
            )

            # Create two identities: approver and requester
            email_approver = f"approver-{uuid.uuid4().hex[:8]}@test.local"
            approver_id = db.identities.insert(
                tenant_id=tenant_id,
                username=email_approver,
                email=email_approver,
                identity_type="human",
                auth_provider="local",
                is_active=True,
                is_superuser=False,
                mfa_enabled=False,
                must_change_password=False,
                portal_role="viewer",
                full_name="Approver User",
                created_at=now,
                updated_at=now,
            )

            email_requester = f"requester-{uuid.uuid4().hex[:8]}@test.local"
            requester_id = db.identities.insert(
                tenant_id=tenant_id,
                username=email_requester,
                email=email_requester,
                identity_type="human",
                auth_provider="local",
                is_active=True,
                is_superuser=False,
                mfa_enabled=False,
                must_change_password=False,
                portal_role="viewer",
                full_name="Requester User",
                created_at=now,
                updated_at=now,
            )

            # Create a stream (owned by approver so they can edit)
            stream_id = db.stream_playbooks.insert(
                tenant_id=tenant_id,
                village_id=f"test-{uuid.uuid4().hex[:24]}",
                name="Test Stream",
                description="Test stream for approvals",
                owner_identity_id=approver_id,  # Approver owns it so they can edit
                created_by_identity_id=approver_id,
                trigger_type="manual",
                is_public=True,  # Public so everyone can access
                is_template=False,
                is_enabled=True,
                tags=[],
                status="active",
                execution_count=0,
                success_count=0,
                failure_count=0,
                created_at=now,
                updated_at=now,
            )

            # Create version
            db.stream_versions.insert(
                tenant_id=tenant_id,
                playbook_id=stream_id,
                version_number=1,
                created_by_identity_id=requester_id,
                nodes_json=[],
                edges_json=[],
                canvas_json={"nodes": [], "edges": []},
                change_summary="Initial version",
                created_at=now,
                updated_at=now,
            )

            # Create an approval gate
            gate_id = db.stream_approval_gates.insert(
                tenant_id=tenant_id,
                gate_id=str(uuid.uuid4()),
                playbook_id=stream_id,
                node_id="node-123",
                name="Production Approval",
                description="Requires approval",
                require_approval=True,
                min_approvers=1,
                approvers=[approver_id],  # Only approver_id can approve
                approver_groups=[],
                timeout_minutes=60,
                is_enabled=True,
                created_at=now,
                updated_at=now,
            )

            # Create a paused execution
            execution_id = str(uuid.uuid4())
            db.stream_executions.insert(
                tenant_id=tenant_id,
                playbook_id=stream_id,
                execution_id=execution_id,
                status="paused_for_approval",
                trigger_type="manual",
                triggered_by_identity_id=requester_id,
                input_json={"test": "data"},
                created_at=now,
                updated_at=now,
            )

            db.commit()
            return {
                "tenant_id": tenant_id,
                "approver_id": approver_id,
                "requester_id": requester_id,
                "stream_id": stream_id,
                "gate_id": gate_id,
                "execution_id": execution_id,
            }

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
    async def test_list_approval_gates(self, app, client):
        """Test listing approval gates for a stream."""
        db = app.db
        t = self.fixtures["tenant_id"]
        approver_id = self.fixtures["approver_id"]
        stream_id = self.fixtures["stream_id"]
        token = self._token(app, t, approver_id)

        response = await client.get(
            f"/api/v1/streams/{stream_id}/approval-gates",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = await response.get_json()
        assert len(data["gates"]) > 0
        assert data["count"] == 1
        assert data["gates"][0]["name"] == "Production Approval"

    @pytest.mark.asyncio
    async def test_create_approval_gate(self, app, client):
        """Test creating an approval gate."""
        t = self.fixtures["tenant_id"]
        approver_id = self.fixtures["approver_id"]
        stream_id = self.fixtures["stream_id"]
        token = self._token(app, t, approver_id)

        response = await client.post(
            f"/api/v1/streams/{stream_id}/approval-gates",
            json={
                "node_id": "node-456",
                "name": "Staging Approval",
                "description": "Staging gate",
                "require_approval": True,
                "min_approvers": 1,
                "approvers": [approver_id],
                "is_enabled": True,
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 201
        data = await response.get_json()
        assert data["name"] == "Staging Approval"
        assert data["is_enabled"] is True

    @pytest.mark.asyncio
    async def test_get_my_approvals_as_approver(self, app, client):
        """Test getting pending approvals for an approver."""
        t = self.fixtures["tenant_id"]
        approver_id = self.fixtures["approver_id"]
        token = self._token(app, t, approver_id)

        response = await client.get(
            "/api/v1/streams/my-approvals",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = await response.get_json()
        assert data["count"] >= 1
        assert any(
            a["execution_id"] == self.fixtures["execution_id"]
            for a in data["pending_approvals"]
        )

    @pytest.mark.asyncio
    async def test_get_my_approvals_as_non_approver(self, app, client):
        """Test that non-approvers see no pending approvals."""
        db = app.db
        t = self.fixtures["tenant_id"]

        # Create a new identity that is not an approver
        now = datetime.now(timezone.utc)
        email = f"other-{uuid.uuid4().hex[:8]}@test.local"
        other_id = db.identities.insert(
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
            full_name="Other User",
            created_at=now,
            updated_at=now,
        )
        db.commit()

        from apps.api.utils.async_utils import run_in_threadpool

        await run_in_threadpool(lambda: None)

        token = self._token(app, t, other_id)

        response = await client.get(
            "/api/v1/streams/my-approvals",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = await response.get_json()
        assert data["count"] == 0

    @pytest.mark.asyncio
    async def test_approve_execution_as_approver(self, app, client):
        """Test approving a paused execution as an authorized approver."""
        t = self.fixtures["tenant_id"]
        approver_id = self.fixtures["approver_id"]
        execution_id = self.fixtures["execution_id"]
        token = self._token(app, t, approver_id)

        response = await client.post(
            f"/api/v1/streams/executions/{execution_id}/approve",
            json={"comment": "Looks good"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = await response.get_json()
        assert "approval_id" in data

        # Verify execution status changed
        db = app.db

        def check_status():
            execution = (
                db(db.stream_executions.execution_id == execution_id).select().first()
            )
            return execution.status

        from apps.api.utils.async_utils import run_in_threadpool

        status = await run_in_threadpool(check_status)
        assert status == "running"

    @pytest.mark.asyncio
    async def test_reject_execution_as_approver(self, app, client):
        """Test rejecting a paused execution as an authorized approver."""
        db = app.db
        t = self.fixtures["tenant_id"]
        approver_id = self.fixtures["approver_id"]
        requester_id = self.fixtures["requester_id"]
        stream_id = self.fixtures["stream_id"]
        token = self._token(app, t, approver_id)

        # Create another paused execution
        now = datetime.now(timezone.utc)
        execution_id = str(uuid.uuid4())

        def _create_exec():
            db.stream_executions.insert(
                tenant_id=t,
                playbook_id=stream_id,
                execution_id=execution_id,
                status="paused_for_approval",
                trigger_type="manual",
                triggered_by_identity_id=requester_id,
                input_json={},
                created_at=now,
                updated_at=now,
            )
            db.commit()

        from apps.api.utils.async_utils import run_in_threadpool

        await run_in_threadpool(_create_exec)

        response = await client.post(
            f"/api/v1/streams/executions/{execution_id}/reject",
            json={"comment": "Security concerns"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = await response.get_json()
        assert "approval_id" in data

        # Verify execution status changed to failed
        def check_status():
            execution = (
                db(db.stream_executions.execution_id == execution_id).select().first()
            )
            return execution.status

        status = await run_in_threadpool(check_status)
        assert status == "failed"

    @pytest.mark.asyncio
    async def test_reject_requires_comment(self, app, client):
        """Test that rejecting without a comment fails."""
        t = self.fixtures["tenant_id"]
        approver_id = self.fixtures["approver_id"]
        execution_id = self.fixtures["execution_id"]
        token = self._token(app, t, approver_id)

        response = await client.post(
            f"/api/v1/streams/executions/{execution_id}/reject",
            json={},  # No comment
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_approval_status(self, app, client):
        """Test getting approval status for an execution."""
        db = app.db
        t = self.fixtures["tenant_id"]
        approver_id = self.fixtures["approver_id"]
        execution_id = self.fixtures["execution_id"]
        token = self._token(app, t, approver_id)

        # Approve first
        approve_response = await client.post(
            f"/api/v1/streams/executions/{execution_id}/approve",
            json={"comment": "Approved"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert approve_response.status_code == 200

        # Now get status
        response = await client.get(
            f"/api/v1/streams/executions/{execution_id}/approval-status",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = await response.get_json()
        assert data["execution_status"] == "running"
        assert len(data["approvals"]) == 1
        assert data["approvals"][0]["decision"] == "approve"

    @pytest.mark.asyncio
    async def test_non_approver_cannot_approve(self, app, client):
        """Test that non-approvers cannot approve executions."""
        db = app.db
        t = self.fixtures["tenant_id"]

        # Create a non-approver identity
        now = datetime.now(timezone.utc)
        email = f"non-approver-{uuid.uuid4().hex[:8]}@test.local"
        non_approver_id = db.identities.insert(
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
            full_name="Non Approver",
            created_at=now,
            updated_at=now,
        )
        db.commit()

        from apps.api.utils.async_utils import run_in_threadpool

        await run_in_threadpool(lambda: None)

        token = self._token(app, t, non_approver_id)
        execution_id = self.fixtures["execution_id"]

        response = await client.post(
            f"/api/v1/streams/executions/{execution_id}/approve",
            json={"comment": "Approved"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_cross_tenant_approval_isolation(self, app, client):
        """Test that approvals are isolated by tenant."""
        db = app.db

        # Create another tenant
        now = datetime.now(timezone.utc)
        other_tenant_id = db.tenants.insert(
            name="Other Tenant",
            slug=f"other-{uuid.uuid4().hex[:8]}",
            is_active=True,
            created_at=now,
            updated_at=now,
        )

        email = f"other-tenant-user-{uuid.uuid4().hex[:8]}@test.local"
        other_identity_id = db.identities.insert(
            tenant_id=other_tenant_id,
            username=email,
            email=email,
            identity_type="human",
            auth_provider="local",
            is_active=True,
            is_superuser=False,
            mfa_enabled=False,
            must_change_password=False,
            portal_role="viewer",
            full_name="Other Tenant User",
            created_at=now,
            updated_at=now,
        )
        db.commit()

        from apps.api.utils.async_utils import run_in_threadpool

        await run_in_threadpool(lambda: None)

        # Try to access approvals from a different tenant
        token = self._token(app, other_tenant_id, other_identity_id)
        execution_id = self.fixtures["execution_id"]

        response = await client.post(
            f"/api/v1/streams/executions/{execution_id}/approve",
            json={"comment": "Approved"},
            headers={"Authorization": f"Bearer {token}"},
        )

        # Should get 404 or 403, definitely not 200
        assert response.status_code in (404, 403)


@pytest.mark.integration
class TestStreamWebhookHooks:
    """Stream webhook hook (public) tests."""

    @pytest_asyncio.fixture(autouse=True)
    async def setup_db(self, app, test_database_url):
        """Set up test database with webhook fixtures."""
        if not test_database_url:
            pytest.skip("DATABASE_URL not set")

        db = app.db

        def _setup():
            import secrets

            now = datetime.now(timezone.utc)
            tenant_id = db.tenants.insert(
                name="Hooks Tenant",
                slug=f"hks-{uuid.uuid4().hex[:8]}",
                is_active=True,
                created_at=now,
                updated_at=now,
            )

            email = f"hook-user-{uuid.uuid4().hex[:8]}@test.local"
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
                full_name="Hook User",
                created_at=now,
                updated_at=now,
            )

            # Create a stream (public for webhook testing)
            stream_id = db.stream_playbooks.insert(
                tenant_id=tenant_id,
                village_id=f"test-{uuid.uuid4().hex[:24]}",
                name="Test Stream for Hooks",
                description="Test stream",
                owner_identity_id=identity_id,
                created_by_identity_id=identity_id,
                trigger_type="webhook",
                is_public=True,  # Public so tests can access
                is_template=False,
                is_enabled=True,
                tags=[],
                status="active",
                execution_count=0,
                success_count=0,
                failure_count=0,
                created_at=now,
                updated_at=now,
            )

            # Create version
            db.stream_versions.insert(
                tenant_id=tenant_id,
                playbook_id=stream_id,
                version_number=1,
                created_by_identity_id=identity_id,
                nodes_json=[],
                edges_json=[],
                canvas_json={"nodes": [], "edges": []},
                change_summary="Initial",
                created_at=now,
                updated_at=now,
            )

            # Create a webhook (allow all methods for testing)
            token = secrets.token_urlsafe(32)
            webhook_id = db.stream_webhooks.insert(
                tenant_id=tenant_id,
                playbook_id=stream_id,
                name="Test Webhook",
                token=token,
                allowed_methods=[
                    "GET",
                    "POST",
                    "PUT",
                    "PATCH",
                    "DELETE",
                ],  # All methods
                validate_signature=False,
                signature_secret=None,
                is_enabled=True,
                is_active=True,
                trigger_count=0,
                created_at=now,
                updated_at=now,
            )

            db.commit()
            return {
                "tenant_id": tenant_id,
                "identity_id": identity_id,
                "stream_id": stream_id,
                "webhook_id": webhook_id,
                "token": token,
            }

        from apps.api.utils.async_utils import run_in_threadpool

        self.fixtures = await run_in_threadpool(_setup)

    @pytest.mark.asyncio
    async def test_webhook_trigger_valid_token(self, app, client):
        """Test triggering a webhook with a valid token."""
        token = self.fixtures["token"]

        response = await client.post(
            f"/api/v1/hooks/{token}",
            json={"test": "data"},
        )

        assert response.status_code == 202
        data = await response.get_json()
        assert "execution_id" in data
        assert data["status"] == "queued"

    @pytest.mark.asyncio
    async def test_webhook_trigger_invalid_token(self, app, client):
        """Test that invalid tokens return 404."""
        response = await client.post(
            "/api/v1/hooks/invalid-token-12345",
            json={"test": "data"},
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_webhook_trigger_with_method_restriction(self, app, client):
        """Test that method restrictions are enforced."""
        db = app.db
        import secrets

        now = datetime.now(timezone.utc)

        # Create webhook with only POST allowed
        def _create_restricted():
            token = secrets.token_urlsafe(32)
            webhook_id = db.stream_webhooks.insert(
                tenant_id=self.fixtures["tenant_id"],
                playbook_id=self.fixtures["stream_id"],
                name="POST-Only Webhook",
                token=token,
                allowed_methods=["POST"],
                validate_signature=False,
                signature_secret=None,
                is_enabled=True,
                is_active=True,
                trigger_count=0,
                created_at=now,
                updated_at=now,
            )
            db.commit()
            return token

        from apps.api.utils.async_utils import run_in_threadpool

        token = await run_in_threadpool(_create_restricted)

        # POST should work
        response = await client.post(
            f"/api/v1/hooks/{token}",
            json={"test": "data"},
        )
        assert response.status_code == 202

        # GET should fail
        response = await client.get(f"/api/v1/hooks/{token}")
        assert response.status_code == 405

    @pytest.mark.asyncio
    async def test_webhook_test_endpoint(self, app, client):
        """Test the webhook test endpoint."""
        token = self.fixtures["token"]

        response = await client.get(f"/api/v1/hooks/{token}/test")

        assert response.status_code == 200
        data = await response.get_json()
        assert "webhook" in data
        assert "playbook" in data
        assert "request" in data
        assert data["webhook"]["name"] == "Test Webhook"

    @pytest.mark.asyncio
    async def test_webhook_execution_queued_not_inline(self, app, client):
        """Test that webhook executions are queued (202), not executed inline."""
        db = app.db
        token = self.fixtures["token"]

        response = await client.post(
            f"/api/v1/hooks/{token}",
            json={"test": "data"},
        )

        assert response.status_code == 202
        data = await response.get_json()
        execution_id = data["execution_id"]

        # Verify execution was created with status "queued"
        def check_execution():
            execution = (
                db(db.stream_executions.execution_id == execution_id).select().first()
            )
            return execution.status if execution else None

        from apps.api.utils.async_utils import run_in_threadpool

        status = await run_in_threadpool(check_execution)
        assert status == "queued"

    @pytest.mark.asyncio
    async def test_webhook_disabled_returns_404(self, app, client):
        """Disabled webhooks return a uniform 404 (anti-enumeration).

        The hooks route deliberately returns the same 404 for unknown,
        disabled, and orphaned webhook tokens so callers cannot probe which
        tokens exist (see apps/api/modules/streams/routes/hooks.py).
        """
        db = app.db
        token = self.fixtures["token"]

        # Disable the webhook
        def _disable():
            db(db.stream_webhooks.token == token).update(is_enabled=False)
            db.commit()

        from apps.api.utils.async_utils import run_in_threadpool

        await run_in_threadpool(_disable)

        response = await client.post(
            f"/api/v1/hooks/{token}",
            json={"test": "data"},
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_webhook_trigger_all_methods(self, app, client):
        """Test webhook triggering with different HTTP methods."""
        token = self.fixtures["token"]
        methods = [
            ("GET", client.get),
            ("POST", client.post),
            ("PUT", client.put),
            ("PATCH", client.patch),
            ("DELETE", client.delete),
        ]

        for method_name, method_func in methods:
            if method_name == "GET":
                response = await method_func(f"/api/v1/hooks/{token}")
            else:
                response = await method_func(
                    f"/api/v1/hooks/{token}",
                    json={"test": "data"},
                )
            # All methods allowed
            assert response.status_code == 202, f"{method_name} should be allowed"
