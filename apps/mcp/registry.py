"""Centralized MCP tool registry and Ollama function-calling schema definitions."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from apps.mcp.client import ElderClient
    from apps.mcp.tools import (
        api_catalog,
        entities,
        knowledge,
        operations,
        relationships,
        resources,
        security,
        work_management,
    )
else:
    try:
        from apps.mcp.client import ElderClient
        from apps.mcp.tools import (
            api_catalog,
            entities,
            knowledge,
            operations,
            relationships,
            resources,
            security,
            work_management,
        )
    except ImportError:
        from client import ElderClient
        from tools import (
            api_catalog,
            entities,
            knowledge,
            operations,
            relationships,
            resources,
            security,
            work_management,
        )


@dataclass(frozen=True)
class ToolDefinition:
    """Metadata, JSON Schema, and handler reference for one Elder MCP tool."""

    name: str
    description: str
    parameters_schema: dict[str, Any]
    read_only: bool
    required_module: str | None
    endpoint_path: str
    handler: Callable[..., Awaitable[Any]]


def _int(description: str) -> dict[str, Any]:
    return {"type": "integer", "description": description}


def _str(description: str, enum: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "string", "description": description}
    if enum is not None:
        schema["enum"] = enum
    return schema


def _bool(description: str) -> dict[str, Any]:
    return {"type": "boolean", "description": description}


def _obj(description: str) -> dict[str, Any]:
    return {"type": "object", "description": description}


def _arr_str(description: str) -> dict[str, Any]:
    return {
        "type": "array",
        "items": {"type": "string"},
        "description": description,
    }


def _params(
    properties: dict[str, dict[str, Any]] | None = None,
    required: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties or {},
    }
    if required:
        schema["required"] = list(required)
    return schema


TOOL_REGISTRY: tuple[ToolDefinition, ...] = (
    # ------------------------------------------------------------------
    # Core Search & Refs (required_module=None)
    # ------------------------------------------------------------------
    ToolDefinition(
        name="universal_search",
        description="Search across Elder entities, issues, organizations, services, documents, and resources.",
        parameters_schema=_params(
            {
                "q": _str("Search query text."),
                "types": _str("Optional comma-separated resource types filter."),
                "limit": _int("Maximum results to return (default 20)."),
                "offset": _int("Result offset for pagination (default 0)."),
            },
            required=("q",),
        ),
        read_only=True,
        required_module=None,
        endpoint_path="/api/v1/search",
        handler=resources.universal_search,
    ),
    ToolDefinition(
        name="search_graph",
        description="Traverse infrastructure dependencies outward from a start entity ID.",
        parameters_schema=_params(
            {
                "start_entity_id": _int("Starting entity ID."),
                "max_depth": _int("Traversal depth limit (default 2)."),
                "dependency_types": _arr_str("Optional dependency types filter."),
                "entity_filters": _obj("Optional entity attribute filters."),
            },
            required=("start_entity_id",),
        ),
        read_only=True,
        required_module=None,
        endpoint_path="/api/v1/search/graph",
        handler=resources.search_graph,
    ),
    ToolDefinition(
        name="resolve_ref",
        description="Resolve a VillageID or Elder cross-module ref URI into its target resource.",
        parameters_schema=_params(
            {
                "village_id": _str("Compact VillageID to resolve."),
                "ref": _str("Full elder:// or typed ref identifier to resolve."),
            }
        ),
        read_only=True,
        required_module=None,
        endpoint_path="/api/v1/refs/resolve",
        handler=resources.resolve_ref,
    ),
    ToolDefinition(
        name="get_ref_backlinks",
        description="List resources referencing a target identifier or VillageID.",
        parameters_schema=_params(
            {"target": _str("Target ref URI or identifier.")},
            required=("target",),
        ),
        read_only=True,
        required_module=None,
        endpoint_path="/api/v1/refs/backlinks",
        handler=resources.get_ref_backlinks,
    ),
    ToolDefinition(
        name="lookup_village_id",
        description="Look up an Elder resource directly by its VillageID.",
        parameters_schema=_params(
            {"village_id": _str("VillageID token.")},
            required=("village_id",),
        ),
        read_only=True,
        required_module=None,
        endpoint_path="/id/<village_id>",
        handler=resources.lookup_village_id,
    ),
    ToolDefinition(
        name="list_audit_logs",
        description="Query enterprise audit logs filtered by resource, action, category, success, or date window.",
        parameters_schema=_params(
            {
                "resource_type": _str("Filter by resource type."),
                "resource_id": _str("Filter by resource ID."),
                "action": _str("Filter by action name."),
                "category": _str("Filter by audit category."),
                "start_date": _str("ISO8601 start timestamp."),
                "end_date": _str("ISO8601 end timestamp."),
                "success": _bool("Filter by operation success status."),
                "limit": _int("Max rows (default 50)."),
                "offset": _int("Pagination offset (default 0)."),
            }
        ),
        read_only=True,
        required_module=None,
        endpoint_path="/api/v1/audit-enterprise/logs",
        handler=resources.list_audit_logs,
    ),
    # ------------------------------------------------------------------
    # Entities & Infrastructure (required_module="infrastructure")
    # ------------------------------------------------------------------
    ToolDefinition(
        name="search_entities",
        description="Search infrastructure entities (servers, databases, apps, load balancers, etc.).",
        parameters_schema=_params(
            {
                "name": _str("Filter by entity name substring."),
                "organization_id": _int("Filter by owning organization ID."),
                "entity_type": _str("Filter by primary entity type."),
                "sub_type": _str("Filter by entity subtype."),
                "limit": _int("Results per page (default 50)."),
                "page": _int("Page number (default 1)."),
            }
        ),
        read_only=True,
        required_module="infrastructure",
        endpoint_path="/api/v1/entities",
        handler=entities.search_entities,
    ),
    ToolDefinition(
        name="get_entity",
        description="Get complete details and attributes for a single infrastructure entity.",
        parameters_schema=_params(
            {"entity_id": _int("Entity ID.")},
            required=("entity_id",),
        ),
        read_only=True,
        required_module="infrastructure",
        endpoint_path="/api/v1/entities/<int:id>",
        handler=entities.get_entity,
    ),
    ToolDefinition(
        name="update_entity",
        description="Update an infrastructure entity's name, description, or attributes (write operation).",
        parameters_schema=_params(
            {
                "entity_id": _int("Entity ID to update."),
                "name": _str("New entity name."),
                "description": _str("New description."),
                "attributes": _obj("Key/value attributes to merge/update."),
                "metadata": _obj("Alias for attributes to merge/update."),
            },
            required=("entity_id",),
        ),
        read_only=False,
        required_module="infrastructure",
        endpoint_path="/api/v1/entities/<int:id>",
        handler=entities.update_entity,
    ),
    ToolDefinition(
        name="get_entity_relationships",
        description="Get inbound, outbound, or bidirectional dependency relationships for an entity.",
        parameters_schema=_params(
            {
                "entity_id": _int("Entity ID."),
                "direction": _str(
                    "Traversal direction.",
                    enum=["both", "inbound", "outbound", "all", "incoming", "outgoing"],
                ),
            },
            required=("entity_id",),
        ),
        read_only=True,
        required_module="infrastructure",
        endpoint_path="/api/v1/entities/<int:id>/dependencies",
        handler=relationships.get_entity_relationships,
    ),
    ToolDefinition(
        name="search_relationships",
        description="Search dependency edges between Elder entities and resources.",
        parameters_schema=_params(
            {
                "source_id": _int("Source resource ID."),
                "target_id": _int("Target resource ID."),
                "dependency_type": _str("Dependency relationship type."),
                "source_type": _str("Source resource type."),
                "target_type": _str("Target resource type."),
                "source_entity_id": _int("Alias for source_id."),
                "target_entity_id": _int("Alias for target_id."),
                "relationship_type": _str("Alias for dependency_type."),
                "limit": _int("Results per page (default 50)."),
                "page": _int("Page number (default 1)."),
            }
        ),
        read_only=True,
        required_module="infrastructure",
        endpoint_path="/api/v1/dependencies",
        handler=relationships.search_relationships,
    ),
    ToolDefinition(
        name="create_relationship",
        description="Create a dependency link between two Elder resources (write operation).",
        parameters_schema=_params(
            {
                "source_id": _int("Source resource ID."),
                "target_id": _int("Target resource ID."),
                "dependency_type": _str("Dependency type name."),
                "source_type": _str("Source resource type (default 'entity')."),
                "target_type": _str("Target resource type (default 'entity')."),
                "metadata": _obj("Optional edge metadata."),
            },
            required=("source_id", "target_id", "dependency_type"),
        ),
        read_only=False,
        required_module="infrastructure",
        endpoint_path="/api/v1/dependencies",
        handler=relationships.create_relationship,
    ),
    ToolDefinition(
        name="delete_relationship",
        description="Delete a dependency link by dependency ID (write operation).",
        parameters_schema=_params(
            {"dependency_id": _int("Dependency ID to remove.")},
            required=("dependency_id",),
        ),
        read_only=False,
        required_module="infrastructure",
        endpoint_path="/api/v1/dependencies/<int:id>",
        handler=relationships.delete_relationship,
    ),
    ToolDefinition(
        name="list_data_stores",
        description="List data stores with optional classification (PII/PHI/PCI), storage type, and org filters.",
        parameters_schema=_params(
            {
                "search": _str("Name/description search filter."),
                "organization_id": _int("Filter by organization ID."),
                "data_classification": _str(
                    "Classification filter (e.g. confidential, restricted)."
                ),
                "storage_type": _str("Storage engine/type filter."),
                "contains_pii": _bool("Filter for stores holding PII."),
                "contains_phi": _bool("Filter for stores holding PHI."),
                "contains_pci": _bool("Filter for stores holding PCI data."),
                "label": _str("Filter by label name."),
                "limit": _int("Results per page (default 50)."),
                "page": _int("Page number (default 1)."),
            }
        ),
        read_only=True,
        required_module="infrastructure",
        endpoint_path="/api/v1/data-stores",
        handler=entities.list_data_stores,
    ),
    ToolDefinition(
        name="get_data_store",
        description="Get full details for a specific data store by ID.",
        parameters_schema=_params(
            {"data_store_id": _int("Data store ID.")},
            required=("data_store_id",),
        ),
        read_only=True,
        required_module="infrastructure",
        endpoint_path="/api/v1/data-stores/<int:id>",
        handler=entities.get_data_store,
    ),
    ToolDefinition(
        name="list_networks",
        description="List cloud/on-prem networks, VPCs, and subnets.",
        parameters_schema=_params(
            {
                "organization_id": _int("Filter by organization ID."),
                "network_type": _str("Network type filter."),
                "parent_id": _int("Filter by parent network ID."),
                "region": _str("Region filter."),
                "limit": _int("Max rows (default 50)."),
                "offset": _int("Offset (default 0)."),
            }
        ),
        read_only=True,
        required_module="infrastructure",
        endpoint_path="/api/v1/networking/networks",
        handler=entities.list_networks,
    ),
    ToolDefinition(
        name="get_network",
        description="Get details for a specific network by ID.",
        parameters_schema=_params(
            {"network_id": _int("Network ID.")},
            required=("network_id",),
        ),
        read_only=True,
        required_module="infrastructure",
        endpoint_path="/api/v1/networking/networks/<int:network_id>",
        handler=entities.get_network,
    ),
    ToolDefinition(
        name="get_networking_topology",
        description="Get the network topology graph for a specific organization.",
        parameters_schema=_params(
            {
                "organization_id": _int("Organization ID."),
                "include_entities": _bool("Include attached entities in graph."),
            },
            required=("organization_id",),
        ),
        read_only=True,
        required_module="infrastructure",
        endpoint_path="/api/v1/networking/topology/graph",
        handler=entities.get_networking_topology,
    ),
    ToolDefinition(
        name="get_graph",
        description="Fetch the infrastructure dependency graph centered on an organization or entity.",
        parameters_schema=_params(
            {
                "organization_id": _int("Organization ID filter."),
                "entity_type": _str("Entity type filter."),
                "entity_id": _int("Center entity ID."),
                "depth": _int("Graph depth (default 2)."),
                "include_metadata": _bool("Include node metadata."),
            }
        ),
        read_only=True,
        required_module="infrastructure",
        endpoint_path="/api/v1/graph",
        handler=relationships.get_graph,
    ),
    ToolDefinition(
        name="analyze_graph",
        description="Run graph analysis to detect single points of failure, cycles, and centrality hotspots.",
        parameters_schema=_params(
            {"organization_id": _int("Optional organization ID filter.")}
        ),
        read_only=True,
        required_module="infrastructure",
        endpoint_path="/api/v1/graph/analyze",
        handler=relationships.analyze_graph,
    ),
    ToolDefinition(
        name="find_graph_path",
        description="Find shortest dependency paths between two infrastructure entities.",
        parameters_schema=_params(
            {
                "source_entity_id": _int("Origin entity ID."),
                "target_entity_id": _int("Destination entity ID."),
            },
            required=("source_entity_id", "target_entity_id"),
        ),
        read_only=True,
        required_module="infrastructure",
        endpoint_path="/api/v1/graph/path",
        handler=relationships.find_graph_path,
    ),
    ToolDefinition(
        name="list_organizations",
        description="List organizations within the tenant.",
        parameters_schema=_params(
            {
                "search": _str("Search by organization name."),
                "parent_id": _int("Filter by parent organization ID."),
                "limit": _int("Results per page (default 50)."),
                "page": _int("Page number (default 1)."),
            }
        ),
        read_only=True,
        required_module="infrastructure",
        endpoint_path="/api/v1/organizations",
        handler=entities.list_organizations,
    ),
    ToolDefinition(
        name="get_organization",
        description="Get details for a specific organization by ID.",
        parameters_schema=_params(
            {"organization_id": _int("Organization ID.")},
            required=("organization_id",),
        ),
        read_only=True,
        required_module="infrastructure",
        endpoint_path="/api/v1/organizations/<int:id>",
        handler=entities.get_organization,
    ),
    ToolDefinition(
        name="get_organization_children",
        description="Get direct or recursive child organizations under an organization.",
        parameters_schema=_params(
            {
                "organization_id": _int("Parent organization ID."),
                "recursive": _bool("Include full subtree when true."),
            },
            required=("organization_id",),
        ),
        read_only=True,
        required_module="infrastructure",
        endpoint_path="/api/v1/organizations/<int:id>/children",
        handler=entities.get_organization_children,
    ),
    ToolDefinition(
        name="get_organization_tree_stats",
        description="Get rollup infrastructure and resource statistics for an organization subtree.",
        parameters_schema=_params(
            {"org_id": _int("Organization ID.")},
            required=("org_id",),
        ),
        read_only=True,
        required_module="infrastructure",
        endpoint_path="/api/v1/organizations/<int:org_id>/tree-stats",
        handler=entities.get_organization_tree_stats,
    ),
    ToolDefinition(
        name="list_geo_locations",
        description="List geographic datacenter or cloud locations.",
        parameters_schema=_params(
            {"region_code": _str("Optional region code filter.")}
        ),
        read_only=True,
        required_module="infrastructure",
        endpoint_path="/api/v1/geo/locations",
        handler=entities.list_geo_locations,
    ),
    ToolDefinition(
        name="list_geo_regions",
        description="List geographic cloud/datacenter regions.",
        parameters_schema=_params(
            {"provider": _str("Optional provider filter (aws, gcp, azure, onprem).")}
        ),
        read_only=True,
        required_module="infrastructure",
        endpoint_path="/api/v1/geo/regions",
        handler=entities.list_geo_regions,
    ),
    # ------------------------------------------------------------------
    # Work Management (issues, helpdesk)
    # ------------------------------------------------------------------
    ToolDefinition(
        name="list_issues",
        description="List issues with optional status, priority, assignee, project, or milestone filters.",
        parameters_schema=_params(
            {
                "status": _str("Issue status filter."),
                "priority": _str("Issue priority filter."),
                "assignee_id": _int("Assignee identity ID."),
                "reporter_id": _int("Reporter identity ID."),
                "organization_id": _int("Organization ID."),
                "milestone_id": _int("Milestone ID."),
                "project_id": _int("Project ID."),
                "search": _str("Search keyword."),
                "limit": _int("Results per page (default 50)."),
                "page": _int("Page number (default 1)."),
            }
        ),
        read_only=True,
        required_module="issues",
        endpoint_path="/api/v1/issues",
        handler=work_management.list_issues,
    ),
    ToolDefinition(
        name="get_issue",
        description="Get complete details for a single issue by ID.",
        parameters_schema=_params(
            {"issue_id": _int("Issue ID.")},
            required=("issue_id",),
        ),
        read_only=True,
        required_module="issues",
        endpoint_path="/api/v1/issues/<int:id>",
        handler=work_management.get_issue,
    ),
    ToolDefinition(
        name="get_issue_comments",
        description="Get discussion comments posted on a specific issue.",
        parameters_schema=_params(
            {"issue_id": _int("Issue ID.")},
            required=("issue_id",),
        ),
        read_only=True,
        required_module="issues",
        endpoint_path="/api/v1/issues/<int:id>/comments",
        handler=work_management.get_issue_comments,
    ),
    ToolDefinition(
        name="list_projects",
        description="List tracked engineering and IT projects.",
        parameters_schema=_params(
            {
                "organization_id": _int("Organization ID."),
                "status": _str("Project status filter."),
                "search": _str("Search keyword."),
                "limit": _int("Results per page (default 50)."),
                "page": _int("Page number (default 1)."),
            }
        ),
        read_only=True,
        required_module="issues",
        endpoint_path="/api/v1/projects",
        handler=work_management.list_projects,
    ),
    ToolDefinition(
        name="get_project",
        description="Get full details for a project by ID.",
        parameters_schema=_params(
            {"project_id": _int("Project ID.")},
            required=("project_id",),
        ),
        read_only=True,
        required_module="issues",
        endpoint_path="/api/v1/projects/<int:id>",
        handler=work_management.get_project,
    ),
    ToolDefinition(
        name="list_milestones",
        description="List milestones filtered by organization, project, or status.",
        parameters_schema=_params(
            {
                "organization_id": _int("Organization ID."),
                "project_id": _int("Project ID."),
                "status": _str("Milestone status filter."),
                "search": _str("Search keyword."),
                "limit": _int("Results per page (default 50)."),
                "page": _int("Page number (default 1)."),
            }
        ),
        read_only=True,
        required_module="issues",
        endpoint_path="/api/v1/milestones",
        handler=work_management.list_milestones,
    ),
    ToolDefinition(
        name="get_milestone_issues",
        description="List all issues linked to a specific milestone.",
        parameters_schema=_params(
            {"milestone_id": _int("Milestone ID.")},
            required=("milestone_id",),
        ),
        read_only=True,
        required_module="issues",
        endpoint_path="/api/v1/milestones/<int:id>/issues",
        handler=work_management.get_milestone_issues,
    ),
    ToolDefinition(
        name="list_labels",
        description="List configured resource classification/issue labels.",
        parameters_schema=_params(
            {
                "limit": _int("Results per page (default 100)."),
                "page": _int("Page number (default 1)."),
            }
        ),
        read_only=True,
        required_module="issues",
        endpoint_path="/api/v1/labels",
        handler=work_management.list_labels,
    ),
    ToolDefinition(
        name="get_entity_metadata",
        description="Get custom structured metadata key-value fields attached to an entity.",
        parameters_schema=_params(
            {"entity_id": _int("Entity ID.")},
            required=("entity_id",),
        ),
        read_only=True,
        required_module="issues",
        endpoint_path="/api/v1/metadata/entities/<int:id>/metadata",
        handler=work_management.get_entity_metadata,
    ),
    ToolDefinition(
        name="list_helpdesk_tickets",
        description="List helpdesk support tickets filtered by status, priority, assignee, requester, or team.",
        parameters_schema=_params(
            {
                "status": _str("Ticket status filter."),
                "priority": _str("Ticket priority filter."),
                "assignee_id": _int("Assigned agent identity ID."),
                "requester_id": _int("Requester identity ID."),
                "hd_team_id": _int("Helpdesk team ID."),
                "search": _str("Search keyword."),
                "limit": _int("Results per page (default 50)."),
                "page": _int("Page number (default 1)."),
            }
        ),
        read_only=True,
        required_module="helpdesk",
        endpoint_path="/api/v1/helpdesk/tickets",
        handler=work_management.list_helpdesk_tickets,
    ),
    ToolDefinition(
        name="get_helpdesk_ticket",
        description="Get full details and SLA state for a helpdesk ticket.",
        parameters_schema=_params(
            {"ticket_id": _int("Helpdesk ticket ID.")},
            required=("ticket_id",),
        ),
        read_only=True,
        required_module="helpdesk",
        endpoint_path="/api/v1/helpdesk/tickets/<int:ticket_id>",
        handler=work_management.get_helpdesk_ticket,
    ),
    ToolDefinition(
        name="get_helpdesk_stats",
        description="Get helpdesk queue summary and SLA compliance dashboard metrics.",
        parameters_schema=_params(),
        read_only=True,
        required_module="helpdesk",
        endpoint_path="/api/v1/helpdesk/dashboard/stats",
        handler=work_management.get_helpdesk_stats,
    ),
    ToolDefinition(
        name="list_helpdesk_teams",
        description="List helpdesk teams and queues.",
        parameters_schema=_params(),
        read_only=True,
        required_module="helpdesk",
        endpoint_path="/api/v1/helpdesk/teams",
        handler=work_management.list_helpdesk_teams,
    ),
    # ------------------------------------------------------------------
    # Security, SBOM & Access Reviews (sbom, secrets, access_reviews)
    # ------------------------------------------------------------------
    ToolDefinition(
        name="list_vulnerabilities",
        description="List tracked CVEs and vulnerabilities filtered by severity, CVE ID, or keyword.",
        parameters_schema=_params(
            {
                "severity": _str("Severity filter (CRITICAL, HIGH, MEDIUM, LOW)."),
                "cve_id": _str("Exact CVE ID (e.g. CVE-2024-1234)."),
                "source": _str("Vulnerability feed source."),
                "search": _str("Search keyword."),
                "limit": _int("Results per page (default 50)."),
                "page": _int("Page number (default 1)."),
            }
        ),
        read_only=True,
        required_module="sbom",
        endpoint_path="/api/v1/vulnerabilities",
        handler=security.list_vulnerabilities,
    ),
    ToolDefinition(
        name="get_vulnerability",
        description="Get full details and affected components for a vulnerability ID.",
        parameters_schema=_params(
            {"vulnerability_id": _int("Vulnerability ID.")},
            required=("vulnerability_id",),
        ),
        read_only=True,
        required_module="sbom",
        endpoint_path="/api/v1/vulnerabilities/<int:id>",
        handler=security.get_vulnerability,
    ),
    ToolDefinition(
        name="get_vulnerability_dashboard",
        description="Get vulnerability summary counts grouped by severity and remediation state.",
        parameters_schema=_params(),
        read_only=True,
        required_module="sbom",
        endpoint_path="/api/v1/vulnerabilities/dashboard",
        handler=security.get_vulnerability_dashboard,
    ),
    ToolDefinition(
        name="list_sbom_components",
        description="List SBOM software packages filtered by parent service/software, ecosystem, or SPDX license.",
        parameters_schema=_params(
            {
                "parent_type": _str("Parent resource type (e.g. service, software)."),
                "parent_id": _int("Parent resource ID."),
                "package_type": _str(
                    "Package ecosystem (pypi, npm, cargo, golang, etc.)."
                ),
                "license_id": _str("SPDX license identifier."),
                "direct": _bool("True for direct dependencies only."),
                "search": _str("Package name search."),
                "limit": _int("Results per page (default 50)."),
                "page": _int("Page number (default 1)."),
            }
        ),
        read_only=True,
        required_module="sbom",
        endpoint_path="/api/v1/sbom/components",
        handler=security.list_sbom_components,
    ),
    ToolDefinition(
        name="list_sbom_scans",
        description="List SBOM scan execution records and outcomes.",
        parameters_schema=_params(
            {
                "parent_type": _str("Parent resource type."),
                "parent_id": _int("Parent resource ID."),
                "status": _str("Scan status."),
                "scan_type": _str("Scan type."),
                "limit": _int("Results per page (default 50)."),
                "page": _int("Page number (default 1)."),
            }
        ),
        read_only=True,
        required_module="sbom",
        endpoint_path="/api/v1/sbom/scans",
        handler=security.list_sbom_scans,
    ),
    ToolDefinition(
        name="search_services",
        description="List or search registered microservices and applications.",
        parameters_schema=_params(
            {
                "organization_id": _int("Filter by organization ID."),
                "status": _str("Service status filter."),
                "search": _str("Search keyword."),
                "limit": _int("Results per page (default 50)."),
                "page": _int("Page number (default 1)."),
            }
        ),
        read_only=True,
        required_module="sbom",
        endpoint_path="/api/v1/services",
        handler=security.search_services,
    ),
    ToolDefinition(
        name="get_service",
        description="Get full details for a specific service by ID.",
        parameters_schema=_params(
            {"service_id": _int("Service ID.")},
            required=("service_id",),
        ),
        read_only=True,
        required_module="sbom",
        endpoint_path="/api/v1/services/<int:id>",
        handler=security.get_service,
    ),
    ToolDefinition(
        name="get_service_sbom",
        description="Get SBOM package components and dependency tree for a service.",
        parameters_schema=_params(
            {"service_id": _int("Service ID.")},
            required=("service_id",),
        ),
        read_only=True,
        required_module="sbom",
        endpoint_path="/api/v1/services/<int:id>/sbom",
        handler=security.get_service_sbom,
    ),
    ToolDefinition(
        name="list_software",
        description="List software inventory items across the tenant.",
        parameters_schema=_params(
            {
                "organization_id": _int("Organization ID filter."),
                "software_type": _str("Software type filter."),
                "is_active": _bool("Active status filter."),
                "search": _str("Search keyword."),
                "limit": _int("Results per page (default 50)."),
                "page": _int("Page number (default 1)."),
            }
        ),
        read_only=True,
        required_module="sbom",
        endpoint_path="/api/v1/software",
        handler=security.list_software,
    ),
    ToolDefinition(
        name="get_software",
        description="Get details for a specific software catalog item.",
        parameters_schema=_params(
            {"software_id": _int("Software item ID.")},
            required=("software_id",),
        ),
        read_only=True,
        required_module="sbom",
        endpoint_path="/api/v1/software/<int:id>",
        handler=security.get_software,
    ),
    ToolDefinition(
        name="list_license_policies",
        description="List OSS license compliance rules and policies.",
        parameters_schema=_params(
            {
                "organization_id": _int("Organization ID filter."),
                "is_active": _bool("Active status filter."),
            }
        ),
        read_only=True,
        required_module="sbom",
        endpoint_path="/api/v1/license-policies",
        handler=security.list_license_policies,
    ),
    ToolDefinition(
        name="list_certificates",
        description="List TLS/PKI certificates including status and expiring-soon filter.",
        parameters_schema=_params(
            {
                "organization_id": _int("Organization ID filter."),
                "status": _str("Certificate status filter."),
                "cert_type": _str("Certificate type filter."),
                "expiring_soon": _bool("Set true to list certificates expiring soon."),
                "search": _str("Domain/common-name search."),
                "limit": _int("Results per page (default 50)."),
                "page": _int("Page number (default 1)."),
            }
        ),
        read_only=True,
        required_module="secrets",
        endpoint_path="/api/v1/certificates",
        handler=security.list_certificates,
    ),
    ToolDefinition(
        name="list_secrets_metadata",
        description="List secret inventory metadata (names, providers, rotation state; never secret values).",
        parameters_schema=_params(
            {
                "organization_id": _int("Organization ID filter."),
                "provider_id": _int("Secret provider ID filter."),
                "secret_type": _str("Secret type filter."),
                "limit": _int("Max rows (default 50)."),
                "offset": _int("Offset (default 0)."),
            }
        ),
        read_only=True,
        required_module="secrets",
        endpoint_path="/api/v1/secrets",
        handler=security.list_secrets_metadata,
    ),
    ToolDefinition(
        name="list_kms_keys",
        description="List KMS encryption key metadata.",
        parameters_schema=_params(
            {
                "provider_id": _int("KMS provider ID filter."),
                "key_type": _str("Key type/algorithm filter."),
                "enabled_only": _bool("Only return enabled keys."),
            }
        ),
        read_only=True,
        required_module="secrets",
        endpoint_path="/api/v1/keys",
        handler=security.list_kms_keys,
    ),
    ToolDefinition(
        name="list_access_reviews",
        description="List user access review campaigns.",
        parameters_schema=_params(
            {
                "status": _str("Campaign status filter."),
                "group_id": _int("Group ID filter."),
                "limit": _int("Max rows (default 50)."),
                "offset": _int("Offset (default 0)."),
            }
        ),
        read_only=True,
        required_module="access_reviews",
        endpoint_path="/api/v1/access-reviews",
        handler=security.list_access_reviews,
    ),
    ToolDefinition(
        name="get_access_review",
        description="Get full details for an access review campaign by ID.",
        parameters_schema=_params(
            {"review_id": _int("Access review ID.")},
            required=("review_id",),
        ),
        read_only=True,
        required_module="access_reviews",
        endpoint_path="/api/v1/access-reviews/<int:review_id>",
        handler=security.get_access_review,
    ),
    ToolDefinition(
        name="list_pending_group_requests",
        description="List pending group membership elevation/access requests.",
        parameters_schema=_params(
            {
                "limit": _int("Max rows (default 50)."),
                "offset": _int("Offset (default 0)."),
            }
        ),
        read_only=True,
        required_module="access_reviews",
        endpoint_path="/api/v1/group-membership/requests/pending",
        handler=security.list_pending_group_requests,
    ),
    ToolDefinition(
        name="get_entity_resource_roles",
        description="List resource-scoped RBAC role bindings on an infrastructure entity.",
        parameters_schema=_params(
            {"entity_id": _int("Entity ID.")},
            required=("entity_id",),
        ),
        read_only=True,
        required_module="access_reviews",
        endpoint_path="/api/v1/resource-roles/entities/<int:id>/roles",
        handler=security.get_entity_resource_roles,
    ),
    # ------------------------------------------------------------------
    # Operations (services_oncall, ipam, discovery, webhooks_alerting, core)
    # ------------------------------------------------------------------
    ToolDefinition(
        name="list_oncall_rotations",
        description="List on-call schedules and rotations.",
        parameters_schema=_params(
            {
                "org_id": _int("Filter by organization ID."),
                "service_id": _int("Filter by service ID."),
                "scope_type": _str("Rotation scope type."),
                "schedule_type": _str("Schedule cadence type."),
                "is_active": _bool("Filter by active rotations."),
            }
        ),
        read_only=True,
        required_module="services_oncall",
        endpoint_path="/api/v1/on-call/rotations",
        handler=operations.list_oncall_rotations,
    ),
    ToolDefinition(
        name="get_current_oncall_org",
        description="Get the engineer(s) currently on call for an organization.",
        parameters_schema=_params(
            {"org_id": _int("Organization ID.")},
            required=("org_id",),
        ),
        read_only=True,
        required_module="services_oncall",
        endpoint_path="/api/v1/on-call/rotations/current/organization/<int:org_id>",
        handler=operations.get_current_oncall_org,
    ),
    ToolDefinition(
        name="get_current_oncall_service",
        description="Get the engineer(s) currently on call for a specific service.",
        parameters_schema=_params(
            {"service_id": _int("Service ID.")},
            required=("service_id",),
        ),
        read_only=True,
        required_module="services_oncall",
        endpoint_path="/api/v1/on-call/rotations/current/service/<int:service_id>",
        handler=operations.get_current_oncall_service,
    ),
    ToolDefinition(
        name="list_ipam_prefixes",
        description="List IPAM CIDR network prefixes.",
        parameters_schema=_params(
            {
                "organization_id": _int("Organization ID filter."),
                "status": _str("Prefix status filter."),
                "parent_id": _int("Parent prefix ID filter."),
                "search": _str("CIDR or description search."),
                "limit": _int("Results per page (default 50)."),
                "page": _int("Page number (default 1)."),
            }
        ),
        read_only=True,
        required_module="ipam",
        endpoint_path="/api/v1/ipam/prefixes",
        handler=operations.list_ipam_prefixes,
    ),
    ToolDefinition(
        name="list_ipam_addresses",
        description="List allocated IPAM IP addresses.",
        parameters_schema=_params(
            {
                "prefix_id": _int("Parent prefix ID filter."),
                "status": _str("Address status filter."),
                "search": _str("IP or hostname search."),
                "limit": _int("Results per page (default 50)."),
                "page": _int("Page number (default 1)."),
            }
        ),
        read_only=True,
        required_module="ipam",
        endpoint_path="/api/v1/ipam/addresses",
        handler=operations.list_ipam_addresses,
    ),
    ToolDefinition(
        name="list_ipam_vlans",
        description="List IPAM VLAN allocations.",
        parameters_schema=_params(
            {
                "organization_id": _int("Organization ID filter."),
                "status": _str("VLAN status filter."),
                "search": _str("VLAN ID/name search."),
                "limit": _int("Results per page (default 50)."),
                "page": _int("Page number (default 1)."),
            }
        ),
        read_only=True,
        required_module="ipam",
        endpoint_path="/api/v1/ipam/vlans",
        handler=operations.list_ipam_vlans,
    ),
    ToolDefinition(
        name="list_discovery_jobs",
        description="List configured cloud and infrastructure discovery jobs.",
        parameters_schema=_params(
            {
                "provider": _str("Cloud/infra provider filter."),
                "enabled": _bool("Filter by enabled flag."),
                "organization_id": _int("Organization ID filter."),
            }
        ),
        read_only=True,
        required_module="discovery",
        endpoint_path="/api/v1/discovery/jobs",
        handler=operations.list_discovery_jobs,
    ),
    ToolDefinition(
        name="get_discovery_history",
        description="Get recent infrastructure discovery execution history.",
        parameters_schema=_params({"limit": _int("Max entries (default 50).")}),
        read_only=True,
        required_module="discovery",
        endpoint_path="/api/v1/discovery/history",
        handler=operations.get_discovery_history,
    ),
    ToolDefinition(
        name="list_sync_configs",
        description="List external integration synchronization configurations.",
        parameters_schema=_params(),
        read_only=True,
        required_module="discovery",
        endpoint_path="/api/v1/sync/configs",
        handler=operations.list_sync_configs,
    ),
    ToolDefinition(
        name="get_sync_status",
        description="Get overall synchronization health and recent sync run statuses.",
        parameters_schema=_params(),
        read_only=True,
        required_module="discovery",
        endpoint_path="/api/v1/sync/status",
        handler=operations.get_sync_status,
    ),
    ToolDefinition(
        name="list_sync_conflicts",
        description="List unresolved or resolved data synchronization conflicts.",
        parameters_schema=_params(
            {
                "resolved": _bool(
                    "Include resolved conflicts when true (default false)."
                ),
                "config_id": _int("Sync configuration ID filter."),
            }
        ),
        read_only=True,
        required_module="discovery",
        endpoint_path="/api/v1/sync/conflicts",
        handler=operations.list_sync_conflicts,
    ),
    ToolDefinition(
        name="list_webhooks",
        description="List outbound alerting webhook configurations.",
        parameters_schema=_params(
            {
                "enabled": _bool("Filter by enabled state."),
                "status": _str("Webhook status filter."),
                "limit": _int("Max rows (default 50)."),
            }
        ),
        read_only=True,
        required_module="webhooks_alerting",
        endpoint_path="/api/v1/webhooks",
        handler=operations.list_webhooks,
    ),
    ToolDefinition(
        name="list_inbound_webhooks",
        description="List inbound webhook endpoints configured to receive external alerts.",
        parameters_schema=_params(),
        read_only=True,
        required_module="webhooks_alerting",
        endpoint_path="/api/v1/webhooks/inbound",
        handler=operations.list_inbound_webhooks,
    ),
    ToolDefinition(
        name="list_identities",
        description="List user, service-account, and IAM identities in the tenant.",
        parameters_schema=_params(
            {
                "search": _str("Name or email search filter."),
                "identity_type": _str("Identity type filter."),
                "is_active": _bool("Active state filter."),
                "auth_provider": _str("Identity provider filter."),
                "limit": _int("Results per page (default 50)."),
                "page": _int("Page number (default 1)."),
            }
        ),
        read_only=True,
        required_module=None,
        endpoint_path="/api/v1/identities",
        handler=operations.list_identities,
    ),
    ToolDefinition(
        name="get_identity",
        description="Get details for a single user or service identity by ID.",
        parameters_schema=_params(
            {"identity_id": _int("Identity ID.")},
            required=("identity_id",),
        ),
        read_only=True,
        required_module=None,
        endpoint_path="/api/v1/identities/<int:id>",
        handler=operations.get_identity,
    ),
    ToolDefinition(
        name="list_identity_groups",
        description="List IAM identity groups within the tenant.",
        parameters_schema=_params(),
        read_only=True,
        required_module=None,
        endpoint_path="/api/v1/identities/groups",
        handler=operations.list_identity_groups,
    ),
    ToolDefinition(
        name="list_backups",
        description="List Elder system backups and snapshot history.",
        parameters_schema=_params({"enabled": _bool("Filter by enabled status.")}),
        read_only=True,
        required_module=None,
        endpoint_path="/api/v1/backup",
        handler=operations.list_backups,
    ),
    ToolDefinition(
        name="get_backup_stats",
        description="Get aggregate statistics for configured backups and snapshots.",
        parameters_schema=_params(),
        read_only=True,
        required_module=None,
        endpoint_path="/api/v1/backup/stats",
        handler=operations.get_backup_stats,
    ),
    ToolDefinition(
        name="list_system_logs",
        description="Query recent Elder platform/system log entries.",
        parameters_schema=_params(
            {
                "q": _str("Search string filter."),
                "limit": _int("Max rows (default 50)."),
                "offset": _int("Pagination offset (default 0)."),
            }
        ),
        read_only=True,
        required_module=None,
        endpoint_path="/api/v1/logs",
        handler=operations.list_system_logs,
    ),
    # ------------------------------------------------------------------
    # Knowledge & Workstreams (documents, pages, diagrams, ai, streams, flows)
    # ------------------------------------------------------------------
    ToolDefinition(
        name="list_documents",
        description="List knowledge-base runbooks and engineering documents.",
        parameters_schema=_params(
            {
                "status": _str("Document status filter."),
                "category": _str("Document category filter."),
                "q": _str("Title/content search text."),
            }
        ),
        read_only=True,
        required_module="documents",
        endpoint_path="/api/v1/documents",
        handler=knowledge.list_documents,
    ),
    ToolDefinition(
        name="get_document",
        description="Read a knowledge-base document's full content by slug.",
        parameters_schema=_params(
            {"slug": _str("Document URL slug.")},
            required=("slug",),
        ),
        read_only=True,
        required_module="documents",
        endpoint_path="/api/v1/documents/<slug>",
        handler=knowledge.get_document,
    ),
    ToolDefinition(
        name="list_pages",
        description="List wiki/runbook pages with optional status and visibility filters.",
        parameters_schema=_params(
            {
                "status": _str("Page status filter."),
                "visibility": _str("Page visibility filter."),
            }
        ),
        read_only=True,
        required_module="pages",
        endpoint_path="/api/v1/pages",
        handler=knowledge.list_pages,
    ),
    ToolDefinition(
        name="get_page",
        description="Read a wiki/runbook page by slug.",
        parameters_schema=_params(
            {"slug": _str("Page slug.")},
            required=("slug",),
        ),
        read_only=True,
        required_module="pages",
        endpoint_path="/api/v1/pages/<slug>",
        handler=knowledge.get_page,
    ),
    ToolDefinition(
        name="list_diagrams",
        description="List architecture, topology, and system diagrams.",
        parameters_schema=_params(
            {
                "status": _str("Diagram status filter."),
                "q": _str("Search text."),
                "is_template": _bool("Filter template diagrams."),
            }
        ),
        read_only=True,
        required_module="diagrams",
        endpoint_path="/api/v1/diagrams",
        handler=knowledge.list_diagrams,
    ),
    ToolDefinition(
        name="get_diagram",
        description="Get a diagram's details and node/edge structure by ID.",
        parameters_schema=_params(
            {"diagram_id": _int("Diagram ID.")},
            required=("diagram_id",),
        ),
        read_only=True,
        required_module="diagrams",
        endpoint_path="/api/v1/diagrams/<int:diagram_id>",
        handler=knowledge.get_diagram,
    ),
    ToolDefinition(
        name="list_ai_resources",
        description="List registered AI models, agents, and AI governance inventory items.",
        parameters_schema=_params(
            {
                "organization_id": _int("Organization ID filter."),
                "resource_type": _str("AI resource type filter."),
                "status": _str("Governance status filter."),
                "agreement_level": _str("Agreement/risk tier filter."),
                "search": _str("Search keyword."),
            }
        ),
        read_only=True,
        required_module="ai",
        endpoint_path="/api/v1/ai/resources",
        handler=knowledge.list_ai_resources,
    ),
    ToolDefinition(
        name="get_ai_resource",
        description="Get full details for a single AI governance resource by ID.",
        parameters_schema=_params(
            {"resource_id": _int("AI resource ID.")},
            required=("resource_id",),
        ),
        read_only=True,
        required_module="ai",
        endpoint_path="/api/v1/ai/resources/<int:id>",
        handler=knowledge.get_ai_resource,
    ),
    ToolDefinition(
        name="list_streams",
        description="List automated operational workflow streams.",
        parameters_schema=_params(
            {
                "status": _str("Stream status filter."),
                "q": _str("Search keyword."),
                "is_template": _bool("Filter stream templates."),
            }
        ),
        read_only=True,
        required_module="streams",
        endpoint_path="/api/v1/streams",
        handler=knowledge.list_streams,
    ),
    ToolDefinition(
        name="get_stream",
        description="Get full workflow definition for a specific stream by ID.",
        parameters_schema=_params(
            {"stream_id": _int("Stream ID.")},
            required=("stream_id",),
        ),
        read_only=True,
        required_module="streams",
        endpoint_path="/api/v1/streams/<int:stream_id>",
        handler=knowledge.get_stream,
    ),
    ToolDefinition(
        name="list_stream_executions",
        description="List automation stream execution runs globally or for one stream.",
        parameters_schema=_params(
            {
                "stream_id": _int("Optional stream ID to filter executions."),
                "status": _str("Execution status filter."),
            }
        ),
        read_only=True,
        required_module="streams",
        endpoint_path="/api/v1/streams/executions",
        handler=knowledge.list_stream_executions,
    ),
    ToolDefinition(
        name="list_flows",
        description="List CI/CD deployment flow pipelines.",
        parameters_schema=_params(
            {
                "status": _str("Pipeline status filter."),
                "repository_provider": _str("Git provider filter."),
                "is_enabled": _bool("Enabled filter."),
                "search": _str("Search keyword."),
                "page": _int("Page number (default 1)."),
                "per_page": _int("Results per page (default 20)."),
            }
        ),
        read_only=True,
        required_module="flows",
        endpoint_path="/api/v1/flows",
        handler=knowledge.list_flows,
    ),
    ToolDefinition(
        name="list_flow_promotions",
        description="List CI/CD environment promotions and approval states.",
        parameters_schema=_params(
            {
                "status": _str("Promotion status filter."),
                "flow_id": _str("Flow pipeline ID filter."),
                "page": _int("Page number (default 1)."),
                "per_page": _int("Results per page (default 20)."),
            }
        ),
        read_only=True,
        required_module="flows",
        endpoint_path="/api/v1/flows/promotions",
        handler=knowledge.list_flow_promotions,
    ),
    # ------------------------------------------------------------------
    # Village ID v1 Resolution & Catalog-Backed API Parity
    # ------------------------------------------------------------------
    ToolDefinition(
        name="resolve_village_id",
        description="Resolve an Elder resource via /api/v1/id/{village_id} using its compact VillageID.",
        parameters_schema=_params(
            {"village_id": _str("Compact VillageID token.")},
            required=("village_id",),
        ),
        read_only=True,
        required_module="infrastructure",
        endpoint_path="/api/v1/id/<village_id>",
        handler=resources.resolve_village_id,
    ),
    ToolDefinition(
        name="list_api_services",
        description="List all cataloged Elder hub-api service namespaces and operation counts.",
        parameters_schema=_params(),
        read_only=True,
        required_module="ai",
        endpoint_path="/api/v1/modules",
        handler=api_catalog.list_api_services,
    ),
    ToolDefinition(
        name="list_api_operations",
        description="Discover available Elder hub-api operations by service namespace, HTTP method, or search query.",
        parameters_schema=_params(
            {
                "service": _str(
                    "Optional API service namespace filter (e.g. licensing, discovery, webhooks)."
                ),
                "method": _str(
                    "Optional HTTP method filter (GET, POST, PUT, PATCH, DELETE)."
                ),
                "search": _str(
                    "Optional substring filter across service, operation, path, and summary."
                ),
            }
        ),
        read_only=True,
        required_module="ai",
        endpoint_path="/api/v1/modules",
        handler=api_catalog.list_api_operations,
    ),
    ToolDefinition(
        name="describe_api_operation",
        description="Return exact HTTP method, path template, path parameters, and summary for a cataloged hub-api operation.",
        parameters_schema=_params(
            {
                "service": _str("API service namespace."),
                "operation": _str("Operation name within the service."),
            },
            required=("service", "operation"),
        ),
        read_only=True,
        required_module="ai",
        endpoint_path="/api/v1/modules",
        handler=api_catalog.describe_api_operation,
    ),
    ToolDefinition(
        name="invoke_api_operation",
        description="Invoke any cataloged hub-api REST endpoint using caller credentials (1:1 API parity).",
        parameters_schema=_params(
            {
                "service": _str("API service namespace."),
                "operation": _str("Operation name within the service."),
                "path_params": _obj(
                    "Optional path parameter substitutions keyed by parameter name."
                ),
                "query_params": _obj("Optional query string parameters."),
                "body": _obj("Optional JSON request body payload."),
            },
            required=("service", "operation"),
        ),
        read_only=False,
        required_module=None,
        endpoint_path="/api/v1/modules",
        handler=api_catalog.invoke_api_operation,
    ),
    ToolDefinition(
        name="call_api_operation",
        description="Invoke any cataloged hub-api REST endpoint using caller credentials (alias of invoke_api_operation).",
        parameters_schema=_params(
            {
                "service": _str("API service namespace."),
                "operation": _str("Operation name within the service."),
                "path_params": _obj(
                    "Optional path parameter substitutions keyed by parameter name."
                ),
                "query_params": _obj("Optional query string parameters."),
                "body": _obj("Optional JSON request body payload."),
            },
            required=("service", "operation"),
        ),
        read_only=False,
        required_module=None,
        endpoint_path="/api/v1/modules",
        handler=api_catalog.call_api_operation,
    ),
)

TOOL_BY_NAME: dict[str, ToolDefinition] = {tool.name: tool for tool in TOOL_REGISTRY}


def get_tools(
    enabled_modules: Iterable[str] | None = None,
    read_only: bool = True,
) -> list[ToolDefinition]:
    """Return tools filtered by read-only policy and optional enabled modules."""
    module_set = set(enabled_modules) if enabled_modules is not None else None
    result: list[ToolDefinition] = []
    for tool in TOOL_REGISTRY:
        if read_only and not tool.read_only:
            continue
        if (
            module_set is not None
            and tool.required_module is not None
            and tool.required_module not in module_set
        ):
            continue
        result.append(tool)
    return result


def to_ollama_tools(tools: Iterable[ToolDefinition]) -> list[dict[str, Any]]:
    """Convert ``ToolDefinition`` instances to Ollama ``/api/chat`` tool schemas."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters_schema,
            },
        }
        for tool in tools
    ]


def _coerce_arg_value(value: Any, schema: dict[str, Any] | None) -> Any:
    """Coerce common LLM JSON mismatches (e.g. stringified ints/bools) cleanly."""
    if schema is None or value is None:
        return value
    expected_type = schema.get("type")
    if expected_type == "integer" and isinstance(value, str):
        stripped = value.strip()
        if stripped.lstrip("-").isdigit():
            return int(stripped)
    elif expected_type == "boolean" and isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
    return value


async def _execute_tool_impl(
    name: str,
    client: ElderClient,
    arguments: dict[str, Any] | None = None,
    *,
    read_only_only: bool = True,
    allowed_modules: Iterable[str] | None = None,
) -> Any:
    """Shared authorization/module gating and argument validation for MCP tools."""
    tool = TOOL_BY_NAME.get(name)
    if tool is None:
        raise ValueError(f"Unknown MCP tool: {name!r}")
    if read_only_only and not tool.read_only:
        raise PermissionError(
            f"Tool {name!r} is a write operation and read_only is enabled"
        )
    if allowed_modules is not None and tool.required_module is not None:
        if tool.required_module not in set(allowed_modules):
            raise PermissionError(
                f"Tool {name!r} requires module {tool.required_module!r}, which is disabled for this tenant"
            )

    sig = inspect.signature(tool.handler)
    accepted = {param_name for param_name in sig.parameters if param_name != "client"}
    schema_props = tool.parameters_schema.get("properties", {})
    raw_args = arguments or {}
    if not isinstance(raw_args, dict):
        raise ValueError("Tool arguments must be a JSON object")
    call_kwargs: dict[str, Any] = {}
    for key, val in raw_args.items():
        if key in accepted and val is not None:
            call_kwargs[key] = _coerce_arg_value(val, schema_props.get(key))

    for param_name, param in sig.parameters.items():
        if param_name == "client":
            continue
        if param.default is inspect.Parameter.empty and param_name not in call_kwargs:
            raise ValueError(f"Tool {name!r} requires argument {param_name!r}")

    return await tool.handler(client, **call_kwargs)


async def invoke_tool(
    name: str,
    client: ElderClient,
    arguments: dict[str, Any] | None = None,
    *,
    read_only_only: bool = True,
    allowed_modules: Iterable[str] | None = None,
) -> Any:
    """Validate authorization/module gates and invoke an Elder MCP tool handler (default read-only)."""
    return await _execute_tool_impl(
        name,
        client,
        arguments,
        read_only_only=read_only_only,
        allowed_modules=allowed_modules,
    )


async def execute_tool(
    name: str,
    client: ElderClient,
    arguments: dict[str, Any] | None = None,
    *,
    read_only_only: bool = False,
    allowed_modules: Iterable[str] | None = None,
) -> Any:
    """Execute an Elder MCP tool handler across full read+write surface (shared by HTTP and stdio)."""
    return await _execute_tool_impl(
        name,
        client,
        arguments,
        read_only_only=read_only_only,
        allowed_modules=allowed_modules,
    )
