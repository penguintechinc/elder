"""
Regression tests: cost sync job endpoints must expose an exact field set via
`@validate_response` DTOs — not raw `.as_dict()` serialization.

security-audit: "0 @validate_response / raw ORM serialization — no response
schemas". This module additionally fixes a genuine credential leak:
cost_sync_jobs.config_json holds AWS Cost Explorer credentials
(aws_access_key_id/aws_secret_access_key) that were previously returned in
full to any webhooks_alerting:read-scoped caller via `.as_dict()`.
"""

from types import SimpleNamespace

from apps.api.models.pydantic.cost import CostSyncJobListResponse, CostSyncJobResponse


def _fake_row(**kwargs):
    return SimpleNamespace(as_dict=lambda: dict(kwargs))


class TestCostSyncJobResponseFieldSet:
    def test_exact_field_set(self):
        assert set(CostSyncJobResponse.model_fields) == {
            "id",
            "name",
            "provider",
            "organization_id",
            "config_json",
            "schedule_interval",
            "enabled",
            "last_run_at",
            "next_run_at",
            "created_at",
        }

    def test_from_row_redacts_aws_credentials(self):
        row = _fake_row(
            id=1,
            name="aws-monthly",
            provider="aws",
            organization_id=1,
            config_json={
                # deliberately not AWS-key-shaped -- gitleaks flags real-looking
                # AKIA-prefixed strings even in test fixtures; the redaction
                # logic keys off the dict key name, not the value's shape.
                "aws_access_key_id": "not-a-real-credential-fixture-value",
                "aws_secret_access_key": "not-a-real-credential-fixture-value",
                "region": "us-east-1",
            },
            schedule_interval=86400,
            enabled=True,
            last_run_at=None,
            next_run_at=None,
            created_at=None,
        )
        dto = CostSyncJobResponse.from_row(row)
        assert dto.config_json["aws_access_key_id"] == "***REDACTED***"
        assert dto.config_json["aws_secret_access_key"] == "***REDACTED***"
        assert dto.config_json["region"] == "us-east-1"
        assert "not-a-real-credential-fixture-value" not in dto.model_dump_json()


class TestCostSyncJobListResponseFieldSet:
    def test_exact_field_set(self):
        assert set(CostSyncJobListResponse.model_fields) == {"data"}


class TestCostsRoutesUseValidateResponse:
    def test_list_sync_jobs_decorated(self):
        from quart_schema.validation import QUART_SCHEMA_RESPONSE_ATTRIBUTE

        from apps.api.modules.webhooks_alerting.routes import costs as mod

        schemas = getattr(mod.list_sync_jobs, QUART_SCHEMA_RESPONSE_ATTRIBUTE, None)
        assert schemas, "list_sync_jobs is missing @validate_response wiring"
