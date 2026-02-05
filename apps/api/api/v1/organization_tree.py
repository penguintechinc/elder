"""API endpoints for recursive organization tree operations."""

# flake8: noqa: E501


from flask import Blueprint, current_app, jsonify

from apps.api.auth.decorators import login_required
from apps.api.utils.async_utils import run_in_threadpool

bp = Blueprint("organization_tree", __name__)


@bp.route("/organizations/<int:org_id>/tree-stats", methods=["GET"])
@login_required
async def get_organization_tree_stats(org_id: int):
    """
    Get recursive statistics for an organization and all its descendants.

    This endpoint recursively traverses the organization tree starting from the
    specified organization and aggregates counts for all resources across the
    entire tree.

    Path Parameters:
        - org_id: Root organization ID

    Returns:
        200: Aggregated statistics
        {
            "organization_id": 1,
            "organization_name": "Engineering",
            "total_sub_organizations": 15,
            "total_entities": 234,
            "total_issues": 42,
            "total_projects": 8,
            "total_milestones": 23,
            "active_issues": 28,
            "active_projects": 5,
            "organizations": [1, 2, 3, ...],  // All org IDs in tree
            "breakdown": {
                "by_entity_type": {"compute": 50, "database": 30, ...},
                "by_issue_priority": {"high": 10, "medium": 20, ...},
                "by_project_status": {"active": 5, "planning": 2, ...}
            }
        }
        404: Organization not found
    """
    db = current_app.db

    def get_recursive_stats():
        # Verify root organization exists
        root_org = db.organizations[org_id]
        if not root_org:
            return None, "Organization not found", 404

        # Recursively get all descendant organization IDs
        all_org_ids = _get_all_descendant_orgs(db, org_id)

        # Count sub-organizations (excluding root)
        total_sub_orgs = len(all_org_ids) - 1

        # Get all entities for these organizations
        entities = db(db.entities.organization_id.belongs(all_org_ids)).select()
        total_entities = len(entities)

        # Count by entity type
        entity_type_counts = {}
        for entity in entities:
            entity_type = entity.entity_type
            entity_type_counts[entity_type] = entity_type_counts.get(entity_type, 0) + 1

        # Get all issues for these organizations
        issues = db(db.issues.organization_id.belongs(all_org_ids)).select()
        total_issues = len(issues)

        # Count active issues (open or in_progress)
        active_issues = len([i for i in issues if i.status in ("open", "in_progress")])

        # Count by issue priority
        issue_priority_counts = {}
        for issue in issues:
            priority = issue.priority
            issue_priority_counts[priority] = issue_priority_counts.get(priority, 0) + 1

        # Get all projects for these organizations
        projects = db(db.projects.organization_id.belongs(all_org_ids)).select()
        total_projects = len(projects)

        # Count active projects
        active_projects = len([p for p in projects if p.status == "active"])

        # Count by project status
        project_status_counts = {}
        for project in projects:
            status = project.status
            project_status_counts[status] = project_status_counts.get(status, 0) + 1

        # Get all milestones for these organizations
        milestones = db(db.milestones.organization_id.belongs(all_org_ids)).select()
        total_milestones = len(milestones)

        return (
            {
                "organization_id": org_id,
                "organization_name": root_org.name,
                "total_sub_organizations": total_sub_orgs,
                "total_entities": total_entities,
                "total_issues": total_issues,
                "total_projects": total_projects,
                "total_milestones": total_milestones,
                "active_issues": active_issues,
                "active_projects": active_projects,
                "organizations": all_org_ids,
                "breakdown": {
                    "by_entity_type": entity_type_counts,
                    "by_issue_priority": issue_priority_counts,
                    "by_project_status": project_status_counts,
                },
            },
            None,
            None,
        )

    result, error, status = await run_in_threadpool(get_recursive_stats)

    if error:
        return jsonify({"error": error}), status

    return jsonify(result), 200


def _get_all_descendant_orgs(db, org_id: int, visited=None):
    """
    Recursively get all descendant organization IDs including the root.

    Args:
        db: Database connection
        org_id: Root organization ID
        visited: Set of already visited org IDs (to prevent cycles)

    Returns:
        List of all organization IDs in the tree (including root)
    """
    if visited is None:
        visited = set()

    # Prevent infinite loops
    if org_id in visited:
        return []

    visited.add(org_id)
    result = [org_id]

    # Get all direct children
    children = db(db.organizations.parent_id == org_id).select()

    # Recursively get descendants of each child
    for child in children:
        result.extend(_get_all_descendant_orgs(db, child.id, visited))

    return result
