"""API utilities for Elder."""

# flake8: noqa: E501


import math
from typing import Any, Dict, List, Tuple

from flask import request
from marshmallow import ValidationError
from sqlalchemy.orm import Query


def paginate(
    query: Query, page: int = 1, per_page: int = 50
) -> Tuple[List, Dict[str, Any]]:
    """
    Paginate a SQLAlchemy query.

    Args:
        query: SQLAlchemy query to paginate
        page: Page number (1-indexed)
        per_page: Items per page

    Returns:
        Tuple of (items list, pagination metadata dict)
    """
    # Get total count
    total = query.count()

    # Calculate pages
    pages = math.ceil(total / per_page) if per_page > 0 else 0

    # Get items for current page
    offset = (page - 1) * per_page
    items = query.limit(per_page).offset(offset).all()

    # Build pagination metadata
    pagination = {
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
        "has_prev": page > 1,
        "has_next": page < pages,
    }

    return items, pagination


def get_pagination_params() -> Dict[str, int]:
    """
    Extract pagination parameters from request args.

    Returns:
        Dict with 'page' and 'per_page' keys
    """
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)

    # Enforce limits
    page = max(1, page)
    per_page = max(1, min(per_page, 1000))  # Max 1000 per page

    return {"page": page, "per_page": per_page}


def validate_request(schema_class: Any, data: Dict = None) -> Dict[str, Any]:
    """
    Validate request data with marshmallow schema.

    Args:
        schema_class: Marshmallow schema class
        data: Data to validate (defaults to request.json)

    Returns:
        Validated data dict

    Raises:
        ValidationError: If validation fails
    """
    if data is None:
        data = request.get_json() or {}

    schema = schema_class()
    return schema.load(data)


def make_error_response(
    message: str, status_code: int = 400, **kwargs
) -> Tuple[Dict, int]:
    """
    Create a standardized error response.

    Args:
        message: Error message
        status_code: HTTP status code
        **kwargs: Additional fields to include in response

    Returns:
        Tuple of (response dict, status code)
    """
    response = {
        "error": True,
        "message": message,
        "status_code": status_code,
    }
    response.update(kwargs)
    return response, status_code


def make_success_response(
    data: Any = None, message: str = None, status_code: int = 200
) -> Tuple[Dict, int]:
    """
    Create a standardized success response.

    Args:
        data: Response data
        message: Success message (optional)
        status_code: HTTP status code

    Returns:
        Tuple of (response dict, status code)
    """
    response = {
        "error": False,
        "status_code": status_code,
    }

    if message:
        response["message"] = message

    if data is not None:
        response["data"] = data

    return response, status_code


def apply_filters(query: Query, model: Any, filters: Dict[str, Any]) -> Query:
    """
    Apply filters to a SQLAlchemy query.

    Args:
        query: SQLAlchemy query
        model: SQLAlchemy model class
        filters: Dict of field names to filter values

    Returns:
        Filtered query
    """
    for field_name, value in filters.items():
        if value is not None and hasattr(model, field_name):
            field = getattr(model, field_name)

            # Handle different filter types
            if isinstance(value, str) and field_name == "name":
                # Partial match for name fields
                query = query.filter(field.ilike(f"%{value}%"))
            else:
                # Exact match for other fields
                query = query.filter(field == value)

    return query


def get_or_404(model: Any, id: int, error_message: str = None):
    """
    Get model instance by ID or return 404 error.

    Args:
        model: SQLAlchemy model class
        id: Instance ID
        error_message: Custom error message

    Returns:
        Model instance

    Raises:
        404 error if not found
    """
    from apps.api.database import db

    instance = db.session.get(model, id)

    if instance is None:
        message = error_message or f"{model.__name__} with id {id} not found"
        from flask import abort

        abort(404, description=message)

    return instance


def handle_validation_error(error: ValidationError) -> Tuple[Dict, int]:
    """
    Convert marshmallow ValidationError to API error response.

    Args:
        error: ValidationError instance

    Returns:
        Tuple of (error response dict, 400 status code)
    """
    return make_error_response(
        message="Validation error",
        status_code=400,
        validation_errors=error.messages,
    )
