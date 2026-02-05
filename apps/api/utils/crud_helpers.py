"""
Generic CRUD helper utilities for Elder API.

This module provides reusable CRUD operations with pagination, filtering,
and validation support. These helpers reduce code duplication across API endpoints.
"""

# flake8: noqa: E501


from dataclasses import asdict
from typing import Any, Callable, Dict, List, Optional

from flask import current_app, jsonify, request

from apps.api.utils.async_utils import run_in_threadpool

from .api_responses import ApiResponse
from .pydal_helpers import PaginationParams, get_by_id
from .validation_helpers import (
    validate_json_body,
    validate_required_fields,
    validate_resource_exists,
)


class CrudHelper:
    """
    Generic CRUD operations for PyDAL tables with async support.

    This class provides standardized CRUD methods that can be used across
    all API endpoints to ensure consistent behavior and reduce code duplication.
    """

    @staticmethod
    async def list_resources(
        table: Any,
        resource_type: str = "Resource",
        base_query: Optional[Any] = None,
        filter_fn: Optional[Callable[[Any], Any]] = None,
        orderby: Optional[Any] = None,
        transform_fn: Optional[Callable[[Any], Dict]] = None,
        default_per_page: int = 50,
        max_per_page: int = 1000,
    ) -> tuple[Any, int]:
        """
        Generic list endpoint with pagination and optional filtering.

        Args:
            table: PyDAL table object
            resource_type: Human-readable resource name (for responses)
            base_query: Base query to filter resources (e.g., table.id > 0)
            filter_fn: Optional function to apply additional filters from request args
            orderby: Ordering specification (default: ~table.created_at if available)
            transform_fn: Optional function to transform rows to dict (default: row.as_dict())
            default_per_page: Default items per page
            max_per_page: Maximum items per page

        Returns:
            Tuple of (jsonified response, status_code)

        Example:
            return await CrudHelper.list_resources(
                db.entities,
                resource_type="Entity",
                base_query=db.entities.organization_id == org_id,
                orderby=~db.entities.created_at
            )
        """
        db = current_app.db

        # Extract pagination parameters
        pagination = PaginationParams.from_request(default_per_page, max_per_page)

        # Build query
        if base_query is None:
            base_query = table.id > 0

        query = base_query

        # Apply additional filters if provided
        if filter_fn:
            query = filter_fn(query)

        # Default ordering if not specified
        if orderby is None and hasattr(table, "created_at"):
            orderby = ~table.created_at

        # Execute paginated query
        def execute_query():
            total = db(query).count()
            rows = db(query).select(
                orderby=orderby,
                limitby=(pagination.offset, pagination.offset + pagination.per_page),
            )
            return rows, total

        rows, total = await run_in_threadpool(execute_query)

        # Calculate pages
        pages = pagination.calculate_pages(total)

        # Transform rows to dictionaries
        if transform_fn:
            items = [transform_fn(row) for row in rows]
        else:
            items = [row.as_dict() for row in rows]

        # Build response using PaginatedResponse pattern
        from apps.api.models.dataclasses import PaginatedResponse

        response = PaginatedResponse(
            items=items,
            total=total,
            page=pagination.page,
            per_page=pagination.per_page,
            pages=pages,
        )

        return jsonify(asdict(response)), 200

    @staticmethod
    async def create_resource(
        table: Any,
        resource_type: str = "Resource",
        required_fields: Optional[List[str]] = None,
        validate_fn: Optional[Callable[[Dict], Optional[tuple]]] = None,
        pre_insert_fn: Optional[Callable[[Dict], Dict]] = None,
        post_insert_fn: Optional[Callable[[int, Any], None]] = None,
        transform_fn: Optional[Callable[[Any], Dict]] = None,
    ) -> tuple[Any, int]:
        """
        Generic create endpoint with validation.

        Args:
            table: PyDAL table object
            resource_type: Human-readable resource name
            required_fields: List of required field names
            validate_fn: Optional custom validation function (returns error tuple or None)
            pre_insert_fn: Optional function to modify data before insert
            post_insert_fn: Optional function to call after insert (e.g., create related records)
            transform_fn: Optional function to transform result to dict

        Returns:
            Tuple of (jsonified response, status_code)

        Example:
            return await CrudHelper.create_resource(
                db.entities,
                resource_type="Entity",
                required_fields=["name", "type", "organization_id"],
                validate_fn=custom_validation
            )
        """
        db = current_app.db

        # Get and validate JSON body
        data = request.get_json()
        if error := validate_json_body(data):
            return error

        # Validate required fields
        if required_fields:
            if error := validate_required_fields(data, required_fields):
                return error

        # Custom validation
        if validate_fn:
            if error := validate_fn(data):
                return error

        # Pre-insert modifications
        if pre_insert_fn:
            data = pre_insert_fn(data)

        # Insert record
        def do_create():
            record_id = table.insert(**data)
            db.commit()
            return record_id

        try:
            record_id = await run_in_threadpool(do_create)

            # Post-insert operations
            if post_insert_fn:
                await post_insert_fn(record_id, data)

            # Get created record
            created_record = await get_by_id(table, record_id)

            # Transform result
            if transform_fn:
                result = transform_fn(created_record)
            else:
                result = created_record.as_dict()

            return ApiResponse.created(result)

        except Exception as e:
            return ApiResponse.internal_error(
                f"Failed to create {resource_type}: {str(e)}"
            )

    @staticmethod
    async def get_resource(
        table: Any,
        resource_id: int,
        resource_type: str = "Resource",
        transform_fn: Optional[Callable[[Any], Dict]] = None,
        include_related_fn: Optional[Callable[[Any], Dict]] = None,
    ) -> tuple[Any, int]:
        """
        Generic get endpoint by ID.

        Args:
            table: PyDAL table object
            resource_id: ID of resource to retrieve
            resource_type: Human-readable resource name
            transform_fn: Optional function to transform result to dict
            include_related_fn: Optional function to include related data

        Returns:
            Tuple of (jsonified response, status_code)

        Example:
            return await CrudHelper.get_resource(
                db.entities,
                entity_id,
                resource_type="Entity"
            )
        """
        # Get resource
        resource, error = await validate_resource_exists(
            table, resource_id, resource_type
        )
        if error:
            return error

        # Transform result
        if transform_fn:
            result = transform_fn(resource)
        else:
            result = resource.as_dict()

        # Include related data
        if include_related_fn:
            related_data = await include_related_fn(resource)
            result.update(related_data)

        return ApiResponse.success(result)

    @staticmethod
    async def update_resource(
        table: Any,
        resource_id: int,
        resource_type: str = "Resource",
        updateable_fields: Optional[List[str]] = None,
        validate_fn: Optional[Callable[[Dict, Any], Optional[tuple]]] = None,
        pre_update_fn: Optional[Callable[[Dict, Any], Dict]] = None,
        post_update_fn: Optional[Callable[[int, Dict, Any], None]] = None,
        transform_fn: Optional[Callable[[Any], Dict]] = None,
    ) -> tuple[Any, int]:
        """
        Generic update endpoint by ID.

        Args:
            table: PyDAL table object
            resource_id: ID of resource to update
            resource_type: Human-readable resource name
            updateable_fields: List of fields that can be updated (all if None)
            validate_fn: Optional custom validation function
            pre_update_fn: Optional function to modify data before update
            post_update_fn: Optional function to call after update
            transform_fn: Optional function to transform result to dict

        Returns:
            Tuple of (jsonified response, status_code)

        Example:
            return await CrudHelper.update_resource(
                db.entities,
                entity_id,
                resource_type="Entity",
                updateable_fields=["name", "description", "status"]
            )
        """
        db = current_app.db

        # Get and validate JSON body
        data = request.get_json()
        if error := validate_json_body(data):
            return error

        # Verify resource exists
        resource, error = await validate_resource_exists(
            table, resource_id, resource_type
        )
        if error:
            return error

        # Custom validation
        if validate_fn:
            if error := validate_fn(data, resource):
                return error

        # Filter to updateable fields
        update_dict = {}
        if updateable_fields:
            for field in updateable_fields:
                if field in data:
                    update_dict[field] = data[field]
        else:
            update_dict = data.copy()

        # Remove non-updateable fields
        update_dict.pop("id", None)
        update_dict.pop("created_at", None)

        # Pre-update modifications
        if pre_update_fn:
            update_dict = pre_update_fn(update_dict, resource)

        # Update record
        def do_update():
            if update_dict:
                db(table.id == resource_id).update(**update_dict)
                db.commit()
            return table[resource_id]

        try:
            updated_record = await run_in_threadpool(do_update)

            # Post-update operations
            if post_update_fn:
                await post_update_fn(resource_id, update_dict, updated_record)

            # Transform result
            if transform_fn:
                result = transform_fn(updated_record)
            else:
                result = updated_record.as_dict()

            return ApiResponse.success(result)

        except Exception as e:
            return ApiResponse.internal_error(
                f"Failed to update {resource_type}: {str(e)}"
            )

    @staticmethod
    async def delete_resource(
        table: Any,
        resource_id: int,
        resource_type: str = "Resource",
        validate_fn: Optional[Callable[[Any], Optional[tuple]]] = None,
        pre_delete_fn: Optional[Callable[[Any], None]] = None,
        post_delete_fn: Optional[Callable[[int], None]] = None,
    ) -> tuple[Any, int]:
        """
        Generic delete endpoint by ID.

        Args:
            table: PyDAL table object
            resource_id: ID of resource to delete
            resource_type: Human-readable resource name
            validate_fn: Optional validation function (e.g., check for dependencies)
            pre_delete_fn: Optional function to call before delete (e.g., cascade deletes)
            post_delete_fn: Optional function to call after delete (e.g., cleanup)

        Returns:
            Tuple of (jsonified response, status_code)

        Example:
            return await CrudHelper.delete_resource(
                db.entities,
                entity_id,
                resource_type="Entity"
            )
        """
        db = current_app.db

        # Verify resource exists
        resource, error = await validate_resource_exists(
            table, resource_id, resource_type
        )
        if error:
            return error

        # Custom validation (e.g., check for dependencies)
        if validate_fn:
            if error := validate_fn(resource):
                return error

        # Pre-delete operations
        if pre_delete_fn:
            await pre_delete_fn(resource)

        # Delete record
        def do_delete():
            del table[resource_id]
            db.commit()

        try:
            await run_in_threadpool(do_delete)

            # Post-delete operations
            if post_delete_fn:
                await post_delete_fn(resource_id)

            return ApiResponse.no_content()

        except Exception as e:
            return ApiResponse.internal_error(
                f"Failed to delete {resource_type}: {str(e)}"
            )
