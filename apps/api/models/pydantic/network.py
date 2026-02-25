"""
Pydantic 2 models for Network and IPAM domain objects.

Provides validated Pydantic 2 equivalents of Network and IPAM dataclasses:
- NetworkDTO: Immutable frozen DTO for API responses
- IPAMEntryDTO: Immutable frozen DTO for IPAM entries
- CreateNetworkRequest: Request validation for creating networks
- CreateIPAMEntryRequest: Request validation for creating IPAM entries
"""

# flake8: noqa: E501


from datetime import datetime
from typing import Optional

from penguin_libs.pydantic.base import ImmutableModel, RequestModel
from pydantic import Field


class NetworkDTO(ImmutableModel):
    """
    Immutable Network data transfer object.

    Represents a complete Network record with all fields. Used for API responses
    and data serialization. Frozen to prevent accidental modifications.

    Attributes:
        id: Unique database identifier
        tenant_id: Associated tenant ID
        organization_id: Associated organization ID
        name: Network name (1-255 chars)
        description: Optional detailed description
        network_type: Type classification (e.g., 'vlan', 'subnet', 'physical')
        cidr: CIDR notation for the network (e.g., '192.168.1.0/24')
        gateway: Optional gateway IP address
        vlan_id: Optional VLAN ID
        mtu: Optional maximum transmission unit size
        is_active: Active status flag
        created_at: Creation timestamp
        updated_at: Last update timestamp
        village_id: Optional unique hierarchical identifier
    """

    id: int
    tenant_id: int
    organization_id: int
    name: str
    description: Optional[str] = None
    network_type: str
    cidr: str
    gateway: Optional[str] = None
    vlan_id: Optional[int] = None
    mtu: Optional[int] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    village_id: Optional[str] = None


class IPAMEntryDTO(ImmutableModel):
    """
    Immutable IPAM Entry data transfer object.

    Represents an IP Address Management entry with allocation status and
    associations. Used for API responses and data serialization.
    Frozen to prevent accidental modifications.

    Attributes:
        id: Unique database identifier
        tenant_id: Associated tenant ID
        network_id: Associated network ID
        ip_address: IP address (IPv4 or IPv6)
        mac_address: Optional MAC address
        hostname: Optional hostname
        allocation_type: Type of allocation ('static', 'dynamic', 'reserved')
        status: Current status ('available', 'assigned', 'reserved')
        assigned_to_id: Optional identity/resource ID this IP is assigned to
        assigned_to_type: Type of resource assigned to ('identity', 'device', 'service')
        description: Optional detailed description
        dns_reverse: Optional reverse DNS record
        is_active: Active status flag
        created_at: Creation timestamp
        updated_at: Last update timestamp
        village_id: Optional unique hierarchical identifier
    """

    id: int
    tenant_id: int
    network_id: int
    ip_address: str
    mac_address: Optional[str] = None
    hostname: Optional[str] = None
    allocation_type: str
    status: str
    assigned_to_id: Optional[int] = None
    assigned_to_type: Optional[str] = None
    description: Optional[str] = None
    dns_reverse: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    village_id: Optional[str] = None


class CreateNetworkRequest(RequestModel):
    """
    Request to create a new Network.

    Validates all required fields and enforces security constraints.
    Uses RequestModel to reject unknown fields and prevent injection attacks.

    Attributes:
        organization_id: Associated organization ID (required, must be >= 1)
        name: Network name (required)
        network_type: Type classification (required)
        cidr: CIDR notation (required)
        description: Optional detailed description
        gateway: Optional gateway IP address
        vlan_id: Optional VLAN ID
        mtu: Optional maximum transmission unit size
        is_active: Active status (default: True)
    """

    organization_id: int = Field(
        ...,
        ge=1,
        description="Associated organization ID (must be positive)",
    )
    name: str = Field(
        ...,
        description="Network name",
    )
    network_type: str = Field(
        ...,
        description="Type classification (vlan, subnet, physical)",
    )
    cidr: str = Field(
        ...,
        description="CIDR notation (e.g., 192.168.1.0/24)",
    )
    description: Optional[str] = Field(
        default=None,
        description="Optional detailed description",
    )
    gateway: Optional[str] = Field(
        default=None,
        description="Optional gateway IP address",
    )
    vlan_id: Optional[int] = Field(
        default=None,
        description="Optional VLAN ID",
    )
    mtu: Optional[int] = Field(
        default=None,
        description="Optional maximum transmission unit size",
    )
    region: Optional[str] = Field(
        default=None,
        description="Optional region (e.g., us-east-1, eu-west-1)",
    )
    location: Optional[str] = Field(
        default=None,
        description="Optional physical location (e.g., AWS Virginia, Data Center 1)",
    )
    is_active: bool = Field(
        default=True,
        description="Active status",
    )


class UpdateNetworkRequest(RequestModel):
    """
    Request to update an existing Network.

    All fields are optional to support PATCH operations.
    Uses RequestModel to reject unknown fields and prevent injection attacks.

    Attributes:
        name: Optional network name update
        description: Optional detailed description update
        gateway: Optional gateway IP address update
        vlan_id: Optional VLAN ID update
        mtu: Optional maximum transmission unit size update
        is_active: Optional active status update
    """

    name: Optional[str] = Field(
        default=None,
        description="Network name",
    )
    description: Optional[str] = Field(
        default=None,
        description="Optional detailed description",
    )
    gateway: Optional[str] = Field(
        default=None,
        description="Optional gateway IP address",
    )
    vlan_id: Optional[int] = Field(
        default=None,
        description="Optional VLAN ID",
    )
    mtu: Optional[int] = Field(
        default=None,
        description="Optional maximum transmission unit size",
    )
    is_active: Optional[bool] = Field(
        default=None,
        description="Active status",
    )


class CreateIPAMEntryRequest(RequestModel):
    """
    Request to create a new IPAM Entry.

    Validates all required fields and enforces security constraints.
    Uses RequestModel to reject unknown fields and prevent injection attacks.

    Attributes:
        network_id: Associated network ID (required, must be >= 1)
        ip_address: IP address (required)
        allocation_type: Type of allocation (required)
        status: Current status (required)
        mac_address: Optional MAC address
        hostname: Optional hostname
        assigned_to_id: Optional resource ID this IP is assigned to
        assigned_to_type: Optional resource type classification
        description: Optional detailed description
        dns_reverse: Optional reverse DNS record
        is_active: Active status (default: True)
    """

    network_id: int = Field(
        ...,
        ge=1,
        description="Associated network ID (must be positive)",
    )
    ip_address: str = Field(
        ...,
        description="IP address (IPv4 or IPv6)",
    )
    allocation_type: str = Field(
        ...,
        description="Type of allocation (static, dynamic, reserved)",
    )
    status: str = Field(
        ...,
        description="Current status (available, assigned, reserved)",
    )
    mac_address: Optional[str] = Field(
        default=None,
        description="Optional MAC address",
    )
    hostname: Optional[str] = Field(
        default=None,
        description="Optional hostname",
    )
    assigned_to_id: Optional[int] = Field(
        default=None,
        description="Optional resource ID this IP is assigned to",
    )
    assigned_to_type: Optional[str] = Field(
        default=None,
        description="Optional resource type classification",
    )
    description: Optional[str] = Field(
        default=None,
        description="Optional detailed description",
    )
    dns_reverse: Optional[str] = Field(
        default=None,
        description="Optional reverse DNS record",
    )
    is_active: bool = Field(
        default=True,
        description="Active status",
    )
