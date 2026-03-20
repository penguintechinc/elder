"""
Unit tests for Organization model (PyDAL runtime layer).

These tests verify CRUD operations on the organizations table
using the PyDAL database layer that the application actually uses at runtime.
"""

import pytest


class TestOrganizationModel:
    """Test Organization model functionality via PyDAL."""

    def test_organization_creation(self, app):
        """Test creating a basic organization."""
        with app.app_context():
            db = app.db
            org_id = db.organizations.insert(
                name="Test Organization",
                description="A test organization",
                tenant_id=1,
            )
            db.commit()

            org = db.organizations[org_id]
            assert org is not None
            assert org.name == "Test Organization"
            assert org.description == "A test organization"
            assert org.parent_id is None
            assert org.created_at is not None

            # Cleanup
            db(db.organizations.id == org_id).delete()
            db.commit()

    def test_organization_hierarchy(self, app):
        """Test parent-child organization relationships."""
        with app.app_context():
            db = app.db
            parent_id = db.organizations.insert(
                name="Parent Org", tenant_id=1
            )
            db.commit()

            child_id = db.organizations.insert(
                name="Child Org", parent_id=parent_id, tenant_id=1
            )
            db.commit()

            child = db.organizations[child_id]
            assert child.parent_id == parent_id

            children = db(db.organizations.parent_id == parent_id).select()
            assert len(children) == 1
            assert children[0].id == child_id

            # Cleanup
            db(db.organizations.id == child_id).delete()
            db(db.organizations.id == parent_id).delete()
            db.commit()

    def test_organization_ldap_dn(self, app):
        """Test LDAP DN assignment."""
        with app.app_context():
            db = app.db
            org_id = db.organizations.insert(
                name="LDAP Org",
                ldap_dn="ou=engineering,dc=example,dc=com",
                tenant_id=1,
            )
            db.commit()

            org = db.organizations[org_id]
            assert org.ldap_dn == "ou=engineering,dc=example,dc=com"

            # Cleanup
            db(db.organizations.id == org_id).delete()
            db.commit()

    def test_organization_saml_group(self, app):
        """Test SAML group assignment."""
        with app.app_context():
            db = app.db
            org_id = db.organizations.insert(
                name="SAML Org",
                saml_group="engineering-team",
                tenant_id=1,
            )
            db.commit()

            org = db.organizations[org_id]
            assert org.saml_group == "engineering-team"

            # Cleanup
            db(db.organizations.id == org_id).delete()
            db.commit()

    def test_organization_village_id(self, app):
        """Test village_id generation."""
        with app.app_context():
            db = app.db
            org_id = db.organizations.insert(
                name="Village ID Org", tenant_id=1
            )
            db.commit()

            org = db.organizations[org_id]
            assert org.village_id is not None
            assert len(str(org.village_id)) > 0

            # Cleanup
            db(db.organizations.id == org_id).delete()
            db.commit()

    def test_organization_update(self, app):
        """Test updating organization fields."""
        with app.app_context():
            db = app.db
            org_id = db.organizations.insert(
                name="Original Name", tenant_id=1
            )
            db.commit()

            db(db.organizations.id == org_id).update(
                name="Updated Name", description="New description"
            )
            db.commit()

            org = db.organizations[org_id]
            assert org.name == "Updated Name"
            assert org.description == "New description"

            # Cleanup
            db(db.organizations.id == org_id).delete()
            db.commit()

    def test_organization_deletion(self, app):
        """Test organization deletion."""
        with app.app_context():
            db = app.db
            org_id = db.organizations.insert(
                name="Delete Me", tenant_id=1
            )
            db.commit()

            db(db.organizations.id == org_id).delete()
            db.commit()

            deleted_org = db.organizations[org_id]
            assert deleted_org is None

    def test_multiple_organizations(self, app):
        """Test creating multiple organizations."""
        with app.app_context():
            db = app.db
            ids = []
            for name in ["Org 1", "Org 2", "Org 3"]:
                ids.append(
                    db.organizations.insert(name=name, tenant_id=1)
                )
            db.commit()

            orgs = db(db.organizations.id.belongs(ids)).select()
            assert len(orgs) == 3
            org_names = [o.name for o in orgs]
            assert "Org 1" in org_names
            assert "Org 2" in org_names
            assert "Org 3" in org_names

            # Cleanup
            db(db.organizations.id.belongs(ids)).delete()
            db.commit()

    def test_organization_query_by_parent(self, app):
        """Test querying organizations by parent."""
        with app.app_context():
            db = app.db
            parent_id = db.organizations.insert(
                name="Parent", tenant_id=1
            )
            db.commit()

            child_ids = []
            for name in ["Child 1", "Child 2"]:
                child_ids.append(
                    db.organizations.insert(
                        name=name, parent_id=parent_id, tenant_id=1
                    )
                )
            db.commit()

            children = db(db.organizations.parent_id == parent_id).select()
            assert len(children) == 2
            child_names = [c.name for c in children]
            assert "Child 1" in child_names
            assert "Child 2" in child_names

            # Cleanup
            db(db.organizations.id.belongs(child_ids)).delete()
            db(db.organizations.id == parent_id).delete()
            db.commit()
