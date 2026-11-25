"""Unit tests for CodeRun scoped permissions, egress domain validation, ephemeral JWTs, routes, and migration 050."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jwt
import pytest
import sqlalchemy as sa
from penguin_dal import DB
from quart import Quart, g

import apps.api.models.identity  # noqa: F401
import apps.api.models.tenant  # noqa: F401
from alembic.migration import MigrationContext
from alembic.operations import Operations
from apps.api.auth.jwt_handler import (
    MAX_CODERUN_TTL_SECONDS,
    CodeRunIdentity,
    generate_coderun_token,
    get_current_user,
    verify_token,
)
from apps.api.models.base import Base
from apps.api.modules.runs.models.runs import (  # noqa: F401
    RunExecution,
    RunFunction,
    RunFunctionVersion,
)
from apps.api.modules.runs.permissions import (
    ALL_GRANTABLE_SCOPES,
    CODERUN_PERMISSION_CATALOG,
    EgressDomainValidationError,
    ScopeValidationError,
    build_permission_catalog_payload,
    validate_egress_domains,
    validate_granted_scopes,
)
from apps.api.modules.runs.routes.runs import bp as runs_bp

REPO_ROOT = Path(__file__).resolve().parents[2]
MIG_047_PATH = REPO_ROOT / "alembic" / "versions" / "047_runs_serverless_functions.py"
MIG_048_PATH = REPO_ROOT / "alembic" / "versions" / "048_runs_s3_artifact_storage.py"
MIG_050_PATH = REPO_ROOT / "alembic" / "versions" / "050_coderun_permissions.py"

TEST_JWT_SECRET = "test-coderun-jwt-secret-key-at-least-32-bytes"


def _load_migration(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_test_app(tmp_path: Path) -> Quart:
    """Build a minimal Quart app with SQLite penguin-dal and the runs blueprint."""
    app = Quart(__name__)
    app.config["SECRET_KEY"] = TEST_JWT_SECRET
    app.config["JWT_SECRET_KEY"] = TEST_JWT_SECRET
    app.config["JWT_ALGORITHM"] = "HS256"
    app.config["JWT_ISSUER"] = "elder-api"
    app.config["JWT_AUDIENCE"] = "elder-api"

    db_path = tmp_path / "coderun_test.db"
    db_url = f"sqlite:///{db_path}"
    engine = sa.create_engine(db_url)
    Base.metadata.create_all(engine)
    engine.dispose()

    db = DB(db_url)
    now = datetime.now(UTC)
    db.tenants.insert(
        name="Tenant One",
        slug="tenant-one",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.tenants.insert(
        name="Tenant Two",
        slug="tenant-two",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    app.db = db  # type: ignore[attr-defined]
    app.register_blueprint(runs_bp, url_prefix="/api/v1/runs")
    return app


def _mint_service_worker_token(
    app: Quart,
    *,
    sub: str = "svc-runworker",
    tenant: str = "system",
    scopes: list[str] | None = None,
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": sub,
        "type": "service_worker",
        "tenant": tenant,
        "scope": scopes or ["runs:read", "runs:write", "runs:invoke"],
        "roles": ["service_worker"],
        "iat": now,
        "exp": now + timedelta(seconds=300),
        "iss": app.config["JWT_ISSUER"],
        "aud": app.config["JWT_AUDIENCE"],
    }
    return jwt.encode(
        payload, app.config["JWT_SECRET_KEY"], algorithm=app.config["JWT_ALGORITHM"]
    )


class TestPermissionCatalog:
    """Verify canonical grantable scope catalog structure and eligibility."""

    def test_catalog_groups_and_scopes(self) -> None:
        assert set(CODERUN_PERMISSION_CATALOG.keys()) == {
            "resources",
            "integrations",
            "workstreams",
            "work_management",
        }
        assert CODERUN_PERMISSION_CATALOG["resources"] == (
            "entities:read",
            "entities:write",
            "compute:read",
            "compute:write",
            "networking:read",
            "networking:write",
            "organizations:read",
            "organizations:write",
            "dependencies:read",
            "dependencies:write",
            "software:read",
            "software:write",
            "services:read",
            "services:write",
        )
        assert CODERUN_PERMISSION_CATALOG["integrations"] == (
            "integrations:read",
            "integrations:write",
            "integrations:sync",
        )
        assert CODERUN_PERMISSION_CATALOG["workstreams"] == (
            "webhooks:read",
            "webhooks:write",
            "streams:read",
            "streams:execute",
            "runs:read",
            "runs:invoke",
        )
        assert CODERUN_PERMISSION_CATALOG["work_management"] == (
            "issues:read",
            "issues:write",
            "projects:read",
            "projects:write",
        )
        assert len(ALL_GRANTABLE_SCOPES) == 27
        # High-privilege admin/secret scopes must never be in the grantable catalog
        assert "users:admin" not in ALL_GRANTABLE_SCOPES
        assert "secrets:read" not in ALL_GRANTABLE_SCOPES
        assert "runs:write" not in ALL_GRANTABLE_SCOPES

    def test_build_catalog_payload_eligibility(self) -> None:
        regular = build_permission_catalog_payload(
            ["entities:read", "runs:invoke"], is_superuser=False
        )
        assert regular["eligible_scopes"] == ["entities:read", "runs:invoke"]
        resources_map = {
            e["scope"]: e["eligible"] for e in regular["groups"]["resources"]
        }
        assert resources_map["entities:read"] is True
        assert resources_map["entities:write"] is False

        admin = build_permission_catalog_payload([], is_superuser=True)
        assert set(admin["eligible_scopes"]) == ALL_GRANTABLE_SCOPES


class TestScopeValidation:
    """Verify validate_granted_scopes rejects unknown scopes (400) and escalation (403)."""

    def test_valid_subset_deduplicates_preserving_order(self) -> None:
        result = validate_granted_scopes(
            ["entities:read", "issues:write", "entities:read"],
            caller_scopes=["entities:read", "issues:write", "runs:write"],
            is_superuser=False,
        )
        assert result == ["entities:read", "issues:write"]

    def test_none_and_empty_return_empty_list(self) -> None:
        assert validate_granted_scopes(None, [], False) == []
        assert validate_granted_scopes([], [], False) == []

    @pytest.mark.parametrize("bad_input", ["entities:read", 123, {"a": 1}, [""], [42]])
    def test_malformed_input_raises_400(self, bad_input: Any) -> None:
        with pytest.raises(ScopeValidationError) as exc_info:
            validate_granted_scopes(bad_input, ["entities:read"], is_superuser=True)
        assert exc_info.value.status_code == 400

    @pytest.mark.parametrize(
        "ungrantable",
        ["users:admin", "secrets:read", "runs:write", "*:admin", "nonexistent:read"],
    )
    def test_unknown_or_ungrantable_scope_raises_400(self, ungrantable: str) -> None:
        with pytest.raises(ScopeValidationError) as exc_info:
            validate_granted_scopes(
                [ungrantable], caller_scopes=[ungrantable], is_superuser=True
            )
        assert exc_info.value.status_code == 400
        assert ungrantable in exc_info.value.invalid_scopes

    def test_privilege_escalation_raises_403(self) -> None:
        with pytest.raises(ScopeValidationError) as exc_info:
            validate_granted_scopes(
                ["entities:read", "entities:write"],
                caller_scopes=["entities:read", "runs:write"],
                is_superuser=False,
            )
        assert exc_info.value.status_code == 403
        assert exc_info.value.missing_scopes == ["entities:write"]

    def test_superuser_can_grant_any_catalog_scope(self) -> None:
        result = validate_granted_scopes(
            ["entities:write", "integrations:sync"],
            caller_scopes=[],
            is_superuser=True,
        )
        assert result == ["entities:write", "integrations:sync"]


class TestEgressDomainValidation:
    """Verify validate_egress_domains accepts only valid public DNS hostnames."""

    def test_valid_domains_normalized_and_deduplicated(self) -> None:
        result = validate_egress_domains(
            [
                "API.GitHub.com",
                "hooks.slack.com",
                "api.github.com",
                "sub-1.example.co.uk",
            ]
        )
        assert result == ["api.github.com", "hooks.slack.com", "sub-1.example.co.uk"]

    def test_none_and_empty_return_empty_list(self) -> None:
        assert validate_egress_domains(None) == []
        assert validate_egress_domains([]) == []

    @pytest.mark.parametrize(
        "bad_domain",
        [
            "",
            "   ",
            "https://api.github.com",
            "http://example.com",
            "api.github.com/v1/repos",
            "api.github.com?foo=bar",
            "api.github.com:443",
            "user@api.github.com",
            "*.github.com",
            ".example.com",
            "example.com.",
            "10.0.0.1",
            "172.16.0.5",
            "192.168.1.1",
            "127.0.0.1",
            "169.254.169.254",
            "8.8.8.8",
            "::1",
            "[::1]",
            "localhost",
            "sub.localhost",
            "metadata.google.internal",
            "metadata.goog",
            "hub-api.elder.svc.cluster.local",
            "redis.internal",
            "printer.local",
            "singlelabel",
        ],
    )
    def test_invalid_or_disallowed_domains_rejected(self, bad_domain: str) -> None:
        with pytest.raises(EgressDomainValidationError) as exc_info:
            validate_egress_domains([bad_domain])
        assert exc_info.value.status_code == 400
        assert bad_domain in exc_info.value.invalid_domains

    def test_non_list_rejected(self) -> None:
        with pytest.raises(EgressDomainValidationError) as exc_info:
            validate_egress_domains("api.github.com")
        assert exc_info.value.status_code == 400


class TestCodeRunEphemeralJWT:
    """Verify ephemeral CodeRun JWT minting, verification, expiry, and tenant locking."""

    @pytest.mark.asyncio
    async def test_mint_verify_and_get_current_user(self, tmp_path: Path) -> None:
        app = _make_test_app(tmp_path)
        async with app.app_context():
            token = generate_coderun_token(
                tenant_id=7,
                function_id=42,
                execution_id="exec-abc-123",
                scopes=["entities:read", "issues:write"],
                ttl_seconds=120,
            )
            payload = verify_token(token)
            assert payload is not None
            assert payload["type"] == "coderun"
            assert payload["tenant"] == "7"
            assert payload["function_id"] == 42
            assert payload["execution_id"] == "exec-abc-123"
            assert payload["scope"] == ["entities:read", "issues:write"]
            assert payload["roles"] == ["coderun"]

        async with app.test_request_context(
            "/api/v1/runs", headers={"Authorization": f"Bearer {token}"}
        ):
            identity = get_current_user()
            assert isinstance(identity, CodeRunIdentity)
            assert identity.tenant_id == 7
            assert identity.function_id == 42
            assert identity.execution_id == "exec-abc-123"
            assert identity.scopes == ["entities:read", "issues:write"]
            assert identity.is_superuser is False
            assert identity.token_type == "coderun"
            assert identity.get("tenant_id") == 7

    @pytest.mark.asyncio
    async def test_generate_rejects_invalid_arguments(self, tmp_path: Path) -> None:
        app = _make_test_app(tmp_path)
        async with app.app_context():
            with pytest.raises(ValueError, match="tenant_id"):
                generate_coderun_token(0, 1, "exec-1", ["entities:read"])
            with pytest.raises(ValueError, match="tenant_id"):
                generate_coderun_token(-5, 1, "exec-1", ["entities:read"])
            with pytest.raises(ValueError, match="function_id"):
                generate_coderun_token(1, 0, "exec-1", ["entities:read"])
            with pytest.raises(ValueError, match="execution_id"):
                generate_coderun_token(1, 1, "   ", ["entities:read"])
            with pytest.raises(ValueError, match="TTL"):
                generate_coderun_token(
                    1,
                    1,
                    "exec-1",
                    ["entities:read"],
                    ttl_seconds=MAX_CODERUN_TTL_SECONDS + 1,
                )
            with pytest.raises(ValueError, match="TTL"):
                generate_coderun_token(1, 1, "exec-1", ["entities:read"], ttl_seconds=0)
            with pytest.raises(ValueError, match="At least one grantable scope"):
                generate_coderun_token(1, 1, "exec-1", [])
            with pytest.raises(ValueError, match="not grantable"):
                generate_coderun_token(1, 1, "exec-1", ["secrets:read"])

    @pytest.mark.asyncio
    async def test_verify_rejects_expired_over_ttl_empty_scope_and_bad_tenant(
        self, tmp_path: Path
    ) -> None:
        app = _make_test_app(tmp_path)
        secret = app.config["JWT_SECRET_KEY"]
        alg = app.config["JWT_ALGORITHM"]
        now = datetime.now(UTC)

        def _encode(overrides: dict[str, Any]) -> str:
            base: dict[str, Any] = {
                "sub": "coderun:1:2:exec-1",
                "type": "coderun",
                "iat": now,
                "exp": now + timedelta(seconds=120),
                "tenant": "1",
                "function_id": 2,
                "execution_id": "exec-1",
                "roles": ["coderun"],
                "scope": ["entities:read"],
                "iss": "elder-api",
                "aud": "elder-api",
            }
            base.update(overrides)
            return jwt.encode(base, secret, algorithm=alg)

        async with app.app_context():
            # Expired
            assert (
                verify_token(
                    _encode(
                        {
                            "iat": now - timedelta(seconds=200),
                            "exp": now - timedelta(seconds=10),
                        }
                    )
                )
                is None
            )
            # Over MAX_CODERUN_TTL_SECONDS
            assert (
                verify_token(
                    _encode(
                        {"exp": now + timedelta(seconds=MAX_CODERUN_TTL_SECONDS + 30)}
                    )
                )
                is None
            )
            # Empty scopes
            assert verify_token(_encode({"scope": []})) is None
            # Ungrantable scope
            assert verify_token(_encode({"scope": ["secrets:read"]})) is None
            # Non-positive or system tenant
            assert verify_token(_encode({"tenant": "0"})) is None
            assert verify_token(_encode({"tenant": "-1"})) is None
            assert verify_token(_encode({"tenant": "system"})) is None
            assert verify_token(_encode({"tenant": ""})) is None
            # Wrong issuer
            assert verify_token(_encode({"iss": "other-issuer"})) is None
            # Invalid function_id / execution_id
            assert verify_token(_encode({"function_id": 0})) is None
            assert verify_token(_encode({"execution_id": ""})) is None


class TestCodeRunRoutes:
    """End-to-end route tests for catalog, create/update validation, and claim token minting."""

    @pytest.mark.asyncio
    async def test_catalog_create_update_and_claim_token_minting(
        self, tmp_path: Path
    ) -> None:
        app = _make_test_app(tmp_path)
        active_auth: dict[str, Any] = {
            "user": None,
            "claims": {},
        }

        @app.before_request
        async def _inject_test_auth() -> None:
            if active_auth["user"] is not None:
                g.current_user = active_auth["user"]
                g.claims = dict(active_auth["claims"])

        class _MockUser:
            def __init__(
                self, *, is_superuser: bool = False, tenant_id: int = 1
            ) -> None:
                self.id = 10
                self.username = "tester"
                self.is_active = True
                self.is_superuser = is_superuser
                self.tenant_id = tenant_id

        client = app.test_client()

        # 1. GET /api/v1/runs/permissions/catalog for non-superuser
        active_auth["user"] = _MockUser(is_superuser=False, tenant_id=1)
        active_auth["claims"] = {
            "sub": "10",
            "tenant": "1",
            "scope": ["runs:read", "runs:write", "runs:invoke", "entities:read"],
        }
        resp = await client.get("/api/v1/runs/permissions/catalog")
        assert resp.status_code == 200
        body = await resp.get_json()
        assert set(body["catalog"].keys()) == {
            "resources",
            "integrations",
            "workstreams",
            "work_management",
        }
        assert body["eligible_scopes"] == ["entities:read", "runs:read", "runs:invoke"]

        # 2. POST /api/v1/runs rejects privilege escalation with 403
        resp = await client.post(
            "/api/v1/runs",
            json={
                "name": "escalate-fn",
                "runtime": "python",
                "code": "print('hi')",
                "granted_scopes": ["entities:write"],
            },
        )
        assert resp.status_code == 403

        # 3. POST /api/v1/runs rejects unknown scope with 400
        resp = await client.post(
            "/api/v1/runs",
            json={
                "name": "bad-scope-fn",
                "runtime": "python",
                "code": "print('hi')",
                "granted_scopes": ["secrets:read"],
            },
        )
        assert resp.status_code == 400

        # 4. POST /api/v1/runs rejects invalid egress domain with 400
        resp = await client.post(
            "/api/v1/runs",
            json={
                "name": "bad-domain-fn",
                "runtime": "python",
                "code": "print('hi')",
                "allowed_egress_domains": ["169.254.169.254"],
            },
        )
        assert resp.status_code == 400

        # 5. POST /api/v1/runs succeeds with valid scopes and domains
        resp = await client.post(
            "/api/v1/runs",
            json={
                "name": "good-fn",
                "runtime": "python",
                "code": "print('ok')",
                "timeout_seconds": 60,
                "granted_scopes": ["entities:read"],
                "allowed_egress_domains": ["api.github.com"],
            },
        )
        assert resp.status_code == 201
        created = await resp.get_json()
        func_id = created["id"]
        assert created["granted_scopes"] == ["entities:read"]
        assert created["allowed_egress_domains"] == ["api.github.com"]

        # 6. PUT /api/v1/runs/<id> rejects escalation with 403 and accepts valid update
        resp = await client.put(
            f"/api/v1/runs/{func_id}",
            json={"granted_scopes": ["issues:write"]},
        )
        assert resp.status_code == 403

        resp = await client.put(
            f"/api/v1/runs/{func_id}",
            json={
                "timeout_seconds": 300,
                "granted_scopes": ["entities:read", "runs:read"],
                "allowed_egress_domains": ["hooks.slack.com"],
            },
        )
        assert resp.status_code == 200
        updated = await resp.get_json()
        assert updated["granted_scopes"] == ["entities:read", "runs:read"]
        assert updated["allowed_egress_domains"] == ["hooks.slack.com"]

        # Create a second function with NO granted_scopes
        resp = await client.post(
            "/api/v1/runs",
            json={
                "name": "no-scope-fn",
                "runtime": "python",
                "code": "print('plain')",
                "timeout_seconds": 30,
            },
        )
        assert resp.status_code == 201
        plain_func_id = (await resp.get_json())["id"]

        # Queue executions for both functions
        resp = await client.post(f"/api/v1/runs/{func_id}/invoke", json={})
        assert resp.status_code == 202
        resp = await client.post(f"/api/v1/runs/{plain_func_id}/invoke", json={})
        assert resp.status_code == 202

        # 7. Claim pending executions as svc-runworker
        worker_token = _mint_service_worker_token(app)
        active_auth["user"] = None
        active_auth["claims"] = {}

        @app.before_request
        async def _populate_worker_claims() -> None:
            if active_auth["user"] is not None:
                return
            tok = worker_token
            payload = verify_token(tok)
            assert payload is not None
            g.claims = payload

        resp = await client.get(
            "/api/v1/runs/internal/pending?limit=10",
            headers={"Authorization": f"Bearer {worker_token}"},
        )
        assert resp.status_code == 200
        claimed = await resp.get_json()
        assert claimed["count"] == 2
        by_func = {item["function_id"]: item for item in claimed["items"]}

        scoped_item = by_func[func_id]
        assert scoped_item["granted_scopes"] == ["entities:read", "runs:read"]
        assert scoped_item["allowed_egress_domains"] == ["hooks.slack.com"]
        assert "elder_api_token" in scoped_item
        ephemeral = scoped_item["elder_api_token"]
        async with app.app_context():
            decoded = verify_token(ephemeral)
            assert decoded is not None
            assert decoded["type"] == "coderun"
            assert decoded["tenant"] == "1"
            assert decoded["function_id"] == func_id
            assert decoded["execution_id"] == scoped_item["execution_id"]
            assert decoded["scope"] == ["entities:read", "runs:read"]
            # timeout_seconds=300 -> min(300 + 60, 360) == 360
            assert int(round(decoded["exp"] - decoded["iat"])) == 360

        plain_item = by_func[plain_func_id]
        assert plain_item["granted_scopes"] == []
        assert plain_item["allowed_egress_domains"] == []
        assert "elder_api_token" not in plain_item

    @pytest.mark.asyncio
    async def test_rejects_custom_execution_namespace_on_create_and_update(
        self, tmp_path: Path
    ) -> None:
        app = _make_test_app(tmp_path)

        class _AdminUser:
            id = 1
            username = "admin"
            tenant_id = 1
            is_superuser = True
            is_active = True

        @app.before_request
        async def _inject_admin() -> None:
            g.current_user = _AdminUser()
            g.claims = {
                "sub": "1",
                "tenant": "1",
                "scope": ["*:read", "*:write", "*:admin"],
                "roles": ["admin"],
            }

        client = app.test_client()

        # Creating with a non-default namespace (e.g. kube-system / system) must fail 400
        for bad_ns in ("kube-system", "system", "elder", "default"):
            bad_resp = await client.post(
                "/api/v1/runs",
                json={
                    "name": f"bad-ns-{bad_ns}",
                    "runtime": "python",
                    "code": "def handler(d): return d",
                    "execution_namespace": bad_ns,
                },
            )
            assert bad_resp.status_code == 400, f"expected 400 for {bad_ns}"

        # Creating without execution_namespace pins to operator-managed 'elder-runs'
        ok_resp = await client.post(
            "/api/v1/runs",
            json={
                "name": "safe-ns-fn",
                "runtime": "python",
                "code": "def handler(d): return d",
                "worker_cluster": "eu-west",
            },
        )
        assert ok_resp.status_code == 201
        created = await ok_resp.get_json()
        assert created["execution_namespace"] == "elder-runs"
        assert created["worker_cluster"] == "eu-west"

        # Updating execution_namespace to anything else must also fail 400
        upd_bad = await client.put(
            f"/api/v1/runs/{created['id']}",
            json={"execution_namespace": "kube-system"},
        )
        assert upd_bad.status_code == 400

        # Updating with default or omitted execution_namespace succeeds
        upd_ok = await client.put(
            f"/api/v1/runs/{created['id']}",
            json={"worker_cluster": "us-east", "execution_namespace": "elder-runs"},
        )
        assert upd_ok.status_code == 200
        updated = await upd_ok.get_json()
        assert updated["execution_namespace"] == "elder-runs"
        assert updated["worker_cluster"] == "us-east"


class TestMigration050:
    """Verify Alembic migration 050 upgrades and downgrades cleanly on SQLite."""

    def test_sqlite_upgrade_idempotent_and_downgrade(self) -> None:
        mig_047 = _load_migration("mig_047", MIG_047_PATH)
        mig_048 = _load_migration("mig_048", MIG_048_PATH)
        mig_050 = _load_migration("mig_050", MIG_050_PATH)

        assert mig_050.revision == "050"
        assert mig_050.down_revision == "049"

        engine = sa.create_engine("sqlite:///:memory:")
        with engine.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE tenants (id INTEGER PRIMARY KEY)")
            conn.exec_driver_sql("CREATE TABLE identities (id INTEGER PRIMARY KEY)")
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                mig_047.upgrade()
                mig_048.upgrade()
                mig_050.upgrade()
                # Re-running upgrade should be a safe no-op
                mig_050.upgrade()

            inspector = sa.inspect(conn)
            fn_cols = {c["name"] for c in inspector.get_columns("run_functions")}
            ver_cols = {
                c["name"] for c in inspector.get_columns("run_function_versions")
            }
            assert "granted_scopes_json" in fn_cols
            assert "allowed_egress_domains_json" in fn_cols
            assert "granted_scopes_json" in ver_cols

            with Operations.context(ctx):
                mig_050.downgrade()

            inspector_after = sa.inspect(conn)
            fn_cols_after = {
                c["name"] for c in inspector_after.get_columns("run_functions")
            }
            ver_cols_after = {
                c["name"] for c in inspector_after.get_columns("run_function_versions")
            }
            assert "granted_scopes_json" not in fn_cols_after
            assert "allowed_egress_domains_json" not in fn_cols_after
            assert "granted_scopes_json" not in ver_cols_after


class TestClaimWorkerScopeAndAnomalies:
    """Malformed worker tenants are refused; unusual platform claims are flagged."""

    async def _queue_run(self, client: Any, name: str) -> None:
        """Create a function and queue one execution as the active user."""
        resp = await client.post(
            "/api/v1/runs",
            json={"name": name, "runtime": "python", "code": "print(1)"},
        )
        assert resp.status_code == 201
        func_id = (await resp.get_json())["id"]
        resp = await client.post(f"/api/v1/runs/{func_id}/invoke", json={})
        assert resp.status_code == 202

    @pytest.mark.asyncio
    async def test_tenant_scoped_worker_cannot_claim_and_anomalies_are_flagged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from apps.api.modules.runs.routes import runs as runs_routes

        app = _make_test_app(tmp_path)
        active_auth: dict[str, Any] = {"user": None, "claims": {}, "worker": ""}
        warnings: list[dict[str, Any]] = []
        counted: list[dict[str, Any]] = []
        real_warning = runs_routes.logger.warning

        def _capture(event: str, **fields: Any) -> None:
            if event == "run_claim_anomaly":
                warnings.append(fields)
            real_warning(event, **fields)

        monkeypatch.setattr(runs_routes.logger, "warning", _capture, raising=False)
        monkeypatch.setattr(
            runs_routes._claim_anomaly_counter,
            "add",
            lambda amount, attrs=None: counted.append(dict(attrs or {})),
            raising=False,
        )
        monkeypatch.setattr(runs_routes, "_claim_rate_windows", {})
        monkeypatch.setenv("RUNS_CLAIM_RATE_WARN_THRESHOLD", "2")

        class _MockUser:
            def __init__(self, tenant_id: int) -> None:
                self.id = 10
                self.username = "tester"
                self.is_active = True
                self.is_superuser = False
                self.tenant_id = tenant_id

        @app.before_request
        async def _inject_test_auth() -> None:
            if active_auth["user"] is not None:
                g.current_user = active_auth["user"]
                g.claims = dict(active_auth["claims"])
            elif active_auth["worker"]:
                payload = verify_token(active_auth["worker"])
                if payload is not None:
                    g.claims = payload

        client = app.test_client()

        async def _queue(tenant_id: int, name: str) -> None:
            active_auth["user"] = _MockUser(tenant_id)
            active_auth["claims"] = {
                "sub": "10",
                "tenant": str(tenant_id),
                "scope": ["runs:read", "runs:write", "runs:invoke"],
            }
            await self._queue_run(client, name)
            active_auth["user"] = None
            active_auth["claims"] = {}

        async def _claim(tenant: str, limit: int = 10) -> Any:
            token = _mint_service_worker_token(app, tenant=tenant)
            active_auth["worker"] = token
            return await client.get(
                f"/api/v1/runs/internal/pending?limit={limit}&worker_id=wk-1",
                headers={"Authorization": f"Bearer {token}"},
            )

        await _queue(1, "fn-a")
        await _queue(2, "fn-b")

        # A worker token with a malformed or non-positive tenant is refused.
        # (Well-formed tenant-scoped workers are accepted but confined to their
        # tenant: see TestTenantScopedRunWorker.)
        for tenant in ("", "abc", "0", "-1"):
            resp = await _claim(tenant)
            assert resp.status_code in (401, 403), tenant
        assert warnings == []

        # The platform worker's first claim for each tenant is flagged.
        resp = await _claim("system")
        assert resp.status_code == 200
        body = await resp.get_json()
        assert sorted(item["tenant_id"] for item in body["items"]) == [1, 2]
        assert sorted(w["tenant_id"] for w in warnings) == [1, 2]
        assert {w["reason"] for w in warnings} == {"new_tenant"}
        assert all(w["worker_id"] == "wk-1" for w in warnings)
        assert counted == [{"reason": "new_tenant"}] * 2

        # A tenant it already served is not flagged again; exceeding the
        # per-window claim threshold is.
        warnings.clear()
        counted.clear()
        await _queue(1, "fn-c")
        resp = await _claim("system")
        assert (await resp.get_json())["count"] == 1
        assert [w["reason"] for w in warnings] == ["rate_spike"]
        assert warnings[0]["claims_in_window"] == 3
        assert counted == [{"reason": "rate_spike"}]

    def test_anomaly_check_failure_never_fails_the_claim(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from apps.api.modules.runs.routes import runs as runs_routes

        errors: list[str] = []
        monkeypatch.setattr(
            runs_routes.logger,
            "error",
            lambda event, **fields: errors.append(event),
            raising=False,
        )

        class _BrokenDb:
            def __call__(self, *args: Any, **kwargs: Any) -> Any:
                raise RuntimeError("db down")

            def __getattr__(self, name: str) -> Any:
                raise RuntimeError("db down")

        runs_routes._audit_claim_anomalies(
            _BrokenDb(), "wk-1", None, [{"id": 1, "tenant_id": 1}]
        )
        assert errors == ["run_claim_anomaly_check_failed"]

    @pytest.mark.parametrize(
        ("raw", "expected"), [("", 120), ("abc", 120), ("0", 120), ("7", 7)]
    )
    def test_claim_rate_threshold_parsing(
        self, monkeypatch: pytest.MonkeyPatch, raw: str, expected: int
    ) -> None:
        from apps.api.modules.runs.routes import runs as runs_routes

        monkeypatch.setenv("RUNS_CLAIM_RATE_WARN_THRESHOLD", raw)
        assert runs_routes._claim_rate_warn_threshold() == expected


class TestTenantScopedRunWorker:
    """Tenant-scoped svc-runworker tokens are confined to their own tenant's runs."""

    class _MockUser:
        """Minimal tenant user used to queue executions."""

        def __init__(self, tenant_id: int) -> None:
            self.id = 10
            self.username = "tester"
            self.is_active = True
            self.is_superuser = False
            self.tenant_id = tenant_id

    def _wire(self, app: Quart) -> dict[str, Any]:
        """Install a before_request hook driven by the returned auth state dict."""
        state: dict[str, Any] = {"user": None, "claims": {}, "worker": ""}

        @app.before_request
        async def _inject_test_auth() -> None:
            if state["user"] is not None:
                g.current_user = state["user"]
                g.claims = dict(state["claims"])
            elif state["worker"]:
                payload = verify_token(state["worker"])
                if payload is not None:
                    g.claims = payload

        return state

    async def _queue(
        self, client: Any, state: dict[str, Any], tenant_id: int, name: str
    ) -> str:
        """Create a function in ``tenant_id`` and queue one execution; return its id."""
        state["user"] = self._MockUser(tenant_id)
        state["claims"] = {
            "sub": "10",
            "tenant": str(tenant_id),
            "scope": ["runs:read", "runs:write", "runs:invoke"],
        }
        resp = await client.post(
            "/api/v1/runs",
            json={"name": name, "runtime": "python", "code": "print(1)"},
        )
        assert resp.status_code == 201
        func_id = (await resp.get_json())["id"]
        resp = await client.post(f"/api/v1/runs/{func_id}/invoke", json={})
        assert resp.status_code == 202
        execution_id = (await resp.get_json())["execution_id"]
        state["user"] = None
        state["claims"] = {}
        return execution_id

    def _as_worker(self, app: Quart, state: dict[str, Any], tenant: str) -> str:
        """Switch the request auth to a service worker token for ``tenant``."""
        token = _mint_service_worker_token(app, tenant=tenant)
        state["worker"] = token
        return token

    @pytest.mark.asyncio
    async def test_tenant_worker_claims_only_its_tenant_and_platform_claims_all(
        self, tmp_path: Path
    ) -> None:
        app = _make_test_app(tmp_path)
        state = self._wire(app)
        client = app.test_client()
        await self._queue(client, state, 1, "t1-a")
        await self._queue(client, state, 2, "t2-a")
        await self._queue(client, state, 1, "t1-b")

        token = self._as_worker(app, state, "1")
        resp = await client.get(
            "/api/v1/runs/internal/pending?limit=10&worker_id=wk-t1",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = await resp.get_json()
        assert body["count"] == 2
        assert {item["tenant_id"] for item in body["items"]} == {1}

        # Nothing left for tenant 1; tenant 2's run is untouched by it.
        resp = await client.get(
            "/api/v1/runs/internal/pending?limit=10&worker_id=wk-t1",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert (await resp.get_json())["count"] == 0

        token = self._as_worker(app, state, "system")
        resp = await client.get(
            "/api/v1/runs/internal/pending?limit=10&worker_id=wk-sys",
            headers={"Authorization": f"Bearer {token}"},
        )
        body = await resp.get_json()
        assert body["count"] == 1
        assert body["items"][0]["tenant_id"] == 2

    @pytest.mark.asyncio
    async def test_platform_worker_claims_every_tenant(self, tmp_path: Path) -> None:
        app = _make_test_app(tmp_path)
        state = self._wire(app)
        client = app.test_client()
        await self._queue(client, state, 1, "t1")
        await self._queue(client, state, 2, "t2")

        token = self._as_worker(app, state, "system")
        resp = await client.get(
            "/api/v1/runs/internal/pending?limit=10",
            headers={"Authorization": f"Bearer {token}"},
        )
        body = await resp.get_json()
        assert sorted(item["tenant_id"] for item in body["items"]) == [1, 2]

    @pytest.mark.asyncio
    async def test_cross_tenant_result_and_log_submission_rejected(
        self, tmp_path: Path
    ) -> None:
        app = _make_test_app(tmp_path)
        state = self._wire(app)
        client = app.test_client()
        execution_id = await self._queue(client, state, 1, "t1")

        token = self._as_worker(app, state, "1")
        resp = await client.get(
            "/api/v1/runs/internal/pending?limit=1",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert (await resp.get_json())["count"] == 1

        # Tenant 2's worker cannot write logs or a result for tenant 1's run.
        token = self._as_worker(app, state, "2")
        resp = await client.post(
            f"/api/v1/runs/internal/executions/{execution_id}/logs",
            headers={"Authorization": f"Bearer {token}"},
            json={"logs": "forged"},
        )
        assert resp.status_code == 404
        resp = await client.post(
            f"/api/v1/runs/internal/executions/{execution_id}/result",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "completed", "exit_code": 0, "output_data": "forged"},
        )
        assert resp.status_code == 404
        db = app.db  # type: ignore[attr-defined]
        row = db(db.run_executions.execution_id == execution_id).select().first()
        assert row.status == "running"
        assert row.stdout_log != "forged"
        assert row.output_json != "forged"

        # The owning tenant's worker can.
        token = self._as_worker(app, state, "1")
        resp = await client.post(
            f"/api/v1/runs/internal/executions/{execution_id}/logs",
            headers={"Authorization": f"Bearer {token}"},
            json={"logs": "hello"},
        )
        assert resp.status_code == 200
        assert (await resp.get_json())["updated"] is True
        resp = await client.post(
            f"/api/v1/runs/internal/executions/{execution_id}/result",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "completed", "exit_code": 0},
        )
        assert resp.status_code == 200
        assert (await resp.get_json())["status"] == "completed"

    @pytest.mark.asyncio
    async def test_tenant_scoped_non_runworker_sub_refused(
        self, tmp_path: Path
    ) -> None:
        app = _make_test_app(tmp_path)
        state = self._wire(app)
        client = app.test_client()
        token = _mint_service_worker_token(
            app, sub="svc-scanner", tenant="1", scopes=["discovery:read"]
        )
        state["worker"] = token
        resp = await client.get(
            "/api/v1/runs/internal/pending",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code in (401, 403)
