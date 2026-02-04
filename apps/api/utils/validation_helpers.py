"""
Common validation utilities for Elder API.

This module provides reusable validation functions for common patterns
like organization/tenant validation, required field checks, etc.
"""

# flake8: noqa: E501


import datetime
from typing import Any, Optional, Tuple

import pytz
from croniter import croniter
from flask import current_app

from apps.api.utils.async_utils import run_in_threadpool

from .api_responses import ApiResponse


async def validate_organization_and_get_tenant(
    org_id: int,
) -> Tuple[Optional[Any], Optional[int], Optional[Tuple[Any, int]]]:
    """
    Validate that an organization exists and has a tenant assigned.

    Args:
        org_id: Organization ID to validate

    Returns:
        Tuple of (organization, tenant_id, error_response)
        - If validation succeeds: (org_row, tenant_id, None)
        - If validation fails: (None, None, (error_json, status_code))

    Usage:
        org, tenant_id, error = await validate_organization_and_get_tenant(org_id)
        if error:
            return error
        # Continue with org and tenant_id

    Example:
        org, tenant_id, error = await validate_organization_and_get_tenant(data["organization_id"])
        if error:
            return error
        # org and tenant_id are now available for use
    """
    db = current_app.db

    def get_org():
        return db.organizations[org_id]

    org = await run_in_threadpool(get_org)

    if not org:
        return None, None, ApiResponse.not_found("Organization", org_id)

    if not org.tenant_id:
        return None, None, ApiResponse.error("Organization must have a tenant", 400)

    return org, org.tenant_id, None


async def validate_tenant_exists(
    tenant_id: int,
) -> Tuple[Optional[Any], Optional[Tuple[Any, int]]]:
    """
    Validate that a tenant exists.

    Args:
        tenant_id: Tenant ID to validate

    Returns:
        Tuple of (tenant, error_response)
        - If validation succeeds: (tenant_row, None)
        - If validation fails: (None, (error_json, status_code))

    Usage:
        tenant, error = await validate_tenant_exists(tenant_id)
        if error:
            return error

    Example:
        tenant, error = await validate_tenant_exists(data["tenant_id"])
        if error:
            return error
    """
    db = current_app.db

    def get_tenant():
        return db.tenants[tenant_id]

    tenant = await run_in_threadpool(get_tenant)

    if not tenant:
        return None, ApiResponse.not_found("Tenant", tenant_id)

    return tenant, None


def validate_required_fields(
    data: dict, required_fields: list
) -> Optional[Tuple[Any, int]]:
    """
    Validate that all required fields are present in the data dict.

    Args:
        data: Dictionary to validate
        required_fields: List of required field names

    Returns:
        Error response tuple if validation fails, None if successful

    Usage:
        error = validate_required_fields(data, ["name", "type"])
        if error:
            return error

    Example:
        error = validate_required_fields(request_data, ["name", "organization_id"])
        if error:
            return error
    """
    for field in required_fields:
        if not data.get(field):
            return ApiResponse.validation_error(field, "is required")
    return None


def validate_json_body(data: Any) -> Optional[Tuple[Any, int]]:
    """
    Validate that request body contains JSON data.

    Args:
        data: Request data to validate (typically from request.get_json())

    Returns:
        Error response tuple if validation fails, None if successful

    Usage:
        data = request.get_json()
        error = validate_json_body(data)
        if error:
            return error

    Example:
        data = request.get_json()
        if error := validate_json_body(data):
            return error
    """
    if not data:
        return ApiResponse.bad_request("Request body must be JSON")
    return None


async def validate_resource_exists(
    table: Any, resource_id: int, resource_type: str = "Resource"
) -> Tuple[Optional[Any], Optional[Tuple[Any, int]]]:
    """
    Validate that a resource exists in a PyDAL table.

    Args:
        table: PyDAL table object
        resource_id: ID of resource to validate
        resource_type: Human-readable name of resource type (for error message)

    Returns:
        Tuple of (resource, error_response)
        - If validation succeeds: (resource_row, None)
        - If validation fails: (None, (error_json, status_code))

    Usage:
        resource, error = await validate_resource_exists(db.entities, entity_id, "Entity")
        if error:
            return error

    Example:
        entity, error = await validate_resource_exists(db.entities, id, "Entity")
        if error:
            return error
    """

    def get_resource():
        return table[resource_id]

    resource = await run_in_threadpool(get_resource)

    if not resource:
        return None, ApiResponse.not_found(resource_type, resource_id)

    return resource, None


def validate_pagination_params(
    page: int, per_page: int, max_per_page: int = 1000
) -> Optional[Tuple[Any, int]]:
    """
    Validate pagination parameters.

    Args:
        page: Page number (must be >= 1)
        per_page: Items per page (must be >= 1 and <= max_per_page)
        max_per_page: Maximum allowed per_page value (default: 1000)

    Returns:
        Error response tuple if validation fails, None if successful

    Usage:
        error = validate_pagination_params(page, per_page)
        if error:
            return error

    Example:
        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 50, type=int)
        if error := validate_pagination_params(page, per_page):
            return error
    """
    if page < 1:
        return ApiResponse.bad_request("Page must be >= 1")

    if per_page < 1:
        return ApiResponse.bad_request("per_page must be >= 1")

    if per_page > max_per_page:
        return ApiResponse.bad_request(f"per_page must be <= {max_per_page}")

    return None


def validate_enum_value(
    value: str, allowed_values: list, field_name: str = "value"
) -> Optional[Tuple[Any, int]]:
    """
    Validate that a value is in a list of allowed values (enum validation).

    Args:
        value: Value to validate
        allowed_values: List of allowed values
        field_name: Name of field (for error message)

    Returns:
        Error response tuple if validation fails, None if successful

    Usage:
        error = validate_enum_value(status, ["active", "inactive"], "status")
        if error:
            return error

    Example:
        error = validate_enum_value(
            data.get("status"),
            ["active", "inactive", "archived"],
            "status"
        )
        if error:
            return error
    """
    if value not in allowed_values:
        allowed_str = ", ".join(allowed_values)
        return ApiResponse.bad_request(f"{field_name} must be one of: {allowed_str}")
    return None


def validate_cron_expression(cron_expr: str) -> Optional[Tuple[Any, int]]:
    """
    Validate that a cron expression is valid using croniter.

    Args:
        cron_expr: Cron expression string (e.g., "0 0 * * *")

    Returns:
        Error response tuple if validation fails, None if successful

    Usage:
        error = validate_cron_expression(data["schedule_cron"])
        if error:
            return error

    Example:
        error = validate_cron_expression("0 0 * * *")
        if error:
            return error
    """
    try:
        now = datetime.datetime.now(datetime.timezone.utc)
        cron = croniter(cron_expr, now)
        cron.get_next(datetime.datetime)
    except Exception as e:
        return ApiResponse.error(f"Invalid cron expression: {str(e)}", 400)
    return None


def validate_timezone(tz_name: str) -> Optional[Tuple[Any, int]]:
    """
    Validate that a timezone string is valid using pytz.

    Args:
        tz_name: Timezone name (e.g., "US/Eastern", "UTC")

    Returns:
        Error response tuple if validation fails, None if successful

    Usage:
        error = validate_timezone(data["timezone"])
        if error:
            return error

    Example:
        error = validate_timezone("US/Pacific")
        if error:
            return error
    """
    try:
        pytz.timezone(tz_name)
    except pytz.exceptions.UnknownTimeZoneError:
        return ApiResponse.error(
            f"Invalid timezone: {tz_name}. Use standard timezone names (e.g., US/Eastern, Europe/London)",
            400,
        )
    return None


def validate_shift_config(shift_config: dict) -> Optional[Tuple[Any, int]]:
    """
    Validate shift configuration for follow-the-sun rotations.

    Checks:
    - timezones list exists and is non-empty
    - Each timezone has required fields: timezone, shift_start_hour, shift_end_hour
    - shift_start_hour < shift_end_hour
    - Hours are 0-23
    - 24-hour coverage across all timezones (no gaps)
    - No overlapping shifts within same timezone

    Args:
        shift_config: Shift configuration dict with timezones list

    Returns:
        Error response tuple if validation fails, None if successful

    Usage:
        error = validate_shift_config(data["shift_config"])
        if error:
            return error

    Example:
        config = {
            "timezones": [
                {"timezone": "US/Eastern", "shift_start_hour": 9, "shift_end_hour": 17},
                {"timezone": "Europe/London", "shift_start_hour": 17, "shift_end_hour": 1}
            ]
        }
        error = validate_shift_config(config)
        if error:
            return error
    """
    if not isinstance(shift_config, dict):
        return ApiResponse.error("shift_config must be a dictionary", 400)

    timezones = shift_config.get("timezones", [])
    if not isinstance(timezones, list) or not timezones:
        return ApiResponse.error(
            "shift_config must contain a non-empty timezones list", 400
        )

    # Track coverage hours to check for gaps and overlaps
    coverage_hours = set()

    for tz_entry in timezones:
        if not isinstance(tz_entry, dict):
            return ApiResponse.error("Each timezone entry must be a dictionary", 400)

        # Validate required fields
        tz_name = tz_entry.get("timezone")
        if not tz_name:
            return ApiResponse.error("Each timezone must have a 'timezone' field", 400)

        # Validate timezone name
        try:
            pytz.timezone(tz_name)
        except pytz.exceptions.UnknownTimeZoneError:
            return ApiResponse.error(f"Invalid timezone: {tz_name}", 400)

        # Validate shift hours
        start_hour = tz_entry.get("shift_start_hour")
        end_hour = tz_entry.get("shift_end_hour")

        if start_hour is None or end_hour is None:
            return ApiResponse.error(
                f"Timezone {tz_name} must have shift_start_hour and shift_end_hour", 400
            )

        if not isinstance(start_hour, int) or not isinstance(end_hour, int):
            return ApiResponse.error(
                f"Timezone {tz_name}: shift hours must be integers", 400
            )

        if start_hour < 0 or start_hour > 23:
            return ApiResponse.error(
                f"Timezone {tz_name}: shift_start_hour must be 0-23", 400
            )

        if end_hour < 0 or end_hour > 23:
            return ApiResponse.error(
                f"Timezone {tz_name}: shift_end_hour must be 0-23", 400
            )

        # Allow wrap-around (e.g., 22:00 to 06:00)
        if start_hour == end_hour:
            return ApiResponse.error(
                f"Timezone {tz_name}: shift_start_hour cannot equal shift_end_hour", 400
            )

        # Track coverage for gap detection
        if start_hour < end_hour:
            for hour in range(start_hour, end_hour):
                if hour in coverage_hours:
                    return ApiResponse.error(
                        f"Timezone {tz_name}: shift hours overlap with another timezone",
                        400,
                    )
                coverage_hours.add(hour)
        else:
            # Wrap-around case (e.g., 22:00 to 06:00)
            for hour in list(range(start_hour, 24)) + list(range(0, end_hour)):
                if hour in coverage_hours:
                    return ApiResponse.error(
                        f"Timezone {tz_name}: shift hours overlap with another timezone",
                        400,
                    )
                coverage_hours.add(hour)

    return None


async def validate_no_overlap(
    db,
    rotation_id: int,
    identity_id: int,
    start_dt: datetime.datetime,
    end_dt: datetime.datetime,
    exclude_override_id: Optional[int] = None,
) -> Optional[Tuple[Any, int]]:
    """
    Validate that an override doesn't overlap with existing overrides.

    Checks for overlapping time ranges for the same identity in the same rotation.
    Optionally excludes an override from the check (for updates).

    Args:
        db: PyDAL database instance
        rotation_id: ID of the rotation
        identity_id: ID of the identity being overridden
        start_dt: Override start datetime
        end_dt: Override end datetime
        exclude_override_id: Override ID to exclude from check (for updates)

    Returns:
        Error response tuple if overlap found, None if no overlap

    Usage:
        error = await validate_no_overlap(db, rotation_id, identity_id, start, end)
        if error:
            return error

    Example:
        error = await validate_no_overlap(
            db, 123, 456,
            datetime.datetime.now(),
            datetime.datetime.now() + datetime.timedelta(days=1)
        )
        if error:
            return error
    """

    def check_overlap():
        query = (
            (db.on_call_overrides.rotation_id == rotation_id)
            & (db.on_call_overrides.original_identity_id == identity_id)
            & (db.on_call_overrides.start_datetime < end_dt)
            & (db.on_call_overrides.end_datetime > start_dt)
        )

        if exclude_override_id:
            query &= db.on_call_overrides.id != exclude_override_id

        existing = db(query).select(limitby=(0, 1))
        return len(existing) > 0

    overlap_exists = await run_in_threadpool(check_overlap)

    if overlap_exists:
        return ApiResponse.error(
            "Override time range overlaps with an existing override for this identity",
            400,
        )

    return None
