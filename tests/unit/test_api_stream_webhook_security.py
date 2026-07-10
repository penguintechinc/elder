"""Regression: stream webhook token must not leak to read-only viewers.

A webhook token is the credential for the public /api/v1/hooks/<token> inbound
trigger. If list_webhooks returned it to anyone with mere read access (a viewer
share, or any reader of a public stream), that reader could trigger executions
— a read->execute privilege escalation. Only callers with EDIT rights may see
the raw token/url.

regression: security-review-stream-webhook-token-exposure
"""

import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
import pytest_asyncio


@pytest.mark.integration
class TestStreamWebhookTokenExposure:
    @pytest_asyncio.fixture(autouse=True)
    async def setup_db(self, app, test_database_url):
        if not test_database_url:
            pytest.skip("DATABASE_URL not set")
        db = app.db
        from apps.api.utils.async_utils import run_in_threadpool

        def _setup():
            now = datetime.now(timezone.utc)
            tenant_id = db.tenants.insert(
                name="WH Tenant",
                slug=f"wh-{uuid.uuid4().hex[:8]}",
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
            viewer_id = _ident("viewer")
            stream_id = db.stream_playbooks.insert(
                tenant_id=tenant_id,
                village_id=f"test-{uuid.uuid4().hex[:24]}",
                name="Hooked Stream",
                description="",
                owner_identity_id=owner_id,
                created_by_identity_id=owner_id,
                trigger_type="webhook",
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
                tenant_id=tenant_id,
                playbook_id=stream_id,
                shared_with_identity_id=viewer_id,
                shared_by_identity_id=owner_id,
                permission="viewer",
                created_at=now,
                updated_at=now,
            )
            wh_token = f"tok-{uuid.uuid4().hex}"
            db.stream_webhooks.insert(
                tenant_id=tenant_id,
                playbook_id=stream_id,
                name="inbound",
                token=wh_token,
                allowed_methods=["POST"],
                validate_signature=False,
                is_enabled=True,
                created_at=now,
                updated_at=now,
            )
            db.commit()
            return {
                "tenant_id": tenant_id,
                "owner_id": owner_id,
                "viewer_id": viewer_id,
                "stream_id": stream_id,
                "wh_token": wh_token,
            }

        self.fx = await run_in_threadpool(_setup)

    def _token(self, app, tenant_id, identity_id):
        now = datetime.now(timezone.utc)
        payload = {
            "sub": str(identity_id),
            "iat": now,
            "exp": now + timedelta(hours=1),
            "scope": ["streams:read", "streams:write", "streams:execute"],
            "tenant": str(tenant_id),
            "identity_id": identity_id,
            "roles": ["viewer"],
        }
        secret = app.config.get("JWT_SECRET_KEY") or app.config.get("SECRET_KEY")
        return jwt.encode(payload, secret, algorithm="HS256")

    @pytest.mark.asyncio
    async def test_owner_sees_token_viewer_does_not(self, app, client):
        t = self.fx["tenant_id"]
        sid = self.fx["stream_id"]

        owner_tok = self._token(app, t, self.fx["owner_id"])
        owner_resp = await client.get(
            f"/api/v1/streams/{sid}/webhooks",
            headers={"Authorization": f"Bearer {owner_tok}"},
        )
        assert owner_resp.status_code == 200
        owner_body = await owner_resp.get_json()
        owner_items = (
            owner_body.get("data", owner_body)
            if isinstance(owner_body, dict)
            else owner_body
        )
        assert owner_items[0]["token"] == self.fx["wh_token"]
        assert owner_items[0]["url"]

        viewer_tok = self._token(app, t, self.fx["viewer_id"])
        viewer_resp = await client.get(
            f"/api/v1/streams/{sid}/webhooks",
            headers={"Authorization": f"Bearer {viewer_tok}"},
        )
        # Viewer can see the webhook exists (read access) ...
        assert viewer_resp.status_code == 200
        viewer_body = await viewer_resp.get_json()
        viewer_items = (
            viewer_body.get("data", viewer_body)
            if isinstance(viewer_body, dict)
            else viewer_body
        )
        assert viewer_items[0]["name"] == "inbound"
        # ... but the credential is withheld.
        assert viewer_items[0]["token"] is None
        assert viewer_items[0]["url"] is None
