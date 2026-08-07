"""Issues management API endpoints for Elder enterprise features using PyDAL with async/await."""

# flake8: noqa: E501


import asyncio
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional, Tuple

from penguin_libs.pydantic import RequestModel
from pydantic import Field
from quart import Blueprint, current_app, g, jsonify, request

from apps.api.auth.decorators import login_required, require_scope
from apps.api.models.dataclasses import (
    IssueCommentDTO,
    IssueDTO,
    IssueLabelDTO,
    PaginatedResponse,
    from_pydal_row,
    from_pydal_rows,
)
from apps.api.modules.helpdesk.common import identity_in_tenant
from apps.api.modules.issues.routes.common import _tenant_id, get_tenant_scoped_issue
from apps.api.services.webhooks.assignment import (
    AssignmentEvent,
    send_issue_assigned_webhooks,
)
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.pydal_helpers import PaginationParams
from apps.api.utils.quart_validation import validated_request
from shared.webhooks import send_issue_created_webhooks

bp = Blueprint("issues", __name__)

logger = logging.getLogger(__name__)


def _org_unit_in_tenant(db: Any, org_unit_id: Optional[int], tenant_id: int) -> bool:
    """Return True if org_unit_id is unset or belongs to tenant_id.

    Org-unit counterpart to identity_in_tenant: guards against cross-tenant
    IDOR when an issue's assignee_id targets organizations.id
    (assignee_type="org_unit"). A None id is treated as valid.
    """
    if org_unit_id is None:
        return True
    return (
        db(
            (db.organizations.id == org_unit_id)
            & (db.organizations.tenant_id == tenant_id)
        )
        .select()
        .first()
        is not None
    )


def _resolve_assignee_type(
    db: Any,
    tenant_id: int,
    assignee_id: Optional[int],
    assignee_type: Optional[str],
) -> Tuple[Optional[str], bool]:
    """Resolve the polymorphic assignee_type and validate assignee_id is in tenant.

    Returns (resolved_type, ok). resolved_type is None when assignee_id is
    None (no assignee being set/changed) and ok is always True in that case.
    Defaults resolved_type to "identity" for back-compat when assignee_id is
    given without an explicit assignee_type. ok=False means the target row
    doesn't exist in the caller's tenant (cross-tenant IDOR guard).
    """
    if assignee_id is None:
        return None, True
    resolved = assignee_type or "identity"
    if resolved == "identity":
        return resolved, identity_in_tenant(db, assignee_id, tenant_id)
    return resolved, _org_unit_in_tenant(db, assignee_id, tenant_id)


# ============================================================================
# Request Models
# ============================================================================


class CreateIssueRequest(RequestModel):
    """Request to create a new issue."""

    title: str = Field(..., min_length=1, max_length=255, description="Issue title")
    organization_id: int = Field(..., ge=1, description="Organization ID")
    description: Optional[str] = Field(default=None, description="Issue description")
    status: str = Field(default="open", description="Issue status")
    priority: str = Field(default="medium", description="Priority level")
    issue_type: str = Field(default="other", description="Issue type")
    assignee_id: Optional[int] = Field(default=None, ge=1, description="Assignee ID")
    assignee_type: Optional[Literal["identity", "org_unit"]] = Field(
        default=None,
        description="Disambiguates assignee_id: identities.id or organizations.id",
    )
    is_incident: int = Field(default=0, description="Is incident flag")
    channel: Optional[str] = Field(
        default=None,
        max_length=20,
        description="Support channel the issue was raised through (e.g. email, chat, phone)",
    )
    category: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Support category/topic (e.g. billing, technical)",
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default=None, description="Universal free-form JSON metadata bag"
    )
    parent_issue_id: Optional[int] = Field(
        default=None, ge=1, description="Parent issue id for sub-tasks"
    )


class UpdateIssueRequest(RequestModel):
    """Request to update an existing issue."""

    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = Field(default=None)
    status: Optional[str] = Field(default=None)
    priority: Optional[str] = Field(default=None)
    issue_type: Optional[str] = Field(default=None)
    assignee_id: Optional[int] = Field(default=None, ge=1)
    assignee_type: Optional[Literal["identity", "org_unit"]] = Field(
        default=None,
        description="Disambiguates assignee_id: identities.id or organizations.id",
    )
    organization_id: Optional[int] = Field(default=None, ge=1)
    is_incident: Optional[int] = Field(default=None)
    channel: Optional[str] = Field(default=None, max_length=20)
    category: Optional[str] = Field(default=None, max_length=100)
    metadata: Optional[Dict[str, Any]] = Field(
        default=None, description="Universal free-form JSON metadata bag"
    )
    parent_issue_id: Optional[int] = Field(default=None, ge=1)


class CreateIssueCommentRequest(RequestModel):
    """Request to create an issue comment."""

    content: str = Field(..., min_length=1, description="Comment content")


class CreateIssueLabelRequest(RequestModel):
    """Request to create an issue label."""

    name: str = Field(..., min_length=1, max_length=255, description="Label name")
    color: str = Field(..., description="Label color (hex)")
    description: Optional[str] = Field(default=None, description="Label description")


class AddIssueLabelRequest(RequestModel):
    """Request to add a label to an issue."""

    label_id: int = Field(..., ge=1, description="Label ID")


class CreateIssueEntityLinkRequest(RequestModel):
    """Request to link an entity to an issue."""

    entity_id: int = Field(..., ge=1, description="Entity ID")


class LinkIssueToProjectRequest(RequestModel):
    """Request to link an issue to a project."""

    project_id: int = Field(..., ge=1, description="Project ID")


class LinkIssueToMilestoneRequest(RequestModel):
    """Request to link an issue to a milestone."""

    milestone_id: int = Field(..., ge=1, description="Milestone ID")


@bp.route("", methods=["GET"])
@login_required
@require_scope("issues:read")
async def list_issues():
    """
    List issues with optional filtering.

    Query Parameters:
        - organization_id: Filter by organization
        - status: Filter by status (open/in_progress/closed/resolved)
        - priority: Filter by priority (low/medium/high/critical)
        - assignee_id: Filter by assignee
        - reporter_id: Filter by creator
        - page: Page number (default: 1)
        - per_page: Items per page (default: 50)

    Returns:
        200: List of issues with pagination
        400: Invalid parameters
        403: License required

    Example:
        GET /api/v1/issues?organization_id=1&status=open
    """
    db = current_app.db

    tenant_id = _tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not found"}), 403

    # Get pagination params
    pagination = PaginationParams.from_request()

    # Build query
    def get_issues():
        query = db.issues.tenant_id == tenant_id

        # Apply filters
        # Note: organization_id is not a column in issues table (removed)
        # if request.args.get("organization_id"):
        #     org_id = request.args.get("organization_id", type=int)
        #     query &= db.issues.organization_id == org_id

        if request.args.get("status"):
            query &= db.issues.status == request.args.get("status")

        if request.args.get("priority"):
            query &= db.issues.priority == request.args.get("priority")

        if request.args.get("assignee_id"):
            assignee_id = request.args.get("assignee_id", type=int)
            query &= db.issues.assignee_id == assignee_id

        if request.args.get("reporter_id"):
            reporter_id = request.args.get("reporter_id", type=int)
            query &= db.issues.reporter_id == reporter_id

        # Get count and rows
        total = db(query).count()
        rows = db(query).select(
            orderby=~db.issues.created_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )

        return total, rows

    total, rows = await run_in_threadpool(get_issues)

    # Calculate total pages
    pages = pagination.calculate_pages(total)

    # Convert to DTOs
    items = from_pydal_rows(rows, IssueDTO)

    # Create paginated response
    response = PaginatedResponse(
        items=[asdict(item) for item in items],
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
        pages=pages,
    )

    return jsonify(asdict(response)), 200


@bp.route("", methods=["POST"])
@login_required
@require_scope("issues:write")
@validated_request(body_model=CreateIssueRequest)
async def create_issue(body: CreateIssueRequest):
    """
    Create a new issue.

    Requires viewer role on the resource.

    Request Body:
        {
            "title": "Server not responding",
            "description": "The web server is returning 500 errors",
            "priority": "high",
            "assignee_id": 5,
            "organization_id": 1
        }

    Returns:
        201: Issue created
        400: Invalid request
        403: License required or insufficient permissions

    Example:
        POST /api/v1/issues
    """
    db = current_app.db

    tenant_id = _tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not found"}), 403

    # Get organization to derive tenant_id
    def get_org():
        return db.organizations[body.organization_id]

    org = await run_in_threadpool(get_org)
    if not org:
        return jsonify({"error": "Organization not found"}), 404
    if not org.tenant_id:
        return jsonify({"error": "Organization must have a tenant"}), 400
    # Cross-tenant IDOR guard: the org must belong to the caller's own
    # tenant, not merely exist. Treat a foreign-tenant org as not-found
    # (matches documents.py:91, streams.py:62,108) rather than leaking its
    # existence via a distinct error.
    if org.tenant_id != tenant_id:
        return jsonify({"error": "Organization not found"}), 404

    # Resolve + validate the polymorphic assignee (IDOR guard: target must be
    # in the caller's tenant) before persisting.
    if body.assignee_id is not None:
        assignee_type_resolved, assignee_ok = await run_in_threadpool(
            lambda: _resolve_assignee_type(
                db, tenant_id, body.assignee_id, body.assignee_type
            )
        )
        if not assignee_ok:
            return jsonify({"error": "Assignee not found in tenant"}), 404
    elif body.assignee_type is not None:
        return jsonify({"error": "assignee_type requires assignee_id"}), 400
    else:
        assignee_type_resolved = None

    # Capture current_user before thread pool (Flask g doesn't propagate to threads)
    current_user_id = g.current_user.id

    def create():
        # Create issue
        now = datetime.now(timezone.utc)
        issue_id = db.issues.insert(
            title=body.title,
            description=body.description,
            status=(body.status or "open").upper(),
            priority=(body.priority or "medium").upper(),
            issue_type=(body.issue_type or "other").upper(),
            reporter_id=current_user_id,
            assignee_id=body.assignee_id,
            assignee_type=assignee_type_resolved,
            resource_type="organization",
            resource_id=body.organization_id,
            is_incident=body.is_incident,
            channel=body.channel,
            category=body.category,
            metadata=body.metadata,
            parent_issue_id=body.parent_issue_id,
            tenant_id=tenant_id,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        # Explicitly select the row to get all fields
        return db(db.issues.id == issue_id).select().first()

    issue = await run_in_threadpool(create)

    # Send issue created webhooks asynchronously (fire and forget)
    if issue.resource_id and issue.resource_type == "organization":
        asyncio.create_task(
            send_issue_created_webhooks(
                db=db,
                issue_id=issue.id,
                issue_title=issue.title,
                issue_type=issue.issue_type,
                is_incident=issue.is_incident if hasattr(issue, "is_incident") else 0,
                organization_id=issue.resource_id,
                web_url_base=current_app.config.get("WEB_URL", "http://localhost:3000"),
            )
        )

    # Send issue.assigned webhooks asynchronously (fire and forget) when the
    # issue was created with an assignee already set. This must never delay
    # or fail issue creation. send_issue_assigned_webhooks itself is
    # fire-and-forget-safe internally, but building AssignmentEvent and
    # scheduling the task both happen eagerly on the request path (before
    # asyncio.create_task hands off), so both are guarded here too:
    # `getattr` covers issues.village_id being absent from the physical
    # table on DBs built by `alembic upgrade head` (no migration adds it,
    # only the ORM model declares it via VillageIDMixin — PyDAL raises
    # AttributeError for a physically-missing column, not None), and the
    # try/except is belt-and-suspenders against any other unexpected
    # failure in event construction or scheduling.
    if issue.assignee_id is not None:
        try:
            asyncio.create_task(
                send_issue_assigned_webhooks(
                    db,
                    AssignmentEvent(
                        issue_id=issue.id,
                        village_id=getattr(issue, "village_id", None),
                        issue_type=issue.issue_type,
                        status=issue.status,
                        assignee_type=issue.assignee_type,
                        assignee_id=issue.assignee_id,
                        tenant_id=tenant_id,
                        actor_id=current_user_id,
                    ),
                )
            )
        except (
            Exception
        ) as exc:  # pragma: no cover - defensive; must never fail create_issue
            logger.warning(
                "issue_assigned_webhook_schedule_failed",
                extra={"issue_id": issue.id, "error": str(exc)[:200]},
            )

    issue_dto = from_pydal_row(issue, IssueDTO)
    return jsonify(asdict(issue_dto)), 201


@bp.route("/<int:id>", methods=["GET"])
@login_required
@require_scope("issues:read")
async def get_issue(id: int):
    """
    Get a single issue by ID.

    Path Parameters:
        - id: Issue ID

    Returns:
        200: Issue details
        403: License required
        404: Issue not found

    Example:
        GET /api/v1/issues/1
    """
    db = current_app.db

    tenant_id = _tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not found"}), 403

    issue = await run_in_threadpool(
        lambda: db((db.issues.id == id) & (db.issues.tenant_id == tenant_id))
        .select()
        .first()
    )

    if not issue:
        return jsonify({"error": "Issue not found"}), 404

    issue_dto = from_pydal_row(issue, IssueDTO)
    return jsonify(asdict(issue_dto)), 200


@bp.route("/<int:id>", methods=["PATCH"])
@login_required
@require_scope("issues:write")
@validated_request(body_model=UpdateIssueRequest)
async def update_issue(id: int, body: UpdateIssueRequest):
    """
    Update an issue.

    Requires operator role to close issue.
    Requires maintainer role to edit other fields.

    Path Parameters:
        - id: Issue ID

    Request Body:
        {
            "title": "Updated title",
            "description": "Updated description",
            "status": "closed",
            "priority": "critical",
            "assignee_id": 10
        }

    Returns:
        200: Issue updated
        400: Invalid request
        403: License required or insufficient permissions
        404: Issue not found

    Example:
        PATCH /api/v1/issues/1
    """
    db = current_app.db

    tenant_id = _tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not found"}), 403

    # If organization is being changed, validate it exists and belongs to
    # the caller's own tenant before persisting.
    if body.organization_id:

        def get_org():
            return db.organizations[body.organization_id]

        org = await run_in_threadpool(get_org)
        if not org:
            return jsonify({"error": "Organization not found"}), 404
        if not org.tenant_id:
            return jsonify({"error": "Organization must have a tenant"}), 400
        # Cross-tenant IDOR guard: treat a foreign-tenant org as not-found
        # (matches documents.py:91, streams.py:62,108).
        if org.tenant_id != tenant_id:
            return jsonify({"error": "Organization not found"}), 404

    # Resolve + validate the polymorphic assignee (IDOR guard: target must be
    # in the caller's tenant) before persisting.
    if body.assignee_id is not None:
        assignee_type_resolved, assignee_ok = await run_in_threadpool(
            lambda: _resolve_assignee_type(
                db, tenant_id, body.assignee_id, body.assignee_type
            )
        )
        if not assignee_ok:
            return jsonify({"error": "Assignee not found in tenant"}), 404
    elif body.assignee_type is not None:
        return jsonify({"error": "assignee_type requires assignee_id"}), 400
    else:
        assignee_type_resolved = None

    def update():
        # Check if issue exists and belongs to caller's tenant
        issue = (
            db((db.issues.id == id) & (db.issues.tenant_id == tenant_id))
            .select()
            .first()
        )
        if not issue:
            return None, "Issue not found", 404

        # Build update fields
        update_fields = {}

        if body.title is not None:
            update_fields["title"] = body.title
        if body.description is not None:
            update_fields["description"] = body.description
        if body.status is not None:
            update_fields["status"] = body.status.upper()
            # Set closed_at if closing
            if body.status.upper() in ("CLOSED", "RESOLVED"):
                update_fields["closed_at"] = datetime.now(timezone.utc)
        if body.priority is not None:
            update_fields["priority"] = body.priority.upper()
        if body.assignee_id is not None:
            update_fields["assignee_id"] = body.assignee_id
            update_fields["assignee_type"] = assignee_type_resolved
        if body.organization_id is not None:
            update_fields["resource_id"] = body.organization_id
            update_fields["resource_type"] = "organization"
        if body.is_incident is not None:
            update_fields["is_incident"] = body.is_incident
        if body.channel is not None:
            update_fields["channel"] = body.channel
        if body.category is not None:
            update_fields["category"] = body.category
        if body.metadata is not None:
            update_fields["metadata"] = body.metadata
        if body.parent_issue_id is not None:
            update_fields["parent_issue_id"] = body.parent_issue_id

        # Update issue (re-scoped to tenant for defense-in-depth)
        db((db.issues.id == id) & (db.issues.tenant_id == tenant_id)).update(
            **update_fields
        )
        db.commit()

        return get_tenant_scoped_issue(db, id, tenant_id), None, None

    result, error, status = await run_in_threadpool(update)

    if error:
        return jsonify({"error": error}), status

    issue_dto = from_pydal_row(result, IssueDTO)
    return jsonify(asdict(issue_dto)), 200


@bp.route("/<int:id>", methods=["DELETE"])
@login_required
@require_scope("issues:write")
async def delete_issue(id: int):
    """
    Delete an issue.

    Requires maintainer role on the resource.

    Path Parameters:
        - id: Issue ID

    Returns:
        204: Issue deleted
        403: License required or insufficient permissions
        404: Issue not found

    Example:
        DELETE /api/v1/issues/1
    """
    db = current_app.db

    tenant_id = _tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not found"}), 403

    def delete():
        issue = (
            db((db.issues.id == id) & (db.issues.tenant_id == tenant_id))
            .select()
            .first()
        )
        if not issue:
            return None, "Issue not found", 404

        # Delete issue (cascade deletes comments, labels, links); re-scoped
        # to tenant for defense-in-depth.
        db((db.issues.id == id) & (db.issues.tenant_id == tenant_id)).delete()
        db.commit()

        return True, None, None

    result, error, status = await run_in_threadpool(delete)

    if error:
        return jsonify({"error": error}), status

    return "", 204


# ============================================================================
# Issue Comments Endpoints
# ============================================================================


@bp.route("/<int:id>/comments", methods=["GET"])
@login_required
@require_scope("issues:read")
async def list_issue_comments(id: int):
    """
    List comments for an issue.

    Path Parameters:
        - id: Issue ID

    Returns:
        200: List of comments
        403: License required
        404: Issue not found

    Example:
        GET /api/v1/issues/1/comments
    """
    db = current_app.db

    tenant_id = _tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not found"}), 403

    def get_comments():
        # Verify issue exists and belongs to caller's tenant
        issue = get_tenant_scoped_issue(db, id, tenant_id)
        if not issue:
            return None, "Issue not found", 404

        # Get comments
        comments = db(db.issue_comments.issue_id == id).select(
            orderby=db.issue_comments.created_at
        )

        return comments, None, None

    result, error, status = await run_in_threadpool(get_comments)

    if error:
        return jsonify({"error": error}), status

    # Convert to DTOs
    items = from_pydal_rows(result, IssueCommentDTO)

    return (
        jsonify({"items": [asdict(item) for item in items], "total": len(items)}),
        200,
    )


@bp.route("/<int:id>/comments", methods=["POST"])
@login_required
@require_scope("issues:write")
@validated_request(body_model=CreateIssueCommentRequest)
async def create_issue_comment(id: int, body: CreateIssueCommentRequest):
    """
    Add a comment to an issue.

    Path Parameters:
        - id: Issue ID

    Request Body:
        {
            "content": "This has been fixed"
        }

    Returns:
        201: Comment created
        400: Invalid request
        403: License required
        404: Issue not found

    Example:
        POST /api/v1/issues/1/comments
    """
    db = current_app.db

    tenant_id = _tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not found"}), 403

    def create():
        from shared.utils.village_id import generate_village_id

        # Verify issue exists and belongs to caller's tenant
        issue = get_tenant_scoped_issue(db, id, tenant_id)
        if not issue:
            return None, "Issue not found", 404

        # Create comment
        now = datetime.now(timezone.utc)
        redis_client = current_app.redis_client
        village_id = generate_village_id(issue.tenant_id, redis_client)
        comment_id = db.issue_comments.insert(
            issue_id=id,
            author_id=g.current_user.id,
            content=body.content,
            tenant_id=issue.tenant_id,
            village_id=village_id,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        return db.issue_comments[comment_id], None, None

    result, error, status = await run_in_threadpool(create)

    if error:
        return jsonify({"error": error}), status

    comment_dto = from_pydal_row(result, IssueCommentDTO)
    return jsonify(asdict(comment_dto)), 201


@bp.route("/<int:id>/comments/<int:comment_id>", methods=["DELETE"])
@login_required
@require_scope("issues:write")
async def delete_issue_comment(id: int, comment_id: int):
    """
    Delete a comment from an issue.

    Path Parameters:
        - id: Issue ID
        - comment_id: Comment ID

    Returns:
        204: Comment deleted
        403: License required or not comment author
        404: Issue or comment not found

    Example:
        DELETE /api/v1/issues/1/comments/5
    """
    db = current_app.db

    tenant_id = _tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not found"}), 403

    def delete():
        # Verify issue exists and belongs to caller's tenant
        issue = get_tenant_scoped_issue(db, id, tenant_id)
        if not issue:
            return None, "Issue not found", 404

        # Get comment
        comment = db.issue_comments[comment_id]
        if not comment or comment.issue_id != id:
            return None, "Comment not found", 404

        # Check if user is author or superuser
        if comment.author_id != g.current_user.id and not g.current_user.is_superuser:
            return None, "Only comment author can delete comments", 403

        # Delete comment
        db(db.issue_comments.id == comment_id).delete()
        db.commit()

        return True, None, None

    result, error, status = await run_in_threadpool(delete)

    if error:
        return jsonify({"error": error}), status

    return "", 204


# ============================================================================
# Issue Labels Endpoints
# ============================================================================


@bp.route("/labels", methods=["GET"])
@login_required
@require_scope("issues:read")
async def list_issue_labels():
    """
    List all available issue labels.

    Returns:
        200: List of labels
        403: License required

    Example:
        GET /api/v1/issues/labels
    """
    db = current_app.db

    labels = await run_in_threadpool(
        lambda: db(db.issue_labels.id > 0).select(orderby=db.issue_labels.name)
    )

    # Convert to DTOs
    items = from_pydal_rows(labels, IssueLabelDTO)

    return (
        jsonify({"items": [asdict(item) for item in items], "total": len(items)}),
        200,
    )


@bp.route("/labels", methods=["POST"])
@login_required
@require_scope("issues:write")
@validated_request(body_model=CreateIssueLabelRequest)
async def create_issue_label(body: CreateIssueLabelRequest):
    """
    Create a new issue label.

    Request Body:
        {
            "name": "security",
            "color": "#ff0000",
            "description": "Security related issues"
        }

    Returns:
        201: Label created
        400: Invalid request
        403: License required

    Example:
        POST /api/v1/issues/labels
    """
    db = current_app.db

    def create():
        # Check if label exists
        existing = db(db.issue_labels.name == body.name).select().first()
        if existing:
            return None, "Label already exists", 409

        # Create label
        now = datetime.now(timezone.utc)
        label_id = db.issue_labels.insert(
            name=body.name,
            color=body.color,
            description=body.description,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        return db.issue_labels[label_id], None, None

    result, error, status = await run_in_threadpool(create)

    if error:
        return jsonify({"error": error}), status

    label_dto = from_pydal_row(result, IssueLabelDTO)
    return jsonify(asdict(label_dto)), 201


@bp.route("/<int:id>/labels", methods=["GET"])
@login_required
@require_scope("issues:read")
async def list_issue_labels_for_issue(id: int):
    """
    List all labels assigned to a specific issue.

    Path Parameters:
        - id: Issue ID

    Returns:
        200: List of labels assigned to the issue
        403: License required
        404: Issue not found

    Example:
        GET /api/v1/issues/1/labels
    """
    db = current_app.db

    tenant_id = _tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not found"}), 403

    def get_labels():
        # Verify issue exists and belongs to caller's tenant
        issue = get_tenant_scoped_issue(db, id, tenant_id)
        if not issue:
            return None, "Issue not found", 404

        rows = db(
            (db.issue_label_assignments.issue_id == id)
            & (db.issue_label_assignments.label_id == db.issue_labels.id)
        ).select(db.issue_labels.ALL)

        items = from_pydal_rows(rows, IssueLabelDTO)
        return items, None, None

    result, error, status = await run_in_threadpool(get_labels)

    if error:
        return jsonify({"error": error}), status

    return (
        jsonify({"items": [asdict(item) for item in result], "total": len(result)}),
        200,
    )


@bp.route("/<int:id>/labels", methods=["POST"])
@login_required
@require_scope("issues:write")
@validated_request(body_model=AddIssueLabelRequest)
async def add_issue_label(id: int, body: AddIssueLabelRequest):
    """
    Add a label to an issue.

    Path Parameters:
        - id: Issue ID

    Request Body:
        {
            "label_id": 3
        }

    Returns:
        200: Label added
        400: Invalid request
        403: License required
        404: Issue or label not found
        409: Label already assigned

    Example:
        POST /api/v1/issues/1/labels
    """
    db = current_app.db

    label_id = body.label_id

    tenant_id = _tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not found"}), 403

    def add_label():
        # Verify issue exists and belongs to caller's tenant
        issue = get_tenant_scoped_issue(db, id, tenant_id)
        if not issue:
            return None, "Issue not found", 404

        # Verify label exists
        label = db.issue_labels[label_id]
        if not label:
            return None, "Label not found", 404

        # Check if already assigned
        existing = (
            db(
                (db.issue_label_assignments.issue_id == id)
                & (db.issue_label_assignments.label_id == label_id)
            )
            .select()
            .first()
        )

        if existing:
            return None, "Label already assigned to this issue", 409

        # Add label assignment
        db.issue_label_assignments.insert(
            issue_id=id,
            label_id=label_id,
        )
        db.commit()

        return {"message": "Label added successfully"}, None, None

    result, error, status = await run_in_threadpool(add_label)

    if error:
        return jsonify({"error": error}), status

    return jsonify(result), 200


@bp.route("/<int:id>/labels/<int:label_id>", methods=["DELETE"])
@login_required
@require_scope("issues:write")
async def remove_issue_label(id: int, label_id: int):
    """
    Remove a label from an issue.

    Path Parameters:
        - id: Issue ID
        - label_id: Label ID

    Returns:
        204: Label removed
        403: License required
        404: Issue, label, or assignment not found

    Example:
        DELETE /api/v1/issues/1/labels/3
    """
    db = current_app.db

    tenant_id = _tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not found"}), 403

    def remove_label():
        # Verify issue exists and belongs to caller's tenant
        issue = get_tenant_scoped_issue(db, id, tenant_id)
        if not issue:
            return None, "Issue not found", 404

        # Remove label assignment
        deleted = db(
            (db.issue_label_assignments.issue_id == id)
            & (db.issue_label_assignments.label_id == label_id)
        ).delete()

        db.commit()

        if deleted == 0:
            return None, "Label not assigned to this issue", 404

        return True, None, None

    result, error, status = await run_in_threadpool(remove_label)

    if error:
        return jsonify({"error": error}), status

    return "", 204


# ============================================================================
# Issue Entity Links Endpoints
# ============================================================================


@bp.route("/<int:id>/links", methods=["GET"])
@login_required
@require_scope("issues:read")
async def list_issue_entity_links(id: int):
    """
    List entity links for an issue.

    Path Parameters:
        - id: Issue ID

    Returns:
        200: List of entity links
        403: License required
        404: Issue not found

    Example:
        GET /api/v1/issues/1/links
    """
    db = current_app.db

    tenant_id = _tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not found"}), 403

    def get_links():
        # Verify issue exists and belongs to caller's tenant
        issue = get_tenant_scoped_issue(db, id, tenant_id)
        if not issue:
            return None, "Issue not found", 404

        # Get links
        links = db(db.issue_entity_links.issue_id == id).select()

        # Convert to list of dicts with entity info
        link_list = []
        for link in links:
            entity = db.entities[link.entity_id]
            link_list.append(
                {
                    "id": link.id,
                    "entity_id": link.entity_id,
                    "entity_name": entity.name if entity else None,
                    "created_at": link.created_at,
                }
            )

        return link_list, None, None

    result, error, status = await run_in_threadpool(get_links)

    if error:
        return jsonify({"error": error}), status

    return jsonify({"items": result, "total": len(result)}), 200


@bp.route("/<int:id>/links", methods=["POST"])
@login_required
@require_scope("issues:write")
@validated_request(body_model=CreateIssueEntityLinkRequest)
async def create_issue_entity_link(id: int, body: CreateIssueEntityLinkRequest):
    """
    Link an entity to an issue.

    Path Parameters:
        - id: Issue ID

    Request Body:
        {
            "entity_id": 42
        }

    Returns:
        201: Link created
        400: Invalid request
        403: License required
        404: Issue or entity not found
        409: Link already exists

    Example:
        POST /api/v1/issues/1/links
    """
    db = current_app.db

    entity_id = body.entity_id

    tenant_id = _tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not found"}), 403

    def create_link():
        # Verify issue exists and belongs to caller's tenant
        issue = get_tenant_scoped_issue(db, id, tenant_id)
        if not issue:
            return None, "Issue not found", 404

        # Verify entity exists
        entity = db.entities[entity_id]
        if not entity:
            return None, "Entity not found", 404

        # Check if link already exists
        existing = (
            db(
                (db.issue_entity_links.issue_id == id)
                & (db.issue_entity_links.entity_id == entity_id)
            )
            .select()
            .first()
        )

        if existing:
            return None, "Entity already linked to this issue", 409

        # Create link
        link_id = db.issue_entity_links.insert(
            issue_id=id,
            entity_id=entity_id,
        )
        db.commit()

        link = db.issue_entity_links[link_id]

        return (
            {
                "id": link.id,
                "entity_id": link.entity_id,
                "entity_name": entity.name,
                "created_at": link.created_at,
            },
            None,
            None,
        )

    result, error, status = await run_in_threadpool(create_link)

    if error:
        return jsonify({"error": error}), status

    return jsonify(result), 201


@bp.route("/<int:id>/links/<int:link_id>", methods=["DELETE"])
@login_required
@require_scope("issues:write")
async def delete_issue_entity_link(id: int, link_id: int):
    """
    Remove an entity link from an issue.

    Path Parameters:
        - id: Issue ID
        - link_id: Link ID

    Returns:
        204: Link removed
        403: License required
        404: Issue or link not found

    Example:
        DELETE /api/v1/issues/1/links/10
    """
    db = current_app.db

    tenant_id = _tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not found"}), 403

    def delete_link():
        # Verify issue exists and belongs to caller's tenant
        issue = get_tenant_scoped_issue(db, id, tenant_id)
        if not issue:
            return None, "Issue not found", 404

        # Get link
        link = db.issue_entity_links[link_id]
        if not link or link.issue_id != id:
            return None, "Link not found", 404

        # Delete link
        db(db.issue_entity_links.id == link_id).delete()
        db.commit()

        return True, None, None

    result, error, status = await run_in_threadpool(delete_link)

    if error:
        return jsonify({"error": error}), status

    return "", 204


@bp.route("/<int:id>/links/by-entity/<int:entity_id>", methods=["DELETE"])
@login_required
@require_scope("issues:write")
async def delete_issue_entity_link_by_entity(id: int, entity_id: int):
    """
    Remove an entity link from an issue by entity ID.

    Path Parameters:
        - id: Issue ID
        - entity_id: Entity ID

    Returns:
        204: Link removed
        403: License required
        404: Issue or link not found

    Example:
        DELETE /api/v1/issues/1/links/by-entity/42
    """
    db = current_app.db

    tenant_id = _tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not found"}), 403

    def delete_link():
        # Verify issue exists and belongs to caller's tenant
        issue = get_tenant_scoped_issue(db, id, tenant_id)
        if not issue:
            return None, "Issue not found", 404

        # Get link by issue_id + entity_id
        link = (
            db(
                (db.issue_entity_links.issue_id == id)
                & (db.issue_entity_links.entity_id == entity_id)
            )
            .select()
            .first()
        )
        if not link:
            return None, "Link not found", 404

        db(db.issue_entity_links.id == link.id).delete()
        db.commit()

        return True, None, None

    result, error, status = await run_in_threadpool(delete_link)

    if error:
        return jsonify({"error": error}), status

    return "", 204


# ==========================================
# Issue-Project Linking Endpoints
# ==========================================


@bp.route("/<int:id>/projects", methods=["POST"])
@login_required
@require_scope("issues:write")
@validated_request(body_model=LinkIssueToProjectRequest)
async def link_issue_to_project(id: int, body: LinkIssueToProjectRequest):
    """
    Link an issue to a project.

    Path Parameters:
        - id: Issue ID

    Request Body:
        - project_id: Project ID to link

    Returns:
        201: Link created
        400: Invalid request
        404: Issue or project not found

    Example:
        POST /api/v1/issues/1/projects
        {"project_id": 5}
    """
    db = current_app.db

    tenant_id = _tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not found"}), 403

    def create_link():
        # Verify issue exists and belongs to caller's tenant
        issue = get_tenant_scoped_issue(db, id, tenant_id)
        if not issue:
            return None, "Issue not found", 404

        # Verify project exists and belongs to caller's tenant
        project = (
            db(
                (db.projects.id == body.project_id)
                & (db.projects.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not project:
            return None, "Project not found", 404

        # Check if link already exists
        existing = (
            db(
                (db.issue_project_links.issue_id == id)
                & (db.issue_project_links.project_id == body.project_id)
            )
            .select()
            .first()
        )

        if existing:
            return None, "Issue is already linked to this project", 400

        # Create link
        link_id = db.issue_project_links.insert(issue_id=id, project_id=body.project_id)
        db.commit()

        link = db.issue_project_links[link_id]
        return link, None, None

    link, error, status = await run_in_threadpool(create_link)

    if error:
        return jsonify({"error": error}), status

    return (
        jsonify(
            {"id": link.id, "issue_id": link.issue_id, "project_id": link.project_id}
        ),
        201,
    )


@bp.route("/<int:id>/projects/<int:project_id>", methods=["DELETE"])
@login_required
@require_scope("issues:write")
async def unlink_issue_from_project(id: int, project_id: int):
    """
    Remove a project link from an issue.

    Path Parameters:
        - id: Issue ID
        - project_id: Project ID

    Returns:
        204: Link removed
        404: Issue or link not found

    Example:
        DELETE /api/v1/issues/1/projects/5
    """
    db = current_app.db

    tenant_id = _tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not found"}), 403

    def delete_link():
        # Verify issue exists and belongs to caller's tenant
        issue = get_tenant_scoped_issue(db, id, tenant_id)
        if not issue:
            return None, "Issue not found", 404

        # Find and delete link
        deleted = db(
            (db.issue_project_links.issue_id == id)
            & (db.issue_project_links.project_id == project_id)
        ).delete()

        if not deleted:
            return None, "Link not found", 404

        db.commit()
        return True, None, None

    result, error, status = await run_in_threadpool(delete_link)

    if error:
        return jsonify({"error": error}), status

    return "", 204


# ==========================================
# Issue-Milestone Linking Endpoints
# ==========================================


@bp.route("/<int:id>/milestones", methods=["POST"])
@login_required
@require_scope("issues:write")
@validated_request(body_model=LinkIssueToMilestoneRequest)
async def link_issue_to_milestone(id: int, body: LinkIssueToMilestoneRequest):
    """
    Link an issue to a milestone.

    Path Parameters:
        - id: Issue ID

    Request Body:
        - milestone_id: Milestone ID to link

    Returns:
        201: Link created
        400: Invalid request
        404: Issue or milestone not found

    Example:
        POST /api/v1/issues/1/milestones
        {"milestone_id": 3}
    """
    db = current_app.db

    tenant_id = _tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not found"}), 403

    def create_link():
        # Verify issue exists and belongs to caller's tenant
        issue = get_tenant_scoped_issue(db, id, tenant_id)
        if not issue:
            return None, "Issue not found", 404

        # Verify milestone exists and belongs to caller's tenant
        milestone = (
            db(
                (db.milestones.id == body.milestone_id)
                & (db.milestones.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not milestone:
            return None, "Milestone not found", 404

        # Check if link already exists
        existing = (
            db(
                (db.issue_milestone_links.issue_id == id)
                & (db.issue_milestone_links.milestone_id == body.milestone_id)
            )
            .select()
            .first()
        )

        if existing:
            return None, "Issue is already linked to this milestone", 400

        # Create link
        link_id = db.issue_milestone_links.insert(
            issue_id=id, milestone_id=body.milestone_id
        )
        db.commit()

        link = db.issue_milestone_links[link_id]
        return link, None, None

    link, error, status = await run_in_threadpool(create_link)

    if error:
        return jsonify({"error": error}), status

    return (
        jsonify(
            {
                "id": link.id,
                "issue_id": link.issue_id,
                "milestone_id": link.milestone_id,
            }
        ),
        201,
    )


@bp.route("/<int:id>/milestones/<int:milestone_id>", methods=["DELETE"])
@login_required
@require_scope("issues:write")
async def unlink_issue_from_milestone(id: int, milestone_id: int):
    """
    Remove a milestone link from an issue.

    Path Parameters:
        - id: Issue ID
        - milestone_id: Milestone ID

    Returns:
        204: Link removed
        404: Issue or link not found

    Example:
        DELETE /api/v1/issues/1/milestones/3
    """
    db = current_app.db

    tenant_id = _tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not found"}), 403

    def delete_link():
        # Verify issue exists and belongs to caller's tenant
        issue = get_tenant_scoped_issue(db, id, tenant_id)
        if not issue:
            return None, "Issue not found", 404

        # Find and delete link
        deleted = db(
            (db.issue_milestone_links.issue_id == id)
            & (db.issue_milestone_links.milestone_id == milestone_id)
        ).delete()

        if not deleted:
            return None, "Link not found", 404

        db.commit()
        return True, None, None

    result, error, status = await run_in_threadpool(delete_link)

    if error:
        return jsonify({"error": error}), status

    return "", 204
