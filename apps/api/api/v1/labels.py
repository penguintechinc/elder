"""Labels management API endpoints for Elder using PyDAL with async/await."""

# flake8: noqa: E501


from dataclasses import asdict
from typing import Optional

from flask import Blueprint, current_app, jsonify
from penguin_libs.pydantic import Description1000, Name255, RequestModel
from penguin_libs.pydantic.flask_integration import validated_request

from apps.api.auth.decorators import login_required
from apps.api.models.dataclasses import (
    IssueLabelDTO,
    PaginatedResponse,
    from_pydal_row,
    from_pydal_rows,
)
from apps.api.utils.async_utils import run_in_threadpool

bp = Blueprint("labels", __name__)


class ListLabelsQuery(RequestModel):
    """Query parameters for listing labels."""

    search: Optional[str] = None
    page: int = 1
    per_page: int = 50

    model_config = {
        "json_schema_extra": {
            "examples": [{"search": "bug", "page": 1, "per_page": 50}]
        }
    }


class CreateLabelRequest(RequestModel):
    """Request body for creating a label."""

    name: Name255
    description: Optional[Description1000] = None
    color: str = "#cccccc"


class UpdateLabelRequest(RequestModel):
    """Request body for updating a label."""

    name: Optional[Name255] = None
    description: Optional[Description1000] = None
    color: Optional[str] = None


@bp.route("", methods=["GET"])
@login_required
@validated_request(query_model=ListLabelsQuery)
async def list_labels(query: ListLabelsQuery):
    """
    List all labels with optional filtering.

    Query Parameters:
        - search: Search in name and description
        - page: Page number (default: 1)
        - per_page: Items per page (default: 50)

    Returns:
        200: List of labels with pagination
        400: Invalid parameters

    Example:
        GET /api/v1/labels?search=bug
    """
    db = current_app.db

    # Enforce max per_page limit
    per_page = min(query.per_page, 1000)

    # Build query
    def get_labels():
        query_obj = db.issue_labels.id > 0

        # Apply search filter
        if query.search:
            search_pattern = f"%{query.search}%"
            query_obj &= (db.issue_labels.name.ilike(search_pattern)) | (
                db.issue_labels.description.ilike(search_pattern)
            )

        # Calculate pagination
        offset = (query.page - 1) * per_page

        # Get count and rows
        total = db(query_obj).count()
        rows = db(query_obj).select(
            orderby=db.issue_labels.name, limitby=(offset, offset + per_page)
        )

        return total, rows

    total, rows = await run_in_threadpool(get_labels)

    # Calculate total pages
    pages = (total + per_page - 1) // per_page if total > 0 else 0

    # Convert to DTOs
    items = from_pydal_rows(rows, IssueLabelDTO)

    # Create paginated response
    response = PaginatedResponse(
        items=[asdict(item) for item in items],
        total=total,
        page=query.page,
        per_page=per_page,
        pages=pages,
    )

    return jsonify(asdict(response)), 200


@bp.route("", methods=["POST"])
@login_required
@validated_request(body_model=CreateLabelRequest)
async def create_label(body: CreateLabelRequest):
    """
    Create a new label.

    Request Body:
        {
            "name": "bug",
            "description": "Something isn't working",
            "color": "#d73a4a"
        }

    Returns:
        201: Label created
        400: Invalid request
        409: Label with this name already exists

    Example:
        POST /api/v1/labels
    """
    db = current_app.db

    def create():
        # Check if label already exists
        existing = db(db.issue_labels.name == body.name).select().first()
        if existing:
            return None

        # Create label
        label_id = db.issue_labels.insert(
            name=body.name,
            description=body.description,
            color=body.color,
        )
        db.commit()

        return db.issue_labels[label_id]

    label = await run_in_threadpool(create)

    if not label:
        return jsonify({"error": "Label with this name already exists"}), 409

    label_dto = from_pydal_row(label, IssueLabelDTO)
    return jsonify(asdict(label_dto)), 201


@bp.route("/<int:id>", methods=["GET"])
@login_required
async def get_label(id: int):
    """
    Get a single label by ID.

    Path Parameters:
        - id: Label ID

    Returns:
        200: Label details
        404: Label not found

    Example:
        GET /api/v1/labels/1
    """
    db = current_app.db

    label = await run_in_threadpool(lambda: db.issue_labels[id])

    if not label:
        return jsonify({"error": "Label not found"}), 404

    label_dto = from_pydal_row(label, IssueLabelDTO)
    return jsonify(asdict(label_dto)), 200


@bp.route("/<int:id>", methods=["PUT"])
@login_required
@validated_request(body_model=UpdateLabelRequest)
async def update_label(id: int, body: UpdateLabelRequest):
    """
    Update a label.

    Path Parameters:
        - id: Label ID

    Request Body:
        {
            "name": "critical-bug",
            "color": "#ff0000"
        }

    Returns:
        200: Label updated
        400: Invalid request
        404: Label not found
        409: Label with this name already exists

    Example:
        PUT /api/v1/labels/1
    """
    db = current_app.db

    def update():
        label = db.issue_labels[id]
        if not label:
            return None, False

        # Check if name is being changed to an existing one
        if body.name is not None and body.name != label.name:
            existing = db(db.issue_labels.name == body.name).select().first()
            if existing:
                return None, True

        # Update fields
        update_dict = {}
        if body.name is not None:
            update_dict["name"] = body.name
        if body.description is not None:
            update_dict["description"] = body.description
        if body.color is not None:
            update_dict["color"] = body.color

        if update_dict:
            db(db.issue_labels.id == id).update(**update_dict)
            db.commit()

        return db.issue_labels[id], False

    label, name_exists = await run_in_threadpool(update)

    if label is None and name_exists:
        return jsonify({"error": "Label with this name already exists"}), 409
    if label is None:
        return jsonify({"error": "Label not found"}), 404

    label_dto = from_pydal_row(label, IssueLabelDTO)
    return jsonify(asdict(label_dto)), 200


@bp.route("/<int:id>", methods=["DELETE"])
@login_required
async def delete_label(id: int):
    """
    Delete a label.

    Path Parameters:
        - id: Label ID

    Returns:
        204: Label deleted
        404: Label not found

    Example:
        DELETE /api/v1/labels/1
    """
    db = current_app.db

    def delete():
        label = db.issue_labels[id]
        if not label:
            return False

        del db.issue_labels[id]
        db.commit()
        return True

    success = await run_in_threadpool(delete)

    if not success:
        return jsonify({"error": "Label not found"}), 404

    return "", 204
