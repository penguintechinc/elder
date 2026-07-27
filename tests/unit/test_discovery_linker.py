"""Tests for the cloud-discovery relationship linker (PR1 core engine)."""

import uuid

import pytest

from apps.api.models.security import Certificate
from apps.api.modules.infrastructure.models.infrastructure import (
    DataStore,
    NetworkingResource,
)
from apps.api.modules.sbom.models.assets import Service, Software
from apps.worker.discovery.service import DiscoveryService


def test_domain_tables_have_external_id_column():
    # regression: edge resolution keys on external_id; these tables lacked it.
    for model in (NetworkingResource, Service, Software, DataStore, Certificate):
        assert "external_id" in model.__table__.columns, model.__tablename__


@pytest.fixture
def seeded(app):
    """A DiscoveryService on the test DAL with one tenant + organization.

    The test DB is a session-persistent SQLite file with no per-test
    rollback, so a fixed tenant slug would collide with the UNIQUE
    constraint on `tenants.slug` the second time this fixture runs. Use a
    per-invocation unique slug instead (same pattern as
    tests/unit/test_api_diagram_collab.py) so repeated/parallel use of this
    fixture never collides.
    """
    db = app.db
    tenant_id = db.tenants.insert(
        name="Linker Test Tenant",
        slug=f"linker-test-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    org_id = db.organizations.insert(name="Linker Test Org", tenant_id=tenant_id)
    db.commit()
    yield DiscoveryService(db), db, org_id


def test_entity_insert_sets_external_id(seeded):
    service, db, org_id = seeded
    eid = service._store_as_entity(
        org_id,
        {"name": "web-1", "resource_type": "ec2_instance", "resource_id": "i-abc"},
        "compute",
    )
    db.commit()
    row = db(db.entities.id == eid).select().first()
    assert row.external_id == "i-abc"


def test_entity_update_path_does_not_raise_and_refreshes_metadata(seeded):
    # regression: the UPDATE branch of _store_as_entity wrote an "attributes"
    # key, but the entities table has no such column (it's "metadata"), so
    # re-discovering an existing entity raised CompileError: Unconsumed
    # column names: attributes. This is uncaught in _store_as_entity.
    service, db, org_id = seeded
    resource = {
        "name": "web-1",
        "resource_type": "ec2_instance",
        "resource_id": "i-update-path",
        "region": "us-east-1",
    }

    first_id = service._store_as_entity(org_id, resource, "compute")
    db.commit()

    # Second call on the same org/sub_type/name takes the UPDATE branch.
    second_id = service._store_as_entity(org_id, resource, "compute")
    db.commit()

    assert second_id == first_id

    row = db(db.entities.id == second_id).select().first()
    assert row.external_id == "i-update-path"
    # penguin-dal returns the metadata column as `metadata` (the actual DB
    # column name), not `entity_metadata` (the SQLAlchemy attribute alias).
    assert row.metadata is not None
    assert row.metadata.get("resource_id") == "i-update-path"
    assert row.metadata.get("region") == "us-east-1"


def test_upsert_network_entity_mapping_creates_row(seeded):
    # Characterization test: this is expected to PASS immediately.
    # The historical bug (networking_resource_id vs network_id) is already
    # fixed on this branch; this guards against future regression.
    service, db, org_id = seeded

    net_id = service._upsert_networking_resource(
        org_id, name="vpc-1", network_type="vpc"
    )
    db.commit()
    assert net_id is not None

    entity_id = service._store_as_entity(
        org_id,
        {"name": "web-2", "resource_type": "ec2_instance", "resource_id": "i-map-1"},
        "compute",
    )
    db.commit()
    assert entity_id is not None

    service._upsert_network_entity_mapping(net_id, entity_id, "in_network")
    db.commit()

    row = (
        db(
            (db.network_entity_mappings.network_id == net_id)
            & (db.network_entity_mappings.entity_id == entity_id)
        )
        .select()
        .first()
    )
    assert row is not None
    assert row.relationship_type == "in_network"
