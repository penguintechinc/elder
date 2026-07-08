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
        with app.app_context():
            db = app.db
            self.org_id = db.organizations.insert(
                name="Test Org", tenant_id=1
            )
            db.commit()
            yield
            # Cleanup org (entities cleaned in each test)
            db(db.organizations.id == self.org_id).delete()
            db.commit()

    def test_entity_creation(self, app):
        """Test creating a basic entity."""
        with app.app_context():
            db = app.db
            entity_id = db.entities.insert(
                name="Test Server",
                entity_type="compute",
                organization_id=self.org_id,
                description="A test server",
            )
            db.commit()

            entity = db.entities[entity_id]
            assert entity is not None
            assert entity.name == "Test Server"
            assert entity.entity_type == "compute"
            assert entity.organization_id == self.org_id
            assert entity.description == "A test server"

            # Cleanup
            db(db.entities.id == entity_id).delete()
            db.commit()

    def test_entity_types(self, app):
        """Test various entity types."""
        with app.app_context():
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
                        entity_type=entity_type,
                        organization_id=self.org_id,
                    )
                )
            db.commit()

            for eid, (entity_type, name) in zip(ids, entity_types):
                entity = db.entities[eid]
                assert entity is not None
                assert entity.entity_type == entity_type
                assert entity.name == name

            # Cleanup
            db(db.entities.id.belongs(ids)).delete()
            db.commit()

    def test_entity_attributes(self, app):
        """Test entity attributes (JSON) field."""
        with app.app_context():
            db = app.db
            attrs = {"cpu": "8 cores", "memory": "32GB", "os": "Ubuntu 22.04"}
            entity_id = db.entities.insert(
                name="Server with attrs",
                entity_type="compute",
                organization_id=self.org_id,
                attributes=attrs,
            )
            db.commit()

            entity = db.entities[entity_id]
            assert entity.attributes is not None
            assert entity.attributes["cpu"] == "8 cores"
            assert entity.attributes["memory"] == "32GB"

            # Cleanup
            db(db.entities.id == entity_id).delete()
            db.commit()

    def test_entity_village_id(self, app):
        """Test village_id generation."""
        with app.app_context():
            db = app.db
            entity_id = db.entities.insert(
                name="Village Entity",
                entity_type="compute",
                organization_id=self.org_id,
            )
            db.commit()

            entity = db.entities[entity_id]
            assert entity.village_id is not None
            assert len(str(entity.village_id)) > 0

            # Cleanup
            db(db.entities.id == entity_id).delete()
            db.commit()

    def test_entity_update(self, app):
        """Test updating entity fields."""
        with app.app_context():
            db = app.db
            entity_id = db.entities.insert(
                name="Original Name",
                entity_type="compute",
                organization_id=self.org_id,
            )
            db.commit()

            db(db.entities.id == entity_id).update(
                name="Updated Name", description="New description"
            )
            db.commit()

            entity = db.entities[entity_id]
            assert entity.name == "Updated Name"
            assert entity.description == "New description"

            # Cleanup
            db(db.entities.id == entity_id).delete()
            db.commit()

    def test_entity_deletion(self, app):
        """Test entity deletion."""
        with app.app_context():
            db = app.db
            entity_id = db.entities.insert(
                name="Delete Me",
                entity_type="compute",
                organization_id=self.org_id,
            )
            db.commit()

            db(db.entities.id == entity_id).delete()
            db.commit()

            deleted = db.entities[entity_id]
            assert deleted is None

    def test_multiple_entities_same_org(self, app):
        """Test multiple entities in same organization."""
        with app.app_context():
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
                        entity_type=etype,
                        organization_id=self.org_id,
                    )
                )
            db.commit()

            entities = db(
                db.entities.organization_id == self.org_id
            ).select()
            assert len(entities) >= 3

            # Cleanup
            db(db.entities.id.belongs(ids)).delete()
            db.commit()

    def test_entity_query_by_type(self, app):
        """Test querying entities by type."""
        with app.app_context():
            db = app.db
            ids = []
            ids.append(
                db.entities.insert(
                    name="Server 1",
                    entity_type="compute",
                    organization_id=self.org_id,
                )
            )
            ids.append(
                db.entities.insert(
                    name="Server 2",
                    entity_type="compute",
                    organization_id=self.org_id,
                )
            )
            ids.append(
                db.entities.insert(
                    name="Router 1",
                    entity_type="network",
                    organization_id=self.org_id,
                )
            )
            db.commit()

            compute = db(
                (db.entities.entity_type == "compute")
                & (db.entities.id.belongs(ids))
            ).select()
            assert len(compute) == 2

            network = db(
                (db.entities.entity_type == "network")
                & (db.entities.id.belongs(ids))
            ).select()
            assert len(network) == 1

            # Cleanup
            db(db.entities.id.belongs(ids)).delete()
            db.commit()

    def test_entity_hierarchy(self, app):
        """Test parent-child entity relationships."""
        with app.app_context():
            db = app.db
            parent_id = db.entities.insert(
                name="Parent DC",
                entity_type="datacenter",
                organization_id=self.org_id,
            )
            db.commit()

            child_id = db.entities.insert(
                name="Child Server",
                entity_type="compute",
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
