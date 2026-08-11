"""Regression: approval-status must require read access to the underlying stream.

get_approval_status exposes approver identities (PII), decisions and comments.
It was only tenant-scoped, so any tenant user with streams:read could enumerate
approver PII for a stream they had no access to. It must now require
_can_read_stream on the owning stream.

regression: security-review-stream-approval-status-broken-access-control
"""

import uuid
from datetime import UTC, datetime, timedelta, timezone

import jwt
import pytest
import pytest_asyncio


@pytest.mark.integration
class TestApprovalStatusAuthz:
    @pytest_asyncio.fixture(autouse=True)
    async def setup_db(self, app, test_database_url):
        if not test_database_url:
            pytest.skip("DATABASE_URL not set")
        db = app.db
        from apps.api.utils.async_utils import run_in_threadpool

        def _setup():
            now = datetime.now(UTC)
            tenant_id = db.tenants.insert(
                name="Appr Tenant",
                slug=f"ap-{uuid.uuid4().hex[:8]}",
                is_active=True,
                created_at=now,
                updated_at=now,
            )

            def _ident(role):
                em = f"{role}-{uuid.uuid4().hex[:8]}@test.local"
                return db.identities.insert(
                    tenant_id=tenant_id,
                    username=em,
                    email=em,
                    identity_type="human",
                    auth_provider="local",
                    is_active=True,
                    is_superuser=False,
                    mfa_enabled=False,
                    must_change_password=False,
                    portal_role="viewer",
                    full_name=role,
                    created_at=now,
                    updated_at=now,
                )

            owner_id = _ident("owner")
            outsider_id = _ident("outsider")  # tenant member, NO share on stream
            stream_id = db.stream_playbooks.insert(
                tenant_id=tenant_id,
                village_id=f"test-{uuid.uuid4().hex[:24]}",
                name="Private Stream",
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
            exec_id = f"exec-{uuid.uuid4().hex}"
            db.stream_executions.insert(
                tenant_id=tenant_id,
                execution_id=exec_id,
                playbook_id=stream_id,
                status="paused_for_approval",
                trigger_type="manual",
                created_at=now,
                updated_at=now,
            )
            db.commit()
            return {
                "tenant_id": tenant_id,
                "owner_id": owner_id,
                "outsider_id": outsider_id,
                "exec_id": exec_id,
            }

        self.fx = await run_in_threadpool(_setup)

    def _token(self, app, tenant_id, identity_id):
        now = datetime.now(UTC)
        payload = {
            "sub": str(identity_id),
            "iat": now,
            "exp": now + timedelta(hours=1),
            "scope": ["streams:read", "streams:write"],
            "tenant": str(tenant_id),
            "identity_id": identity_id,
            "roles": ["viewer"],
        }
        secret = app.config.get("JWT_SECRET_KEY") or app.config.get("SECRET_KEY")
        return jwt.encode(payload, secret, algorithm="HS256")

    @pytest.mark.asyncio
    async def test_outsider_cannot_read_approval_status(self, app, client):
        t = self.fx["tenant_id"]
        exec_id = self.fx["exec_id"]

        # Owner (readable) → 200
        owner_tok = self._token(app, t, self.fx["owner_id"])
        ok = await client.get(
            f"/api/v1/streams/executions/{exec_id}/approval-status",
            headers={"Authorization": f"Bearer {owner_tok}"},
        )
        assert ok.status_code == 200

        # Tenant member with no access to the stream → 404 (not leaked)
        outsider_tok = self._token(app, t, self.fx["outsider_id"])
        denied = await client.get(
            f"/api/v1/streams/executions/{exec_id}/approval-status",
            headers={"Authorization": f"Bearer {outsider_tok}"},
        )
        assert denied.status_code == 404

    @pytest.mark.asyncio
    async def test_min_approvers_enforced(self, app, client):
        """A gate with min_approvers=2 must NOT resume after a single approval."""
        db = app.db
        t = self.fx["tenant_id"]
        from apps.api.utils.async_utils import run_in_threadpool

        def _seed():
            now = datetime.now(UTC)

            def _ident():
                em = f"appr-{uuid.uuid4().hex[:8]}@test.local"
                return db.identities.insert(
                    tenant_id=t,
                    username=em,
                    email=em,
                    identity_type="human",
                    auth_provider="local",
                    is_active=True,
                    is_superuser=False,
                    mfa_enabled=False,
                    must_change_password=False,
                    portal_role="viewer",
                    full_name="Approver",
                    created_at=now,
                    updated_at=now,
                )

            a1, a2 = _ident(), _ident()
            # Public stream so the approvers can read it (satisfies _can_read).
            stream_id = db.stream_playbooks.insert(
                tenant_id=t,
                village_id=f"test-{uuid.uuid4().hex[:24]}",
                name="Two-Approver Stream",
                description="",
                owner_identity_id=self.fx["owner_id"],
                created_by_identity_id=self.fx["owner_id"],
                trigger_type="manual",
                is_public=True,
                is_template=False,
                is_enabled=True,
                tags=[],
                status="draft",
                execution_count=0,
                success_count=0,
                failure_count=0,
                created_at=now,
                updated_at=now,
            )
            db.stream_approval_gates.insert(
                tenant_id=t,
                gate_id=str(uuid.uuid4()),
                playbook_id=stream_id,
                node_id="gate-node",
                name="Two Approvers",
                require_approval=True,
                min_approvers=2,
                approvers=[a1, a2],
                approver_groups=[],
                is_enabled=True,
                created_at=now,
                updated_at=now,
            )
            exec_id = f"exec-{uuid.uuid4().hex}"
            db.stream_executions.insert(
                tenant_id=t,
                execution_id=exec_id,
                playbook_id=stream_id,
                status="paused_for_approval",
                trigger_type="manual",
                created_at=now,
                updated_at=now,
            )
            db.commit()
            return a1, a2, exec_id

        a1, a2, exec_id = await run_in_threadpool(_seed)

        # First approval — threshold (2) not met, must stay paused.
        r1 = await client.post(
            f"/api/v1/streams/executions/{exec_id}/approve",
            json={"comment": "ok"},
            headers={"Authorization": f"Bearer {self._token(app, t, a1)}"},
        )
        assert r1.status_code == 200
        b1 = await r1.get_json()
        b1 = b1.get("data", b1) if isinstance(b1, dict) else b1
        assert b1["resumed"] is False

        def _status():
            row = db(db.stream_executions.execution_id == exec_id).select().first()
            return row.status

        assert await run_in_threadpool(_status) == "paused_for_approval"

        # Second, distinct approver — threshold met, now resumes.
        r2 = await client.post(
            f"/api/v1/streams/executions/{exec_id}/approve",
            json={"comment": "ok2"},
            headers={"Authorization": f"Bearer {self._token(app, t, a2)}"},
        )
        assert r2.status_code == 200
        b2 = await r2.get_json()
        b2 = b2.get("data", b2) if isinstance(b2, dict) else b2
        assert b2["resumed"] is True
        assert await run_in_threadpool(_status) == "running"
