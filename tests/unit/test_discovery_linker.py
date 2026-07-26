"""Tests for the cloud-discovery relationship linker (PR1 core engine)."""

from apps.api.models.security import Certificate
from apps.api.modules.infrastructure.models.infrastructure import (
    DataStore,
    NetworkingResource,
)
from apps.api.modules.sbom.models.assets import Service, Software


def test_domain_tables_have_external_id_column():
    # regression: edge resolution keys on external_id; these tables lacked it.
    for model in (NetworkingResource, Service, Software, DataStore, Certificate):
        assert "external_id" in model.__table__.columns, model.__tablename__
