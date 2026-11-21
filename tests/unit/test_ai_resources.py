"""Unit tests for the AI Resources module (DTOs, route schema wiring, mapping)."""

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from apps.api.models.pydantic.ai import (
    AIResourceDTO,
    AIResourceListResponse,
    CreateAIResourceRequest,
    UpdateAIResourceRequest,
)
from apps.api.modules.ai.routes import _row_to_dto


class TestAIResourceDTOFieldSet:
    def test_exact_field_set(self):
        assert set(AIResourceDTO.model_fields) == {
            "id",
            "tenant_id",
            "name",
            "description",
            "organization_id",
            "resource_type",
            "provider",
            "model_name",
            "endpoint_url",
            "status",
            "agreement_level",
            "plan_info",
            "budget_monthly",
            "cost_per_10m_tokens",
            "seats",
            "parent_system_id",
            "owner_id",
            "notes",
            "tags",
            "metadata",
            "is_active",
            "created_at",
            "updated_at",
            "village_id",
        }


class TestAIResourceListResponseFieldSet:
    def test_exact_field_set(self):
        assert set(AIResourceListResponse.model_fields) == {
            "items",
            "total",
            "page",
            "per_page",
            "pages",
        }


class TestAIResourceRoutesUseValidateResponse:
    def test_all_handlers_decorated(self):
        from quart_schema.validation import QUART_SCHEMA_RESPONSE_ATTRIBUTE

        from apps.api.modules.ai import routes as mod

        for name in (
            "list_ai_resources",
            "create_ai_resource",
            "get_ai_resource",
            "update_ai_resource",
        ):
            func = getattr(mod, name)
            schemas = getattr(func, QUART_SCHEMA_RESPONSE_ATTRIBUTE, None)
            assert schemas, f"{name} is missing @validate_response wiring"


class TestAIResourceMappingAndValidation:
    def test_row_to_dto_maps_extra_metadata_and_decimals(self):
        now = datetime.now(UTC)
        row = SimpleNamespace(
            id=1,
            tenant_id=10,
            name="Claude 3.7 Sonnet",
            description="Primary LLM",
            organization_id=5,
            resource_type="llm",
            provider="Anthropic",
            model_name="claude-3-7-sonnet",
            endpoint_url="https://api.anthropic.com",
            status="allowed",
            agreement_level="enterprise",
            plan_info="Enterprise",
            budget_monthly=Decimal("1500.00"),
            cost_per_10m_tokens=Decimal("30.0000"),
            seats=50,
            parent_system_id=None,
            owner_id=2,
            notes="Approved for prod",
            tags=["prod", "llm"],
            extra_metadata={"baa": True, "fedramp": "moderate"},
            is_active=True,
            created_at=now,
            updated_at=now,
            village_id=None,
        )
        dto = _row_to_dto(row)
        assert dto.metadata == {"baa": True, "fedramp": "moderate"}
        assert dto.budget_monthly == 1500.0
        assert dto.cost_per_10m_tokens == 30.0
        assert dto.resource_type == "llm"
        assert dto.status == "allowed"
        assert dto.agreement_level == "enterprise"

    def test_request_models_accept_valid_literals(self):
        req = CreateAIResourceRequest(
            name="LiteLLM Gateway",
            organization_id=1,
            resource_type="ai_gateway",
            status="restricted",
            agreement_level="team",
            metadata={"baa": False},
        )
        assert req.resource_type == "ai_gateway"
        assert req.metadata == {"baa": False}

        upd = UpdateAIResourceRequest(status="allowed", seats=100)
        assert upd.status == "allowed"
        assert upd.seats == 100
