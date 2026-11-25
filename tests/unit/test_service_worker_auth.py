"""Unit and API tests for ``service_worker`` machine JWT authentication."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import jwt
import pytest

from apps.api.auth.jwt_handler import (
    SERVICE_WORKER_ALLOWED_SCOPES,
    ServiceWorkerIdentity,
    get_current_user,
    verify_token,
)


def _mint_worker_token(
    app,
    *,
    sub: str = "svc-scanner",
    token_type: str = "service_worker",
    tenant: str | int | bool | None = "system",
    scope: list[str] | str | None = None,
    iss: str | None = None,
    aud: str | None = None,
    iat_offset: int = 0,
    exp_offset: int = 300,
    include_iat: bool = True,
    include_exp: bool = True,
) -> str:
    """Sign a ``service_worker`` test JWT with configurable claims."""
    secret = app.config.get("JWT_SECRET_KEY") or app.config.get("SECRET_KEY")
    algorithm = app.config.get("JWT_ALGORITHM", "HS256")
    now = datetime.now(UTC)

    payload: dict = {
        "sub": sub,
        "type": token_type,
        "iss": app.config.get("JWT_ISSUER", "elder-api") if iss is None else iss,
        "aud": app.config.get("JWT_AUDIENCE", "elder-api") if aud is None else aud,
    }
    if include_iat:
        payload["iat"] = int((now + timedelta(seconds=iat_offset)).timestamp())
    if include_exp:
        payload["exp"] = int((now + timedelta(seconds=exp_offset)).timestamp())
    if tenant is not None:
        payload["tenant"] = tenant
    if scope is None:
        payload["scope"] = sorted(
            SERVICE_WORKER_ALLOWED_SCOPES.get(sub, ["discovery:read"])
        )
    else:
        payload["scope"] = scope
    return jwt.encode(payload, secret, algorithm=algorithm)


def _create_tenant(db, *, name: str, is_active: bool = True) -> int:
    tid = db.tenants.insert(
        name=name,
        slug=f"sw-{uuid4().hex[:8]}",
        is_active=is_active,
    )
    db.commit()
    return tid


def _create_org(db, tenant_id: int, name: str = "Worker Org") -> int:
    now = datetime.now(UTC)
    oid = db.organizations.insert(
        name=name,
        tenant_id=tenant_id,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return oid


def _create_service(
    db, tenant_id: int, org_id: int, *, repo: str = "https://example.com/repo.git"
) -> int:
    now = datetime.now(UTC)
    sid = db.services.insert(
        tenant_id=tenant_id,
        name=f"svc-{uuid4().hex[:6]}",
        organization_id=org_id,
        is_public=False,
        repository_url=repo,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return sid


def _create_discovery_job(db, org_id: int, provider: str = "network") -> int:
    now = datetime.now(UTC)
    jid = db.discovery_jobs.insert(
        name=f"job-{uuid4().hex[:6]}",
        provider=provider,
        config_json={"_organization_id": org_id, "targets": ["10.0.0.1"]},
        schedule_interval=0,
        enabled=True,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return jid


class TestVerifyServiceWorkerToken:
    """Direct validation of ``service_worker`` JWT claims in ``verify_token``."""

    @pytest.mark.asyncio
    async def test_valid_svc_scanner_system_token(self, app):
        async with app.app_context():
            token = _mint_worker_token(
                app,
                sub="svc-scanner",
                tenant="system",
                scope=["discovery:read", "sbom:write", "vulnerabilities:write"],
            )
            payload = verify_token(token)
            assert payload is not None
            assert payload["sub"] == "svc-scanner"
            assert payload["type"] == "service_worker"
            assert payload["tenant"] == "system"
            assert payload["roles"] == ["service_worker"]
            assert payload["scope"] == [
                "discovery:read",
                "sbom:write",
                "vulnerabilities:write",
            ]

    @pytest.mark.asyncio
    async def test_valid_svc_integrations_tenant_token_with_space_scopes(self, app):
        async with app.app_context():
            token = _mint_worker_token(
                app,
                sub="svc-integrations",
                tenant="002",
                scope="entities:read integrations:sync",
            )
            payload = verify_token(token)
            assert payload is not None
            assert payload["sub"] == "svc-integrations"
            assert payload["tenant"] == "2"
            assert payload["scope"] == ["entities:read", "integrations:sync"]

    @pytest.mark.asyncio
    async def test_rejects_wrong_issuer_and_audience(self, app):
        async with app.app_context():
            bad_iss = _mint_worker_token(app, iss="wrong-issuer")
            bad_aud = _mint_worker_token(app, aud="wrong-audience")
            assert verify_token(bad_iss) is None
            assert verify_token(bad_aud) is None

    @pytest.mark.asyncio
    async def test_rejects_ttl_bounds_and_missing_timestamps(self, app):
        async with app.app_context():
            too_long = _mint_worker_token(app, exp_offset=601)
            zero_life = _mint_worker_token(app, iat_offset=10, exp_offset=10)
            missing_iat = _mint_worker_token(app, include_iat=False)
            expired = _mint_worker_token(app, iat_offset=-600, exp_offset=-10)
            assert verify_token(too_long) is None
            assert verify_token(zero_life) is None
            assert verify_token(missing_iat) is None
            assert verify_token(expired) is None

    @pytest.mark.asyncio
    async def test_rejects_unknown_sub(self, app):
        async with app.app_context():
            tok = _mint_worker_token(
                app,
                sub="svc-rogue",
                scope=["discovery:read"],
            )
            assert verify_token(tok) is None

    @pytest.mark.asyncio
    async def test_rejects_empty_wildcard_or_unlisted_scopes(self, app):
        async with app.app_context():
            empty = _mint_worker_token(app, scope=[])
            wildcard = _mint_worker_token(app, scope=["*"])
            other_service_scope = _mint_worker_token(
                app, sub="svc-scanner", scope=["entities:write"]
            )
            other_service_vuln = _mint_worker_token(
                app, sub="svc-integrations", scope=["vulnerabilities:write"]
            )
            assert verify_token(empty) is None
            assert verify_token(wildcard) is None
            assert verify_token(other_service_scope) is None
            assert verify_token(other_service_vuln) is None

    @pytest.mark.asyncio
    async def test_rejects_invalid_tenant_claims(self, app):
        async with app.app_context():
            for bad_tenant in ["", "0", "-1", "abc", 1, True]:
                tok = _mint_worker_token(app, tenant=bad_tenant)
                assert verify_token(tok) is None

    @pytest.mark.asyncio
    async def test_get_current_user_builds_service_worker_identity(self, app):
        async with app.test_request_context(
            "/api/v1/discovery/jobs/pending",
            headers={"Authorization": f"Bearer {_mint_worker_token(app)}"},
        ):
            user = get_current_user()
            assert isinstance(user, ServiceWorkerIdentity)
            assert user.id == 0
            assert user.username == "svc-scanner"
            assert user.tenant_id is None
            assert user.portal_role == "service_worker"
            assert user.token_type == "service_worker"

    @pytest.mark.asyncio
    async def test_service_researcher_still_rejects_system_tenant(self, app):
        secret = app.config.get("JWT_SECRET_KEY") or app.config.get("SECRET_KEY")
        algorithm = app.config.get("JWT_ALGORITHM", "HS256")
        now = datetime.now(UTC)
        bad_researcher = jwt.encode(
            {
                "sub": "service-researcher:1",
                "type": "service_researcher",
                "tenant": "system",
                "scope": ["discovery:read"],
                "iss": app.config.get("JWT_ISSUER", "elder-api"),
                "aud": app.config.get("JWT_AUDIENCE", "elder-api"),
                "iat": now,
                "exp": now + timedelta(seconds=300),
            },
            secret,
            algorithm=algorithm,
        )
        async with app.app_context():
            assert verify_token(bad_researcher) is None


class TestServiceWorkerRoutesCrossTenantAndIsolation:
    """API tests verifying system service_worker workflows and user isolation."""

    @pytest.mark.asyncio
    async def test_discovery_cross_tenant_worker_lifecycle(self, async_client, app):
        db = app.db
        t_active_a = _create_tenant(db, name="SW Discovery A", is_active=True)
        t_active_b = _create_tenant(db, name="SW Discovery B", is_active=True)
        t_inactive = _create_tenant(db, name="SW Discovery Inactive", is_active=False)

        org_a = _create_org(db, t_active_a, "Org A")
        org_b = _create_org(db, t_active_b, "Org B")
        org_inactive = _create_org(db, t_inactive, "Org Inactive")

        job_a = _create_discovery_job(db, org_a, provider="network")
        job_b = _create_discovery_job(db, org_b, provider="http_screenshot")
        job_banner = _create_discovery_job(db, org_b, provider="banner")
        job_ignored = _create_discovery_job(db, org_a, provider="aws")
        job_inactive = _create_discovery_job(db, org_inactive, provider="network")

        worker_token = _mint_worker_token(app, sub="svc-scanner", tenant="system")
        headers = {"Authorization": f"Bearer {worker_token}"}

        resp = await async_client.get("/api/v1/discovery/jobs/pending", headers=headers)
        assert resp.status_code == 200, (await resp.get_data()).decode()
        jobs = (await resp.get_json())["jobs"]
        returned_ids = {j["id"] for j in jobs}
        assert {job_a, job_b, job_banner}.issubset(returned_ids)
        assert job_ignored not in returned_ids
        assert job_inactive not in returned_ids

        start_resp = await async_client.post(
            f"/api/v1/discovery/jobs/{job_b}/start", headers=headers
        )
        assert start_resp.status_code == 200
        claim_token = (await start_resp.get_json())["claim_token"]

        complete_resp = await async_client.post(
            f"/api/v1/discovery/jobs/{job_b}/complete",
            headers=headers,
            json={
                "success": True,
                "results": {"scanned": 1},
                "claim_token": claim_token,
            },
        )
        assert complete_resp.status_code == 200

        inactive_start = await async_client.post(
            f"/api/v1/discovery/jobs/{job_inactive}/start", headers=headers
        )
        assert inactive_start.status_code == 404

    @pytest.mark.asyncio
    async def test_sbom_pending_resolves_per_tenant_credentials_only(
        self, async_client, app
    ):
        db = app.db
        now = datetime.now(UTC)
        tenant_a = _create_tenant(db, name="SW SBOM Tenant A", is_active=True)
        tenant_b = _create_tenant(db, name="SW SBOM Tenant B", is_active=True)
        tenant_inactive = _create_tenant(db, name="SW SBOM Inactive", is_active=False)

        org_a = _create_org(db, tenant_a, "SBOM Org A")
        org_b = _create_org(db, tenant_b, "SBOM Org B")
        org_inactive = _create_org(db, tenant_inactive, "SBOM Org Inactive")

        svc_a = _create_service(db, tenant_a, org_a)
        svc_b = _create_service(db, tenant_b, org_b)
        svc_inactive = _create_service(db, tenant_inactive, org_inactive)

        secret_b_id = db.builtin_secrets.insert(
            name="git-secret-b",
            organization_id=org_b,
            secret_json={"token": "secret-token-b"},
            secret_type="git_token",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        # Scan in tenant A intentionally references secret belonging to tenant B -> must NOT resolve!
        scan_a_cross_cred = db.sbom_scans.insert(
            tenant_id=tenant_a,
            parent_type="service",
            parent_id=svc_a,
            scan_type="git_clone",
            status="pending",
            repository_url="https://example.com/a.git",
            credential_type="builtin_secret",
            credential_id=secret_b_id,
            credential_mapping={"field": "token"},
        )
        scan_b_own_cred = db.sbom_scans.insert(
            tenant_id=tenant_b,
            parent_type="service",
            parent_id=svc_b,
            scan_type="git_clone",
            status="pending",
            repository_url="https://example.com/b.git",
            credential_type="builtin_secret",
            credential_id=secret_b_id,
            credential_mapping={"field": "token"},
        )
        scan_inactive = db.sbom_scans.insert(
            tenant_id=tenant_inactive,
            parent_type="service",
            parent_id=svc_inactive,
            scan_type="git_clone",
            status="pending",
            repository_url="https://example.com/inactive.git",
        )
        db.commit()

        worker_token = _mint_worker_token(app, sub="svc-scanner", tenant="system")
        headers = {"Authorization": f"Bearer {worker_token}"}

        resp = await async_client.get("/api/v1/sbom/scans/pending", headers=headers)
        assert resp.status_code == 200, (await resp.get_data()).decode()
        scans = await resp.get_json()
        by_id = {s["id"]: s for s in scans}

        assert scan_inactive not in by_id
        assert scan_a_cross_cred in by_id
        assert scan_b_own_cred in by_id
        assert "_resolved_token" not in by_id[scan_a_cross_cred]
        assert by_id[scan_b_own_cred]["_resolved_token"] == "secret-token-b"

        # Start and complete scan_b via service_worker
        start_resp = await async_client.post(
            f"/api/v1/sbom/scans/{scan_b_own_cred}/start", headers=headers
        )
        assert start_resp.status_code == 200, (await start_resp.get_data()).decode()
        claim_token = (await start_resp.get_json())["claim_token"]

        with patch("asyncio.create_task") as mock_bg:
            mock_bg.side_effect = lambda coro: coro.close()
            res_resp = await async_client.post(
                f"/api/v1/sbom/scans/{scan_b_own_cred}/results",
                headers=headers,
                json={
                    "claim_token": claim_token,
                    "success": True,
                    "components": [
                        {
                            "name": "serde",
                            "version": "1.0.219",
                            "purl": "pkg:cargo/serde@1.0.219",
                            "package_type": "cargo",
                            "scope": "runtime",
                            "direct": True,
                            "source_file": "Cargo.toml",
                        }
                    ],
                    "files_scanned": ["Cargo.toml"],
                    "commit_hash": "deadbeef",
                    "scan_duration_ms": 42,
                },
            )
        assert res_resp.status_code == 200, (await res_resp.get_data()).decode()
        comp_rows = db(
            (db.sbom_components.parent_id == svc_b)
            & (db.sbom_components.name == "serde")
        ).select()
        assert len(comp_rows) == 1
        assert comp_rows[0].tenant_id == tenant_b

    @pytest.mark.asyncio
    async def test_sbom_schedules_due_create_scan_and_update(self, async_client, app):
        db = app.db
        now = datetime.now(UTC)
        tenant_b = _create_tenant(db, name="SW Sched Tenant B", is_active=True)
        org_b = _create_org(db, tenant_b, "Sched Org B")
        svc_b = _create_service(db, tenant_b, org_b)

        sched_id = db.sbom_scan_schedules.insert(
            tenant_id=tenant_b,
            parent_type="service",
            parent_id=svc_b,
            schedule_cron="0 * * * *",
            is_active=True,
            next_run_at=now - timedelta(minutes=5),
            created_at=now,
            updated_at=now,
        )
        db.commit()

        worker_token = _mint_worker_token(app, sub="svc-scanner", tenant="system")
        headers = {"Authorization": f"Bearer {worker_token}"}

        due_resp = await async_client.get("/api/v1/sbom/schedules/due", headers=headers)
        assert due_resp.status_code == 200, (await due_resp.get_data()).decode()
        due_ids = {s["id"] for s in await due_resp.get_json()}
        assert sched_id in due_ids

        create_resp = await async_client.post(
            "/api/v1/sbom/scans",
            headers=headers,
            json={
                "parent_type": "service",
                "parent_id": svc_b,
                "scan_type": "git_clone",
            },
        )
        assert create_resp.status_code == 201, (await create_resp.get_data()).decode()
        created = await create_resp.get_json()
        assert created["scan_type"] == "git_clone"
        assert created["tenant_id"] == tenant_b

        next_iso = (now + timedelta(hours=1)).isoformat()
        update_resp = await async_client.put(
            f"/api/v1/sbom/schedules/{sched_id}",
            headers=headers,
            json={
                "last_run_at": now.isoformat(),
                "next_run_at": next_iso,
            },
        )
        assert update_resp.status_code == 200, (await update_resp.get_data()).decode()

    @pytest.mark.asyncio
    @patch("apps.api.services.sbom.vulnerability.nvd_sync.NVDSyncService")
    async def test_nvd_sync_allows_svc_scanner_rejects_integrations_and_observer(
        self, mock_nvd_cls, async_client, app, generate_token
    ):
        mock_nvd = AsyncMock()
        mock_nvd.sync_vulnerabilities = AsyncMock(
            return_value={"processed": 2, "updated": 1, "skipped": 1, "errors": 0}
        )
        mock_nvd_cls.return_value = mock_nvd

        scan_token = _mint_worker_token(app, sub="svc-scanner", tenant="system")
        ok_resp = await async_client.post(
            "/api/v1/vulnerabilities/nvd-sync",
            headers={"Authorization": f"Bearer {scan_token}"},
            json={"max_vulns": 500},
        )
        assert ok_resp.status_code == 202, (await ok_resp.get_data()).decode()

        # svc-integrations cannot carry sbom:write or vulnerabilities:write
        int_token = _mint_worker_token(
            app,
            sub="svc-integrations",
            tenant="system",
            scope=["discovery:write"],
        )
        forbid_int = await async_client.post(
            "/api/v1/vulnerabilities/nvd-sync",
            headers={"Authorization": f"Bearer {int_token}"},
            json={"max_vulns": 500},
        )
        assert forbid_int.status_code == 403

        # Regular observer user with sbom:write scope still blocked by @role_required("admin")
        with patch("apps.api.auth.decorators.get_current_user") as mock_get_user:
            mock_get_user.return_value = MagicMock(
                id=99,
                is_superuser=False,
                portal_role="observer",
                get=lambda k, d=None: "observer" if k == "portal_role" else d,
            )
            user_tok = generate_token(tenant_id=1, scopes=["sbom:write"])
            forbid_user = await async_client.post(
                "/api/v1/vulnerabilities/nvd-sync",
                headers={"Authorization": f"Bearer {user_tok}"},
                json={"max_vulns": 500},
            )
        assert forbid_user.status_code == 403

    @pytest.mark.asyncio
    async def test_human_token_with_system_tenant_is_rejected_on_pending_routes(
        self, async_client, app
    ):
        secret = app.config.get("JWT_SECRET_KEY") or app.config.get("SECRET_KEY")
        algorithm = app.config.get("JWT_ALGORITHM", "HS256")
        now = datetime.now(UTC)
        forged_human = jwt.encode(
            {
                "sub": "1",
                "type": "access",
                "tenant": "system",
                "scope": ["discovery:read", "sbom:read"],
                "roles": ["admin"],
                "iat": now,
                "exp": now + timedelta(minutes=5),
            },
            secret,
            algorithm=algorithm,
        )
        with patch("apps.api.auth.decorators.get_current_user") as mock_user:
            mock_user.return_value = MagicMock(
                id=1, is_superuser=False, tenant_id=1, token_type="access"
            )
            resp = await async_client.get(
                "/api/v1/discovery/jobs/pending",
                headers={"Authorization": f"Bearer {forged_human}"},
            )
            assert resp.status_code == 400
