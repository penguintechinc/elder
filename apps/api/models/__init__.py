"""Database models for Elder application using PyDAL."""

# flake8: noqa: E501

# PyDAL / SQLAlchemy table definitions
from apps.api.models.csv_import import CsvImportJob

# Python 3.12 dataclass DTOs
from apps.api.models.dataclasses import (
    AuditLogDTO,
    CreateDependencyRequest,
    CreateEntityRequest,
    CreateIdentityGroupRequest,
    CreateIdentityRequest,
    CreateIssueCommentRequest,
    CreateIssueRequest,
    CreateMetadataFieldRequest,
    CreateOrganizationRequest,
    CreateResourceRoleRequest,
    DependencyDTO,
    EntityDTO,
    IdentityDTO,
    IdentityGroupDTO,
    IssueCommentDTO,
    IssueDTO,
    IssueLabelDTO,
    LoginRequest,
    LoginResponse,
    MetadataFieldDTO,
    OrganizationDTO,
    PaginatedResponse,
    PermissionDTO,
    RegisterRequest,
    ResourceRoleDTO,
    RoleDTO,
    UpdateEntityRequest,
    UpdateIdentityRequest,
    UpdateIssueRequest,
    UpdateOrganizationRequest,
    from_pydal_row,
    from_pydal_rows,
    to_dict,
)
from apps.api.models.remote_workers import RemoteWorker

__all__ = [
    # SQLAlchemy models
    "CsvImportJob",
    "RemoteWorker",
    # DTOs - Organizations
    "OrganizationDTO",
    "CreateOrganizationRequest",
    "UpdateOrganizationRequest",
    # DTOs - Entities
    "EntityDTO",
    "CreateEntityRequest",
    "UpdateEntityRequest",
    # DTOs - Dependencies
    "DependencyDTO",
    "CreateDependencyRequest",
    # DTOs - Identities
    "IdentityDTO",
    "CreateIdentityRequest",
    "UpdateIdentityRequest",
    # DTOs - Groups
    "IdentityGroupDTO",
    "CreateIdentityGroupRequest",
    # DTOs - RBAC
    "RoleDTO",
    "PermissionDTO",
    "ResourceRoleDTO",
    "CreateResourceRoleRequest",
    # DTOs - Issues
    "IssueDTO",
    "CreateIssueRequest",
    "UpdateIssueRequest",
    "IssueLabelDTO",
    "IssueCommentDTO",
    "CreateIssueCommentRequest",
    # DTOs - Metadata
    "MetadataFieldDTO",
    "CreateMetadataFieldRequest",
    # DTOs - Auth
    "LoginRequest",
    "LoginResponse",
    "RegisterRequest",
    # DTOs - Audit
    "AuditLogDTO",
    # DTOs - Pagination
    "PaginatedResponse",
    # Helper functions
    "from_pydal_row",
    "from_pydal_rows",
    "to_dict",
]
