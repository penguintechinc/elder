"""Schemas package for API validation."""

# flake8: noqa: E501

from .organization import OrganizationCreateSchema, OrganizationUpdateSchema

__all__ = [
    "OrganizationCreateSchema",
    "OrganizationUpdateSchema",
]
