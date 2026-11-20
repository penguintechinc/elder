"""Unit tests for ``svc-integrations`` internal REST API and worker decoupling."""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import jwt
import pytest


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _encode_jwt(app, payload: dict) -> str:
    secret = app.config.get("JWT_SECRET_KEY") or app.config.get("SECRET_KEY")
    algorithm = app.config.get("JWT_ALGORITHM", "HS256")
    now = _utc_now()
    full_payload = {
        "iss": app.config.get("JWT_ISSUER", "elder-api"),
        "aud": app.config.get("JWT_AUDIENCE", "elder-api"),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
        **payload,
    }
    return jwt.encode(full_payload, secret, algorithm=algorithm)


def _service_worker_token(
    app,
    *,
    sub: str = "svc-integrations",
    token_type: str = "service_worker",
    tenant: str = "system",
    scope: list[str] | None = None,
) -> str:
    return _encode_jwt(
        app,
        {
            "sub": sub,
            "type": token_type,
            "tenant": tenant,
            "scope": ["integrations:sync"] if scope is None else scope,
        },
    )


def _seed_tenant(db, *, name: str = "Tenant", is_active: bool = True) -> int:
    now = _utc_now()
    tid = db.tenants.insert(
        name=name,
        slug=f"int-{uuid4().hex[:8]}",
        is_active=is_active,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return int(tid)


def _seed_org(db, tenant_id: int, *, name: str = "Org", is_active: bool = True) -> int:
    now = _utc_now()
    oid = db.organizations.insert(
        name=f"{name}-{uuid4().hex[:6]}",
        tenant_id=tenant_id,
        is_active=is_active,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return int(oid)


def _seed_identity(
    db,
    tenant_id: int,
    org_id: int,
    *,
    is_superuser: bool = False,
    portal_role: str = "observer",
) -> int:
    now = _utc_now()
    uid = uuid4().hex[:8]
    ident_id = db.identities.insert(
        tenant_id=tenant_id,
        organization_id=org_id,
        identity_type="human",
        username=f"user-{uid}",
        email=f"user-{uid}@example.test",
        full_name=f"User {uid}",
        auth_provider="local",
        auth_provider_id=uid,
        is_active=True,
        is_superuser=is_superuser,
        mfa_enabled=False,
        must_change_password=False,
        portal_role=portal_role,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return int(ident_id)


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestIntegrationsInternalAuth:
    """Verify strict authentication, scope, and svc-integrations identity gating."""

    @pytest.mark.parametrize(
        ("method", "path", "payload"),
        [
            ("GET", "/api/v1/integrations/internal/pending", None),
            ("POST", "/api/v1/integrations/internal/discovery/1/start", {}),
            ("POST", "/api/v1/integrations/internal/discovery/1/complete", {}),
            ("POST", "/api/v1/integrations/internal/sync/1/apply", {}),
            ("POST", "/api/v1/integrations/internal/iam/1/apply", {}),
            ("POST", "/api/v1/integrations/internal/google-workspace/1/apply", {}),
        ],
    )
    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(
        self, async_client, method: str, path: str, payload: dict | None
    ):
        resp = await async_client.open(path, method=method, json=payload)
        assert resp.status_code == 401

    @pytest.mark.parametrize(
        ("method", "path", "payload"),
        [
            ("GET", "/api/v1/integrations/internal/pending", None),
            ("POST", "/api/v1/integrations/internal/discovery/1/start", {}),
            ("POST", "/api/v1/integrations/internal/discovery/1/complete", {}),
            ("POST", "/api/v1/integrations/internal/sync/1/apply", {}),
            ("POST", "/api/v1/integrations/internal/iam/1/apply", {}),
            ("POST", "/api/v1/integrations/internal/google-workspace/1/apply", {}),
        ],
    )
    @pytest.mark.asyncio
    async def test_normal_and_admin_users_rejected_403(
        self, app, async_client, method: str, path: str, payload: dict | None
    ):
        db = app.db
        tid = _seed_tenant(db, name="AuthTestTenant")
        org_id = _seed_org(db, tid, name="AuthTestOrg")
        normal_id = _seed_identity(
            db, tid, org_id, is_superuser=False, portal_role="observer"
        )
        admin_id = _seed_identity(
            db, tid, org_id, is_superuser=True, portal_role="admin"
        )

        normal_token = _encode_jwt(
            app,
            {
                "sub": str(normal_id),
                "tenant": str(tid),
                "scope": ["integrations:sync", "discovery:read"],
                "roles": ["viewer"],
            },
        )
        admin_token = _encode_jwt(
            app,
            {
                "sub": str(admin_id),
                "tenant": str(tid),
                "scope": ["integrations:sync", "discovery:admin"],
                "roles": ["admin"],
            },
        )

        r_normal = await async_client.open(
            path,
            method=method,
            json=payload,
            headers=_auth_header(normal_token),
        )
        r_admin = await async_client.open(
            path,
            method=method,
            json=payload,
            headers=_auth_header(admin_token),
        )
        assert r_normal.status_code == 403
        assert r_admin.status_code == 403

    @pytest.mark.asyncio
    async def test_wrong_service_worker_sub_tenant_type_or_scope_rejected(
        self, app, async_client
    ):
        db = app.db
        tid = _seed_tenant(db, name="WrongTypeTenant")
        org_id = _seed_org(db, tid, name="WrongTypeOrg")
        user_id = _seed_identity(db, tid, org_id)

        wrong_sub_valid_scope = _service_worker_token(
            app, sub="svc-scanner", scope=["discovery:read"]
        )
        wrong_sub_forged_scope = _service_worker_token(
            app, sub="svc-scanner", scope=["integrations:sync"]
        )
        wrong_tenant = _service_worker_token(app, tenant="1")
        wrong_type_user = _encode_jwt(
            app,
            {
                "sub": str(user_id),
                "type": "access",
                "tenant": "system",
                "scope": ["integrations:sync"],
            },
        )
        from apps.api.auth.jwt_handler import generate_service_researcher_token

        async with app.app_context():
            wrong_type_researcher = generate_service_researcher_token(
                tid, ["discovery:read"], caller_sub=str(user_id)
            )
        missing_scope = _service_worker_token(app, scope=["discovery:read"])

        for token in (
            wrong_sub_valid_scope,
            wrong_tenant,
            wrong_type_user,
            wrong_type_researcher,
            missing_scope,
        ):
            resp = await async_client.get(
                "/api/v1/integrations/internal/pending",
                headers=_auth_header(token),
            )
            assert resp.status_code == 403

        forged_resp = await async_client.get(
            "/api/v1/integrations/internal/pending",
            headers=_auth_header(wrong_sub_forged_scope),
        )
        assert forged_resp.status_code == 401

    @pytest.mark.asyncio
    async def test_valid_svc_integrations_service_worker_allowed(
        self, app, async_client
    ):
        token = _service_worker_token(app)
        resp = await async_client.get(
            "/api/v1/integrations/internal/pending",
            headers=_auth_header(token),
        )
        assert resp.status_code == 200
        body = await resp.get_json()
        assert "generated_at" in body
        assert "discovery_jobs" in body
        assert "sync_configs" in body
        assert "iam_providers" in body
        assert "google_workspace_providers" in body


class TestIntegrationsInternalPending:
    """Verify pending job/config/provider enumeration, scheduling, credentials, and redaction."""

    @pytest.mark.asyncio
    async def test_pending_filtering_scheduling_credentials_and_force(
        self, app, async_client
    ):
        db = app.db
        now = _utc_now()
        past = now - timedelta(hours=2)
        future = now + timedelta(hours=2)
        recent = now - timedelta(seconds=30)

        active_tid = _seed_tenant(db, name="ActiveTenant", is_active=True)
        inactive_tid = _seed_tenant(db, name="InactiveTenant", is_active=False)
        active_org = _seed_org(db, active_tid, name="ActiveOrg", is_active=True)
        inactive_org = _seed_org(db, active_tid, name="InactiveOrg", is_active=False)
        org_in_inactive_tenant = _seed_org(
            db, inactive_tid, name="OrgInactiveTenant", is_active=True
        )

        secret_id = db.builtin_secrets.insert(
            name=f"aws-cred-{uuid4().hex[:6]}",
            organization_id=active_org,
            secret_type="cloud",
            secret_value="super-secret-inline-token",
            secret_json={"access_key": "AKIA_TEST_SECRET", "region": "us-west-2"},
            is_active=True,
            created_at=now,
            updated_at=now,
        )

        # Due discovery job with credential_id + mapping
        due_job_id = db.discovery_jobs.insert(
            name="aws-prod-due",
            provider="aws",
            config_json={"_organization_id": active_org, "account": "111122223333"},
            schedule_interval=3600,
            enabled=True,
            credential_type="builtin_secret",
            credential_id=secret_id,
            credential_mapping={"aws_access_key_id": "access_key"},
            last_run_at=None,
            created_at=now,
            updated_at=now,
        )
        # Scanner-owned providers must ALWAYS be excluded even with ?force=true
        scanner_job_ids: set[int] = set()
        for scanner_provider in ("network", "http_screenshot", "banner"):
            scanner_job_ids.add(
                int(
                    db.discovery_jobs.insert(
                        name=f"scanner-{scanner_provider}-{uuid4().hex[:6]}",
                        provider=scanner_provider,
                        config_json={"_organization_id": active_org},
                        schedule_interval=300,
                        enabled=True,
                        last_run_at=None,
                        created_at=now,
                        updated_at=now,
                    )
                )
            )
        # Not yet due discovery job (only returned when ?force=true)
        not_due_job_id = db.discovery_jobs.insert(
            name=f"gcp-not-due-{uuid4().hex[:6]}",
            provider="gcp",
            config_json={"_organization_id": active_org},
            schedule_interval=3600,
            enabled=True,
            last_run_at=recent,
            next_run_at=future,
            created_at=now,
            updated_at=now,
        )
        # Inactive org & inactive tenant jobs must be excluded even with ?force=true
        inactive_org_job_id = db.discovery_jobs.insert(
            name=f"aws-inactive-org-{uuid4().hex[:6]}",
            provider="aws",
            config_json={"_organization_id": inactive_org},
            schedule_interval=3600,
            enabled=True,
            last_run_at=None,
            created_at=now,
            updated_at=now,
        )
        inactive_tenant_job_id = db.discovery_jobs.insert(
            name=f"aws-inactive-tenant-{uuid4().hex[:6]}",
            provider="aws",
            config_json={"_organization_id": org_in_inactive_tenant},
            schedule_interval=3600,
            enabled=True,
            last_run_at=None,
            created_at=now,
            updated_at=now,
        )

        # Seed sync_configs, mappings, and issues
        issue_mapped_id = db.issues.insert(
            tenant_id=active_tid,
            organization_id=active_org,
            resource_type="organization",
            resource_id=active_org,
            title="Mapped Issue",
            description="Existing mapped issue",
            status="OPEN",
            priority="HIGH",
            issue_type="BUG",
            is_incident=0,
            reporter_id=None,
            created_at=now,
            updated_at=now,
        )
        issue_unmapped_id = db.issues.insert(
            tenant_id=active_tid,
            organization_id=active_org,
            resource_type="organization",
            resource_id=active_org,
            title="Local Unmapped Issue",
            description="Created locally for two-way push",
            status="OPEN",
            priority="MEDIUM",
            issue_type="OPERATIONS",
            is_incident=0,
            reporter_id=None,
            created_at=now,
            updated_at=now,
        )
        due_sync_id = db.sync_configs.insert(
            name=f"gh-due-{uuid4().hex[:6]}",
            platform="github",
            enabled=True,
            sync_interval=300,
            batch_fallback_enabled=True,
            batch_size=50,
            two_way_create=True,
            webhook_enabled=False,
            webhook_secret="super-webhook-secret",
            last_sync_at=None,
            last_batch_sync_at=None,
            config_json={
                "elder_organization_id": active_org,
                "owner": "penguintechinc",
                "repo": "elder",
                "token": "ghp_very_secret_token",
            },
            created_at=now,
            updated_at=now,
        )
        db.sync_mappings.insert(
            elder_type="issue",
            elder_id=issue_mapped_id,
            external_platform="github",
            external_id="42",
            sync_config_id=due_sync_id,
            sync_status="synced",
            sync_method="batch",
            last_synced_at=past,
            created_at=past,
        )
        not_due_sync_id = db.sync_configs.insert(
            name=f"jira-not-due-{uuid4().hex[:6]}",
            platform="jira",
            enabled=True,
            sync_interval=3600,
            batch_fallback_enabled=True,
            batch_size=50,
            two_way_create=False,
            webhook_enabled=False,
            last_sync_at=recent,
            last_batch_sync_at=recent,
            config_json={"elder_organization_id": active_org, "project": "ELD"},
            created_at=now,
            updated_at=now,
        )

        # Seed IAM providers
        due_iam_id = db.iam_providers.insert(
            name=f"aws-iam-due-{uuid4().hex[:6]}",
            provider_type="aws",
            config_json={"sync_interval": 300, "role_arn": "arn:aws:iam::123:role/r"},
            organization_id=active_org,
            enabled=True,
            last_sync_at=past,
            created_at=now,
            updated_at=now,
        )
        not_due_iam_id = db.iam_providers.insert(
            name=f"k8s-iam-recent-{uuid4().hex[:6]}",
            provider_type="kubernetes",
            config_json={"sync_interval": 3600},
            organization_id=active_org,
            enabled=True,
            last_sync_at=recent,
            created_at=now,
            updated_at=now,
        )
        inactive_org_iam_id = db.iam_providers.insert(
            name=f"iam-inactive-org-{uuid4().hex[:6]}",
            provider_type="aws",
            config_json={"sync_interval": 60},
            organization_id=inactive_org,
            enabled=True,
            last_sync_at=None,
            created_at=now,
            updated_at=now,
        )

        # Seed Google Workspace providers
        raw_gws_creds = {
            "service_account_json": {
                "type": "service_account",
                "client_email": "svc@proj.iam.gserviceaccount.com",
                "private_key": "fake-service-account-key-material",
            },
            "customer_id": "C0123456",
            "sync_interval": 600,
        }
        due_gws_id = db.google_workspace_providers.insert(
            name=f"gws-due-{uuid4().hex[:6]}",
            domain="example.test",
            admin_email="secret-admin@example.test",
            credentials_json=raw_gws_creds,
            organization_id=active_org,
            enabled=True,
            last_sync_at=None,
            created_at=now,
            updated_at=now,
        )
        not_due_gws_id = db.google_workspace_providers.insert(
            name=f"gws-recent-{uuid4().hex[:6]}",
            domain="recent.example.test",
            admin_email="admin2@recent.example.test",
            credentials_json={"sync_interval": 3600, "customer_id": "C999"},
            organization_id=active_org,
            enabled=True,
            last_sync_at=recent,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        token = _service_worker_token(app)
        resp = await async_client.get(
            "/api/v1/integrations/internal/pending",
            headers=_auth_header(token),
        )
        assert resp.status_code == 200
        data = await resp.get_json()

        resp_force = await async_client.get(
            "/api/v1/integrations/internal/pending?force=true",
            headers=_auth_header(token),
        )
        assert resp_force.status_code == 200
        forced = await resp_force.get_json()

        # Non-forced discovery assertions
        disc_ids = {j["id"] for j in data["discovery_jobs"]}
        assert due_job_id in disc_ids
        assert not_due_job_id not in disc_ids
        assert inactive_org_job_id not in disc_ids
        assert inactive_tenant_job_id not in disc_ids
        assert scanner_job_ids.isdisjoint(disc_ids)
        assert all(
            j["provider"] not in {"network", "http_screenshot", "banner"}
            for j in data["discovery_jobs"]
        )
        due_job = next(j for j in data["discovery_jobs"] if j["id"] == due_job_id)
        assert due_job["config"]["aws_access_key_id"] == "AKIA_TEST_SECRET"

        # Forced discovery returns not_due_job_id too, still excludes scanner & inactive orgs
        forced_disc_ids = {j["id"] for j in forced["discovery_jobs"]}
        assert {due_job_id, not_due_job_id}.issubset(forced_disc_ids)
        assert inactive_org_job_id not in forced_disc_ids
        assert inactive_tenant_job_id not in forced_disc_ids
        assert scanner_job_ids.isdisjoint(forced_disc_ids)
        assert all(
            j["provider"] not in {"network", "http_screenshot", "banner"}
            for j in forced["discovery_jobs"]
        )

        # Sync assertions
        sync_ids = {s["id"] for s in data["sync_configs"]}
        assert due_sync_id in sync_ids
        assert not_due_sync_id not in sync_ids
        due_sync = next(s for s in data["sync_configs"] if s["id"] == due_sync_id)
        assert "webhook_secret" not in due_sync
        assert len(due_sync["mappings"]) == 1
        assert due_sync["mappings"][0]["external_id"] == "42"
        local_ids = {item["id"] for item in due_sync["local_items"]}
        assert {issue_mapped_id, issue_unmapped_id}.issubset(local_ids)
        assert not_due_sync_id in {s["id"] for s in forced["sync_configs"]}

        # IAM assertions
        iam_ids = {p["id"] for p in data["iam_providers"]}
        assert due_iam_id in iam_ids
        assert not_due_iam_id not in iam_ids
        assert inactive_org_iam_id not in iam_ids
        forced_iam_ids = {p["id"] for p in forced["iam_providers"]}
        assert not_due_iam_id in forced_iam_ids
        assert inactive_org_iam_id not in forced_iam_ids

        # GWS assertions
        gws_ids = {p["id"] for p in data["google_workspace_providers"]}
        assert due_gws_id in gws_ids
        assert not_due_gws_id not in gws_ids
        due_gws = next(
            p for p in data["google_workspace_providers"] if p["id"] == due_gws_id
        )
        assert due_gws["credentials"] == raw_gws_creds
        assert not_due_gws_id in {p["id"] for p in forced["google_workspace_providers"]}

    @pytest.mark.asyncio
    async def test_no_secrets_credentials_or_admin_email_logged(
        self, app, async_client, caplog
    ):
        db = app.db
        now = _utc_now()
        tid = _seed_tenant(db, name="LogRedactTenant")
        org_id = _seed_org(db, tid, name="LogRedactOrg")

        db.google_workspace_providers.insert(
            name=f"gws-redact-{uuid4().hex[:6]}",
            domain="redact.example.test",
            admin_email="sensitive-admin@redact.example.test",
            credentials_json={
                "private_key": "SUPER_SECRET_RSA_KEY_MATERIAL",
                "client_secret": "GOCSPX-TOPSECRET",
            },
            organization_id=org_id,
            enabled=True,
            last_sync_at=None,
            created_at=now,
            updated_at=now,
        )
        db.sync_configs.insert(
            name=f"sync-redact-{uuid4().hex[:6]}",
            platform="github",
            enabled=True,
            sync_interval=300,
            batch_fallback_enabled=True,
            batch_size=50,
            two_way_create=True,
            webhook_enabled=False,
            webhook_secret="WHSEC_NEVER_LOG_ME",
            config_json={
                "elder_organization_id": org_id,
                "api_token": "SECRET_GITHUB_PAT_999",
            },
            created_at=now,
            updated_at=now,
        )
        db.commit()

        token = _service_worker_token(app)
        caplog.clear()
        with caplog.at_level("DEBUG"):
            resp = await async_client.get(
                "/api/v1/integrations/internal/pending?force=true",
                headers=_auth_header(token),
            )
        assert resp.status_code == 200
        log_blob = "\n".join(rec.getMessage() for rec in caplog.records)
        for sensitive in (
            "sensitive-admin@redact.example.test",
            "SUPER_SECRET_RSA_KEY_MATERIAL",
            "GOCSPX-TOPSECRET",
            "WHSEC_NEVER_LOG_ME",
            "SECRET_GITHUB_PAT_999",
        ):
            assert sensitive not in log_blob


class TestIntegrationsInternalDiscoveryLifecycle:
    """Verify /discovery/<job_id>/start and /complete endpoints."""

    @pytest.mark.asyncio
    async def test_missing_job_returns_404_and_invalid_status_returns_400(
        self, app, async_client
    ):
        db = app.db
        now = _utc_now()
        tid = _seed_tenant(db, name="DiscErrTenant")
        org_id = _seed_org(db, tid, name="DiscErrOrg")
        job_id = db.discovery_jobs.insert(
            name=f"job-err-{uuid4().hex[:6]}",
            provider="aws",
            config_json={"_organization_id": org_id},
            schedule_interval=3600,
            enabled=True,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        token = _service_worker_token(app)
        r_start_404 = await async_client.post(
            "/api/v1/integrations/internal/discovery/999999/start",
            headers=_auth_header(token),
            json={},
        )
        r_complete_404 = await async_client.post(
            "/api/v1/integrations/internal/discovery/999999/complete",
            headers=_auth_header(token),
            json={"status": "completed"},
        )
        r_complete_400 = await async_client.post(
            f"/api/v1/integrations/internal/discovery/{job_id}/complete",
            headers=_auth_header(token),
            json={"status": "bogus"},
        )
        assert r_start_404.status_code == 404
        assert r_complete_404.status_code == 404
        assert r_complete_400.status_code == 400

    @pytest.mark.asyncio
    async def test_start_and_complete_persists_resources_deps_and_schedule(
        self, app, async_client
    ):
        db = app.db
        now = _utc_now()
        tid = _seed_tenant(db, name="DiscFlowTenant")
        org_id = _seed_org(db, tid, name="DiscFlowOrg")
        job_id = db.discovery_jobs.insert(
            name=f"aws-disc-{uuid4().hex[:6]}",
            provider="aws",
            config_json={"_organization_id": org_id},
            schedule_interval=1800,
            enabled=True,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        token = _service_worker_token(app)
        r_start = await async_client.post(
            f"/api/v1/integrations/internal/discovery/{job_id}/start",
            headers=_auth_header(token),
            json={},
        )
        assert r_start.status_code == 200
        start_body = await r_start.get_json()
        history_id = start_body["history_id"]
        assert start_body["job_id"] == job_id

        hist_row = db(db.discovery_history.id == history_id).select().first()
        assert hist_row is not None
        assert hist_row.status == "running"

        r_complete = await async_client.post(
            f"/api/v1/integrations/internal/discovery/{job_id}/complete",
            headers=_auth_header(token),
            json={
                "history_id": history_id,
                "status": "completed",
                "organization_id": org_id,
                "resources": [
                    {
                        "name": "vpc-main",
                        "category": "network",
                        "resource_type": "vpc",
                        "external_id": f"vpc-{org_id}",
                        "provider": "aws",
                        "metadata": {"vpc_id": f"vpc-{org_id}"},
                    },
                    {
                        "name": "web-01",
                        "category": "compute",
                        "resource_type": "ec2_instance",
                        "external_id": f"i-{org_id}",
                        "provider": "aws",
                        "metadata": {},
                    },
                ],
                "dependencies": [
                    {
                        "source_external_id": f"i-{org_id}",
                        "target_external_id": f"vpc-{org_id}",
                        "target_kind": "networking_resource",
                        "edge_type": "in_network",
                    }
                ],
            },
        )
        assert r_complete.status_code == 200
        complete_body = await r_complete.get_json()
        assert complete_body["status"] == "completed"
        assert complete_body["entities_discovered"] == 2
        assert complete_body["entities_created"] >= 1
        assert complete_body["dependencies_created"] >= 1

        # Second complete run updates existing entity and creates history when history_id omitted
        r_second = await async_client.post(
            f"/api/v1/integrations/internal/discovery/{job_id}/complete",
            headers=_auth_header(token),
            json={
                "status": "completed",
                "organization_id": org_id,
                "resources": [
                    {
                        "name": "web-01",
                        "category": "compute",
                        "resource_type": "ec2_instance",
                        "external_id": f"i-{org_id}",
                        "provider": "aws",
                        "metadata": {"reseen": True},
                    }
                ],
                "dependencies": [
                    {
                        "source_external_id": f"i-{org_id}",
                        "target_external_id": f"vpc-{org_id}",
                        "target_kind": "networking_resource",
                        "edge_type": "connects_to",
                        "metadata": {"port": 443},
                    }
                ],
            },
        )
        assert r_second.status_code == 200
        second_body = await r_second.get_json()
        assert second_body["entities_updated"] >= 1
        assert second_body["dependencies_created"] >= 1

        # Third complete run verifies standalone dependency edges on existing DB entities
        r_third = await async_client.post(
            f"/api/v1/integrations/internal/discovery/{job_id}/complete",
            headers=_auth_header(token),
            json={
                "status": "completed",
                "organization_id": org_id,
                "resources": [],
                "dependencies": [
                    {
                        "source_external_id": f"i-{org_id}",
                        "source_type": "entity",
                        "target_external_id": f"vpc-{org_id}",
                        "target_kind": "networking_resource",
                        "edge_type": "depends_on",
                        "metadata": {"via": "standalone"},
                    }
                ],
            },
        )
        assert r_third.status_code == 200
        third_body = await r_third.get_json()
        assert third_body["dependencies_created"] == 1

        entity = (
            db(
                (db.entities.organization_id == org_id)
                & (db.entities.external_id == f"i-{org_id}")
            )
            .select()
            .first()
        )
        vpc = (
            db(
                (db.networking_resources.organization_id == org_id)
                & (db.networking_resources.external_id == f"vpc-{org_id}")
            )
            .select()
            .first()
        )
        assert entity is not None
        assert vpc is not None
        dep = (
            db(
                (db.dependencies.source_type == "entity")
                & (db.dependencies.source_id == entity.id)
                & (db.dependencies.target_type == "networking_resource")
                & (db.dependencies.target_id == vpc.id)
            )
            .select()
            .first()
        )
        assert dep is not None

        job_after = db(db.discovery_jobs.id == job_id).select().first()
        assert job_after.last_run_at is not None
        assert job_after.next_run_at is not None
        assert job_after.next_run_at > job_after.last_run_at


class TestIntegrationsInternalSyncApply:
    """Verify /sync/<config_id>/apply persistence."""

    @pytest.mark.asyncio
    async def test_sync_apply_upserts_issues_mappings_conflicts_and_history(
        self, app, async_client
    ):
        db = app.db
        now = _utc_now()
        tid = _seed_tenant(db, name="SyncApplyTenant")
        org_id = _seed_org(db, tid, name="SyncApplyOrg")

        local_issue_id = db.issues.insert(
            tenant_id=tid,
            organization_id=org_id,
            resource_type="organization",
            resource_id=org_id,
            title="Local Issue To Push",
            description="Created inside Elder",
            status="OPEN",
            priority="MEDIUM",
            issue_type="BUG",
            is_incident=0,
            reporter_id=None,
            created_at=now,
            updated_at=now,
        )
        config_id = db.sync_configs.insert(
            name=f"gh-apply-{uuid4().hex[:6]}",
            platform="github",
            enabled=True,
            sync_interval=300,
            batch_fallback_enabled=True,
            batch_size=100,
            two_way_create=True,
            webhook_enabled=True,
            config_json={"elder_organization_id": org_id},
            created_at=now,
            updated_at=now,
        )
        no_create_cfg_id = db.sync_configs.insert(
            name=f"jira-no-create-{uuid4().hex[:6]}",
            platform="jira",
            enabled=True,
            sync_interval=300,
            batch_fallback_enabled=True,
            batch_size=100,
            two_way_create=False,
            webhook_enabled=False,
            config_json={"elder_organization_id": org_id},
            created_at=now,
            updated_at=now,
        )
        db.commit()

        token = _service_worker_token(app)
        r_404 = await async_client.post(
            "/api/v1/integrations/internal/sync/999999/apply",
            headers=_auth_header(token),
            json={},
        )
        assert r_404.status_code == 404

        # Respect two_way_create=False when unmapped
        r_skip = await async_client.post(
            f"/api/v1/integrations/internal/sync/{no_create_cfg_id}/apply",
            headers=_auth_header(token),
            json={
                "sync_type": "batch",
                "pulled_items": [
                    {
                        "external_id": "ELD-999",
                        "title": "Should Not Be Created",
                        "status": "OPEN",
                    }
                ],
            },
        )
        assert r_skip.status_code == 200
        skip_body = await r_skip.get_json()
        assert skip_body["items_synced"] == 0

        r_apply = await async_client.post(
            f"/api/v1/integrations/internal/sync/{config_id}/apply",
            headers=_auth_header(token),
            json={
                "correlation_id": "corr-1234",
                "sync_type": "batch",
                "success": True,
                "pulled_items": [
                    {
                        "external_id": "gh-101",
                        "title": "Pulled External Bug",
                        "description": "From GitHub",
                        "status": "IN_PROGRESS",
                        "priority": "HIGH",
                        "issue_type": "BUG",
                    }
                ],
                "pushed_mappings": [
                    {
                        "elder_id": local_issue_id,
                        "elder_type": "issue",
                        "external_id": "gh-202",
                        "sync_status": "synced",
                    }
                ],
                "conflicts": [
                    {
                        "external_id": "gh-202",
                        "conflict_type": "concurrent_edit",
                        "elder_data": {"title": "Local Issue To Push"},
                        "external_data": {"title": "Remote Edit"},
                        "resolution_strategy": "manual",
                    }
                ],
            },
        )
        assert r_apply.status_code == 200
        body = await r_apply.get_json()
        assert body["sync_config_id"] == config_id
        assert body["items_synced"] == 2
        assert body["conflicts_recorded"] == 1
        assert body["history_id"] > 0

        pulled_mapping = (
            db(
                (db.sync_mappings.sync_config_id == config_id)
                & (db.sync_mappings.external_id == "gh-101")
            )
            .select()
            .first()
        )
        assert pulled_mapping is not None
        created_issue = db(db.issues.id == pulled_mapping.elder_id).select().first()
        assert created_issue is not None
        assert created_issue.title == "Pulled External Bug"
        assert created_issue.status == "IN_PROGRESS"

        pushed_mapping = (
            db(
                (db.sync_mappings.sync_config_id == config_id)
                & (db.sync_mappings.external_id == "gh-202")
            )
            .select()
            .first()
        )
        assert pushed_mapping is not None
        assert pushed_mapping.elder_id == local_issue_id
        assert pushed_mapping.sync_status == "conflict"

        conflict_row = (
            db(db.sync_conflicts.mapping_id == pushed_mapping.id).select().first()
        )
        assert conflict_row is not None
        assert conflict_row.conflict_type == "concurrent_edit"

        cfg_after = db(db.sync_configs.id == config_id).select().first()
        assert cfg_after.last_sync_at is not None
        assert cfg_after.last_batch_sync_at is not None


class TestIntegrationsInternalIamAndGwsApply:
    """Verify /iam/<id>/apply and /google-workspace/<id>/apply persistence & PII safety."""

    @pytest.mark.asyncio
    async def test_iam_apply_persists_tokenized_users_groups_roles_and_memberships(
        self, app, async_client, caplog
    ):
        db = app.db
        now = _utc_now()
        tid = _seed_tenant(db, name="IamTenant")
        org_id = _seed_org(db, tid, name="IamOrg")
        provider_id = db.iam_providers.insert(
            name=f"aws-iam-{uuid4().hex[:6]}",
            provider_type="aws",
            config_json={"sync_interval": 300},
            organization_id=org_id,
            enabled=True,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        token = _service_worker_token(app)
        with caplog.at_level("DEBUG"):
            r_404 = await async_client.post(
                "/api/v1/integrations/internal/iam/999999/apply",
                headers=_auth_header(token),
                json={},
            )
            assert r_404.status_code == 404

            r_ok = await async_client.post(
                f"/api/v1/integrations/internal/iam/{provider_id}/apply",
                headers=_auth_header(token),
                json={
                    "success": True,
                    "users": [
                        {
                            "external_id": "arn:aws:iam::123:user/u1",
                            "uuid": "uuid-iam-u1",
                            "email": "private-iam-user@example.test",
                            "full_name": "Private IAM Person",
                            "is_service_account": True,
                        }
                    ],
                    "groups": [
                        {
                            "external_id": "grp-admins",
                            "name": f"aws-admins-{org_id}",
                            "description": "Cloud admins",
                        }
                    ],
                    "roles": [
                        {
                            "external_id": "arn:aws:iam::123:role/ReadOnly",
                            "arn": "arn:aws:iam::123:role/ReadOnly",
                            "name": f"aws-readonly-{org_id}",
                        }
                    ],
                    "memberships": [
                        {
                            "user_external_id": "arn:aws:iam::123:user/u1",
                            "group_external_id": "grp-admins",
                            "role_name": f"aws-readonly-{org_id}",
                        }
                    ],
                },
            )
        assert r_ok.status_code == 200
        body = await r_ok.get_json()
        assert body["users_synced"] == 1
        assert body["groups_synced"] == 1
        assert body["roles_synced"] == 1
        assert body["memberships_synced"] == 2

        ident = (
            db(
                (db.identities.organization_id == org_id)
                & (db.identities.auth_provider == "aws")
                & (db.identities.auth_provider_id == "arn:aws:iam::123:user/u1")
            )
            .select()
            .first()
        )
        assert ident is not None
        assert ident.username == "uuid-iam-u1"
        assert ident.email is None
        assert ident.full_name is None

        grp = db(db.identity_groups.provider_group_id == "grp-admins").select().first()
        assert grp is not None
        mem = (
            db(
                (db.identity_group_memberships.identity_id == ident.id)
                & (db.identity_group_memberships.group_id == grp.id)
            )
            .select()
            .first()
        )
        assert mem is not None
        ur = (
            db(
                (db.user_roles.identity_id == ident.id)
                & (db.user_roles.organization_id == org_id)
            )
            .select()
            .first()
        )
        assert ur is not None

        role_ent = (
            db(
                (db.entities.organization_id == org_id)
                & (db.entities.external_id == "arn:aws:iam::123:role/ReadOnly")
            )
            .select()
            .first()
        )
        assert role_ent is not None
        provider_after = db(db.iam_providers.id == provider_id).select().first()
        assert provider_after.last_sync_at is not None

        log_blob = "\n".join(rec.getMessage() for rec in caplog.records)
        assert "private-iam-user@example.test" not in log_blob
        assert "Private IAM Person" not in log_blob

    @pytest.mark.asyncio
    async def test_google_workspace_apply_persists_and_never_logs_pii(
        self, app, async_client, caplog
    ):
        db = app.db
        now = _utc_now()
        tid = _seed_tenant(db, name="GwsTenant")
        org_id = _seed_org(db, tid, name="GwsOrg")
        provider_id = db.google_workspace_providers.insert(
            name=f"gws-{uuid4().hex[:6]}",
            domain="corp.example.test",
            admin_email="secret-gws-admin@corp.example.test",
            credentials_json={"customer_id": "C001"},
            organization_id=org_id,
            enabled=True,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        token = _service_worker_token(app)
        with caplog.at_level("DEBUG"):
            r_404 = await async_client.post(
                "/api/v1/integrations/internal/google-workspace/999999/apply",
                headers=_auth_header(token),
                json={},
            )
            assert r_404.status_code == 404

            r_ok = await async_client.post(
                f"/api/v1/integrations/internal/google-workspace/{provider_id}/apply",
                headers=_auth_header(token),
                json={
                    "success": True,
                    "users": [
                        {
                            "id": "g-u1",
                            "primaryEmail": "jane@corp.example.test",
                            "name": {"fullName": "Jane Doe"},
                            "suspended": False,
                        },
                        {
                            "id": "g-u2",
                            "primaryEmail": "suspended@corp.example.test",
                            "name": {"fullName": "Suspended User"},
                            "suspended": True,
                        },
                    ],
                    "groups": [
                        {
                            "id": "g-grp-eng",
                            "name": f"gws-eng-{org_id}",
                            "description": "Engineering",
                        }
                    ],
                    "memberships": [
                        {
                            "user_external_id": "g-u1",
                            "group_external_id": "g-grp-eng",
                        }
                    ],
                },
            )
        assert r_ok.status_code == 200
        body = await r_ok.get_json()
        assert body["users_synced"] == 2
        assert body["groups_synced"] == 1
        assert body["memberships_synced"] == 1

        u1 = (
            db(
                (db.identities.organization_id == org_id)
                & (db.identities.auth_provider == "google")
                & (db.identities.auth_provider_id == "g-u1")
            )
            .select()
            .first()
        )
        u2 = (
            db(
                (db.identities.organization_id == org_id)
                & (db.identities.auth_provider == "google")
                & (db.identities.auth_provider_id == "g-u2")
            )
            .select()
            .first()
        )
        assert u1 is not None and bool(u1.is_active) is True
        assert u2 is not None and bool(u2.is_active) is False

        grp = db(db.identity_groups.provider_group_id == "g-grp-eng").select().first()
        assert grp is not None
        mem = (
            db(
                (db.identity_group_memberships.identity_id == u1.id)
                & (db.identity_group_memberships.group_id == grp.id)
            )
            .select()
            .first()
        )
        assert mem is not None

        provider_after = (
            db(db.google_workspace_providers.id == provider_id).select().first()
        )
        assert provider_after.last_sync_at is not None

        log_blob = "\n".join(rec.getMessage() for rec in caplog.records)
        for pii in (
            "secret-gws-admin@corp.example.test",
            "jane@corp.example.test",
            "Jane Doe",
            "suspended@corp.example.test",
        ):
            assert pii not in log_blob


class TestWorkerServiceDecoupling:
    """Verify WorkerService is decoupled from connectors and discovery polling."""

    @pytest.mark.asyncio
    async def test_worker_service_decoupled_status_and_health(self):
        stub_modules = {}
        if importlib.util.find_spec("aiocron") is None:
            stub_modules["aiocron"] = MagicMock()

        with patch.dict(sys.modules, stub_modules):
            from apps.worker.main import WorkerService

            with patch.object(WorkerService, "_init_database"):
                worker = WorkerService()

            for removed_attr in (
                "connectors",
                "discovery_executor",
                "_initialize_connectors",
                "_sync_connector",
                "_run_sync_cycle",
                "_init_discovery_executor",
                "_run_discovery_poll",
            ):
                assert not hasattr(worker, removed_attr)

            for kept_attr in (
                "_init_jobbus",
                "_service_node_heartbeat",
                "_setup_scheduled_syncs",
                "_sweeper_task",
            ):
                assert hasattr(worker, kept_attr)

            client = worker.health_app.test_client()
            health_resp = await client.get("/healthz")
            status_resp = await client.get("/status")
            assert health_resp.status_code == 200
            assert status_resp.status_code == 200
            status_body = await status_resp.get_json()
            assert status_body["integrations_owner"] == "svc-integrations"
            assert "connectors" not in status_body
