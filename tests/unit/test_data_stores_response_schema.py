"""
Regression tests: data store endpoints must expose an exact field set via
`@validate_response` DTOs — not raw `.as_dict()` serialization.

security-audit: "0 @validate_response / raw ORM serialization — no response
schemas".
"""

from apps.api.models.pydantic.data_store import (
    DataStoreLabelAssignmentResponse,
    DataStoreLabelListResponse,
    DataStoreLabelResponse,
    DataStoreListResponse,
    DataStoreResponse,
)

EXPECTED_DATA_STORE_FIELDS = {
    "id",
    "tenant_id",
    "organization_id",
    "name",
    "external_id",
    "description",
    "storage_type",
    "storage_provider",
    "location_region",
    "location_physical",
    "data_classification",
    "encryption_at_rest",
    "encryption_in_transit",
    "encryption_key_id",
    "retention_days",
    "backup_enabled",
    "backup_frequency",
    "access_control_type",
    "poc_identity_id",
    "compliance_frameworks",
    "contains_pii",
    "contains_phi",
    "contains_pci",
    "size_bytes",
    "last_access_audit",
    "metadata",
    "created_by",
    "is_active",
    "village_id",
    "created_at",
    "updated_at",
}


class TestDataStoreResponseFieldSet:
    def test_exact_field_set(self):
        assert set(DataStoreResponse.model_fields) == EXPECTED_DATA_STORE_FIELDS


class TestDataStoreListResponseFieldSet:
    def test_exact_field_set(self):
        assert set(DataStoreListResponse.model_fields) == {
            "items",
            "total",
            "page",
            "per_page",
            "pages",
        }


class TestDataStoreLabelResponseFieldSet:
    def test_exact_field_set(self):
        assert set(DataStoreLabelResponse.model_fields) == {
            "id",
            "name",
            "color",
            "description",
            "created_at",
            "updated_at",
        }


class TestDataStoreLabelListResponseFieldSet:
    def test_exact_field_set(self):
        assert set(DataStoreLabelListResponse.model_fields) == {"labels"}


class TestDataStoreLabelAssignmentResponseFieldSet:
    def test_exact_field_set(self):
        assert set(DataStoreLabelAssignmentResponse.model_fields) == {
            "id",
            "data_store_id",
            "label_id",
            "created_at",
        }


class TestDataStoresRoutesUseValidateResponse:
    def test_all_handlers_decorated(self):
        from quart_schema.validation import QUART_SCHEMA_RESPONSE_ATTRIBUTE

        from apps.api.modules.infrastructure.routes import data_stores as mod

        for name in (
            "list_data_stores",
            "create_data_store",
            "get_data_store",
            "update_data_store",
            "get_data_store_labels",
            "add_data_store_label",
        ):
            func = getattr(mod, name)
            schemas = getattr(func, QUART_SCHEMA_RESPONSE_ATTRIBUTE, None)
            assert schemas, f"{name} is missing @validate_response wiring"
