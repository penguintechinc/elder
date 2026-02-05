"""Issue comments API endpoints with Pydantic validation."""

# flake8: noqa: E501


from dataclasses import asdict

from flask import Blueprint, current_app, g, jsonify
from models.dataclasses import IssueCommentDTO
from penguin_libs.pydantic import Description1000, RequestModel
from penguin_libs.pydantic.flask_integration import validated_request
from utils.async_utils import run_in_threadpool

from apps.api.auth.decorators import login_required
from apps.api.models.dataclasses import from_pydal_row, from_pydal_rows
from penguin_licensing.decorators import license_required

bp = Blueprint("comments", __name__)


class CreateCommentRequest(RequestModel):
    """Pydantic model for creating a comment."""

    content: Description1000


class UpdateCommentRequest(RequestModel):
    """Pydantic model for updating a comment."""

    content: Description1000


# ============================================================================
# Issue Comments Endpoints
# ============================================================================


@bp.route("/<int:id>/comments", methods=["GET"])
@login_required
@license_required("enterprise")
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

    def get_comments():
        # Verify issue exists
        issue = db.issues[id]
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
@license_required("enterprise")
@validated_request(body_model=CreateCommentRequest)
async def create_issue_comment(id: int, body: CreateCommentRequest):
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

    def create():
        # Verify issue exists
        issue = db.issues[id]
        if not issue:
            return None, "Issue not found", 404

        # Create comment
        comment_id = db.issue_comments.insert(
            issue_id=id,
            author_id=g.current_user.id,
            content=body.content,
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
@license_required("enterprise")
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

    def delete():
        # Verify issue exists
        issue = db.issues[id]
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


@bp.route("/<int:id>/comments/<int:comment_id>", methods=["PATCH"])
@login_required
@license_required("enterprise")
@validated_request(body_model=UpdateCommentRequest)
async def update_issue_comment(id: int, comment_id: int, body: UpdateCommentRequest):
    """
    Update a comment on an issue.

    Path Parameters:
        - id: Issue ID
        - comment_id: Comment ID

    Request Body:
        {
            "content": "Updated comment text"
        }

    Returns:
        200: Comment updated
        400: Invalid request
        403: License required or not comment author
        404: Issue or comment not found

    Example:
        PATCH /api/v1/issues/1/comments/5
    """
    db = current_app.db

    def update():
        # Verify issue exists
        issue = db.issues[id]
        if not issue:
            return None, "Issue not found", 404

        # Get comment
        comment = db.issue_comments[comment_id]
        if not comment or comment.issue_id != id:
            return None, "Comment not found", 404

        # Check if user is author or superuser
        if comment.author_id != g.current_user.id and not g.current_user.is_superuser:
            return None, "Only comment author can update comments", 403

        # Update comment
        db.issue_comments[comment_id] = dict(content=body.content)
        db.commit()

        return db.issue_comments[comment_id], None, None

    result, error, status = await run_in_threadpool(update)

    if error:
        return jsonify({"error": error}), status

    comment_dto = from_pydal_row(result, IssueCommentDTO)
    return jsonify(asdict(comment_dto)), 200
