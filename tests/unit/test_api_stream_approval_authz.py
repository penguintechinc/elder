"""Regression: approval-status must require read access to the underlying stream.

get_approval_status exposes approver identities (PII), decisions and comments.
It was only tenant-scoped, so any tenant user with streams:read could enumerate
approver PII for a stream they had no access to. It must now require
_can_read_stream on the owning stream.

regression: security-review-stream-approval-status-broken-access-control
"""

import uuid
from datetime import datetime, timedelta, timezone

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
            now = datetime.now(timezone.utc)
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
        now = datetime.now(timezone.utc)
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
