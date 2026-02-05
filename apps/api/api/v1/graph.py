"""Graph visualization API endpoints using PyDAL with async/await."""

# flake8: noqa: E501


from typing import Dict

import networkx as nx
from flask import Blueprint, current_app, jsonify, request

from apps.api.auth.decorators import login_required
from apps.api.utils.async_utils import run_in_threadpool

bp = Blueprint("graph", __name__)

# Valid resource types for map
VALID_RESOURCE_TYPES = [
    "organization",
    "entity",
    "identity",
    "project",
    "milestone",
    "issue",
]


@bp.route("", methods=["GET"])
async def get_graph():
    """
    Get full dependency graph or filtered subgraph.

    Query Parameters:
        - organization_id: Filter by organization
        - entity_type: Filter by entity type
        - entity_id: Center graph on specific entity
        - depth: Maximum depth from entity_id (default: 2, -1 for all)
        - include_metadata: Include entity metadata (default: false)

    Returns:
        200: Graph data in vis.js compatible format
        {
            "nodes": [{"id": 1, "label": "Entity Name", "type": "compute", ...}],
            "edges": [{"from": 1, "to": 2, "type": "depends_on", ...}]
        }
    """
    db = current_app.db

    # Get filter parameters
    org_id = request.args.get("organization_id", type=int)
    entity_type = request.args.get("entity_type")
    entity_id = request.args.get("entity_id", type=int)
    depth = request.args.get("depth", 2, type=int)
    include_metadata = request.args.get("include_metadata", "false").lower() == "true"

    def get_graph_data():
        # Build entity query
        query = db.entities.id > 0

        if org_id:
            query &= db.entities.organization_id == org_id

        if entity_type:
            query &= db.entities.entity_type == entity_type

        # If entity_id specified, get subgraph centered on that entity
        if entity_id:
            entity = db.entities[entity_id]
            if not entity:
                return None, "Entity not found", 404

            entities = _get_entity_subgraph(db, entity, depth)
        else:
            # Get all entities matching filters
            entities = db(query).select()

        # Get entity IDs for dependency filtering
        entity_ids = [e.id for e in entities]

        # Early return if no entities found
        if not entity_ids:
            return (
                {
                    "nodes": [],
                    "edges": [],
                    "stats": {
                        "entity_count": 0,
                        "dependency_count": 0,
                    },
                },
                None,
                200,
            )

        # Get dependencies between these entities
        # Dependencies table uses source_type/source_id, not source_entity_id
        dependencies = db(
            (db.dependencies.source_type == "entity")
            & (db.dependencies.source_id.belongs(entity_ids))
            & (db.dependencies.target_type == "entity")
            & (db.dependencies.target_id.belongs(entity_ids))
        ).select()

        # Build vis.js compatible graph data
        nodes = []
        for entity in entities:
            node = {
                "id": entity.id,
                "label": entity.name,
                "type": entity.entity_type,
                "organization_id": entity.organization_id,
            }

            if include_metadata and entity.attributes:
                node["metadata"] = entity.attributes

            # Add visual styling based on entity type
            node["shape"], node["color"] = _get_node_style(entity.entity_type)

            nodes.append(node)

        edges = []
        for dep in dependencies:
            edge = {
                "id": dep.id,
                "from": dep.source_id,
                "to": dep.target_id,
                "type": dep.dependency_type,
                "arrows": "to",
            }

            # Add visual styling based on dependency type
            edge["color"], edge["dashes"] = _get_edge_style(dep.dependency_type)

            edges.append(edge)

        return (
            {
                "nodes": nodes,
                "edges": edges,
                "stats": {
                    "entity_count": len(entities),
                    "dependency_count": len(dependencies),
                },
            },
            None,
            200,
        )

    result, error, status = await run_in_threadpool(get_graph_data)

    if error:
        return jsonify({"error": error}), status

    return jsonify(result), status


@bp.route("/analyze", methods=["GET"])
async def analyze_graph():
    """
    Analyze dependency graph and return insights.

    Query Parameters:
        - organization_id: Filter by organization

    Returns:
        200: Graph analysis metrics
    """
    db = current_app.db

    org_id = request.args.get("organization_id", type=int)

    def analyze():
        # Build query
        query = db.entities.id > 0
        if org_id:
            query &= db.entities.organization_id == org_id

        entities = db(query).select()
        entity_ids = [e.id for e in entities]

        # Early return if no entities found
        if not entity_ids:
            return {
                "basic_stats": {
                    "total_entities": 0,
                    "total_dependencies": 0,
                    "entities_by_type": {},
                },
                "graph_metrics": {
                    "density": 0,
                    "is_directed_acyclic": True,
                },
                "centrality": {},
                "critical_paths": [],
                "issues": {
                    "circular_dependencies": 0,
                    "cycles": [],
                    "isolated_entities": 0,
                },
            }

        dependencies = db(
            (db.dependencies.source_type == "entity")
            & (db.dependencies.source_id.belongs(entity_ids))
            & (db.dependencies.target_type == "entity")
            & (db.dependencies.target_id.belongs(entity_ids))
        ).select()

        # Build NetworkX graph for analysis
        G = nx.DiGraph()

        for entity in entities:
            G.add_node(
                entity.id,
                **{
                    "name": entity.name,
                    "type": entity.entity_type,
                },
            )

        for dep in dependencies:
            G.add_edge(dep.source_id, dep.target_id)

        # Calculate metrics
        analysis = {
            "basic_stats": {
                "total_entities": len(entities),
                "total_dependencies": len(dependencies),
                "entities_by_type": _count_by_type(entities),
            },
            "graph_metrics": {
                "density": nx.density(G) if len(entities) > 1 else 0,
                "is_directed_acyclic": nx.is_directed_acyclic_graph(G),
            },
            "centrality": {},
            "critical_paths": [],
        }

        # Node centrality (most connected/important entities)
        if len(entities) > 0:
            in_degree = dict(G.in_degree())
            out_degree = dict(G.out_degree())

            # Top 10 most depended upon (high in-degree)
            most_depended = sorted(in_degree.items(), key=lambda x: x[1], reverse=True)[
                :10
            ]
            analysis["centrality"]["most_depended_upon"] = [
                {"entity_id": eid, "name": G.nodes[eid]["name"], "in_degree": deg}
                for eid, deg in most_depended
                if deg > 0
            ]

            # Top 10 with most dependencies (high out-degree)
            most_dependent = sorted(
                out_degree.items(), key=lambda x: x[1], reverse=True
            )[:10]
            analysis["centrality"]["most_dependent"] = [
                {"entity_id": eid, "name": G.nodes[eid]["name"], "out_degree": deg}
                for eid, deg in most_dependent
                if deg > 0
            ]

        # Detect cycles (circular dependencies)
        try:
            cycles = list(nx.simple_cycles(G))
            analysis["issues"] = {
                "circular_dependencies": len(cycles),
                "cycles": cycles[:5] if cycles else [],  # Return first 5 cycles
            }
        except Exception:
            analysis["issues"] = {"circular_dependencies": 0, "cycles": []}

        # Find isolated entities (no dependencies)
        isolated = [n for n in G.nodes() if G.degree(n) == 0]
        analysis["issues"]["isolated_entities"] = len(isolated)

        return analysis

    analysis = await run_in_threadpool(analyze)
    return jsonify(analysis), 200


@bp.route("/path", methods=["GET"])
async def find_path():
    """
    Find dependency path between two entities.

    Query Parameters:
        - from: Source entity ID
        - to: Target entity ID

    Returns:
        200: Path information
        404: No path found
    """
    db = current_app.db

    from_id = request.args.get("from", type=int)
    to_id = request.args.get("to", type=int)

    if not from_id or not to_id:
        return jsonify({"error": "Both 'from' and 'to' parameters required"}), 400

    def find_path_impl():
        # Verify entities exist
        from_entity = db.entities[from_id]
        to_entity = db.entities[to_id]

        if not from_entity:
            return None, "Source entity not found", 404
        if not to_entity:
            return None, "Target entity not found", 404

        # Build graph - only include entity dependencies
        dependencies = db(
            (db.dependencies.source_type == "entity")
            & (db.dependencies.target_type == "entity")
        ).select()
        G = nx.DiGraph()

        for dep in dependencies:
            G.add_edge(dep.source_id, dep.target_id, dependency_id=dep.id)

        # Find path
        try:
            path = nx.shortest_path(G, from_id, to_id)
            path_length = len(path) - 1

            # Get entities in path
            entities_in_path = db(db.entities.id.belongs(path)).select()
            entity_map = {e.id: e for e in entities_in_path}

            path_details = [
                {
                    "id": eid,
                    "name": entity_map[eid].name,
                    "type": entity_map[eid].entity_type,
                }
                for eid in path
            ]

            return (
                {
                    "path_exists": True,
                    "path_length": path_length,
                    "path": path_details,
                },
                None,
                200,
            )

        except nx.NetworkXNoPath:
            return (
                {
                    "path_exists": False,
                    "message": f"No dependency path from {from_entity.name} to {to_entity.name}",
                },
                None,
                200,
            )

    result, error, status = await run_in_threadpool(find_path_impl)

    if error:
        return jsonify({"error": error}), status

    return jsonify(result), status


@bp.route("/map", methods=["GET"])
@login_required
async def get_map():
    """
    Get global map of all resources and relationships for visualization.

    Query Parameters:
        - tenant_id: Filter by tenant
        - organization_id: Filter by organization (includes children)
        - resource_types: Comma-separated list (organization,entity,identity,project,milestone,issue)
        - entity_types: Comma-separated entity subtypes (network,compute,storage,etc.)
        - include_hierarchical: Include parent-child relationships (default: true)
        - include_dependencies: Include polymorphic dependencies (default: true)
        - limit: Maximum nodes to return (default: 500)

    Returns:
        200: Graph data in vis.js compatible format
    """
    db = current_app.db

    # Get filter parameters
    tenant_id = request.args.get("tenant_id", type=int)
    org_id = request.args.get("organization_id", type=int)
    resource_types_param = request.args.get("resource_types", "")
    entity_types_param = request.args.get("entity_types", "")
    include_hierarchical = (
        request.args.get("include_hierarchical", "true").lower() == "true"
    )
    include_dependencies = (
        request.args.get("include_dependencies", "true").lower() == "true"
    )
    limit = request.args.get("limit", 500, type=int)

    # Parse resource types
    resource_types = (
        [t.strip() for t in resource_types_param.split(",") if t.strip()]
        if resource_types_param
        else VALID_RESOURCE_TYPES
    )
    entity_types = (
        [t.strip() for t in entity_types_param.split(",") if t.strip()]
        if entity_types_param
        else []
    )

    def get_map_data():
        nodes = []
        edges = []
        node_ids = set()  # Track unique node IDs as "type:id"

        # Helper to add a node
        def add_node(
            resource_type: str,
            resource_id: int,
            label: str,
            subtype: str = None,
            extra: dict = None,
        ):
            node_key = f"{resource_type}:{resource_id}"
            if node_key in node_ids:
                return
            if len(nodes) >= limit:
                return
            node_ids.add(node_key)

            node = {
                "id": node_key,
                "label": label,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "type": subtype or resource_type,
            }
            node["shape"], node["color"] = _get_node_style_by_resource(
                resource_type, subtype
            )
            if extra:
                node.update(extra)
            nodes.append(node)

        # Helper to add an edge
        def add_edge(
            from_type: str,
            from_id: int,
            to_type: str,
            to_id: int,
            edge_type: str,
            is_hierarchical: bool = False,
        ):
            from_key = f"{from_type}:{from_id}"
            to_key = f"{to_type}:{to_id}"
            # Only add edge if both nodes exist
            if from_key not in node_ids or to_key not in node_ids:
                return
            edge = {
                "id": f"{from_key}->{to_key}",
                "from": from_key,
                "to": to_key,
                "type": edge_type,
                "arrows": "to",
                "is_hierarchical": is_hierarchical,
            }
            edge["color"], edge["dashes"] = _get_edge_style(edge_type)
            edges.append(edge)

        # Get organizations that match filters
        org_ids_to_include = set()
        if "organization" in resource_types:
            org_query = db.organizations.id > 0
            if tenant_id:
                org_query &= db.organizations.tenant_id == tenant_id
            if org_id:
                # Get this org and all children recursively
                org_ids_to_include = _get_org_tree(db, org_id)
                if org_ids_to_include:
                    org_query &= db.organizations.id.belongs(list(org_ids_to_include))

            orgs = db(org_query).select(limitby=(0, limit))
            for org in orgs:
                add_node(
                    "organization",
                    org.id,
                    org.name,
                    org.organization_type,
                    {
                        "parent_id": org.parent_id,
                        "organization_type": org.organization_type,
                    },
                )
                org_ids_to_include.add(org.id)

        # Get entities
        if "entity" in resource_types:
            entity_query = db.entities.id > 0
            if tenant_id:
                entity_query &= db.entities.tenant_id == tenant_id
            if org_ids_to_include:
                entity_query &= db.entities.organization_id.belongs(
                    list(org_ids_to_include)
                )
            elif org_id:
                entity_query &= db.entities.organization_id == org_id
            if entity_types:
                entity_query &= db.entities.entity_type.belongs(entity_types)

            entities = db(entity_query).select(limitby=(0, limit))
            for entity in entities:
                add_node(
                    "entity",
                    entity.id,
                    entity.name,
                    entity.entity_type,
                    {
                        "organization_id": entity.organization_id,
                        "parent_id": entity.parent_id,
                    },
                )

        # Get identities
        if "identity" in resource_types:
            identity_query = db.identities.id > 0
            if tenant_id:
                identity_query &= db.identities.tenant_id == tenant_id
            if org_ids_to_include:
                identity_query &= db.identities.organization_id.belongs(
                    list(org_ids_to_include)
                )

            identities = db(identity_query).select(limitby=(0, limit))
            for identity in identities:
                label = identity.full_name or identity.username
                add_node(
                    "identity",
                    identity.id,
                    label,
                    identity.identity_type,
                    {"organization_id": identity.organization_id},
                )

        # Get projects
        if "project" in resource_types:
            project_query = db.projects.id > 0
            if tenant_id:
                project_query &= db.projects.tenant_id == tenant_id
            if org_ids_to_include:
                project_query &= db.projects.organization_id.belongs(
                    list(org_ids_to_include)
                )

            projects = db(project_query).select(limitby=(0, limit))
            for project in projects:
                add_node(
                    "project",
                    project.id,
                    project.name,
                    "project",
                    {
                        "organization_id": project.organization_id,
                        "status": project.status,
                    },
                )

        # Get milestones
        if "milestone" in resource_types:
            milestone_query = db.milestones.id > 0
            if tenant_id:
                milestone_query &= db.milestones.tenant_id == tenant_id
            if org_ids_to_include:
                milestone_query &= db.milestones.organization_id.belongs(
                    list(org_ids_to_include)
                )

            milestones = db(milestone_query).select(limitby=(0, limit))
            for milestone in milestones:
                add_node(
                    "milestone",
                    milestone.id,
                    milestone.title,
                    "milestone",
                    {
                        "organization_id": milestone.organization_id,
                        "status": milestone.status,
                    },
                )

        # Get issues
        if "issue" in resource_types:
            issue_query = db.issues.id > 0
            if tenant_id:
                issue_query &= db.issues.tenant_id == tenant_id
            if org_ids_to_include:
                issue_query &= db.issues.organization_id.belongs(
                    list(org_ids_to_include)
                )

            issues = db(issue_query).select(limitby=(0, limit))
            for issue in issues:
                add_node(
                    "issue",
                    issue.id,
                    issue.title,
                    issue.issue_type,
                    {
                        "organization_id": issue.organization_id,
                        "status": issue.status,
                        "priority": issue.priority,
                    },
                )

        # Add hierarchical edges
        if include_hierarchical:
            for node in nodes:
                resource_type = node["resource_type"]

                # Organization parent relationships
                if resource_type == "organization" and node.get("parent_id"):
                    add_edge(
                        "organization",
                        node.get("parent_id"),
                        "organization",
                        node["resource_id"],
                        "parent_of",
                        True,
                    )

                # Entity to organization
                if resource_type == "entity" and node.get("organization_id"):
                    add_edge(
                        "organization",
                        node.get("organization_id"),
                        "entity",
                        node["resource_id"],
                        "contains",
                        True,
                    )

                # Entity parent relationships
                if resource_type == "entity" and node.get("parent_id"):
                    add_edge(
                        "entity",
                        node.get("parent_id"),
                        "entity",
                        node["resource_id"],
                        "parent_of",
                        True,
                    )

                # Identity to organization
                if resource_type == "identity" and node.get("organization_id"):
                    add_edge(
                        "organization",
                        node.get("organization_id"),
                        "identity",
                        node["resource_id"],
                        "contains",
                        True,
                    )

                # Project to organization
                if resource_type == "project" and node.get("organization_id"):
                    add_edge(
                        "organization",
                        node.get("organization_id"),
                        "project",
                        node["resource_id"],
                        "contains",
                        True,
                    )

                # Milestone to organization
                if resource_type == "milestone" and node.get("organization_id"):
                    add_edge(
                        "organization",
                        node.get("organization_id"),
                        "milestone",
                        node["resource_id"],
                        "contains",
                        True,
                    )

                # Issue to organization
                if resource_type == "issue" and node.get("organization_id"):
                    add_edge(
                        "organization",
                        node.get("organization_id"),
                        "issue",
                        node["resource_id"],
                        "contains",
                        True,
                    )

        # Add polymorphic dependency edges
        if include_dependencies:
            dep_query = db.dependencies.id > 0
            if tenant_id:
                dep_query &= db.dependencies.tenant_id == tenant_id

            dependencies = db(dep_query).select()
            for dep in dependencies:
                add_edge(
                    dep.source_type,
                    dep.source_id,
                    dep.target_type,
                    dep.target_id,
                    dep.dependency_type,
                    False,
                )

        return {
            "nodes": nodes,
            "edges": edges,
            "stats": {
                "node_count": len(nodes),
                "edge_count": len(edges),
                "resource_types": list(set(n["resource_type"] for n in nodes)),
                "truncated": len(nodes) >= limit,
            },
        }

    result = await run_in_threadpool(get_map_data)
    return jsonify(result), 200


def _get_org_tree(db, org_id: int) -> set:
    """Get organization and all its children recursively."""
    org_ids = {org_id}
    children = db(db.organizations.parent_id == org_id).select()
    for child in children:
        org_ids.update(_get_org_tree(db, child.id))
    return org_ids


def _get_node_style_by_resource(resource_type: str, subtype: str = None) -> tuple:
    """Get vis.js node styling based on resource type and subtype."""
    # Resource type base styles
    resource_styles = {
        "organization": ("box", "#3498db"),
        "entity": ("circle", "#e74c3c"),
        "identity": ("triangle", "#9b59b6"),
        "project": ("square", "#27ae60"),
        "milestone": ("star", "#f39c12"),
        "issue": ("diamond", "#e67e22"),
    }

    # Entity subtype overrides
    entity_subtypes = {
        "datacenter": ("box", "#2c3e50"),
        "vpc": ("box", "#2980b9"),
        "subnet": ("ellipse", "#1abc9c"),
        "compute": ("circle", "#e74c3c"),
        "network": ("diamond", "#f39c12"),
        "storage": ("box", "#8e44ad"),
        "security": ("hexagon", "#c0392b"),
        "user": ("triangle", "#9b59b6"),
    }

    if resource_type == "entity" and subtype in entity_subtypes:
        return entity_subtypes[subtype]

    return resource_styles.get(resource_type, ("dot", "#95a5a6"))


def _get_entity_subgraph(db, entity, depth: int):
    """
    Get entities within depth distance from given entity.

    Args:
        db: PyDAL database instance
        entity: Center entity (PyDAL row)
        depth: Maximum depth (-1 for unlimited)

    Returns:
        List of entities in subgraph
    """
    if depth == -1:
        depth = 9999

    visited = {entity.id}
    current_level = [entity]
    all_entities = [entity]

    for _ in range(depth):
        if not current_level:
            break

        next_level = []

        for e in current_level:
            # Get outgoing dependencies (this entity depends on others)
            outgoing = db(
                (db.dependencies.source_type == "entity")
                & (db.dependencies.source_id == e.id)
            ).select()
            for dep in outgoing:
                if dep.target_id not in visited:
                    visited.add(dep.target_id)
                    target = db.entities[dep.target_id]
                    if target:
                        next_level.append(target)
                        all_entities.append(target)

            # Get incoming dependencies (others depend on this entity)
            incoming = db(
                (db.dependencies.target_type == "entity")
                & (db.dependencies.target_id == e.id)
            ).select()
            for dep in incoming:
                if dep.source_id not in visited:
                    visited.add(dep.source_id)
                    source = db.entities[dep.source_id]
                    if source:
                        next_level.append(source)
                        all_entities.append(source)

        current_level = next_level

    return all_entities


def _count_by_type(entities) -> Dict[str, int]:
    """Count entities by type."""
    counts = {}
    for entity in entities:
        type_val = entity.entity_type
        counts[type_val] = counts.get(type_val, 0) + 1
    return counts


def _get_node_style(entity_type: str) -> tuple:
    """Get vis.js node styling based on entity type."""
    styles = {
        "datacenter": ("box", "#3498db"),
        "vpc": ("box", "#2980b9"),
        "subnet": ("ellipse", "#1abc9c"),
        "compute": ("circle", "#e74c3c"),
        "network": ("diamond", "#f39c12"),
        "user": ("triangle", "#9b59b6"),
        "security_issue": ("star", "#e67e22"),
    }
    return styles.get(entity_type, ("dot", "#95a5a6"))


def _get_edge_style(dependency_type: str) -> tuple:
    """Get vis.js edge styling based on dependency type."""
    styles = {
        "depends_on": ("#34495e", False),  # solid
        "related_to": ("#95a5a6", True),  # dashed
        "part_of": ("#2ecc71", False),  # solid green
    }
    return styles.get(dependency_type, ("#7f8c8d", False))
