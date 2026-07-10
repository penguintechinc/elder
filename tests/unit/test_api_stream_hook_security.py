"""Streams webhook security tests (HTTP, fail-closed validation).

Tests uniform 404 for enumeration oracle closure, fail-closed signature validation,
and valid webhook acceptance.

regression: streams-webhook-security-phase4b
"""

import hashlib
import hmac
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio


@pytest.mark.integration
class TestStreamWebhookSecurity:
    """Test webhook security: enumeration oracle and signature validation."""

    @pytest_asyncio.fixture(autouse=True)
    async def setup_db(self, app, test_database_url):
        """Set up test database with tenant, playbook, webhooks."""
        if not test_database_url:
            pytest.skip("DATABASE_URL not set")

        db = app.db

        def _setup():
            now = datetime.now(timezone.utc)
            tenant_id = db.tenants.insert(
                name=f"Webhook Test Tenant {uuid.uuid4().hex[:8]}",
                slug=f"webhook-{uuid.uuid4().hex[:8]}",
                is_active=True,
                created_at=now,
                updated_at=now,
            )

            # Create an identity for playbook owner
            email = f"webhook-user-{uuid.uuid4().hex[:8]}@test.local"
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
                full_name="Webhook User",
                created_at=now,
                updated_at=now,
            )

            def _new_playbook(name, enabled):
                return db.stream_playbooks.insert(
                    tenant_id=tenant_id,
                    village_id=f"test-{uuid.uuid4().hex[:24]}",
                    name=name,
                    description="",
                    owner_identity_id=identity_id,
                    created_by_identity_id=identity_id,
                    trigger_type="webhook",
                    is_public=False,
                    is_template=False,
                    is_enabled=enabled,
                    tags=[],
                    status="draft",
                    execution_count=0,
                    success_count=0,
                    failure_count=0,
                    created_at=now,
                    updated_at=now,
                )

            # Create an enabled playbook
            playbook_id = _new_playbook("Test Webhook Playbook", True)

            # Create a disabled playbook
            disabled_playbook_id = _new_playbook("Disabled Webhook Playbook", False)

            # Create enabled webhook with no signature validation
            enabled_token = f"tok-{uuid.uuid4().hex}"
            db.stream_webhooks.insert(
                tenant_id=tenant_id,
                playbook_id=playbook_id,
                name="Enabled Webhook No Sig",
                token=enabled_token,
                is_enabled=True,
                allowed_methods=["POST"],
                validate_signature=False,
                signature_secret=None,
                trigger_count=0,
                created_at=now,
                updated_at=now,
            )

            # Create enabled webhook with signature validation (no secret)
            sig_required_no_secret_token = f"tok-{uuid.uuid4().hex}"
            db.stream_webhooks.insert(
                tenant_id=tenant_id,
                playbook_id=playbook_id,
                name="Sig Required No Secret",
                token=sig_required_no_secret_token,
                is_enabled=True,
                allowed_methods=["POST"],
                validate_signature=True,
                signature_secret=None,  # Misconfigured
                trigger_count=0,
                created_at=now,
                updated_at=now,
            )

            # Create enabled webhook with signature validation (has secret)
            sig_required_with_secret_token = f"tok-{uuid.uuid4().hex}"
            signature_secret = "test-secret-key-12345"
            db.stream_webhooks.insert(
                tenant_id=tenant_id,
                playbook_id=playbook_id,
                name="Sig Required With Secret",
                token=sig_required_with_secret_token,
                is_enabled=True,
                allowed_methods=["POST"],
                validate_signature=True,
                signature_secret=signature_secret,
                trigger_count=0,
                created_at=now,
                updated_at=now,
            )

            # Create disabled webhook
            disabled_token = f"tok-{uuid.uuid4().hex}"
            db.stream_webhooks.insert(
                tenant_id=tenant_id,
                playbook_id=playbook_id,
                name="Disabled Webhook",
                token=disabled_token,
                is_enabled=False,
                allowed_methods=["POST"],
                validate_signature=False,
                signature_secret=None,
                trigger_count=0,
                created_at=now,
                updated_at=now,
            )

            # Webhook for disabled playbook
            webhook_disabled_pb_token = f"tok-{uuid.uuid4().hex}"
            db.stream_webhooks.insert(
                tenant_id=tenant_id,
                playbook_id=disabled_playbook_id,
                name="Webhook for Disabled Playbook",
                token=webhook_disabled_pb_token,
                is_enabled=True,
                allowed_methods=["POST"],
                validate_signature=False,
                signature_secret=None,
                trigger_count=0,
                created_at=now,
                updated_at=now,
            )

            db.commit()

            return {
                "tenant_id": tenant_id,
                "playbook_id": playbook_id,
                "enabled_token": enabled_token,
                "disabled_token": disabled_token,
                "sig_required_no_secret_token": sig_required_no_secret_token,
                "sig_required_with_secret_token": sig_required_with_secret_token,
                "webhook_disabled_pb_token": webhook_disabled_pb_token,
                "signature_secret": signature_secret,
            }

        from apps.api.utils.async_utils import run_in_threadpool

        self.fixtures = await run_in_threadpool(_setup)

    @pytest.mark.asyncio
    async def test_unknown_token_returns_404(self, client):
        """Unknown webhook token returns 404 (uniform)."""
        unknown_token = f"tok-{uuid.uuid4().hex}"

        response = await client.post(
            f"/api/v1/hooks/{unknown_token}", json={"data": "test"}
        )

        assert response.status_code == 404
        data = await response.get_json()
        assert "not found" in data.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_disabled_webhook_returns_404(self, client):
        """Disabled webhook token returns 404 (uniform, not 403)."""
        disabled_token = self.fixtures["disabled_token"]

        response = await client.post(
            f"/api/v1/hooks/{disabled_token}", json={"data": "test"}
        )

        # MUST be 404, not 403 — prevents enumeration oracle
        assert response.status_code == 404
        data = await response.get_json()
        assert "not found" in data.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_webhook_for_disabled_playbook_returns_404(self, client):
        """Webhook for disabled playbook returns 404 (uniform)."""
        webhook_disabled_pb_token = self.fixtures["webhook_disabled_pb_token"]

        response = await client.post(
            f"/api/v1/hooks/{webhook_disabled_pb_token}", json={"data": "test"}
        )

        # MUST be 404 — disabled playbook is not different from missing playbook
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_signature_validation_required_missing_secret_fails(self, client):
        """Webhook with validate_signature=True but missing secret rejects request (fail-closed)."""
        sig_required_no_secret_token = self.fixtures["sig_required_no_secret_token"]

        response = await client.post(
            f"/api/v1/hooks/{sig_required_no_secret_token}",
            json={"data": "test"},
            headers={"X-Hub-Signature-256": "sha256=invalid"},
        )

        # MUST be 401 (signature validation failed) — fail-closed when secret missing
        assert response.status_code == 401
        data = await response.get_json()
        assert "signature" in data.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_signature_validation_required_missing_header_fails(self, client):
        """Webhook with validate_signature=True but missing signature header rejects (fail-closed)."""
        sig_required_with_secret_token = self.fixtures["sig_required_with_secret_token"]

        # POST without signature header (not even empty)
        response = await client.post(
            f"/api/v1/hooks/{sig_required_with_secret_token}", json={"data": "test"}
        )

        # MUST be 401 — fail-closed
        assert response.status_code == 401
        data = await response.get_json()
        assert "signature" in data.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_signature_validation_required_invalid_signature_fails(self, client):
        """Webhook with valid secret but invalid signature rejects (fail-closed)."""
        sig_required_with_secret_token = self.fixtures["sig_required_with_secret_token"]

        response = await client.post(
            f"/api/v1/hooks/{sig_required_with_secret_token}",
            json={"data": "test"},
            headers={"X-Hub-Signature-256": "sha256=invalid_sig"},
        )

        # MUST be 401
        assert response.status_code == 401
        data = await response.get_json()
        assert "signature" in data.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_signature_validation_required_valid_signature_succeeds(self, client):
        """Webhook with valid signature is accepted (202 queued)."""
        sig_required_with_secret_token = self.fixtures["sig_required_with_secret_token"]
        signature_secret = self.fixtures["signature_secret"]

        payload = b'{"data": "test"}'
        signature = hmac.new(
            signature_secret.encode("utf-8"), payload, hashlib.sha256
        ).hexdigest()
        signature_header = f"sha256={signature}"

        response = await client.post(
            f"/api/v1/hooks/{sig_required_with_secret_token}",
            data=payload,
            headers={
                "X-Hub-Signature-256": signature_header,
                "Content-Type": "application/json",
            },
        )

        # MUST be 202 (execution queued)
        assert response.status_code == 202
        data = await response.get_json()
        assert "execution_id" in data
        assert "queued" in data.get("status", "").lower()

    @pytest.mark.asyncio
    async def test_enabled_webhook_without_signature_validation_succeeds(self, client):
        """Enabled webhook with validate_signature=False accepts POST (202)."""
        enabled_token = self.fixtures["enabled_token"]

        response = await client.post(
            f"/api/v1/hooks/{enabled_token}", json={"data": "test"}
        )

        # MUST be 202 (execution queued)
        assert response.status_code == 202
        data = await response.get_json()
        assert "execution_id" in data

    @pytest.mark.asyncio
    async def test_webhook_with_multiple_allowed_methods(self, app, client):
        """Webhook can be triggered with multiple HTTP methods."""
        db = app.db

        def _setup():
            now = datetime.now(timezone.utc)
            enabled = db.stream_playbooks.is_enabled == True  # noqa: E712
            playbook = db(enabled).select().first()
            if not playbook:
                pytest.skip("No enabled playbook found")

            multi_method_token = f"tok-{uuid.uuid4().hex}"
            db.stream_webhooks.insert(
                tenant_id=playbook.tenant_id,
                playbook_id=playbook.id,
                name="Multi-Method Webhook",
                token=multi_method_token,
                is_enabled=True,
                allowed_methods=["GET", "POST", "PUT"],
                validate_signature=False,
                signature_secret=None,
                trigger_count=0,
                created_at=now,
                updated_at=now,
            )
            db.commit()
            return multi_method_token

        from apps.api.utils.async_utils import run_in_threadpool

        multi_method_token = await run_in_threadpool(_setup)

        # Test with POST
        response = await client.post(
            f"/api/v1/hooks/{multi_method_token}", json={"data": "test"}
        )
        assert response.status_code == 202

        # Test with PUT
        response = await client.put(
            f"/api/v1/hooks/{multi_method_token}", json={"data": "test"}
        )
        assert response.status_code == 202

    @pytest.mark.asyncio
    async def test_webhook_with_restricted_methods_rejects_others(self, app, client):
        """Webhook restricts allowed HTTP methods."""
        db = app.db

        def _setup():
            now = datetime.now(timezone.utc)
            enabled = db.stream_playbooks.is_enabled == True  # noqa: E712
            playbook = db(enabled).select().first()
            if not playbook:
                pytest.skip("No enabled playbook found")

            restricted_token = f"tok-{uuid.uuid4().hex}"
            db.stream_webhooks.insert(
                tenant_id=playbook.tenant_id,
                playbook_id=playbook.id,
                name="POST-Only Webhook",
                token=restricted_token,
                is_enabled=True,
                allowed_methods=["POST"],
                validate_signature=False,
                signature_secret=None,
                trigger_count=0,
                created_at=now,
                updated_at=now,
            )
            db.commit()
            return restricted_token

        from apps.api.utils.async_utils import run_in_threadpool

        restricted_token = await run_in_threadpool(_setup)

        # POST should work
        response = await client.post(
            f"/api/v1/hooks/{restricted_token}", json={"data": "test"}
        )
        assert response.status_code == 202

        # GET should fail
        response = await client.get(f"/api/v1/hooks/{restricted_token}")
        assert response.status_code == 405
        data = await response.get_json()
        assert "method" in data.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_webhook_creates_execution_record(self, app, client):
        """Webhook creates stream_executions record."""
        enabled_token = self.fixtures["enabled_token"]

        response = await client.post(
            f"/api/v1/hooks/{enabled_token}", json={"test_data": "value"}
        )

        assert response.status_code == 202
        data = await response.get_json()
        execution_id = data.get("execution_id")
        assert execution_id is not None

        # Verify execution record exists
        db = app.db

        def _verify():
            execution = (
                db(db.stream_executions.execution_id == execution_id).select().first()
            )
            assert execution is not None
            assert execution.status == "queued"
            assert execution.trigger_type == "webhook"

        from apps.api.utils.async_utils import run_in_threadpool

        await run_in_threadpool(_verify)

    @pytest.mark.asyncio
    async def test_test_webhook_endpoint_404_unknown_token(self, client):
        """Webhook test endpoint returns 404 for unknown token."""
        unknown_token = f"tok-{uuid.uuid4().hex}"

        response = await client.get(f"/api/v1/hooks/{unknown_token}/test")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_test_webhook_endpoint_returns_webhook_info(self, client):
        """Webhook test endpoint returns webhook configuration."""
        enabled_token = self.fixtures["enabled_token"]

        response = await client.get(f"/api/v1/hooks/{enabled_token}/test")

        assert response.status_code == 200
        data = await response.get_json()
        webhook_info = data.get("webhook", {})
        assert webhook_info.get("name") == "Enabled Webhook No Sig"
        assert webhook_info.get("is_enabled") is True
        assert webhook_info.get("validate_signature") is False

    @pytest.mark.asyncio
    async def test_test_webhook_validates_signature_if_required(self, client):
        """Webhook test endpoint validates signature if required."""
        sig_required_with_secret_token = self.fixtures["sig_required_with_secret_token"]
        signature_secret = self.fixtures["signature_secret"]

        # Test with invalid signature
        response = await client.post(
            f"/api/v1/hooks/{sig_required_with_secret_token}/test",
            data=b'{"data": "test"}',
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": "sha256=invalid",
            },
        )

        assert response.status_code == 200
        data = await response.get_json()
        test_result = data
        assert test_result.get("signature_valid") is False

        # Test with valid signature
        payload = b'{"data": "test"}'
        signature = hmac.new(
            signature_secret.encode("utf-8"), payload, hashlib.sha256
        ).hexdigest()
        signature_header = f"sha256={signature}"

        response = await client.post(
            f"/api/v1/hooks/{sig_required_with_secret_token}/test",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": signature_header,
            },
        )

        assert response.status_code == 200
        data = await response.get_json()
        test_result = data
        assert test_result.get("signature_valid") is True
