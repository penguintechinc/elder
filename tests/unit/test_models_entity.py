"""
Unit tests for Entity model (PyDAL runtime layer).

These tests verify CRUD operations on the entities table
using the PyDAL database layer that the application actually uses at runtime.
"""

import pytest


class TestEntityModel:
    """Test Entity model functionality via PyDAL."""

    @pytest.fixture(autouse=True)
    def setup_org(self, app):
        """Create a test organization for entity tests."""
        db = app.db
        self.org_id = db.organizations.insert(name="Test Org", tenant_id=1)
        db.commit()
        yield
        # Cleanup org (entities cleaned in each test)
        db(db.organizations.id == self.org_id).delete()
        db.commit()

    def test_entity_creation(self, app):
        """Test creating a basic entity."""
        db = app.db
        entity_id = db.entities.insert(
            name="Test Server",
            type="compute",
            organization_id=self.org_id,
        )
        db.commit()

        entity = db.entities[entity_id]
        assert entity is not None
        assert entity.name == "Test Server"
        assert entity.type == "compute"
        assert entity.organization_id == self.org_id

        # Cleanup
        db(db.entities.id == entity_id).delete()
        db.commit()

    def test_entity_types(self, app):
        """Test various entity types."""
        db = app.db
        entity_types = [
            ("datacenter", "DC1"),
            ("subnet", "10.0.0.0/24"),
            ("compute", "server-01"),
            ("network", "router-01"),
        ]

        ids = []
        for entity_type, name in entity_types:
            ids.append(
                db.entities.insert(
                    name=name,
                    type=entity_type,
                    organization_id=self.org_id,
                )
            )
        db.commit()

        for eid, (entity_type, name) in zip(ids, entity_types):
            entity = db.entities[eid]
            assert entity is not None
            assert entity.type == entity_type
            assert entity.name == name

        # Cleanup
        db(db.entities.id.belongs(ids)).delete()
        db.commit()

    def test_entity_attributes(self, app):
        """Test entity metadata (JSON) field."""
        db = app.db
        attrs = {"cpu": "8 cores", "memory": "32GB", "os": "Ubuntu 22.04"}
        entity_id = db.entities.insert(
            name="Server with attrs",
            type="compute",
            organization_id=self.org_id,
            metadata=attrs,
        )
        db.commit()

        entity = db.entities[entity_id]
        # PyDAL returns metadata column as 'metadata' (not entity_metadata)
        assert entity.metadata is not None
        assert entity.metadata["cpu"] == "8 cores"
        assert entity.metadata["memory"] == "32GB"

        # Cleanup
        db(db.entities.id == entity_id).delete()
        db.commit()

    def test_entity_village_id(self, app):
        """Test village_id column is nullable and can be set explicitly."""
        db = app.db
        # Create entity without village_id (should be nullable)
        entity_id = db.entities.insert(
            name="Village Entity",
            type="compute",
            organization_id=self.org_id,
        )
        db.commit()

        entity = db.entities[entity_id]
        # village_id is nullable, should be None if not explicitly set
        assert entity.village_id is None

        # Now test setting village_id explicitly (AD-3 format: TTTTTTTT-OOOOOOOOOOOOOOOO)
        valid_village_id = "00000001-0000000000000001"
        db(db.entities.id == entity_id).update(village_id=valid_village_id)
        db.commit()

        entity = db.entities[entity_id]
        assert entity.village_id == valid_village_id

        # Cleanup
        db(db.entities.id == entity_id).delete()
        db.commit()

    def test_entity_update(self, app):
        """Test updating entity fields."""
        db = app.db
        entity_id = db.entities.insert(
            name="Original Name",
            type="compute",
            organization_id=self.org_id,
        )
        db.commit()

        db(db.entities.id == entity_id).update(name="Updated Name", status="active")
        db.commit()

        entity = db.entities[entity_id]
        assert entity.name == "Updated Name"
        assert entity.status == "active"

        # Cleanup
        db(db.entities.id == entity_id).delete()
        db.commit()

    def test_entity_deletion(self, app):
        """Test entity deletion."""
        db = app.db
        entity_id = db.entities.insert(
            name="Delete Me",
            type="compute",
            organization_id=self.org_id,
        )
        db.commit()

        db(db.entities.id == entity_id).delete()
        db.commit()

        deleted = db.entities[entity_id]
        assert deleted is None

    def test_multiple_entities_same_org(self, app):
        """Test multiple entities in same organization."""
        db = app.db
        ids = []
        for name, etype in [
            ("Entity 1", "compute"),
            ("Entity 2", "network"),
            ("Entity 3", "subnet"),
        ]:
            ids.append(
                db.entities.insert(
                    name=name,
                    type=etype,
                    organization_id=self.org_id,
                )
            )
        db.commit()

        entities = db(db.entities.organization_id == self.org_id).select()
        assert len(entities) >= 3

        # Cleanup
        db(db.entities.id.belongs(ids)).delete()
        db.commit()

    def test_entity_query_by_type(self, app):
        """Test querying entities by type."""
        db = app.db
        ids = []
        ids.append(
            db.entities.insert(
                name="Server 1",
                type="compute",
                organization_id=self.org_id,
            )
        )
        ids.append(
            db.entities.insert(
                name="Server 2",
                type="compute",
                organization_id=self.org_id,
            )
        )
        ids.append(
            db.entities.insert(
                name="Router 1",
                type="network",
                organization_id=self.org_id,
            )
        )
        db.commit()

        compute = db(
            (db.entities.type == "compute") & (db.entities.id.belongs(ids))
        ).select()
        assert len(compute) == 2

        network = db(
            (db.entities.type == "network") & (db.entities.id.belongs(ids))
        ).select()
        assert len(network) == 1

        # Cleanup
        db(db.entities.id.belongs(ids)).delete()
        db.commit()

    def test_entity_hierarchy(self, app):
        """Test parent-child entity relationships."""
        db = app.db
        parent_id = db.entities.insert(
            name="Parent DC",
            type="datacenter",
            organization_id=self.org_id,
        )
        db.commit()

        child_id = db.entities.insert(
            name="Child Server",
            type="compute",
            organization_id=self.org_id,
            parent_id=parent_id,
        )
        db.commit()

        child = db.entities[child_id]
        assert child.parent_id == parent_id

        children = db(db.entities.parent_id == parent_id).select()
        assert len(children) == 1

        # Cleanup
        db(db.entities.id == child_id).delete()
        db(db.entities.id == parent_id).delete()
        db.commit()
