"""
Integration tests for complete workflows.

These tests verify end-to-end functionality with real database interactions
(using test database), but still avoid external network calls.
"""

from apps.api import db
from apps.api.models.entity_types import EntityType
from apps.api.modules.infrastructure.models.dependency import Dependency
from apps.api.modules.infrastructure.models.entity import Entity
from apps.api.modules.infrastructure.models.organization import Organization


class TestCompleteWorkflow:
    """Test complete application workflows."""

    def test_organization_entity_dependency_workflow(self, app):
        """Test creating complete organization -> entity -> dependency workflow."""
        with app.app_context():
            # Step 1: Create organization hierarchy
            root = Organization(name="Root Org")
            db.session.add(root)
            db.session.commit()

            dept = Organization(name="Engineering", parent_id=root.id)
            db.session.add(dept)
            db.session.commit()

            team = Organization(name="Backend Team", parent_id=dept.id)
            db.session.add(team)
            db.session.commit()

            # Verify hierarchy
            assert team.parent.id == dept.id
            assert dept.parent.id == root.id
            assert len(root.children) == 1
            assert len(dept.children) == 1

            # Step 2: Create entities
            datacenter = Entity(
                name="DC1",
                entity_type=EntityType.DATACENTER,
                organization_id=root.id,
                metadata={"region": "us-west-2"},
            )
            db.session.add(datacenter)

            subnet = Entity(
                name="10.0.1.0/24",
                entity_type=EntityType.SUBNET,
                organization_id=dept.id,
                metadata={"vlan": "100"},
            )
            db.session.add(subnet)

            server = Entity(
                name="web-server-01",
                entity_type=EntityType.COMPUTE,
                organization_id=team.id,
                metadata={"cpu": "4", "memory": "16GB"},
            )
            db.session.add(server)
            db.session.commit()

            # Step 3: Create dependencies
            # Server depends on subnet
            dep1 = Dependency(
                source_entity_id=server.id,
                target_entity_id=subnet.id,
                dependency_type="depends_on",
            )
            db.session.add(dep1)

            # Subnet depends on datacenter
            dep2 = Dependency(
                source_entity_id=subnet.id,
                target_entity_id=datacenter.id,
                dependency_type="part_of",
            )
            db.session.add(dep2)
            db.session.commit()

            # Verify complete setup
            assert server.dependencies_out.count() == 1
            assert subnet.dependencies_out.count() == 1
            assert datacenter.dependencies_in.count() == 1

            # Verify we can trace dependencies
            server_deps = Dependency.query.filter_by(source_entity_id=server.id).all()
            assert len(server_deps) == 1
            assert server_deps[0].target.name == "10.0.1.0/24"

    def test_multi_tier_organization_structure(self, app):
        """Test complex multi-tier organization structure."""
        with app.app_context():
            # Create 3-tier structure
            company = Organization(name="ACME Corp")
            db.session.add(company)
            db.session.commit()

            # Departments
            engineering = Organization(name="Engineering", parent_id=company.id)
            sales = Organization(name="Sales", parent_id=company.id)
            db.session.add_all([engineering, sales])
            db.session.commit()

            # Teams under Engineering
            backend = Organization(name="Backend", parent_id=engineering.id)
            frontend = Organization(name="Frontend", parent_id=engineering.id)
            db.session.add_all([backend, frontend])
            db.session.commit()

            # Verify structure
            assert len(company.children) == 2
            assert len(engineering.children) == 2
            assert backend.parent.parent.id == company.id

            # Query all descendants
            all_orgs = Organization.query.all()
            assert len(all_orgs) >= 5

    def test_entity_type_distribution(self, app):
        """Test creating and querying various entity types."""
        with app.app_context():
            org = Organization(name="Test Org")
            db.session.add(org)
            db.session.commit()

            # Create one of each entity type
            entities = [
                Entity(
                    name="DC1",
                    entity_type=EntityType.DATACENTER,
                    organization_id=org.id,
                ),
                Entity(
                    name="10.0.0.0/16",
                    entity_type=EntityType.SUBNET,
                    organization_id=org.id,
                ),
                Entity(
                    name="server-01",
                    entity_type=EntityType.COMPUTE,
                    organization_id=org.id,
                ),
                Entity(
                    name="router-01",
                    entity_type=EntityType.NETWORK,
                    organization_id=org.id,
                ),
                Entity(
                    name="admin", entity_type=EntityType.USER, organization_id=org.id
                ),
                Entity(
                    name="CVE-2024-1234",
                    entity_type=EntityType.SECURITY_ISSUE,
                    organization_id=org.id,
                ),
            ]
            db.session.add_all(entities)
            db.session.commit()

            # Query by type
            compute = Entity.query.filter_by(entity_type=EntityType.COMPUTE).all()
            assert len(compute) >= 1

            network = Entity.query.filter_by(entity_type=EntityType.NETWORK).all()
            assert len(network) >= 1

            # Verify all types present
            all_entities = Entity.query.filter_by(organization_id=org.id).all()
            assert len(all_entities) == 6

    def test_dependency_graph_traversal(self, app):
        """Test traversing dependency graph."""
        with app.app_context():
            org = Organization(name="Test Org")
            db.session.add(org)
            db.session.commit()

            # Create chain: A -> B -> C -> D
            entity_a = Entity(
                name="A", entity_type=EntityType.COMPUTE, organization_id=org.id
            )
            entity_b = Entity(
                name="B", entity_type=EntityType.COMPUTE, organization_id=org.id
            )
            entity_c = Entity(
                name="C", entity_type=EntityType.COMPUTE, organization_id=org.id
            )
            entity_d = Entity(
                name="D", entity_type=EntityType.COMPUTE, organization_id=org.id
            )
            db.session.add_all([entity_a, entity_b, entity_c, entity_d])
            db.session.commit()

            dep_ab = Dependency(
                source_entity_id=entity_a.id,
                target_entity_id=entity_b.id,
                dependency_type="depends_on",
            )
            dep_bc = Dependency(
                source_entity_id=entity_b.id,
                target_entity_id=entity_c.id,
                dependency_type="depends_on",
            )
            dep_cd = Dependency(
                source_entity_id=entity_c.id,
                target_entity_id=entity_d.id,
                dependency_type="depends_on",
            )
            db.session.add_all([dep_ab, dep_bc, dep_cd])
            db.session.commit()

            # Verify chain
            assert entity_a.dependencies_out.count() == 1
            assert entity_b.dependencies_in.count() == 1
            assert entity_b.dependencies_out.count() == 1
            assert entity_d.dependencies_in.count() == 1
            assert entity_d.dependencies_out.count() == 0

            # Get all dependencies
            all_deps = Dependency.query.all()
            assert len(all_deps) >= 3

    def test_metadata_persistence(self, app):
        """Test metadata persistence across operations."""
        with app.app_context():
            org = Organization(
                name="Meta Org", metadata={"key1": "value1", "key2": 123}
            )
            db.session.add(org)
            db.session.commit()
            org_id = org.id

            # Retrieve and verify
            retrieved = Organization.query.get(org_id)
            assert retrieved.metadata["key1"] == "value1"
            assert retrieved.metadata["key2"] == 123

            # Update metadata
            retrieved.metadata["key3"] = "value3"
            db.session.commit()

            # Verify update
            updated = Organization.query.get(org_id)
            assert "key3" in updated.metadata
            assert updated.metadata["key3"] == "value3"
