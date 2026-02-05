"""
Pydantic 2 models for IPAM (IP Address Management) domain objects.

Provides validated Pydantic 2 models for IPAM entities:
- IPAMPrefixDTO: Immutable frozen DTO for API responses
- IPAMAddressDTO: Immutable frozen DTO for IP addresses
- IPAMVlanDTO: Immutable frozen DTO for VLAN data
- CreateIPAMPrefixRequest: Request validation for creating prefixes
- UpdateIPAMPrefixRequest: Request validation for updating prefixes
- CreateIPAMAddressRequest: Request validation for creating addresses
- UpdateIPAMAddressRequest: Request validation for updating addresses
- CreateIPAMVlanRequest: Request validation for creating VLANs
- UpdateIPAMVlanRequest: Request validation for updating VLANs
"""

# flake8: noqa: E501


from datetime import datetime
from typing import Optional

from penguin_libs.pydantic.base import ImmutableModel, RequestModel
from pydantic import Field


class IPAMPrefixDTO(ImmutableModel):
    """
    Immutable IPAM Prefix data transfer object.

    Represents a complete IPAM Prefix record with all fields. Used for API
    responses and data serialization. Frozen to prevent accidental modifications.

    Attributes:
        id: Unique database identifier
        tenant_id: Associated tenant ID
        organization_id: Associated organization ID
        prefix: CIDR notation (e.g., '10.0.0.0/24')
        description: Optional detailed description
        status: Current status (active/reserved/deprecated)
        parent_id: Optional parent prefix ID for hierarchical relationships
        vlan_id: Optional associated VLAN ID
        is_pool: Whether prefix is used as address pool
        created_at: Creation timestamp
        updated_at: Last update timestamp
    """

    id: int
    tenant_id: int
    organization_id: int
    prefix: str
    description: Optional[str] = None
    status: str
    parent_id: Optional[int] = None
    vlan_id: Optional[int] = None
    is_pool: bool
    created_at: datetime
    updated_at: datetime


class IPAMAddressDTO(ImmutableModel):
    """
    Immutable IPAM Address data transfer object.

    Represents a complete IPAM Address record. Used for API responses and
    data serialization. Frozen to prevent accidental modifications.

    Attributes:
        id: Unique database identifier
        tenant_id: Associated tenant ID
        prefix_id: Associated prefix ID
        address: IP address with prefix (e.g., '10.0.0.1/32')
        description: Optional detailed description
        status: Current status (active/reserved/deprecated/dhcp)
        dns_name: Optional DNS hostname
        created_at: Creation timestamp
        updated_at: Last update timestamp
    """

    id: int
    tenant_id: int
    prefix_id: int
    address: str
    description: Optional[str] = None
    status: str
    dns_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class IPAMVlanDTO(ImmutableModel):
    """
    Immutable IPAM VLAN data transfer object.

    Represents a complete IPAM VLAN record. Used for API responses and
    data serialization. Frozen to prevent accidental modifications.

    Attributes:
        id: Unique database identifier
        tenant_id: Associated tenant ID
        organization_id: Associated organization ID
        vid: VLAN ID (0-4094)
        name: VLAN name
        description: Optional detailed description
        status: Current status (active/reserved/deprecated)
        created_at: Creation timestamp
        updated_at: Last update timestamp
    """

    id: int
    tenant_id: int
    organization_id: int
    vid: int
    name: str
    description: Optional[str] = None
    status: str
    created_at: datetime
    updated_at: datetime


class CreateIPAMPrefixRequest(RequestModel):
    """
    Request to create a new IPAM Prefix.

    Validates all required fields and enforces security constraints.
    Uses RequestModel to reject unknown fields and prevent injection attacks.

    Attributes:
        prefix: CIDR notation (required)
        organization_id: Associated organization ID (required, must be >= 1)
        description: Optional detailed description
        status: Current status (default: 'active')
        parent_id: Optional parent prefix ID for hierarchies
        vlan_id: Optional associated VLAN ID
        is_pool: Whether prefix is address pool (default: False)
    """

    prefix: str = Field(
        ...,
        description="CIDR notation (e.g., 10.0.0.0/24)",
    )
    organization_id: int = Field(
        ...,
        ge=1,
        description="Associated organization ID (must be positive)",
    )
    description: Optional[str] = Field(
        default=None,
        description="Optional detailed description",
    )
    status: str = Field(
        default="active",
        description="Current status (active/reserved/deprecated)",
    )
    parent_id: Optional[int] = Field(
        default=None,
        ge=1,
        description="Optional parent prefix ID",
    )
    vlan_id: Optional[int] = Field(
        default=None,
        ge=1,
        description="Optional associated VLAN ID",
    )
    is_pool: bool = Field(
        default=False,
        description="Whether prefix is used as address pool",
    )


class UpdateIPAMPrefixRequest(RequestModel):
    """
    Request to update an existing IPAM Prefix.

    All fields are optional - only provided fields will be updated.
    Uses RequestModel to reject unknown fields and prevent injection attacks.

    Attributes:
        prefix: CIDR notation (optional)
        description: Optional detailed description
        status: Current status (optional)
        parent_id: Optional parent prefix ID
        vlan_id: Optional associated VLAN ID
        is_pool: Whether prefix is address pool (optional)
        organization_id: Associated organization ID (optional)
    """

    prefix: Optional[str] = Field(
        default=None,
        description="CIDR notation",
    )
    description: Optional[str] = Field(
        default=None,
        description="Optional detailed description",
    )
    status: Optional[str] = Field(
        default=None,
        description="Current status",
    )
    parent_id: Optional[int] = Field(
        default=None,
        ge=1,
        description="Optional parent prefix ID",
    )
    vlan_id: Optional[int] = Field(
        default=None,
        ge=1,
        description="Optional associated VLAN ID",
    )
    is_pool: Optional[bool] = Field(
        default=None,
        description="Whether prefix is address pool",
    )
    organization_id: Optional[int] = Field(
        default=None,
        ge=1,
        description="Associated organization ID",
    )


class CreateIPAMAddressRequest(RequestModel):
    """
    Request to create a new IPAM Address.

    Validates all required fields and enforces security constraints.
    Uses RequestModel to reject unknown fields and prevent injection attacks.

    Attributes:
        address: IP address with prefix (required)
        prefix_id: Associated prefix ID (required, must be >= 1)
        description: Optional detailed description
        status: Current status (default: 'active')
        dns_name: Optional DNS hostname
    """

    address: str = Field(
        ...,
        description="IP address with prefix (e.g., 10.0.0.1/32)",
    )
    prefix_id: int = Field(
        ...,
        ge=1,
        description="Associated prefix ID (must be positive)",
    )
    description: Optional[str] = Field(
        default=None,
        description="Optional detailed description",
    )
    status: str = Field(
        default="active",
        description="Current status (active/reserved/deprecated/dhcp)",
    )
    dns_name: Optional[str] = Field(
        default=None,
        description="Optional DNS hostname",
    )


class UpdateIPAMAddressRequest(RequestModel):
    """
    Request to update an existing IPAM Address.

    All fields are optional - only provided fields will be updated.
    Uses RequestModel to reject unknown fields and prevent injection attacks.

    Attributes:
        address: IP address with prefix (optional)
        description: Optional detailed description
        status: Current status (optional)
        prefix_id: Associated prefix ID (optional)
        dns_name: Optional DNS hostname
    """

    address: Optional[str] = Field(
        default=None,
        description="IP address with prefix",
    )
    description: Optional[str] = Field(
        default=None,
        description="Optional detailed description",
    )
    status: Optional[str] = Field(
        default=None,
        description="Current status",
    )
    prefix_id: Optional[int] = Field(
        default=None,
        ge=1,
        description="Associated prefix ID",
    )
    dns_name: Optional[str] = Field(
        default=None,
        description="Optional DNS hostname",
    )


class CreateIPAMVlanRequest(RequestModel):
    """
    Request to create a new IPAM VLAN.

    Validates all required fields and enforces security constraints.
    Uses RequestModel to reject unknown fields and prevent injection attacks.

    Attributes:
        vid: VLAN ID (required, 0-4094)
        name: VLAN name (required)
        organization_id: Associated organization ID (required, must be >= 1)
        description: Optional detailed description
        status: Current status (default: 'active')
    """

    vid: int = Field(
        ...,
        ge=0,
        le=4094,
        description="VLAN ID (0-4094)",
    )
    name: str = Field(
        ...,
        description="VLAN name",
    )
    organization_id: int = Field(
        ...,
        ge=1,
        description="Associated organization ID (must be positive)",
    )
    description: Optional[str] = Field(
        default=None,
        description="Optional detailed description",
    )
    status: str = Field(
        default="active",
        description="Current status (active/reserved/deprecated)",
    )


class UpdateIPAMVlanRequest(RequestModel):
    """
    Request to update an existing IPAM VLAN.

    All fields are optional - only provided fields will be updated.
    Uses RequestModel to reject unknown fields and prevent injection attacks.

    Attributes:
        vid: VLAN ID (optional, 0-4094)
        name: VLAN name (optional)
        description: Optional detailed description
        status: Current status (optional)
        organization_id: Associated organization ID (optional)
    """

    vid: Optional[int] = Field(
        default=None,
        ge=0,
        le=4094,
        description="VLAN ID",
    )
    name: Optional[str] = Field(
        default=None,
        description="VLAN name",
    )
    description: Optional[str] = Field(
        default=None,
        description="Optional detailed description",
    )
    status: Optional[str] = Field(
        default=None,
        description="Current status",
    )
    organization_id: Optional[int] = Field(
        default=None,
        ge=1,
        description="Associated organization ID",
    )
