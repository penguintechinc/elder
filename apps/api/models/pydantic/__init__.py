"""
Pydantic 2 domain models for Elder application entities.

This module provides Pydantic 2 equivalents of Elder dataclasses with proper
validation, immutability for DTOs, and security hardening for requests.
"""

# flake8: noqa: E501


from .entity import CreateEntityRequest, EntityDTO, UpdateEntityRequest
from .group import (
    AccessRequestDTO,
    AddGroupMemberRequest,
    ApproveOrDenyRequestRequest,
    BulkApproveRequestsRequest,
    BulkApproveResult,
    CreateAccessRequestRequest,
    GroupDTO,
    GroupMemberDTO,
    ListGroupsResponse,
    ListMembersResponse,
    ListRequestsResponse,
    UpdateGroupRequest,
)
from .identity import (
    AuthProvider,
    CreateIdentityGroupRequest,
    CreateIdentityRequest,
    IdentityDTO,
    IdentityGroupDTO,
    IdentityType,
    PortalRole,
    UpdateIdentityGroupRequest,
    UpdateIdentityRequest,
)
from .ipam import (
    CreateIPAMAddressRequest,
    CreateIPAMPrefixRequest,
    CreateIPAMVlanRequest,
    IPAMAddressDTO,
    IPAMPrefixDTO,
    IPAMVlanDTO,
    UpdateIPAMAddressRequest,
    UpdateIPAMPrefixRequest,
    UpdateIPAMVlanRequest,
)
from .issue import (
    CreateIssueRequest,
    IssueDTO,
    IssuePriority,
    IssueSeverity,
    IssueStatus,
    UpdateIssueRequest,
)
from .label import CreateLabelRequest, LabelDTO, UpdateLabelRequest
from .license_policy import (
    CreateLicensePolicyRequest,
    LicensePolicyDTO,
    UpdateLicensePolicyRequest,
)
from .metadata import (
    CreateMetadataFieldRequest,
    MetadataFieldDTO,
    UpdateMetadataFieldRequest,
)
from .network import (
    CreateIPAMEntryRequest,
    CreateNetworkRequest,
    IPAMEntryDTO,
    NetworkDTO,
)
from .organization import (
    CreateOrganizationRequest,
    OrganizationDTO,
    OrganizationType,
    UpdateOrganizationRequest,
)
from .resource_role import (
    CreateResourceRoleRequest,
    ResourceRoleResponse,
    ResourceType,
    RoleType,
)
from .service import CreateServiceRequest, ServiceDTO, UpdateServiceRequest
from .vulnerability import (
    CreateVulnerabilityRequest,
    VulnerabilityDTO,
    VulnerabilitySeverity,
)

__all__ = [
    "EntityDTO",
    "CreateEntityRequest",
    "UpdateEntityRequest",
    "IdentityDTO",
    "CreateIdentityRequest",
    "UpdateIdentityRequest",
    "IdentityGroupDTO",
    "CreateIdentityGroupRequest",
    "UpdateIdentityGroupRequest",
    "IdentityType",
    "AuthProvider",
    "PortalRole",
    "IPAMEntryDTO",
    "CreateIPAMEntryRequest",
    "IssueDTO",
    "CreateIssueRequest",
    "UpdateIssueRequest",
    "IssueStatus",
    "IssuePriority",
    "IssueSeverity",
    "LabelDTO",
    "CreateLabelRequest",
    "UpdateLabelRequest",
    "MetadataFieldDTO",
    "CreateMetadataFieldRequest",
    "UpdateMetadataFieldRequest",
    "NetworkDTO",
    "CreateNetworkRequest",
    "OrganizationType",
    "OrganizationDTO",
    "CreateOrganizationRequest",
    "UpdateOrganizationRequest",
    "VulnerabilitySeverity",
    "VulnerabilityDTO",
    "CreateVulnerabilityRequest",
    "LicensePolicyDTO",
    "CreateLicensePolicyRequest",
    "UpdateLicensePolicyRequest",
    "IPAMPrefixDTO",
    "IPAMAddressDTO",
    "IPAMVlanDTO",
    "CreateIPAMPrefixRequest",
    "UpdateIPAMPrefixRequest",
    "CreateIPAMAddressRequest",
    "UpdateIPAMAddressRequest",
    "CreateIPAMVlanRequest",
    "UpdateIPAMVlanRequest",
    "UpdateGroupRequest",
    "CreateAccessRequestRequest",
    "AddGroupMemberRequest",
    "ApproveOrDenyRequestRequest",
    "BulkApproveRequestsRequest",
    "GroupDTO",
    "AccessRequestDTO",
    "GroupMemberDTO",
    "ListGroupsResponse",
    "ListRequestsResponse",
    "ListMembersResponse",
    "BulkApproveResult",
    "ResourceRoleResponse",
    "CreateResourceRoleRequest",
    "ResourceType",
    "RoleType",
    "ServiceDTO",
    "CreateServiceRequest",
    "UpdateServiceRequest",
]
