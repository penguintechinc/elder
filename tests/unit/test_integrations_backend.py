"""Unit and route tests for backend integration providers (Discovery, IAM, Google Workspace)."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from penguin_dal import DAL, Field
from quart import Quart, g

from apps.api.modules.discovery.routes.google_workspace import bp as gws_bp
from apps.api.modules.discovery.routes.iam import bp as iam_bp
from apps.api.services.google_workspace.service import GoogleWorkspaceService
from apps.api.services.iam.service import IAMService
from apps.worker.discovery.service import DiscoveryService


def _create_integrations_db(db_path: str) -> DAL:
    """Create a SQLite PyDAL database matching live Postgres table columns."""
    db = DAL(f"sqlite:///{db_path}", migrate=True)
    db.define_table(
        "organizations",
        Field("name", "string"),
        Field("tenant_id", "integer"),
        Field("village_id", "string"),
        Field("created_at", "datetime"),
        Field("updated_at", "datetime"),
    )
    db.define_table(
        "discovery_jobs",
        Field("name", "string", notnull=True),
        Field("provider", "string", notnull=True),
        Field("config_json", "json", notnull=True),
        Field("schedule_interval", "integer", notnull=True),
        Field("enabled", "boolean", notnull=True),
        Field("credential_type", "string"),
        Field("credential_id", "integer"),
        Field("credential_mapping", "json"),
        Field("last_run_at", "datetime"),
        Field("next_run_at", "datetime"),
        Field("village_id", "string"),
        Field("created_at", "datetime"),
        Field("updated_at", "datetime"),
    )
    db.define_table(
        "discovery_history",
        Field("job_id", "integer"),
        Field("started_at", "datetime"),
        Field("completed_at", "datetime"),
        Field("status", "string"),
        Field("entities_discovered", "integer"),
        Field("entities_created", "integer"),
        Field("entities_updated", "integer"),
        Field("error_message", "text"),
        Field("results_json", "json"),
    )
    db.define_table(
        "iam_providers",
        Field("name", "string", notnull=True),
        Field("provider_type", "string", notnull=True),
        Field("config_json", "json", notnull=True),
        Field("organization_id", "integer", notnull=True),
        Field("enabled", "boolean", notnull=True),
        Field("last_sync_at", "datetime"),
        Field("village_id", "string"),
        Field("created_at", "datetime"),
        Field("updated_at", "datetime"),
    )
    db.define_table(
        "iam_users",
        Field("iam_provider_id", "integer"),
    )
    db.define_table(
        "iam_roles",
        Field("iam_provider_id", "integer"),
    )
    db.define_table(
        "google_workspace_providers",
        Field("name", "string", notnull=True),
        Field("domain", "string", notnull=True),
        Field("admin_email", "string", notnull=True),
        Field("credentials_json", "json", notnull=True),
        Field("organization_id", "integer", notnull=True),
        Field("enabled", "boolean", notnull=True),
        Field("last_sync_at", "datetime"),
        Field("village_id", "string"),
        Field("created_at", "datetime"),
        Field("updated_at", "datetime"),
    )
    now = datetime.now(UTC)
    db.organizations.insert(name="Org One", tenant_id=1, created_at=now, updated_at=now)
    db.organizations.insert(name="Org Two", tenant_id=1, created_at=now, updated_at=now)
    db.commit()
    return db


SAMPLE_SA = {
    "type": "service_account",
    "project_id": "elder-test-proj",
    "private_key_id": "key-1",
    "private_key": "fake-service-account-key-for-unit-tests",
    "client_email": "elder-sa@elder-test-proj.iam.gserviceaccount.com",
    "client_id": "1234567890",
    "token_uri": "https://oauth2.googleapis.com/token",
}


class TestDiscoveryServiceHardening:
    """Tests for DiscoveryService provider support, filtering, update merge, and redaction."""

    def test_create_list_filter_and_vultr_fleetdm_providers(self, tmp_path):
        db = _create_integrations_db(str(tmp_path / "disc.db"))
        svc = DiscoveryService(db)

        vultr_job = svc.create_job(
            name="Vultr Infra",
            provider="vultr",
            config={"api_key": "vultr-secret-123"},
            organization_id=1,
            schedule_interval=0,
            description="Primary Vultr account",
        )
        assert vultr_job["provider"] == "vultr"
        assert vultr_job["schedule_interval"] == 0
        assert vultr_job["organization_id"] == 1
        assert vultr_job["description"] == "Primary Vultr account"
        assert vultr_job["config_json"]["api_key"] == "***REDACTED***"

        fleet_job = svc.create_job(
            name="FleetDM Prod",
            provider="fleetdm",
            config={
                "url": "https://fleet.example.com",
                "api_token": "fleet-secret-abc",
                "verify_ssl": True,
            },
            organization_id=2,
            schedule_interval=900,
        )
        assert fleet_job["provider"] == "fleetdm"
        assert fleet_job["config_json"]["api_token"] == "***REDACTED***"

        # Filter by organization_id stored inside config_json["_organization_id"]
        org1_jobs = svc.list_jobs(organization_id=1)
        assert [j["name"] for j in org1_jobs] == ["Vultr Infra"]

        org2_jobs = svc.list_jobs(organization_id=2)
        assert [j["name"] for j in org2_jobs] == ["FleetDM Prod"]

        # Direct organization_id attribute fallback on fake rows works too
        fake_row = MagicMock()
        fake_row.as_dict.return_value = {
            "id": 99,
            "name": "Fake Row",
            "provider": "aws",
            "organization_id": 7,
            "config_json": {"region": "us-east-1"},
            "enabled": True,
        }
        fake_db = MagicMock()
        fake_db.discovery_jobs.id.__gt__.return_value = MagicMock()
        fake_db.return_value.select.return_value = [fake_row]
        assert len(DiscoveryService(fake_db).list_jobs(organization_id=7)) == 1
        assert len(DiscoveryService(fake_db).list_jobs(organization_id=8)) == 0

    def test_update_job_preserves_secrets_and_stores_description_in_config(
        self, tmp_path
    ):
        db = _create_integrations_db(str(tmp_path / "disc_upd.db"))
        svc = DiscoveryService(db)

        created = svc.create_job(
            name="AWS Prod",
            provider="aws",
            config={
                "region": "us-east-1",
                "access_key_id": "AKIA_ORIG",
                "secret_access_key": "SECRET_ORIG",
                "private_key": "PEM_ORIG",
                "services": ["ec2", "s3"],
            },
            organization_id=1,
            schedule_interval=3600,
            description="Initial desc",
        )

        updated = svc.update_job(
            created["id"],
            name="AWS Prod Updated",
            config={
                "region": "us-west-2",
                "access_key_id": "***REDACTED***",
                "secret_access_key": "",
                "private_key": None,
                "services": ["ec2", "rds"],
            },
            schedule_interval=21600,
            description="Updated desc",
            enabled=False,
        )

        assert updated["name"] == "AWS Prod Updated"
        assert updated["enabled"] is False
        assert updated["schedule_interval"] == 21600
        assert updated["description"] == "Updated desc"
        assert updated["organization_id"] == 1
        assert updated["config_json"]["region"] == "us-west-2"
        assert updated["config_json"]["access_key_id"] == "***REDACTED***"
        assert updated["config_json"]["secret_access_key"] == "***REDACTED***"
        assert updated["config_json"]["private_key"] == "***REDACTED***"

        # Verify raw DB row kept original secrets intact
        raw = db.discovery_jobs[created["id"]]
        assert raw.config_json["access_key_id"] == "AKIA_ORIG"
        assert raw.config_json["secret_access_key"] == "SECRET_ORIG"
        assert raw.config_json["private_key"] == "PEM_ORIG"
        assert raw.config_json["_organization_id"] == 1
        assert raw.config_json["_description"] == "Updated desc"

    def test_sanitize_job_masks_all_sensitive_fields(self, tmp_path):
        db = _create_integrations_db(str(tmp_path / "disc_san.db"))
        svc = DiscoveryService(db)
        sanitized = svc._sanitize_job(
            {
                "id": 1,
                "provider": "aws",
                "config_json": {
                    "region": "us-east-1",
                    "access_key_id": "a",
                    "secret_access_key": "b",
                    "credentials_json": "c",
                    "token": "d",
                    "api_token": "e",
                    "api_key": "f",
                    "fleetdm_api_token": "g",
                    "client_secret": "h",
                    "kubeconfig": "i",
                    "private_key": "j",
                },
            }
        )
        for key in (
            "access_key_id",
            "secret_access_key",
            "credentials_json",
            "token",
            "api_token",
            "api_key",
            "fleetdm_api_token",
            "client_secret",
            "kubeconfig",
            "private_key",
        ):
            assert sanitized["config_json"][key] == "***REDACTED***"


class TestIAMServiceHardening:
    """Tests for IAMService DB schema alignment, list filters, and secret preservation."""

    def test_crud_enabled_only_org_filter_and_secret_preservation(self, tmp_path):
        db = _create_integrations_db(str(tmp_path / "iam.db"))
        svc = IAMService(db)

        # Fallback to first organization when organization_id omitted
        p1 = svc.create_provider(
            name="AWS IAM Org1",
            provider_type="aws_iam",
            config={
                "region": "us-east-1",
                "access_key_id": "AKIA_IAM_1",
                "secret_access_key": "SECRET_IAM_1",
                "api_key": "KEY_1",
            },
            description="AWS IAM Desc",
        )
        assert p1["organization_id"] == 1
        assert p1["description"] == "AWS IAM Desc"
        assert p1["config_json"]["access_key_id"] == "***REDACTED***"
        assert p1["config_json"]["secret_access_key"] == "***REDACTED***"
        assert p1["config_json"]["api_key"] == "***REDACTED***"

        p2 = svc.create_provider(
            name="K8s RBAC Org2",
            provider_type="kubernetes",
            config={"kubeconfig": "raw-kubeconfig-yaml", "namespace": "prod"},
            description="K8s Desc",
            organization_id=2,
        )
        assert p2["organization_id"] == 2
        assert p2["config_json"]["kubeconfig"] == "***REDACTED***"

        # Disable p2 and confirm enabled_only=True uses == True (not Python `is True`)
        svc.update_provider(
            p2["id"],
            name="K8s RBAC Disabled",
            config={"kubeconfig": "***REDACTED***", "namespace": "staging"},
            description="Updated K8s Desc",
            enabled=False,
        )

        enabled_list = svc.list_providers(enabled_only=True)
        assert [p["name"] for p in enabled_list] == ["AWS IAM Org1"]

        org2_list = svc.list_providers(organization_id=2)
        assert [p["name"] for p in org2_list] == ["K8s RBAC Disabled"]
        assert org2_list[0]["description"] == "Updated K8s Desc"

        # Verify raw secret preserved in DB
        raw_p2 = db.iam_providers[p2["id"]]
        assert raw_p2.config_json["kubeconfig"] == "raw-kubeconfig-yaml"
        assert raw_p2.config_json["namespace"] == "staging"

    def test_create_provider_requires_org_if_none_exist(self, tmp_path):
        db = _create_integrations_db(str(tmp_path / "iam_empty.db"))
        db(db.organizations.id > 0).delete()
        db.commit()
        svc = IAMService(db)
        with pytest.raises(ValueError, match="organization_id"):
            svc.create_provider(
                name="No Org",
                provider_type="aws_iam",
                config={"region": "us-east-1"},
            )


class TestGoogleWorkspaceServiceHardening:
    """Tests for GoogleWorkspaceService schema alignment, client loading, and masking."""

    def test_crud_schema_alignment_and_secret_preservation(self, tmp_path):
        db = _create_integrations_db(str(tmp_path / "gws.db"))
        svc = GoogleWorkspaceService(db)

        created = svc.create_provider(
            name="Corp Workspace",
            organization_id=1,
            customer_id="C01234567",
            admin_email="dir-admin@corp.example.com",
            service_account_json=json.dumps(SAMPLE_SA),
            description="Primary directory",
        )

        assert created["domain"] == "corp.example.com"
        assert created["customer_id"] == "C01234567"
        assert created["description"] == "Primary directory"
        assert created["has_credentials"] is True
        assert created["credentials_json"] == "***REDACTED***"
        assert "private_key" not in json.dumps(created, default=str)

        listed = svc.list_providers(organization_id=1)
        assert len(listed) == 1
        assert listed[0]["credentials_json"] == "***REDACTED***"

        # Update preserving masked credentials
        updated = svc.update_provider(
            created["id"],
            name="Corp Workspace Renamed",
            domain="new.corp.example.com",
            customer_id="C99999999",
            service_account_json="***REDACTED***",
            description="Updated directory",
            enabled=False,
        )
        assert updated["name"] == "Corp Workspace Renamed"
        assert updated["domain"] == "new.corp.example.com"
        assert updated["customer_id"] == "C99999999"
        assert updated["description"] == "Updated directory"
        assert updated["enabled"] is False
        assert updated["has_credentials"] is True
        assert "private_key" not in json.dumps(updated, default=str)

        # Raw DB still has original SA credentials
        raw = db.google_workspace_providers[created["id"]]
        assert (
            raw.credentials_json["service_account_json"]["project_id"]
            == "elder-test-proj"
        )
        assert raw.credentials_json["customer_id"] == "C99999999"

    def test_workspace_client_and_test_provider_read_nested_and_flat_creds(
        self, tmp_path
    ):
        db = _create_integrations_db(str(tmp_path / "gws_client.db"))
        svc = GoogleWorkspaceService(db)

        created = svc.create_provider(
            name="Client Test",
            organization_id=1,
            customer_id="C00112233",
            admin_email="admin@example.org",
            service_account_json=SAMPLE_SA,
        )

        mock_creds = MagicMock()
        mock_creds.with_subject.return_value = MagicMock()
        mock_directory = MagicMock()
        mock_directory.domains.return_value.list.return_value.execute.return_value = {
            "domains": [{"domainName": "example.org"}]
        }

        with (
            patch(
                "apps.api.services.google_workspace.service.service_account.Credentials.from_service_account_info",
                return_value=mock_creds,
            ) as mock_from_info,
            patch(
                "apps.api.services.google_workspace.service.build",
                return_value=mock_directory,
            ),
        ):
            res = svc.test_provider(created["id"])
            assert res["success"] is True
            assert res["customer_id"] == "C00112233"
            mock_from_info.assert_called_once()
            assert mock_from_info.call_args[0][0]["project_id"] == "elder-test-proj"

            # Flat dict fallback works too
            flat_row = SimpleNamespace(
                admin_email="admin@example.org",
                credentials_json=SAMPLE_SA,
            )
            svc._get_workspace_client(flat_row)
            assert mock_from_info.call_count == 2


class TestIntegrationRoutesRoundtrip:
    """Lightweight HTTP roundtrip tests for IAM and Google Workspace blueprints."""

    @staticmethod
    def _build_app(db):
        app = Quart(__name__)
        app.db = db  # type: ignore[attr-defined]
        app.config["TESTING"] = True

        @app.before_request
        def _inject_user():
            g.current_user = SimpleNamespace(
                id=1,
                is_superuser=True,
                tenant_id=1,
                global_role="admin",
                tenant_role="admin",
            )
            g.claims = {
                "sub": "1",
                "tenant": "1",
                "scope": "discovery:read discovery:write discovery:admin",
                "roles": ["admin"],
            }

        app.register_blueprint(iam_bp, url_prefix="/api/v1/iam")
        app.register_blueprint(gws_bp, url_prefix="/api/v1/google-workspace")
        return app

    @pytest.mark.asyncio
    async def test_iam_and_gws_routes_roundtrip(self, tmp_path):
        db = _create_integrations_db(str(tmp_path / "routes.db"))
        app = self._build_app(db)
        client = app.test_client()

        with (
            patch(
                "apps.api.auth.decorators.get_current_user",
                return_value=SimpleNamespace(
                    id=1, is_superuser=True, global_role="admin", tenant_role="admin"
                ),
            ),
        ):
            iam_create = await client.post(
                "/api/v1/iam/providers",
                json={
                    "name": "Route AWS IAM",
                    "provider_type": "aws_iam",
                    "organization_id": 2,
                    "description": "Route test",
                    "config": {
                        "region": "us-east-1",
                        "access_key_id": "AKIA_ROUTE",
                        "secret_access_key": "SEC_ROUTE",
                    },
                },
            )
            assert iam_create.status_code == 201
            iam_body = await iam_create.get_json()
            assert iam_body["organization_id"] == 2
            assert iam_body["description"] == "Route test"

            iam_list = await client.get("/api/v1/iam/providers?organization_id=2")
            assert iam_list.status_code == 200
            assert (await iam_list.get_json())["count"] == 1

            gws_create = await client.post(
                "/api/v1/google-workspace/providers",
                json={
                    "name": "Route GWS",
                    "organization_id": 1,
                    "domain": "route.example.com",
                    "customer_id": "C_ROUTE",
                    "admin_email": "admin@route.example.com",
                    "service_account_json": SAMPLE_SA,
                    "description": "Route GWS desc",
                },
            )
            assert gws_create.status_code == 201
            gws_body = await gws_create.get_json()
            assert gws_body["domain"] == "route.example.com"
            assert gws_body["credentials_json"] == "***REDACTED***"

            gws_put = await client.put(
                f"/api/v1/google-workspace/providers/{gws_body['id']}",
                json={"name": "Route GWS Updated", "enabled": False},
            )
            assert gws_put.status_code == 200
            assert (await gws_put.get_json())["enabled"] is False
