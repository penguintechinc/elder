"""Unit tests for Remote Workers fleet registry, auth, heartbeats, and migration 051."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import httpx
import jwt
import pytest
import sqlalchemy as sa
from penguin_dal import DB as DAL
from quart import Quart, g, jsonify

from alembic.migration import MigrationContext
from alembic.operations import Operations
from apps.api.api.v1 import admin_workers as admin_workers_routes
from apps.api.api.v1.admin_workers import compute_worker_status
from apps.api.auth.jwt_handler import (
    SERVICE_WORKER_ALLOWED_SCOPES,
    ServiceWorkerIdentity,
    get_current_user,
    hash_worker_token,
    is_system_service_worker,
    verify_token,
)
from apps.api.models.base import Base
from apps.api.models.identity import Identity  # noqa: F401
from apps.api.models.remote_workers import RemoteWorker  # noqa: F401
from apps.api.models.tenant import Tenant  # noqa: F401
from apps.runworker.k8s_runner import K8sRunner
from apps.runworker.main import (
    RunWorkerConfig,
    RunWorkerService,
    _mint_service_worker_jwt,
)

JWT_SECRET = "test-remote-workers-jwt-secret-key-32b"


def _make_user_jwt(
    *,
    user_id: int,
    tenant_id: int = 1,
    role: str = "admin",
    scopes: list[str] | None = None,
) -> str:
    now = datetime.now(UTC)
    if scopes is None:
        scopes = (
            ["settings:read", "settings:write"]
            if role == "admin"
            else ["entities:read"]
        )
    payload = {
        "sub": str(user_id),
        "username": f"user_{user_id}",
        "type": "access",
        "iat": now,
        "exp": now + timedelta(hours=1),
        "tenant": str(tenant_id),
        "roles": [role],
        "scope": scopes,
        "iss": "elder-api",
        "aud": "elder-api",
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def _make_service_worker_jwt(sub: str = "svc-runworker") -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": sub,
        "type": "service_worker",
        "tenant": "system",
        "scope": sorted(SERVICE_WORKER_ALLOWED_SCOPES[sub]),
        "iss": "elder-api",
        "aud": "elder-api",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=300)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


@pytest.fixture
def sqlite_db(tmp_path: Path):
    """Create an isolated SQLite DB with tenants, identities, and remote_workers."""
    db_file = tmp_path / "remote_workers.db"
    db_url = f"sqlite:///{db_file}"
    engine = sa.create_engine(db_url)
    tables = [
        Base.metadata.tables["tenants"],
        Base.metadata.tables["identity_groups"],
        Base.metadata.tables["identities"],
        Base.metadata.tables["remote_workers"],
    ]
    Base.metadata.create_all(engine, tables=tables)
    engine.dispose()

    db = DAL(db_url, pool_size=1)
    now = datetime.now(UTC)
    db.tenants.insert(
        id=1,
        name="Tenant One",
        slug="tenant-one",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.identities.insert(
        id=1,
        tenant_id=1,
        username="admin_user",
        email="admin@example.test",
        identity_type="human",
        auth_provider="local",
        portal_role="admin",
        is_active=True,
        is_superuser=False,
        mfa_enabled=False,
        must_change_password=False,
        created_at=now,
        updated_at=now,
    )
    db.identities.insert(
        id=2,
        tenant_id=1,
        username="observer_user",
        email="observer@example.test",
        identity_type="human",
        auth_provider="local",
        portal_role="observer",
        is_active=True,
        is_superuser=False,
        mfa_enabled=False,
        must_change_password=False,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    yield db
    db.close()


@pytest.fixture
def app(sqlite_db):
    """Build a minimal Quart app with admin_workers blueprint and populate_claims."""
    test_app = Quart(__name__)
    test_app.config["TESTING"] = True
    test_app.config["SECRET_KEY"] = JWT_SECRET
    test_app.config["JWT_SECRET_KEY"] = JWT_SECRET
    test_app.config["JWT_ALGORITHM"] = "HS256"
    test_app.config["JWT_ISSUER"] = "elder-api"
    test_app.config["JWT_AUDIENCE"] = "elder-api"
    test_app.config["ELDER_PUBLIC_URL"] = "https://elder.example.test"
    test_app.db = sqlite_db

    @test_app.before_request
    async def populate_claims():
        from apps.api.auth.jwt_handler import get_token_from_header

        token = get_token_from_header()
        if not token:
            return
        payload = verify_token(token)
        if not payload:
            return
        g.claims = {
            "sub": payload.get("sub", ""),
            "tenant": payload.get("tenant", ""),
            "roles": payload.get("roles", []),
            "scope": payload.get("scope", []),
            "type": payload.get("type", "access"),
            "remote_worker_id": payload.get("remote_worker_id"),
            "remote_worker_db_id": payload.get("remote_worker_db_id"),
        }

    test_app.register_blueprint(
        admin_workers_routes.bp, url_prefix="/api/v1/admin/workers"
    )

    @test_app.route("/api/v1/test/whoami-worker", methods=["GET"])
    async def whoami_worker():
        user = get_current_user()
        if user is None:
            return jsonify({"error": "unauthorized"}), 401
        return (
            jsonify(
                {
                    "username": user.username,
                    "token_type": getattr(user, "token_type", None),
                    "scopes": getattr(user, "scopes", []),
                    "is_system_service_worker": is_system_service_worker(),
                    "remote_worker_id": getattr(user, "remote_worker_id", None),
                }
            ),
            200,
        )

    return test_app


class TestComputeWorkerStatus:
    """Verify liveness status calculation for online, stale, offline, and revoked."""

    def test_revoked_when_revoked_at_set(self) -> None:
        now = datetime.now(UTC)
        row = SimpleNamespace(
            revoked_at=now - timedelta(seconds=10),
            is_enabled=True,
            last_heartbeat_at=now,
            config_json={},
        )
        assert compute_worker_status(row, now) == "revoked"

    def test_revoked_when_disabled(self) -> None:
        now = datetime.now(UTC)
        row = SimpleNamespace(
            revoked_at=None,
            is_enabled=False,
            last_heartbeat_at=now,
            config_json={},
        )
        assert compute_worker_status(row, now) == "revoked"

    def test_offline_when_never_heartbeated(self) -> None:
        now = datetime.now(UTC)
        row = SimpleNamespace(
            revoked_at=None,
            is_enabled=True,
            last_heartbeat_at=None,
            config_json={},
        )
        assert compute_worker_status(row, now) == "offline"

    def test_default_threshold_120s_boundaries(self) -> None:
        now = datetime(2026, 11, 23, 22, 0, 0, tzinfo=UTC)
        # Default poll_interval=30 -> threshold = max(2*30+60, 120) = 120s
        online_row = SimpleNamespace(
            revoked_at=None,
            is_enabled=True,
            last_heartbeat_at=now - timedelta(seconds=120),
            config_json={},
        )
        stale_row = SimpleNamespace(
            revoked_at=None,
            is_enabled=True,
            last_heartbeat_at=now - timedelta(seconds=200),
            config_json={},
        )
        stale_edge_row = SimpleNamespace(
            revoked_at=None,
            is_enabled=True,
            last_heartbeat_at=now - timedelta(seconds=360),
            config_json={},
        )
        offline_row = SimpleNamespace(
            revoked_at=None,
            is_enabled=True,
            last_heartbeat_at=now - timedelta(seconds=361),
            config_json={},
        )
        assert compute_worker_status(online_row, now) == "online"
        assert compute_worker_status(stale_row, now) == "stale"
        assert compute_worker_status(stale_edge_row, now) == "stale"
        assert compute_worker_status(offline_row, now) == "offline"

    def test_custom_poll_interval_scales_threshold(self) -> None:
        now = datetime(2026, 11, 23, 22, 0, 0, tzinfo=UTC)
        # poll_interval=60 -> threshold = max(2*60+60, 120) = 180s
        online_row = SimpleNamespace(
            revoked_at=None,
            is_enabled=True,
            last_heartbeat_at=now - timedelta(seconds=175),
            config_json={"poll_interval_seconds": 60},
        )
        stale_row = SimpleNamespace(
            revoked_at=None,
            is_enabled=True,
            last_heartbeat_at=now - timedelta(seconds=400),
            config_json={"poll_interval_seconds": 60},
        )
        offline_row = SimpleNamespace(
            revoked_at=None,
            is_enabled=True,
            last_heartbeat_at=now - timedelta(seconds=541),
            config_json={"poll_interval_seconds": 60},
        )
        assert compute_worker_status(online_row, now) == "online"
        assert compute_worker_status(stale_row, now) == "stale"
        assert compute_worker_status(offline_row, now) == "offline"


class TestAdminWorkersCRUDAndAuth:
    """End-to-end blueprint tests for onboarding, listing, updating, rotation, and offboarding."""

    def test_onboard_one_time_token_and_hash_storage(self, app, sqlite_db) -> None:
        admin_token = _make_user_jwt(user_id=1, role="admin")

        async def _run() -> dict[str, Any]:
            client = app.test_client()
            resp = await client.post(
                "/api/v1/admin/workers",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={
                    "name": "DAL2 Remote RunWorker",
                    "worker_type": "runworker",
                    "cluster": "dal2-remote",
                    "config": {"poll_interval_seconds": 15, "max_concurrent": 8},
                    "capabilities": {"runtimes": ["python", "nodejs"]},
                },
            )
            assert resp.status_code == 201
            return await resp.get_json()

        data = asyncio.run(_run())
        token = data["token"]
        worker = data["worker"]
        onboarding = data["onboarding"]

        assert token.startswith("ewk_")
        assert worker["token_prefix"] == token[:12]
        assert "token_hash" not in worker
        assert worker["worker_type"] == "runworker"
        assert worker["cluster"] == "dal2-remote"
        assert worker["status"] == "offline"
        assert onboarding["docker_env"]["ELDER_INTERNAL_TOKEN"] == token
        assert onboarding["docker_env"]["ELDER_API_URL"] == "https://elder.example.test"
        assert onboarding["docker_env"]["RUNS_WORKER_CLUSTER"] == "dal2-remote"
        assert any(f"worker.token={token}" == s for s in onboarding["helm_set"])

        # Verify DB stores ONLY SHA-256 hash, never plaintext token
        row = sqlite_db(sqlite_db.remote_workers.id == worker["id"]).select().first()
        assert row is not None
        assert row.token_hash == hashlib.sha256(token.encode("utf-8")).hexdigest()
        assert row.token_hash != token
        assert row.token_prefix == token[:12]

    def test_scope_and_validation_enforcement(self, app) -> None:
        observer_token = _make_user_jwt(user_id=2, role="observer")
        admin_token = _make_user_jwt(user_id=1, role="admin")

        async def _run() -> None:
            client = app.test_client()
            # Unauthenticated -> 401
            r_unauth = await client.get("/api/v1/admin/workers")
            assert r_unauth.status_code == 401

            # Observer missing settings:read / settings:write -> 403
            r_obs_get = await client.get(
                "/api/v1/admin/workers",
                headers={"Authorization": f"Bearer {observer_token}"},
            )
            assert r_obs_get.status_code == 403

            r_obs_post = await client.post(
                "/api/v1/admin/workers",
                headers={"Authorization": f"Bearer {observer_token}"},
                json={"name": "Nope", "worker_type": "runworker"},
            )
            assert r_obs_post.status_code == 403

            # Invalid worker_type -> 400
            r_bad_type = await client.post(
                "/api/v1/admin/workers",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={"name": "Bad", "worker_type": "unknown"},
            )
            assert r_bad_type.status_code == 400

        asyncio.run(_run())

    def test_heartbeat_provisioned_token_live_config_and_jwt_handler(self, app) -> None:
        admin_token = _make_user_jwt(user_id=1, role="admin")

        async def _run() -> None:
            client = app.test_client()
            create_resp = await client.post(
                "/api/v1/admin/workers",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={
                    "name": "Edge RunWorker",
                    "worker_type": "runworker",
                    "cluster": "edge-west",
                    "config": {"max_concurrent": 4},
                },
            )
            assert create_resp.status_code == 201
            created = await create_resp.get_json()
            worker_id = created["worker"]["worker_id"]
            worker_pk = created["worker"]["id"]
            token = created["token"]

            # Provisioned ewk_ token synthesizes ServiceWorkerIdentity via jwt_handler
            whoami_resp = await client.get(
                "/api/v1/test/whoami-worker",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert whoami_resp.status_code == 200
            whoami = await whoami_resp.get_json()
            assert whoami["username"] == "svc-runworker"
            assert whoami["token_type"] == "service_worker"
            assert whoami["is_system_service_worker"] is True
            assert whoami["remote_worker_id"] == worker_id
            assert "runs:invoke" in whoami["scopes"]

            # Update live config via PUT
            put_resp = await client.put(
                f"/api/v1/admin/workers/{worker_pk}",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={"config": {"max_concurrent": 12, "poll_interval_seconds": 10}},
            )
            assert put_resp.status_code == 200

            # Heartbeat returns live config and transitions status to online
            hb_resp = await client.post(
                "/api/v1/admin/workers/heartbeat",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "hostname": "edge-node-01",
                    "version": "4.0.1",
                    "capabilities": {"runner_backend": "sandbox"},
                },
            )
            assert hb_resp.status_code == 200
            hb = await hb_resp.get_json()
            assert hb["ok"] is True
            assert hb["status"] == "active"
            assert hb["worker_id"] == worker_id
            assert hb["config"] == {"max_concurrent": 12, "poll_interval_seconds": 10}

            # GET worker shows online status and updated heartbeat metadata
            get_resp = await client.get(
                f"/api/v1/admin/workers/{worker_pk}",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert get_resp.status_code == 200
            fetched = (await get_resp.get_json())["worker"]
            assert fetched["status"] == "online"
            assert fetched["hostname"] == "edge-node-01"
            assert fetched["version"] == "4.0.1"
            assert fetched["capabilities"] == {"runner_backend": "sandbox"}

        asyncio.run(_run())

    def test_heartbeat_in_cluster_service_worker_jwt_auto_upserts(self, app) -> None:
        admin_token = _make_user_jwt(user_id=1, role="admin")
        sw_jwt = _make_service_worker_jwt("svc-runworker")
        scanner_jwt = _make_service_worker_jwt("svc-scanner")

        async def _run() -> None:
            client = app.test_client()
            hb1 = await client.post(
                "/api/v1/admin/workers/heartbeat",
                headers={"Authorization": f"Bearer {sw_jwt}"},
                json={
                    "worker_id": "svc-runworker-pod-abc",
                    "hostname": "svc-runworker-pod-abc",
                    "cluster": "dal2-alpha",
                    "version": "4.0.0",
                    "capabilities": {"runner_backend": "k8s_job"},
                },
            )
            assert hb1.status_code == 200
            body1 = await hb1.get_json()
            assert body1["ok"] is True
            assert body1["worker_id"] == "svc-runworker-pod-abc"

            hb2 = await client.post(
                "/api/v1/admin/workers/heartbeat",
                headers={"Authorization": f"Bearer {scanner_jwt}"},
                json={
                    "worker_id": "svc-scanner-pod-xyz",
                    "hostname": "svc-scanner-pod-xyz",
                    "cluster": "dal2-alpha",
                },
            )
            assert hb2.status_code == 200

            # Both appear as online in GET /api/v1/admin/workers
            list_resp = await client.get(
                "/api/v1/admin/workers?status=online&cluster=dal2-alpha",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert list_resp.status_code == 200
            listing = await list_resp.get_json()
            assert listing["total"] == 2
            assert listing["counts_by_status"]["online"] == 2
            types = {item["worker_type"] for item in listing["items"]}
            assert types == {"runworker", "scanner"}

        asyncio.run(_run())

    def test_rotate_token_and_offboard_revoked_worker_403(self, app, sqlite_db) -> None:
        admin_token = _make_user_jwt(user_id=1, role="admin")

        async def _run() -> None:
            client = app.test_client()
            create_resp = await client.post(
                "/api/v1/admin/workers",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={"name": "Scanner West", "worker_type": "scanner"},
            )
            assert create_resp.status_code == 201
            created = await create_resp.get_json()
            worker_pk = created["worker"]["id"]
            old_token = created["token"]

            # Scanner ewk_ token synthesizes svc-scanner identity
            whoami_resp = await client.get(
                "/api/v1/test/whoami-worker",
                headers={"Authorization": f"Bearer {old_token}"},
            )
            assert whoami_resp.status_code == 200
            assert (await whoami_resp.get_json())["username"] == "svc-scanner"

            # Rotate token
            rot_resp = await client.post(
                f"/api/v1/admin/workers/{worker_pk}/rotate-token",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert rot_resp.status_code == 200
            rotated = await rot_resp.get_json()
            new_token = rotated["token"]
            assert new_token.startswith("ewk_")
            assert new_token != old_token
            assert rotated["worker"]["token_prefix"] == new_token[:12]

            # Old token rejected on heartbeat (401) and auth (401)
            old_hb = await client.post(
                "/api/v1/admin/workers/heartbeat",
                headers={"Authorization": f"Bearer {old_token}"},
                json={},
            )
            assert old_hb.status_code == 401

            # New token succeeds on heartbeat
            new_hb = await client.post(
                "/api/v1/admin/workers/heartbeat",
                headers={"Authorization": f"Bearer {new_token}"},
                json={"hostname": "scanner-west-1"},
            )
            assert new_hb.status_code == 200

            # Offboard / revoke via DELETE
            del_resp = await client.delete(
                f"/api/v1/admin/workers/{worker_pk}",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert del_resp.status_code == 200
            del_data = await del_resp.get_json()
            assert del_data["revoked"] is True
            assert del_data["worker"]["status"] == "revoked"
            assert del_data["worker"]["is_enabled"] is False
            assert del_data["worker"]["revoked_at"] is not None

            # DB token_hash must be cleared
            row = sqlite_db(sqlite_db.remote_workers.id == worker_pk).select().first()
            assert row.token_hash is None

            # Revoked worker heartbeat rejected with 403!
            revoked_hb = await client.post(
                "/api/v1/admin/workers/heartbeat",
                headers={"Authorization": f"Bearer {new_token}"},
                json={"hostname": "scanner-west-1"},
            )
            assert revoked_hb.status_code == 403

            # Revoked worker token rejected by jwt_handler
            revoked_whoami = await client.get(
                "/api/v1/test/whoami-worker",
                headers={"Authorization": f"Bearer {new_token}"},
            )
            assert revoked_whoami.status_code == 401

            # Rotating a revoked worker returns 409
            rot_revoked = await client.post(
                f"/api/v1/admin/workers/{worker_pk}/rotate-token",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert rot_revoked.status_code == 409

        asyncio.run(_run())

    def test_disabled_worker_heartbeat_rejected_with_403(self, app) -> None:
        admin_token = _make_user_jwt(user_id=1, role="admin")

        async def _run() -> None:
            client = app.test_client()
            created = await (
                await client.post(
                    "/api/v1/admin/workers",
                    headers={"Authorization": f"Bearer {admin_token}"},
                    json={"name": "Disabled Worker", "worker_type": "runworker"},
                )
            ).get_json()
            worker_pk = created["worker"]["id"]
            token = created["token"]

            put_resp = await client.put(
                f"/api/v1/admin/workers/{worker_pk}",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={"is_enabled": False},
            )
            assert put_resp.status_code == 200
            assert (await put_resp.get_json())["worker"]["status"] == "revoked"

            hb_resp = await client.post(
                "/api/v1/admin/workers/heartbeat",
                headers={"Authorization": f"Bearer {token}"},
                json={},
            )
            assert hb_resp.status_code == 403

        asyncio.run(_run())


class TestRunWorkerHeartbeatSender:
    """Verify svc-runworker best-effort heartbeat posting."""

    def test_send_heartbeat_posts_expected_payload(self, tmp_path: Path) -> None:
        captured: list[tuple[str, dict[str, Any], str]] = []

        def handler(req: httpx.Request) -> httpx.Response:
            captured.append(
                (
                    req.url.path,
                    httpx.Response(200, content=req.content).json(),
                    req.headers.get("authorization", ""),
                )
            )
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "status": "active",
                    "worker_id": "rw-1",
                    "config": {},
                },
            )

        cfg = RunWorkerConfig(
            elder_api_url="https://elder.example.test",
            elder_internal_token="ewk_test_token_value",
            worker_id="rw-1",
            worker_cluster="dal2-remote",
            health_file=str(tmp_path / "rw.healthy"),
        )

        async def _run() -> dict[str, Any] | None:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
            ) as client:
                service = RunWorkerService(
                    cfg, runner=MagicMock(spec=K8sRunner), http_client=client
                )
                return await service.send_heartbeat()

        result = asyncio.run(_run())
        assert result is not None
        assert result["ok"] is True
        assert len(captured) == 1
        path, body, auth_hdr = captured[0]
        assert path == "/api/v1/admin/workers/heartbeat"
        assert body["worker_id"] == "rw-1"
        assert body["worker_type"] == "runworker"
        assert body["cluster"] == "dal2-remote"
        assert "capabilities" in body
        assert auth_hdr == "Bearer ewk_test_token_value"

    def test_send_heartbeat_swallows_errors_without_raising(
        self, tmp_path: Path
    ) -> None:
        def failing_handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"error": "hub-api restarting"})

        cfg = RunWorkerConfig(
            elder_api_url="https://elder.example.test",
            jwt_secret_key=JWT_SECRET,
            worker_id="rw-err",
            health_file=str(tmp_path / "rw.healthy"),
        )

        async def _run() -> dict[str, Any] | None:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(failing_handler)
            ) as client:
                service = RunWorkerService(
                    cfg, runner=MagicMock(spec=K8sRunner), http_client=client
                )
                return await service.send_heartbeat()

        # Must return None and never raise
        assert asyncio.run(_run()) is None


class TestMigration051SQLite:
    """Verify Alembic migration 051_remote_workers upgrade and downgrade on SQLite."""

    def test_upgrade_and_downgrade(self, tmp_path: Path) -> None:
        migration_path = (
            Path(__file__).resolve().parents[2]
            / "alembic"
            / "versions"
            / "051_remote_workers.py"
        )
        spec = importlib.util.spec_from_file_location("mig_051", migration_path)
        assert spec is not None and spec.loader is not None
        mig_051 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mig_051)

        assert mig_051.revision == "051"
        assert mig_051.down_revision == "050"

        db_url = f"sqlite:///{tmp_path / 'mig_051.db'}"
        engine = sa.create_engine(db_url)
        # Create referenced parent tables for FK definitions
        Base.metadata.create_all(
            engine,
            tables=[
                Base.metadata.tables["tenants"],
                Base.metadata.tables["identity_groups"],
                Base.metadata.tables["identities"],
            ],
        )

        with engine.begin() as conn:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                mig_051.upgrade()
                # Re-running upgrade must be idempotent
                mig_051.upgrade()

            inspector = sa.inspect(conn)
            assert "remote_workers" in inspector.get_table_names()
            cols = {c["name"] for c in inspector.get_columns("remote_workers")}
            expected_cols = {
                "id",
                "worker_id",
                "tenant_id",
                "worker_type",
                "name",
                "cluster",
                "token_hash",
                "token_prefix",
                "config_json",
                "capabilities_json",
                "hostname",
                "version",
                "is_enabled",
                "last_heartbeat_at",
                "revoked_at",
                "created_by_identity_id",
                "created_at",
                "updated_at",
            }
            assert expected_cols.issubset(cols)
            idx_names = {i["name"] for i in inspector.get_indexes("remote_workers")}
            assert "ix_remote_workers_token_hash" in idx_names
            assert "ix_remote_workers_type_cluster" in idx_names

            with Operations.context(ctx):
                mig_051.downgrade()

            inspector_after = sa.inspect(conn)
            assert "remote_workers" not in inspector_after.get_table_names()

        engine.dispose()


def test_helper_exports_smoke() -> None:
    """Quick sanity check on helper symbols imported from jwt_handler and runworker."""
    assert len(hash_worker_token("ewk_abc")) == 64
    ident = ServiceWorkerIdentity("svc-runworker", None, ["runs:invoke"])
    assert ident.username == "svc-runworker"
    tok = _mint_service_worker_jwt(JWT_SECRET)
    assert tok.count(".") == 2
