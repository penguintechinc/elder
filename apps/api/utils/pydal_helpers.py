"""
PyDAL async helper utilities for Elder API.

This module provides async wrappers for common PyDAL operations,
simplifying the use of run_in_threadpool with database queries.
"""

# flake8: noqa: E501


from typing import Any, List, Optional

from flask import request

from apps.api.utils.async_utils import run_in_threadpool


async def get_by_id(table: Any, resource_id: int) -> Optional[Any]:
    """
    Get a record by ID with async support.

    Args:
        table: PyDAL table object
        resource_id: ID of record to retrieve

    Returns:
        PyDAL Row object if found, None otherwise

    Example:
        org = await get_by_id(db.organizations, org_id)
        if not org:
            return ApiResponse.not_found("Organization")
    """
    return await run_in_threadpool(lambda: table[resource_id])


async def query_count(query: Any) -> int:
    """
    Execute a count query with async support.

    Args:
        query: PyDAL query object

    Returns:
        Count of matching records

    Example:
        query = db.entities.organization_id == org_id
        total = await query_count(db(query))
    """
    return await run_in_threadpool(lambda: query.count())


async def query_select(
    query: Any, orderby: Optional[Any] = None, limitby: Optional[tuple] = None, **kwargs
) -> List[Any]:
    """
    Execute a select query with async support.

    Args:
        query: PyDAL query object
        orderby: Optional ordering specification
        limitby: Optional limit tuple (offset, limit)
        **kwargs: Additional select parameters

    Returns:
        List of PyDAL Row objects

    Example:
        query = db.entities.organization_id == org_id
        rows = await query_select(
            db(query),
            orderby=~db.entities.created_at,
            limitby=(offset, offset + per_page)
        )
    """

    def do_select():
        select_kwargs = {}
        if orderby is not None:
            select_kwargs["orderby"] = orderby
        if limitby is not None:
            select_kwargs["limitby"] = limitby
        select_kwargs.update(kwargs)
        return query.select(**select_kwargs)

    return await run_in_threadpool(do_select)


async def insert_record(table: Any, **data) -> int:
    """
    Insert a record with async support.

    Args:
        table: PyDAL table object
        **data: Field values to insert

    Returns:
        ID of inserted record

    Example:
        new_id = await insert_record(
            db.entities,
            name="Server1",
            type="server",
            organization_id=org_id
        )
    """

    def do_insert():
        record_id = table.insert(**data)
        return record_id

    return await run_in_threadpool(do_insert)


async def update_record(table: Any, record_id: int, **data) -> bool:
    """
    Update a record by ID with async support.

    Args:
        table: PyDAL table object
        record_id: ID of record to update
        **data: Field values to update

    Returns:
        True if update successful, False if record not found

    Example:
        success = await update_record(
            db.entities,
            entity_id,
            name="Updated Name",
            status="active"
        )
    """

    def do_update():
        record = table[record_id]
        if not record:
            return False
        if data:
            table[record_id] = data
        return True

    return await run_in_threadpool(do_update)


async def delete_record(table: Any, record_id: int) -> bool:
    """
    Delete a record by ID with async support.

    Args:
        table: PyDAL table object
        record_id: ID of record to delete

    Returns:
        True if delete successful, False if record not found

    Example:
        success = await delete_record(db.entities, entity_id)
        if not success:
            return ApiResponse.not_found("Entity")
    """

    def do_delete():
        record = table[record_id]
        if not record:
            return False
        del table[record_id]
        return True

    return await run_in_threadpool(do_delete)


async def query_update(query: Any, **data) -> int:
    """
    Update multiple records matching a query with async support.

    Args:
        query: PyDAL query object
        **data: Field values to update

    Returns:
        Number of records updated

    Example:
        count = await query_update(
            db(db.entities.organization_id == org_id),
            status="archived"
        )
    """
    return await run_in_threadpool(lambda: query.update(**data))


async def query_delete(query: Any) -> int:
    """
    Delete multiple records matching a query with async support.

    Args:
        query: PyDAL query object

    Returns:
        Number of records deleted

    Example:
        count = await query_delete(db(db.entities.organization_id == org_id))
    """
    return await run_in_threadpool(lambda: query.delete())


async def commit_db(db: Any) -> None:
    """
    Commit database transaction with async support.

    Args:
        db: PyDAL database instance

    Example:
        await commit_db(current_app.db)
    """
    return await run_in_threadpool(lambda: db.commit())


class PaginationParams:
    """
    Helper class for extracting and managing pagination parameters from Flask requests.
    """

    def __init__(self, page: int, per_page: int, offset: int):
        """
        Initialize pagination parameters.

        Args:
            page: Page number (1-indexed)
            per_page: Number of items per page
            offset: Offset for database query (0-indexed)
        """
        self.page = page
        self.per_page = per_page
        self.offset = offset

    @classmethod
    def from_request(
        cls, default_per_page: int = 50, max_per_page: int = 1000
    ) -> "PaginationParams":
        """
        Extract pagination parameters from Flask request.

        Args:
            default_per_page: Default items per page (default: 50)
            max_per_page: Maximum items per page (default: 1000)

        Returns:
            PaginationParams instance

        Example:
            pagination = PaginationParams.from_request()
            rows = await query_select(
                db(query),
                limitby=(pagination.offset, pagination.offset + pagination.per_page)
            )
        """
        page = request.args.get("page", 1, type=int)
        per_page = min(
            request.args.get("per_page", default_per_page, type=int), max_per_page
        )
        offset = (page - 1) * per_page

        return cls(page=page, per_page=per_page, offset=offset)

    def calculate_pages(self, total: int) -> int:
        """
        Calculate total number of pages.

        Args:
            total: Total number of records

        Returns:
            Total number of pages

        Example:
            pagination = PaginationParams.from_request()
            total = await query_count(db(query))
            pages = pagination.calculate_pages(total)
        """
        if total == 0:
            return 0
        return (total + self.per_page - 1) // self.per_page


async def paginated_query(
    query: Any, pagination: PaginationParams, orderby: Optional[Any] = None
) -> tuple[List[Any], int]:
    """
    Execute a paginated query with count.

    Args:
        query: PyDAL query object
        pagination: PaginationParams instance
        orderby: Optional ordering specification

    Returns:
        Tuple of (rows, total_count)

    Example:
        pagination = PaginationParams.from_request()
        query = db.entities.organization_id == org_id
        rows, total = await paginated_query(
            db(query),
            pagination,
            orderby=~db.entities.created_at
        )
    """
    # Run count and select queries in parallel
    total = await query_count(query)
    rows = await query_select(
        query,
        orderby=orderby,
        limitby=(pagination.offset, pagination.offset + pagination.per_page),
    )

    return rows, total
