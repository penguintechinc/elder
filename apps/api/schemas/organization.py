"""Organization schemas for validation."""

# flake8: noqa: E501

from marshmallow import Schema, fields, validate


class OrganizationCreateSchema(Schema):
    """Schema for creating an organization."""

    name = fields.String(required=True, validate=validate.Length(min=1, max=255))
    description = fields.String(allow_none=True)
    organization_type = fields.String(
        load_default="organization",
        validate=validate.OneOf(
            [
                "department",
                "organization",
                "team",
                "collection",
                "other",
                "customer_company",
            ]
        ),
    )
    parent_id = fields.Integer(allow_none=True)
    tenant_id = fields.Integer(required=True)
    ldap_dn = fields.String(allow_none=True)
    saml_group = fields.String(allow_none=True)
    owner_identity_id = fields.Integer(allow_none=True)
    owner_group_id = fields.Integer(allow_none=True)


class OrganizationUpdateSchema(Schema):
    """Schema for updating an organization."""

    name = fields.String(validate=validate.Length(min=1, max=255))
    description = fields.String(allow_none=True)
    organization_type = fields.String(
        validate=validate.OneOf(
            [
                "department",
                "organization",
                "team",
                "collection",
                "other",
                "customer_company",
            ]
        )
    )
    parent_id = fields.Integer(allow_none=True)
    tenant_id = fields.Integer()
    ldap_dn = fields.String(allow_none=True)
    saml_group = fields.String(allow_none=True)
    owner_identity_id = fields.Integer(allow_none=True)
    owner_group_id = fields.Integer(allow_none=True)
