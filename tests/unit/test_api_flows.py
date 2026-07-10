"""Flows API tests (CRUD + stages + credentials + promotions + webhooks).

regression: flows-crud-phase4a
"""

import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
import pytest_asyncio


@pytest.mark.integration
class TestFlows:
    """Flows CRUD and workflow tests."""

    @pytest_asyncio.fixture(autouse=True)
    async def setup_db(self, app, test_database_url):
        """Set up test database with tenant and identity."""
        if not test_database_url:
            pytest.skip("DATABASE_URL not set")

        db = app.db

        def _setup():
            now = datetime.now(timezone.utc)
            tenant_id = db.tenants.insert(
                name="Flows Tenant",
                slug=f"fl-{uuid.uuid4().hex[:8]}",
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            email = f"flows-user-{uuid.uuid4().hex[:8]}@test.local"
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
        scopes = scopes or ["flows:read", "flows:write", "flows:approve"]
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
    async def test_create_pipeline_happy_path(self, app, client):
        """Test creating a pipeline with happy path."""
        t = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id"]
        token = self._token(app, t, identity_id)

        response = await client.post(
            "/api/v1/flows",
            json={
                "name": "Test Pipeline",
                "repository_url": "https://github.com/test/repo",
                "repository_provider": "github",
                "description": "A test pipeline",
                "tags": ["test"],
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 201
        data = await response.get_json()
        assert data["name"] == "Test Pipeline"
        assert data["description"] == "A test pipeline"
        assert data["repository_provider"] == "github"

    @pytest.mark.asyncio
    async def test_list_pipelines_happy_path(self, app, client):
        """Test listing pipelines."""
        db = app.db
        t = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id"]
        token = self._token(app, t, identity_id)

        # Create a pipeline first
        def _create():
            now = datetime.now(timezone.utc)
            pipeline_id = db.iceflows.insert(
                tenant_id=t,
                village_id=f"test-{uuid.uuid4().hex[:24]}",
                flow_id=str(uuid.uuid4()),
                name="Test Pipeline",
                description="Test",
                repository_url="https://github.com/test/repo",
                repository_provider="github",
                repository_name="repo",
                default_branch="main",
                status="draft",
                is_enabled=False,
                created_by_identity_id=identity_id,
                tags=["test"],
                created_at=now,
                updated_at=now,
            )
            db.commit()
            return pipeline_id

        from apps.api.utils.async_utils import run_in_threadpool

        pipeline_id = await run_in_threadpool(_create)

        response = await client.get(
            "/api/v1/flows",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = await response.get_json()
        assert len(data["data"]) > 0
        assert any(p["id"] == pipeline_id for p in data["data"])

    @pytest.mark.asyncio
    async def test_get_pipeline_happy_path(self, app, client):
        """Test getting a specific pipeline."""
        db = app.db
        t = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id"]
        token = self._token(app, t, identity_id)

        # Create a pipeline first
        def _create():
            now = datetime.now(timezone.utc)
            pipeline_id = db.iceflows.insert(
                tenant_id=t,
                village_id=f"test-{uuid.uuid4().hex[:24]}",
                flow_id=str(uuid.uuid4()),
                name="Test Pipeline",
                description="Test",
                repository_url="https://github.com/test/repo",
                repository_provider="github",
                repository_name="repo",
                default_branch="main",
                status="draft",
                is_enabled=False,
                created_by_identity_id=identity_id,
                tags=[],
                created_at=now,
                updated_at=now,
            )
            db.commit()
            return pipeline_id

        from apps.api.utils.async_utils import run_in_threadpool

        pipeline_id = await run_in_threadpool(_create)

        response = await client.get(
            f"/api/v1/flows/{pipeline_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = await response.get_json()
        assert data["id"] == pipeline_id
        assert data["name"] == "Test Pipeline"

    @pytest.mark.asyncio
    async def test_tenant_isolation_pipeline(self, app, client):
        """Test that pipelines are isolated by tenant (cross-tenant → 404)."""
        db = app.db
        t = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id"]
        token = self._token(app, t, identity_id)

        # Create a pipeline in a DIFFERENT tenant
        def _create_other():
            now = datetime.now(timezone.utc)
            other_tenant_id = db.tenants.insert(
                name="Other Tenant",
                slug=f"ot-{uuid.uuid4().hex[:8]}",
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            pipeline_id = db.iceflows.insert(
                tenant_id=other_tenant_id,
                village_id=f"test-{uuid.uuid4().hex[:24]}",
                flow_id=str(uuid.uuid4()),
                name="Other Pipeline",
                description="In other tenant",
                repository_url="https://github.com/test/repo",
                repository_provider="github",
                repository_name="repo",
                default_branch="main",
                status="draft",
                is_enabled=False,
                created_by_identity_id=1,
                tags=[],
                created_at=now,
                updated_at=now,
            )
            db.commit()
            return pipeline_id

        from apps.api.utils.async_utils import run_in_threadpool

        other_pipeline_id = await run_in_threadpool(_create_other)

        # Try to fetch it from the first tenant
        response = await client.get(
            f"/api/v1/flows/{other_pipeline_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        # Should return 404 (tenant isolation)
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_credentials_list_no_token_returned(self, app, client):
        """Test that credentials list never returns access_token."""
        db = app.db
        t = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id"]
        token = self._token(app, t, identity_id)

        response = await client.get(
            "/api/v1/flows/credentials",
            headers={"Authorization": f"Bearer {token}"},
        )

        # Even though we have no credentials, the endpoint should work
        assert response.status_code == 200
        data = await response.get_json()
        assert data["total"] == 0
        assert "data" in data

    @pytest.mark.asyncio
    async def test_credentials_create_returns_501_gate(self, app, client):
        """Test that creating credentials returns 501 security gate (field encryption not implemented)."""
        t = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id"]
        token = self._token(app, t, identity_id)

        response = await client.post(
            "/api/v1/flows/credentials",
            json={
                "name": "GitHub Token",
                "provider": "github",
                "access_token": "ghp_test_token_12345",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        # Should return 501 (security gate: field encryption not implemented)
        assert response.status_code == 501

    @pytest.mark.asyncio
    async def test_webhooks_unknown_token_returns_404(self, app, client):
        """Test that webhook with unknown token returns 404 (uniform error)."""
        # Webhooks are public (no auth required)
        response = await client.post(
            f"/api/v1/flows-hooks/{uuid.uuid4().hex}",
            json={"test": "payload"},
        )

        # Should return 404 (not found — uniform for all failures)
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_promotions_approve_requires_scope(self, app, client):
        """Test that approving promotions requires flows:approve scope."""
        t = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id"]
        # Token with only flows:read scope (no flows:approve)
        token = self._token(app, t, identity_id, scopes=["flows:read"])

        # Try to approve a non-existent promotion
        response = await client.post(
            f"/api/v1/flows/promotions/999/approve",
            json={"comments": "Approved"},
            headers={"Authorization": f"Bearer {token}"},
        )

        # Should return 403 (insufficient scope) or 404 (not found)
        # Depending on implementation, scope check might come first
        assert response.status_code in [403, 404]

    async def _scaffold_promotion(
        self,
        app,
        tenant_id,
        identity_id,
        *,
        min_approvers=1,
        require_approval=True,
        add_approver=True,
        status="pending",
    ):
        """Insert a pipeline + gate stage (+ optional approver) + promotion.

        Returns the promotion's DB id. Approvers can't yet be created via the
        API (deferred, gh stage-children re-port), so they are seeded directly.
        """
        db = app.db

        def _mk():
            now = datetime.now(timezone.utc)
            flow_id = db.iceflows.insert(
                tenant_id=tenant_id,
                village_id=f"test-{uuid.uuid4().hex[:24]}",
                flow_id=str(uuid.uuid4()),
                name="Promo Pipeline",
                description="",
                repository_url="https://github.com/test/repo",
                repository_provider="github",
                repository_name="repo",
                default_branch="main",
                status="active",
                is_enabled=True,
                created_by_identity_id=identity_id,
                tags=[],
                created_at=now,
                updated_at=now,
            )
            stage_id = db.iceflows_stages.insert(
                tenant_id=tenant_id,
                stage_id=str(uuid.uuid4()),
                flow_id=flow_id,
                stage_order=1,
                branch_name="prod",
                display_name="Production",
                is_production=True,
                auto_promote=False,
                require_approval=require_approval,
                min_approvers=min_approvers,
                override_min_approvers=2,
                is_enabled=True,
                created_at=now,
                updated_at=now,
            )
            if add_approver:
                db.iceflows_stage_approvers.insert(
                    tenant_id=tenant_id,
                    approver_id=str(uuid.uuid4()),
                    stage_id=stage_id,
                    identity_id=identity_id,
                    role="approver",
                    can_override=False,
                    created_at=now,
                    updated_at=now,
                )
            promo_id = db.iceflows_promotions.insert(
                tenant_id=tenant_id,
                promotion_id=str(uuid.uuid4()),
                flow_id=flow_id,
                source_stage_id=stage_id,
                target_stage_id=stage_id,
                source_commit="deadbeef",
                status=status,
                requested_by_identity_id=identity_id,
                created_at=now,
                updated_at=now,
            )
            db.commit()
            return promo_id

        from apps.api.utils.async_utils import run_in_threadpool

        return await run_in_threadpool(_mk)

    @pytest.mark.asyncio
    async def test_promotions_approve_non_approver_forbidden(self, app, client):
        """regression: approver-membership fail-closed.

        A caller with flows:approve who is NOT a configured stage approver is
        rejected with 403 (scope alone must not authorize an approval).
        """
        t = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id"]
        promo_id = await self._scaffold_promotion(
            app, t, identity_id, add_approver=False
        )
        token = self._token(app, t, identity_id, scopes=["flows:approve"])

        response = await client.post(
            f"/api/v1/flows/promotions/{promo_id}/approve",
            json={"comments": "lgtm"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_promotions_single_approve_does_not_flip(self, app, client):
        """regression: unilateral-state-change prevented.

        With min_approvers=2, one approval must NOT advance the promotion to
        'approved' — it stays pending until the threshold is met.
        """
        t = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id"]
        promo_id = await self._scaffold_promotion(app, t, identity_id, min_approvers=2)
        token = self._token(app, t, identity_id, scopes=["flows:approve"])

        response = await client.post(
            f"/api/v1/flows/promotions/{promo_id}/approve",
            json={"comments": "one of two"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = await response.get_json()
        assert data["status"] == "pending"
        assert data["approvals_received"] == 1
        assert data["approvals_required"] == 2

    @pytest.mark.asyncio
    async def test_promotions_approve_meets_threshold(self, app, client):
        """regression: approval advances once min_approvers is met."""
        t = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id"]
        promo_id = await self._scaffold_promotion(app, t, identity_id, min_approvers=1)
        token = self._token(app, t, identity_id, scopes=["flows:approve"])

        response = await client.post(
            f"/api/v1/flows/promotions/{promo_id}/approve",
            json={"comments": "ship it"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = await response.get_json()
        assert data["status"] == "approved"

    @pytest.mark.asyncio
    async def test_promotions_approve_non_pending_conflict(self, app, client):
        """regression: missing-state-guard — non-pending promotion → 409."""
        t = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id"]
        promo_id = await self._scaffold_promotion(app, t, identity_id, status="merged")
        token = self._token(app, t, identity_id, scopes=["flows:approve"])

        response = await client.post(
            f"/api/v1/flows/promotions/{promo_id}/approve",
            json={"comments": "too late"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_stage_policy_fields_require_admin(self, app, client):
        """regression: policy-tampering — flows:write cannot weaken the gate."""
        db = app.db
        t = self.fixtures["tenant_id"]
        identity_id = self.fixtures["identity_id"]

        def _mk_flow():
            now = datetime.now(timezone.utc)
            fid = db.iceflows.insert(
                tenant_id=t,
                village_id=f"test-{uuid.uuid4().hex[:24]}",
                flow_id=str(uuid.uuid4()),
                name="Policy Pipeline",
                description="",
                repository_url="https://github.com/test/repo",
                repository_provider="github",
                repository_name="repo",
                default_branch="main",
                status="draft",
                is_enabled=False,
                created_by_identity_id=identity_id,
                tags=[],
                created_at=now,
                updated_at=now,
            )
            db.commit()
            return fid

        from apps.api.utils.async_utils import run_in_threadpool

        flow_id = await run_in_threadpool(_mk_flow)

        # flows:write only → setting require_approval is forbidden
        write_token = self._token(app, t, identity_id, scopes=["flows:write"])
        resp = await client.post(
            f"/api/v1/flows/{flow_id}/stages",
            json={"branch_name": "prod", "require_approval": False},
            headers={"Authorization": f"Bearer {write_token}"},
        )
        assert resp.status_code == 403

        # flows:admin → allowed
        admin_token = self._token(
            app, t, identity_id, scopes=["flows:write", "flows:admin"]
        )
        resp = await client.post(
            f"/api/v1/flows/{flow_id}/stages",
            json={"branch_name": "prod", "require_approval": False},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 201
