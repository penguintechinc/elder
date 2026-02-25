"""Entity type and sub-type definitions for Elder v1.2.0."""

# flake8: noqa: E501


from typing import Dict, List


# Main entity types
class EntityType:
    """Main entity type constants."""

    NETWORK = "network"
    COMPUTE = "compute"
    STORAGE = "storage"
    DATACENTER = "datacenter"
    SECURITY = "security"


# Network sub-types
class NetworkSubType:
    """Network device sub-types."""

    SUBNET = "subnet"
    FIREWALL = "firewall"
    PROXY = "proxy"
    ROUTER = "router"
    SWITCH = "switch"
    HUB = "hub"
    TUNNEL = "tunnel"
    ROUTE_TABLE = "route_table"
    VRRF = "vrrf"
    VXLAN = "vxlan"
    VLAN = "vlan"
    NAMESPACE = "namespace"  # v1.2.1: Kubernetes/container namespaces
    OTHER = "other"


# Compute sub-types
class ComputeSubType:
    """Compute resource sub-types."""

    SERVER = "server"
    SERVERLESS = "serverless"
    LAPTOP = "laptop"
    MOBILE = "mobile"
    DESKTOP = "desktop"
    KUBERNETES_NODE = "kubernetes_node"
    KUBERNETES_CLUSTER = "kubernetes_cluster"
    VIRTUAL_MACHINE = "virtual_machine"
    LXD_CONTAINER = "lxd_container"
    LXD_VM = "lxd_vm"
    FUNCTION_RUN = "function_run"
    OTHER = "other"


# Storage sub-types
class StorageSubType:
    """Storage resource sub-types."""

    HARD_DISK = "hard_disk"
    NVME_DISK = "nvme_disk"
    SOLID_STATE_DISK = "solid_state_disk"
    VIRTUAL_DISK = "virtual_disk"
    EXTERNAL_DRIVE = "external_drive"
    DATABASE = "database"
    CACHING = "caching"
    QUEUE_SYSTEM = "queue_system"
    OTHER = "other"


# Datacenter sub-types
class DatacenterSubType:
    """Datacenter/network container sub-types."""

    PUBLIC_VPC = "public_vpc"
    PRIVATE_VPC = "private_vpc"
    PHYSICAL = "physical"
    CLOSET = "closet"
    OTHER = "other"


# Security sub-types
class SecuritySubType:
    """Security issue sub-types."""

    VULNERABILITY = "vulnerability"
    ARCHITECTURAL = "architectural"
    CONFIG = "config"
    COMPLIANCE = "compliance"
    CODE = "code"
    REGULATORY = "regulatory"
    OTHER = "other"


# Entity type to sub-types mapping
ENTITY_SUBTYPES: Dict[str, List[str]] = {
    EntityType.NETWORK: [
        NetworkSubType.SUBNET,
        NetworkSubType.FIREWALL,
        NetworkSubType.PROXY,
        NetworkSubType.ROUTER,
        NetworkSubType.SWITCH,
        NetworkSubType.HUB,
        NetworkSubType.TUNNEL,
        NetworkSubType.ROUTE_TABLE,
        NetworkSubType.VRRF,
        NetworkSubType.VXLAN,
        NetworkSubType.VLAN,
        NetworkSubType.NAMESPACE,  # v1.2.1
        NetworkSubType.OTHER,
    ],
    EntityType.COMPUTE: [
        ComputeSubType.SERVER,
        ComputeSubType.SERVERLESS,
        ComputeSubType.LAPTOP,
        ComputeSubType.MOBILE,
        ComputeSubType.DESKTOP,
        ComputeSubType.KUBERNETES_NODE,
        ComputeSubType.KUBERNETES_CLUSTER,
        ComputeSubType.VIRTUAL_MACHINE,
        ComputeSubType.LXD_CONTAINER,
        ComputeSubType.LXD_VM,
        ComputeSubType.FUNCTION_RUN,
        ComputeSubType.OTHER,
    ],
    EntityType.STORAGE: [
        StorageSubType.HARD_DISK,
        StorageSubType.NVME_DISK,
        StorageSubType.SOLID_STATE_DISK,
        StorageSubType.VIRTUAL_DISK,
        StorageSubType.EXTERNAL_DRIVE,
        StorageSubType.DATABASE,
        StorageSubType.CACHING,
        StorageSubType.QUEUE_SYSTEM,
        StorageSubType.OTHER,
    ],
    EntityType.DATACENTER: [
        DatacenterSubType.PUBLIC_VPC,
        DatacenterSubType.PRIVATE_VPC,
        DatacenterSubType.PHYSICAL,
        DatacenterSubType.CLOSET,
        DatacenterSubType.OTHER,
    ],
    EntityType.SECURITY: [
        SecuritySubType.VULNERABILITY,
        SecuritySubType.ARCHITECTURAL,
        SecuritySubType.CONFIG,
        SecuritySubType.COMPLIANCE,
        SecuritySubType.CODE,
        SecuritySubType.REGULATORY,
        SecuritySubType.OTHER,
    ],
}


# Default metadata templates for each sub-type
DEFAULT_METADATA_TEMPLATES: Dict[str, Dict[str, Dict]] = {
    EntityType.NETWORK: {
        NetworkSubType.ROUTER: {
            "routing_protocols": {
                "type": "array",
                "description": "Routing protocols (BGP, OSPF, etc.)",
            },
            "routing_table": {"type": "object", "description": "Routing table data"},
            "interfaces": {"type": "array", "description": "Network interfaces"},
        },
        NetworkSubType.FIREWALL: {
            "rules": {"type": "array", "description": "Firewall rules"},
            "default_policy": {
                "type": "string",
                "description": "Default policy (allow/deny)",
            },
        },
        NetworkSubType.PROXY: {
            "backend_servers": {"type": "array", "description": "Backend servers"},
            "load_balancer_algorithm": {
                "type": "string",
                "description": "Load balancing algorithm",
            },
        },
        NetworkSubType.SUBNET: {
            "cidr_block": {
                "type": "string",
                "description": "CIDR notation (e.g., 10.0.1.0/24)",
            },
            "gateway": {"type": "string", "description": "Gateway IP address"},
            "available_ips": {
                "type": "integer",
                "description": "Available IP addresses",
            },
        },
        NetworkSubType.NAMESPACE: {
            "cluster": {"type": "string", "description": "Kubernetes cluster name"},
            "resource_quota": {
                "type": "object",
                "description": "Resource quotas and limits",
            },
            "labels": {"type": "object", "description": "Namespace labels"},
            "annotations": {"type": "object", "description": "Namespace annotations"},
        },
    },
    EntityType.COMPUTE: {
        ComputeSubType.SERVER: {
            "os": {"type": "string", "description": "Operating system"},
            "kernel_version": {"type": "string", "description": "Kernel version"},
            "cpu_count": {"type": "integer", "description": "Number of CPUs"},
            "memory_gb": {"type": "number", "description": "Memory in GB"},
            "hostname": {"type": "string", "description": "Server hostname"},
        },
        ComputeSubType.VIRTUAL_MACHINE: {
            "os": {"type": "string", "description": "Operating system"},
            "vcpu_count": {"type": "integer", "description": "Virtual CPU count"},
            "memory_gb": {"type": "number", "description": "Memory in GB"},
            "disk_gb": {"type": "number", "description": "Disk size in GB"},
            "hypervisor": {"type": "string", "description": "Hypervisor type"},
        },
        ComputeSubType.KUBERNETES_CLUSTER: {
            "version": {"type": "string", "description": "Kubernetes version"},
            "node_count": {"type": "integer", "description": "Number of nodes"},
            "control_plane_endpoint": {
                "type": "string",
                "description": "API server endpoint",
            },
        },
        ComputeSubType.SERVERLESS: {
            "runtime": {
                "type": "string",
                "description": "Runtime environment (Python, Node.js, etc.)",
            },
            "memory_mb": {"type": "integer", "description": "Allocated memory in MB"},
            "timeout_seconds": {"type": "integer", "description": "Execution timeout"},
        },
        ComputeSubType.LXD_CONTAINER: {
            "os": {"type": "string", "description": "Container OS (Ubuntu, Alpine, etc.)"},
            "memory_gb": {"type": "number", "description": "Allocated memory in GB"},
            "cpu_cores": {"type": "integer", "description": "Allocated CPU cores"},
            "root_disk_gb": {"type": "number", "description": "Root disk size in GB"},
            "status": {"type": "string", "description": "Container status (running, stopped)"},
            "created_at": {"type": "string", "description": "Container creation timestamp"},
        },
        ComputeSubType.LXD_VM: {
            "os": {"type": "string", "description": "VM OS (Ubuntu, Debian, etc.)"},
            "vcpu_count": {"type": "integer", "description": "Virtual CPU cores"},
            "memory_gb": {"type": "number", "description": "Allocated memory in GB"},
            "disk_gb": {"type": "number", "description": "Disk size in GB"},
            "status": {"type": "string", "description": "VM status (running, stopped)"},
            "created_at": {"type": "string", "description": "VM creation timestamp"},
            "boot_mode": {"type": "string", "description": "Boot mode (UEFI, BIOS)"},
        },
    },
    EntityType.STORAGE: {
        StorageSubType.DATABASE: {
            "engine": {
                "type": "string",
                "description": "Database engine (PostgreSQL, MySQL, etc.)",
            },
            "version": {"type": "string", "description": "Database version"},
            "port": {"type": "integer", "description": "Database port"},
            "connection_string": {
                "type": "string",
                "description": "Connection string (masked)",
            },
            "replica_count": {"type": "integer", "description": "Number of replicas"},
        },
        StorageSubType.CACHING: {
            "engine": {
                "type": "string",
                "description": "Cache engine (Redis, Valkey, Memcached)",
            },
            "memory_mb": {"type": "integer", "description": "Cache size in MB"},
            "eviction_policy": {"type": "string", "description": "Eviction policy"},
        },
        StorageSubType.QUEUE_SYSTEM: {
            "engine": {
                "type": "string",
                "description": "Queue engine (SQS, Kafka, RabbitMQ)",
            },
            "message_retention_hours": {
                "type": "integer",
                "description": "Message retention period",
            },
            "max_queue_size": {"type": "integer", "description": "Maximum queue size"},
        },
        StorageSubType.HARD_DISK: {
            "capacity_gb": {"type": "number", "description": "Disk capacity in GB"},
            "rpm": {"type": "integer", "description": "Rotations per minute"},
            "interface": {
                "type": "string",
                "description": "Disk interface (SATA, SAS)",
            },
        },
        StorageSubType.SOLID_STATE_DISK: {
            "capacity_gb": {"type": "number", "description": "SSD capacity in GB"},
            "interface": {"type": "string", "description": "Interface (SATA, NVMe)"},
            "read_speed_mbps": {"type": "integer", "description": "Read speed in MB/s"},
            "write_speed_mbps": {
                "type": "integer",
                "description": "Write speed in MB/s",
            },
        },
    },
    EntityType.DATACENTER: {
        DatacenterSubType.PUBLIC_VPC: {
            "cidr_block": {"type": "string", "description": "VPC CIDR block"},
            "region": {"type": "string", "description": "Cloud region"},
            "internet_gateway": {
                "type": "string",
                "description": "Internet gateway ID",
            },
        },
        DatacenterSubType.PRIVATE_VPC: {
            "cidr_block": {"type": "string", "description": "VPC CIDR block"},
            "region": {"type": "string", "description": "Cloud region"},
            "vpn_connections": {"type": "array", "description": "VPN connections"},
        },
        DatacenterSubType.PHYSICAL: {
            "location": {"type": "string", "description": "Physical location"},
            "provider": {"type": "string", "description": "Datacenter provider"},
            "power_capacity_kw": {
                "type": "number",
                "description": "Power capacity in kW",
            },
            "cooling_type": {"type": "string", "description": "Cooling system type"},
        },
    },
    EntityType.SECURITY: {
        SecuritySubType.VULNERABILITY: {
            "cve": {"type": "string", "description": "CVE identifier"},
            "cvss_score": {"type": "number", "description": "CVSS score (0-10)"},
            "severity": {"type": "string", "description": "Severity level"},
            "affected_versions": {
                "type": "array",
                "description": "Affected software versions",
            },
        },
        SecuritySubType.COMPLIANCE: {
            "framework": {
                "type": "string",
                "description": "Compliance framework (SOC2, ISO27001)",
            },
            "control_id": {"type": "string", "description": "Control identifier"},
            "status": {"type": "string", "description": "Compliance status"},
        },
        SecuritySubType.CONFIG: {
            "misconfiguration_type": {
                "type": "string",
                "description": "Type of misconfiguration",
            },
            "affected_service": {
                "type": "string",
                "description": "Affected service or component",
            },
            "remediation": {"type": "string", "description": "Remediation steps"},
        },
    },
}


def get_subtypes_for_type(entity_type: str) -> List[str]:
    """Get list of sub-types for a given entity type."""
    return ENTITY_SUBTYPES.get(entity_type, [])


def get_default_metadata_for_subtype(entity_type: str, sub_type: str) -> Dict:
    """Get default metadata template for an entity sub-type."""
    type_templates = DEFAULT_METADATA_TEMPLATES.get(entity_type, {})
    return type_templates.get(sub_type, {})


def get_all_entity_types() -> List[str]:
    """Get all entity types."""
    return [
        EntityType.NETWORK,
        EntityType.COMPUTE,
        EntityType.STORAGE,
        EntityType.DATACENTER,
        EntityType.SECURITY,
    ]


def is_valid_entity_type(entity_type: str) -> bool:
    """Check if entity type is valid."""
    return entity_type in get_all_entity_types()


def is_valid_subtype(entity_type: str, sub_type: str) -> bool:
    """Check if sub-type is valid for the given entity type."""
    subtypes = get_subtypes_for_type(entity_type)
    return sub_type in subtypes
