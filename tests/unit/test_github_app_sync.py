"""Unit tests for GitHub App authentication, RS256 JWT signing, token caching, and API validation."""

import json
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from penguin_dal import DAL, Field
from quart import Quart
from quart_schema import QuartSchema

from apps.api.models.pydantic.sync import SyncConfigResponse
from apps.api.modules.discovery.routes.sync import _merge_config_json
from apps.api.modules.discovery.routes.sync import bp as sync_bp
from apps.worker.sync.base import ResourceType
from apps.worker.sync.platforms.github_client import (
    GitHubSyncClient,
    load_github_app_private_key,
    resolve_github_auth_mode,
)
from shared.redaction import REDACTED_PLACEHOLDER, redact_credential_keys


def _generate_rsa_keypair() -> tuple[str, str, str]:
    """Generate an ephemeral RSA-2048 keypair returning (pkcs1_pem, pkcs8_pem, public_pem)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pkcs1_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    pkcs8_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return pkcs1_pem, pkcs8_pem, public_pem


def _generate_ec_pem() -> str:
    """Generate an ephemeral EC private key PEM to test non-RSA rejection."""
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")


def _create_sync_db(db_path: str) -> DAL:
    """Create minimal SQLite PyDAL database for GitHub App sync unit tests."""
    db = DAL(f"sqlite:///{db_path}", migrate=True)
    db.define_table(
        "organizations",
        Field("name", "string"),
        Field("tenant_id", "integer"),
        Field("created_at", "datetime"),
        Field("updated_at", "datetime"),
    )
    db.define_table(
        "sync_configs",
        Field("name", "string", unique=True),
        Field("platform", "string"),
        Field("enabled", "boolean"),
        Field("sync_interval", "integer"),
        Field("batch_fallback_enabled", "boolean"),
        Field("batch_size", "integer"),
        Field("two_way_create", "boolean"),
        Field("webhook_enabled", "boolean"),
        Field("webhook_secret", "string"),
        Field("last_sync_at", "datetime"),
        Field("last_batch_sync_at", "datetime"),
        Field("config_json", "json"),
        Field("created_at", "datetime"),
        Field("updated_at", "datetime"),
    )
    db.define_table(
        "sync_history",
        Field("sync_config_id", "integer"),
        Field("correlation_id", "string"),
        Field("sync_type", "string"),
        Field("items_synced", "integer"),
        Field("items_failed", "integer"),
        Field("started_at", "datetime"),
        Field("completed_at", "datetime"),
        Field("success", "boolean"),
        Field("error_message", "text"),
        Field("sync_metadata", "json"),
    )
    db.define_table(
        "sync_mappings",
        Field("elder_type", "string"),
        Field("elder_id", "integer"),
        Field("external_platform", "string"),
        Field("external_id", "string"),
        Field("sync_config_id", "integer"),
        Field("sync_status", "string"),
        Field("sync_method", "string"),
        Field("last_synced_at", "datetime"),
        Field("elder_updated_at", "datetime"),
        Field("external_updated_at", "datetime"),
        Field("created_at", "datetime"),
        Field("updated_at", "datetime"),
    )
    db.define_table(
        "sync_conflicts",
        Field("mapping_id", "integer"),
        Field("conflict_type", "string"),
        Field("elder_data", "json"),
        Field("external_data", "json"),
        Field("resolution_strategy", "string"),
        Field("resolved", "boolean"),
        Field("resolved_at", "datetime"),
        Field("resolved_by_id", "integer"),
        Field("created_at", "datetime"),
    )
    db.define_table(
        "projects",
        Field("name", "string"),
        Field("description", "text"),
        Field("status", "string"),
        Field("start_date", "datetime"),
        Field("target_date", "datetime"),
        Field("organization_id", "integer"),
        Field("tenant_id", "integer"),
        Field("created_at", "datetime"),
        Field("updated_at", "datetime"),
    )
    db.define_table(
        "milestones",
        Field("title", "string"),
        Field("description", "text"),
        Field("status", "string"),
        Field("due_date", "datetime"),
        Field("closed_at", "datetime"),
        Field("organization_id", "integer"),
        Field("tenant_id", "integer"),
        Field("created_at", "datetime"),
        Field("updated_at", "datetime"),
    )
    db.define_table(
        "issue_labels",
        Field("name", "string"),
        Field("color", "string"),
        Field("description", "text"),
        Field("created_at", "datetime"),
        Field("updated_at", "datetime"),
    )
    db.define_table(
        "issues",
        Field("title", "string"),
        Field("description", "text"),
        Field("status", "string"),
        Field("priority", "string"),
        Field("issue_type", "string"),
        Field("resource_type", "string"),
        Field("resource_id", "integer"),
        Field("organization_id", "integer"),
        Field("tenant_id", "integer"),
        Field("is_incident", "integer"),
        Field("reporter_id", "integer"),
        Field("due_date", "datetime"),
        Field("closed_at", "datetime"),
        Field("created_at", "datetime"),
        Field("updated_at", "datetime"),
    )
    db.define_table(
        "issue_label_assignments",
        Field("issue_id", "integer"),
        Field("label_id", "integer"),
    )
    db.define_table(
        "issue_milestone_links",
        Field("issue_id", "integer"),
        Field("milestone_id", "integer"),
        Field("created_at", "datetime"),
    )
    db.define_table(
        "issue_project_links",
        Field("issue_id", "integer"),
        Field("project_id", "integer"),
        Field("created_at", "datetime"),
    )
    now = datetime.now(UTC)
    db.organizations.insert(
        name="Acme Org", tenant_id=7, created_at=now, updated_at=now
    )
    db.commit()
    return db


def _mock_transport(client: GitHubSyncClient, handler) -> None:
    old = client.client
    client.client = httpx.Client(
        base_url=old.base_url,
        headers=old.headers,
        transport=httpx.MockTransport(handler),
        timeout=30.0,
    )
    old.close()


@pytest.fixture
def sync_db(tmp_path) -> DAL:
    db = _create_sync_db(str(tmp_path / "github_app_sync.db"))
    yield db
    db.close()


@pytest.fixture
def test_logger() -> logging.Logger:
    return logging.getLogger("test.github_app_sync")


class TestGitHubAppJwtSigningAndPemFormats:
    def test_rs256_jwt_verifies_with_pyjwt_for_pkcs1_pkcs8_and_escaped_newlines(
        self, sync_db, test_logger
    ):
        pkcs1_pem, pkcs8_pem, public_pem = _generate_rsa_keypair()
        escaped_pkcs1 = pkcs1_pem.strip().replace("\n", "\\n")
        fixed_now = datetime.now(UTC).replace(microsecond=0)

        for variant_name, pem_val in (
            ("pkcs1", pkcs1_pem),
            ("pkcs8", pkcs8_pem),
            ("escaped_single_line", escaped_pkcs1),
        ):
            client = GitHubSyncClient(
                {
                    "app_id": "123456",
                    "installation_id": "987654",
                    "private_key": pem_val,
                    "owner": "penguintechinc",
                    "repo_name": "elder",
                },
                sync_db,
                1,
                test_logger,
            )
            assert client.auth_mode == "github_app", variant_name
            assert client.validate_config() is True, variant_name

            token = client._generate_app_jwt(now=fixed_now)
            header = jwt.get_unverified_header(token)
            assert header["alg"] == "RS256"
            assert header["typ"] == "JWT"

            claims = jwt.decode(
                token,
                public_pem,
                algorithms=["RS256"],
                issuer="123456",
                options={"verify_iat": False},
            )
            now_ts = int(fixed_now.timestamp())
            assert claims["iss"] == "123456"
            assert claims["iat"] == now_ts - 60
            assert claims["exp"] == now_ts + 9 * 60
            client.close()

    def test_rejects_invalid_and_non_rsa_private_keys_without_silent_pat_fallback(
        self, sync_db, test_logger
    ):
        ec_pem = _generate_ec_pem()

        for bad_key in ("", "not-a-pem", REDACTED_PLACEHOLDER, ec_pem):
            with pytest.raises(ValueError):
                load_github_app_private_key(bad_key)

            # Even if api_token is also present, github_app mode must fail loudly
            client = GitHubSyncClient(
                {
                    "auth_mode": "github_app",
                    "app_id": "123456",
                    "installation_id": "987654",
                    "private_key": bad_key,
                    "api_token": "ghp_should_not_be_used_as_fallback",
                    "owner": "penguintechinc",
                    "repo_name": "elder",
                },
                sync_db,
                1,
                test_logger,
            )
            assert client.validate_config() is False
            detail = client.test_connection_detail()
            assert detail["connected"] is False
            assert detail["auth_mode"] == "github_app"
            with pytest.raises(ValueError):
                client._get_auth_token()
            client.close()

    def test_auth_mode_resolution_defaults_and_pat_compat(self):
        assert (
            resolve_github_auth_mode(
                {"app_id": "1", "installation_id": "2", "private_key": "k"}
            )
            == "github_app"
        )
        assert resolve_github_auth_mode({"api_token": "ghp_123"}) == "pat"
        assert (
            resolve_github_auth_mode(
                {
                    "auth_mode": "pat",
                    "app_id": "1",
                    "installation_id": "2",
                    "private_key": "k",
                    "api_token": "ghp_123",
                }
            )
            == "pat"
        )
        with pytest.raises(ValueError):
            resolve_github_auth_mode({"auth_mode": "oauth2"})


class TestInstallationTokenExchangeAndLifecycle:
    def test_token_exchange_caching_auto_refresh_and_401_retry_rest_and_graphql(
        self, sync_db, test_logger
    ):
        pkcs1_pem, _, public_pem = _generate_rsa_keypair()
        client = GitHubSyncClient(
            {
                "auth_mode": "github_app",
                "app_id": 778899,
                "installation_id": "445566",
                "private_key": pkcs1_pem,
                "owner": "penguintechinc",
                "repo_name": "elder",
                "elder_organization_id": 1,
            },
            sync_db,
            1,
            test_logger,
        )

        mint_counter = {"count": 0}
        issue_calls: list[str] = []
        graphql_calls: list[str] = []
        force_next_401 = {"rest": False, "graphql": False}
        next_expiry = {"dt": datetime.now(UTC) + timedelta(hours=1)}

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/app/installations/445566/access_tokens":
                assert req.method == "POST"
                assert req.headers["accept"] == "application/vnd.github+json"
                assert req.headers["x-github-api-version"] == "2022-11-28"
                auth_header = req.headers["authorization"]
                assert auth_header.startswith("Bearer ")
                claims = jwt.decode(
                    auth_header.split(" ", 1)[1],
                    public_pem,
                    algorithms=["RS256"],
                    issuer="778899",
                )
                assert claims["iss"] == "778899"

                mint_counter["count"] += 1
                tok = f"ghs_install_tok_{mint_counter['count']}"
                return httpx.Response(
                    201,
                    json={
                        "token": tok,
                        "expires_at": next_expiry["dt"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "permissions": {"issues": "write", "metadata": "read"},
                    },
                )

            if req.url.path == "/repos/penguintechinc/elder/labels":
                issue_calls.append(req.headers["authorization"])
                if force_next_401["rest"]:
                    force_next_401["rest"] = False
                    return httpx.Response(401, json={"message": "Bad credentials"})
                return httpx.Response(200, json=[])

            if req.url.path == "/graphql":
                graphql_calls.append(req.headers["authorization"])
                if force_next_401["graphql"]:
                    force_next_401["graphql"] = False
                    return httpx.Response(401, json={"message": "Bad credentials"})
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "organization": {
                                "id": "O_1",
                                "projectsV2": {
                                    "pageInfo": {
                                        "hasNextPage": False,
                                        "endCursor": None,
                                    },
                                    "nodes": [],
                                },
                            }
                        }
                    },
                )

            return httpx.Response(404)

        _mock_transport(client, handler)

        # 1. First REST & GraphQL calls share the cached token (1 mint total)
        res1 = client.batch_sync(ResourceType.LABEL, direction="pull")
        assert res1.is_success
        res2 = client.batch_sync(ResourceType.PROJECT, direction="pull")
        assert res2.is_success
        assert mint_counter["count"] == 1
        assert issue_calls[-1] == "Bearer ghs_install_tok_1"
        assert graphql_calls[-1] == "Bearer ghs_install_tok_1"

        # 2. Auto-refresh when cached token is within 5 minutes of expiry
        client._token_expires_at = datetime.now(UTC) + timedelta(minutes=4)
        res3 = client.batch_sync(ResourceType.LABEL, direction="pull")
        assert res3.is_success
        assert mint_counter["count"] == 2
        assert issue_calls[-1] == "Bearer ghs_install_tok_2"

        # 3. HTTP 401 on REST refreshes token and retries once
        force_next_401["rest"] = True
        res4 = client.batch_sync(ResourceType.LABEL, direction="pull")
        assert res4.is_success
        assert mint_counter["count"] == 3
        assert issue_calls[-2:] == [
            "Bearer ghs_install_tok_2",
            "Bearer ghs_install_tok_3",
        ]

        # 4. HTTP 401 on GraphQL refreshes token and retries once
        force_next_401["graphql"] = True
        res5 = client.batch_sync(ResourceType.PROJECT, direction="pull")
        assert res5.is_success
        assert mint_counter["count"] == 4
        assert graphql_calls[-2:] == [
            "Bearer ghs_install_tok_3",
            "Bearer ghs_install_tok_4",
        ]

        client.close()

    def test_test_connection_detail_verifies_mint_and_repo_never_leaks_secrets(
        self, sync_db, test_logger
    ):
        pkcs1_pem, _, _ = _generate_rsa_keypair()
        client = GitHubSyncClient(
            {
                "app_id": "1010",
                "installation_id": "2020",
                "private_key": pkcs1_pem,
                "owner": "penguintechinc",
                "repo_name": "elder",
                "base_url": "https://git.corp.example/api/v3",
            },
            sync_db,
            1,
            test_logger,
        )

        secret_token = "ghs_super_secret_installation_token_999"
        future_exp = (datetime.now(UTC) + timedelta(hours=1)).replace(microsecond=0)
        future_exp_z = future_exp.isoformat().replace("+00:00", "Z")
        seen_paths: list[str] = []

        def handler(req: httpx.Request) -> httpx.Response:
            seen_paths.append(req.url.path)
            if req.url.path == "/api/v3/app/installations/2020/access_tokens":
                return httpx.Response(
                    201,
                    json={
                        "token": secret_token,
                        "expires_at": future_exp_z,
                        "permissions": {
                            "issues": "write",
                            "metadata": "read",
                            "organization_projects": "write",
                        },
                    },
                )
            if req.url.path == "/api/v3/repos/penguintechinc/elder":
                assert req.headers["authorization"] == f"Bearer {secret_token}"
                return httpx.Response(
                    200,
                    json={
                        "full_name": "penguintechinc/elder",
                        "private": True,
                        "default_branch": "main",
                        "html_url": "https://git.corp.example/penguintechinc/elder",
                        "open_issues_count": 3,
                    },
                )
            return httpx.Response(404)

        _mock_transport(client, handler)
        detail = client.test_connection_detail()
        assert detail["connected"] is True
        assert detail["success"] is True
        assert detail["auth_mode"] == "github_app"
        assert detail["repo_full_name"] == "penguintechinc/elder"
        assert detail["token_expires_at"] == future_exp.isoformat()
        assert detail["permissions"] == {
            "issues": "write",
            "metadata": "read",
            "organization_projects": "write",
        }
        serialized = json.dumps(detail)
        assert pkcs1_pem[:30] not in serialized
        assert secret_token not in serialized
        assert seen_paths == [
            "/api/v3/app/installations/2020/access_tokens",
            "/api/v3/repos/penguintechinc/elder",
        ]
        assert client.test_connection() is True
        client.close()

    def test_token_exchange_failure_fails_loudly_without_leaking_key(
        self, sync_db, test_logger
    ):
        pkcs1_pem, _, _ = _generate_rsa_keypair()
        client = GitHubSyncClient(
            {
                "app_id": "1010",
                "installation_id": "2020",
                "private_key": pkcs1_pem,
                "owner": "penguintechinc",
                "repo_name": "elder",
            },
            sync_db,
            1,
            test_logger,
        )

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                401, json={"message": "A JSON web token could not be decoded"}
            )

        _mock_transport(client, handler)
        detail = client.test_connection_detail()
        assert detail["connected"] is False
        assert detail["auth_mode"] == "github_app"
        assert "HTTP 401" in detail["error"]
        assert "-----BEGIN" not in detail["error"]
        assert client.test_connection() is False
        client.close()


class TestGitHubAppApiRoutesValidationAndRedaction:
    @pytest.fixture
    def api_setup(self, sync_db):
        app = Quart(__name__)
        app.config["TESTING"] = True
        app.config["JWT_SECRET_KEY"] = "test-jwt-secret"
        app.config["SECRET_KEY"] = "test-secret"
        QuartSchema(app)
        app.db = sync_db  # type: ignore[attr-defined]
        app.register_blueprint(sync_bp)
        return app, sync_db

    def test_private_key_redaction_and_merge_preservation(self):
        redacted = redact_credential_keys(
            {
                "auth_mode": "github_app",
                "app_id": "12345",
                "installation_id": "67890",
                "private_key": "ephemeral-pem-secret-material",
            }
        )
        assert redacted["private_key"] == REDACTED_PLACEHOLDER
        assert redacted["app_id"] == "12345"

        row = SimpleNamespace(
            as_dict=lambda: {
                "id": 10,
                "name": "app-sync",
                "platform": "github",
                "enabled": True,
                "sync_interval": 300,
                "batch_fallback_enabled": True,
                "batch_size": 100,
                "two_way_create": True,
                "webhook_enabled": True,
                "webhook_secret": "whsec",
                "last_sync_at": None,
                "last_batch_sync_at": None,
                "config_json": {
                    "auth_mode": "github_app",
                    "app_id": "12345",
                    "installation_id": "67890",
                    "private_key": "SENSITIVE_PEM",
                },
                "created_at": None,
                "updated_at": None,
            }
        )
        dto = SyncConfigResponse.from_row(row)
        assert dto.config_json is not None
        assert dto.config_json["private_key"] == REDACTED_PLACEHOLDER
        assert "SENSITIVE_PEM" not in dto.model_dump_json()

        merged_omitted = _merge_config_json(
            {"auth_mode": "github_app", "private_key": "REAL_PEM"},
            {"auth_mode": "github_app", "owner": "o", "repo_name": "r"},
        )
        assert merged_omitted["private_key"] == "REAL_PEM"

        merged_redacted = _merge_config_json(
            {"auth_mode": "github_app", "private_key": "REAL_PEM"},
            {
                "auth_mode": "github_app",
                "private_key": REDACTED_PLACEHOLDER,
            },
        )
        assert merged_redacted["private_key"] == "REAL_PEM"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_patch_test_and_trigger_github_app_and_legacy_pat(
        self, mock_get_user, api_setup
    ):
        app, db = api_setup
        mock_get_user.return_value = SimpleNamespace(
            id=1, username="admin", is_superuser=True, portal_role="admin", tenant_id=7
        )
        client = app.test_client()
        pkcs1_pem, pkcs8_pem, _ = _generate_rsa_keypair()

        # Validation failures on create
        invalid_payloads = [
            {"owner": "org", "repo_name": "repo", "auth_mode": "oauth"},
            {
                "owner": "org",
                "repo_name": "repo",
                "auth_mode": "github_app",
                "installation_id": "123",
                "private_key": pkcs1_pem,
            },
            {
                "owner": "org",
                "repo_name": "repo",
                "auth_mode": "github_app",
                "app_id": "123",
                "installation_id": "0",
                "private_key": pkcs1_pem,
            },
            {
                "owner": "org",
                "repo_name": "repo",
                "auth_mode": "github_app",
                "app_id": "123",
                "installation_id": "not-int",
                "private_key": pkcs1_pem,
            },
            {
                "owner": "org",
                "repo_name": "repo",
                "auth_mode": "github_app",
                "app_id": "123",
                "installation_id": "456",
                "private_key": REDACTED_PLACEHOLDER,
            },
            {
                "owner": "org",
                "repo_name": "repo",
                "auth_mode": "github_app",
                "app_id": "123",
                "installation_id": "456",
                "private_key": "not-a-valid-pem",
            },
            {
                "owner": "org",
                "repo_name": "repo",
                "auth_mode": "github_app",
                "app_id": "123",
                "installation_id": "456",
                "private_key": _generate_ec_pem(),
            },
            {
                "owner": "org",
                "repo_name": "repo",
                "auth_mode": "pat",
                "api_token": REDACTED_PLACEHOLDER,
            },
        ]
        for idx, cfg_json in enumerate(invalid_payloads):
            resp = await client.post(
                "/api/v1/sync/configs",
                json={
                    "name": f"BadApp-{idx}",
                    "platform": "github",
                    "config_json": cfg_json,
                },
            )
            assert resp.status_code == 400, f"Expected 400 for case {idx}: {cfg_json}"

        # Create valid GitHub App config (inferred auth_mode from app fields)
        create_resp = await client.post(
            "/api/v1/sync/configs",
            json={
                "name": "Elder GitHub App",
                "platform": "github",
                "config_json": {
                    "owner": "penguintechinc",
                    "repo_name": "elder",
                    "app_id": "112233",
                    "installation_id": "445566",
                    "private_key": pkcs1_pem,
                    "elder_organization_id": 1,
                },
            },
        )
        assert create_resp.status_code == 201
        created = json.loads(await create_resp.get_data())["config"]
        cfg_id = created["id"]
        assert created["config_json"]["private_key"] == REDACTED_PLACEHOLDER
        assert db.sync_configs[cfg_id].config_json["private_key"] == pkcs1_pem

        # PATCH sending ***REDACTED*** preserves stored PEM cleanly
        patch_keep = await client.patch(
            f"/api/v1/sync/configs/{cfg_id}",
            json={
                "sync_interval": 900,
                "config_json": {
                    "auth_mode": "github_app",
                    "owner": "penguintechinc",
                    "repo_name": "elder",
                    "app_id": "112233",
                    "installation_id": "445566",
                    "private_key": REDACTED_PLACEHOLDER,
                    "elder_organization_id": 1,
                },
            },
        )
        assert patch_keep.status_code == 200
        assert db.sync_configs[cfg_id].sync_interval == 900
        assert db.sync_configs[cfg_id].config_json["private_key"] == pkcs1_pem

        # PATCH with a new PKCS#8 PEM updates stored key; invalid PEM fails 400
        patch_bad = await client.patch(
            f"/api/v1/sync/configs/{cfg_id}",
            json={
                "config_json": {
                    "auth_mode": "github_app",
                    "owner": "penguintechinc",
                    "repo_name": "elder",
                    "app_id": "112233",
                    "installation_id": "445566",
                    "private_key": "corrupted-pem",
                }
            },
        )
        assert patch_bad.status_code == 400
        assert db.sync_configs[cfg_id].config_json["private_key"] == pkcs1_pem

        patch_rotate = await client.patch(
            f"/api/v1/sync/configs/{cfg_id}",
            json={
                "config_json": {
                    "auth_mode": "github_app",
                    "owner": "penguintechinc",
                    "repo_name": "elder",
                    "app_id": "112233",
                    "installation_id": "445566",
                    "private_key": pkcs8_pem,
                    "elder_organization_id": 1,
                }
            },
        )
        assert patch_rotate.status_code == 200
        assert db.sync_configs[cfg_id].config_json["private_key"] == pkcs8_pem

        # Test /test and /trigger endpoints with GitHub App config
        route_future_exp_z = (
            (datetime.now(UTC) + timedelta(hours=2))
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )

        def mock_transport_handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/app/installations/445566/access_tokens":
                return httpx.Response(
                    201,
                    json={
                        "token": "ghs_route_test_tok",
                        "expires_at": route_future_exp_z,
                        "permissions": {"issues": "write", "metadata": "read"},
                    },
                )
            if req.url.path == "/repos/penguintechinc/elder":
                return httpx.Response(
                    200,
                    json={
                        "full_name": "penguintechinc/elder",
                        "default_branch": "main",
                    },
                )
            if req.url.path == "/repos/penguintechinc/elder/labels":
                return httpx.Response(
                    200, json=[{"name": "app-label", "color": "00aa55"}]
                )
            return httpx.Response(404)

        real_httpx_client = httpx.Client

        def fake_httpx_client(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(mock_transport_handler)
            return real_httpx_client(*args, **kwargs)

        with patch(
            "apps.worker.sync.platforms.github_client.httpx.Client",
            side_effect=fake_httpx_client,
        ):
            test_resp = await client.post(f"/api/v1/sync/configs/{cfg_id}/test")
            assert test_resp.status_code == 200
            test_data = json.loads(await test_resp.get_data())
            assert test_data["connected"] is True
            assert test_data["auth_mode"] == "github_app"
            assert test_data["repo_full_name"] == "penguintechinc/elder"
            assert "ghs_route_test_tok" not in json.dumps(test_data)

            trig_resp = await client.post(
                f"/api/v1/sync/configs/{cfg_id}/trigger",
                json={"resource_types": ["label"], "direction": "pull"},
            )
            assert trig_resp.status_code == 200
            assert json.loads(await trig_resp.get_data())["items_synced"] == 1

        # Legacy PAT creation still works cleanly
        pat_resp = await client.post(
            "/api/v1/sync/configs",
            json={
                "name": "Legacy PAT Repo",
                "platform": "github",
                "config_json": {
                    "auth_mode": "pat",
                    "owner": "penguintechinc",
                    "repo_name": "legacy",
                    "api_token": "github_pat_legacy",
                },
            },
        )
        assert pat_resp.status_code == 201
        pat_cfg = json.loads(await pat_resp.get_data())["config"]
        assert pat_cfg["config_json"]["auth_mode"] == "pat"
        assert pat_cfg["config_json"]["api_token"] == REDACTED_PLACEHOLDER
