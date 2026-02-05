"""License policy management API endpoints for Elder using PyDAL with async/await."""

# flake8: noqa: E501


import fnmatch
from dataclasses import asdict

import structlog
from flask import Blueprint, current_app, jsonify, request
from apps.api.models.pydantic import (
    CreateLicensePolicyRequest,
    LicensePolicyDTO,
    UpdateLicensePolicyRequest,
)
from pydantic import ValidationError

from apps.api.auth.decorators import login_required, resource_role_required
from apps.api.models.dataclasses import PaginatedResponse
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.pydal_helpers import PaginationParams
from apps.api.utils.validation_helpers import (
    validate_json_body,
    validate_required_fields,
    validate_resource_exists,
)
from apps.api.utils.async_utils import run_in_threadpool

bp = Blueprint("license_policies", __name__)
logger = structlog.get_logger()


def _match_license_pattern(license_id: str, pattern: str) -> bool:
    """
    Check if a license ID matches a pattern.

    Supports wildcards (* and ?) in patterns.

    Args:
        license_id: SPDX license identifier (e.g., "MIT", "Apache-2.0")
        pattern: Pattern to match against (e.g., "MIT", "Apache-*", "GPL-*")

    Returns:
        True if license matches pattern, False otherwise
    """
    if not license_id or not pattern:
        return False

    # Case-insensitive matching
    return fnmatch.fnmatch(license_id.lower(), pattern.lower())


def _check_component_against_policy(component: dict, policy: dict) -> dict:
    """
    Check a component against a license policy.

    Args:
        component: Component dict with license_name field
        policy: Policy dict with allowed_licenses, denied_licenses, action

    Returns:
        Violation dict if policy is violated, None otherwise
        Format: {
            "component_name": str,
            "component_version": str,
            "license": str,
            "policy_name": str,
            "policy_action": str,
            "reason": str
        }
    """
    license_name = component.get("license_name")
    if not license_name:
        return None

    allowed = policy.get("allowed_licenses") or []
    denied = policy.get("denied_licenses") or []

    # Check denied licenses first
    for pattern in denied:
        if _match_license_pattern(license_name, pattern):
            return {
                "component_name": component.get("name"),
                "component_version": component.get("version"),
                "license": license_name,
                "policy_name": policy.get("name"),
                "policy_action": policy.get("action"),
                "reason": f"License '{license_name}' is denied by policy",
            }

    # Check allowed licenses if any are specified
    if allowed:
        allowed_match = False
        for pattern in allowed:
            if _match_license_pattern(license_name, pattern):
                allowed_match = True
                break

        if not allowed_match:
            return {
                "component_name": component.get("name"),
                "component_version": component.get("version"),
                "license": license_name,
                "policy_name": policy.get("name"),
                "policy_action": policy.get("action"),
                "reason": f"License '{license_name}' is not in allowed list",
            }

    return None


@bp.route("", methods=["GET"])
@login_required
async def list_policies():
    """
    List license policies with optional filtering.

    Query Parameters:
        - organization_id: Filter by organization ID
        - is_active: Filter by active status (true/false)
        - page: Page number (default: 1)
        - per_page: Items per page (default: 50)

    Returns:
        200: List of license policies with pagination
        400: Invalid parameters

    Example:
        GET /api/v1/license-policies?organization_id=1&is_active=true
    """
    db = current_app.db

    # Get pagination params
    pagination = PaginationParams.from_request()

    # Build query
    def get_policies():
        query = db.license_policies.id > 0

        # Apply filters
        if request.args.get("organization_id"):
            org_id = request.args.get("organization_id", type=int)
            query &= db.license_policies.organization_id == org_id

        if request.args.get("is_active"):
            is_active = request.args.get("is_active", "true").lower() == "true"
            query &= db.license_policies.is_active == is_active

        # Get count and rows
        total = db(query).count()
        rows = db(query).select(
            orderby=~db.license_policies.created_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )

        return total, rows

    total, rows = await run_in_threadpool(get_policies)

    # Calculate total pages
    pages = pagination.calculate_pages(total)

    # Convert to DTOs
    items = [LicensePolicyDTO.from_pydal_row(row) for row in rows]

    # Create paginated response
    response = PaginatedResponse(
        items=[item.to_dict() for item in items],
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
        pages=pages,
    )

    return jsonify(asdict(response)), 200


@bp.route("", methods=["POST"])
@login_required
async def create_policy():
    """
    Create a new license policy.

    Requires viewer role.

    Request Body:
        - name: Policy name - required
        - organization_id: Organization ID - required
        - description: Policy description - optional
        - allowed_licenses: List of allowed SPDX license identifiers - optional
        - denied_licenses: List of denied SPDX license identifiers - optional
        - action: Action to take on violation (warn, block) - default: warn
        - is_active: Whether policy is active - default: true

    Returns:
        201: Policy created successfully
        400: Invalid request
        403: Insufficient permissions

    Example:
        POST /api/v1/license-policies
        {
            "name": "Corporate Open Source Policy",
            "organization_id": 1,
            "description": "Approved open source licenses",
            "allowed_licenses": ["MIT", "Apache-2.0", "BSD-*"],
            "denied_licenses": ["GPL-*", "AGPL-*"],
            "action": "block"
        }
    """
    db = current_app.db

    # Validate JSON body
    data = request.get_json()
    if error := validate_json_body(data):
        return error

    # Validate request with Pydantic
    try:
        req = CreateLicensePolicyRequest(**data)
    except ValidationError as e:
        errors = [str(err.get("msg", "")) for err in e.errors()]
        return ApiResponse.error(f"Validation error: {', '.join(errors)}", 400)

    # Validate organization exists
    org, error = await validate_resource_exists(
        db.organizations, req.organization_id, "Organization"
    )
    if error:
        return error

    # Validate action
    if req.action not in ["warn", "block"]:
        return ApiResponse.error("action must be 'warn' or 'block'", 400)

    def create():
        policy_id = db.license_policies.insert(
            name=req.name,
            organization_id=req.organization_id,
            description=req.description,
            allowed_licenses=req.allowed_licenses or [],
            denied_licenses=req.denied_licenses or [],
            action=req.action,
            is_active=req.is_active,
        )
        db.commit()
        return db.license_policies[policy_id]

    policy = await run_in_threadpool(create)

    policy_dto = LicensePolicyDTO.from_pydal_row(policy)
    logger.info(
        "license_policy_created",
        policy_id=policy.id,
        name=policy.name,
        organization_id=req.organization_id,
    )
    return ApiResponse.created(policy_dto.to_dict())


@bp.route("/<int:id>", methods=["GET"])
@login_required
async def get_policy(id: int):
    """
    Get a single license policy by ID.

    Path Parameters:
        - id: Policy ID

    Returns:
        200: Policy details
        404: Policy not found

    Example:
        GET /api/v1/license-policies/1
    """
    db = current_app.db

    # Validate resource exists
    policy, error = await validate_resource_exists(
        db.license_policies, id, "License Policy"
    )
    if error:
        return error

    policy_dto = LicensePolicyDTO.from_pydal_row(policy)
    return ApiResponse.success(policy_dto.to_dict())


@bp.route("/<int:id>", methods=["PUT"])
@login_required
@resource_role_required("maintainer")
async def update_policy(id: int):
    """
    Update a license policy.

    Requires maintainer role.

    Path Parameters:
        - id: Policy ID

    Request Body:
        - name: Policy name - optional
        - description: Policy description - optional
        - allowed_licenses: List of allowed SPDX license identifiers - optional
        - denied_licenses: List of denied SPDX license identifiers - optional
        - action: Action to take on violation (warn, block) - optional
        - is_active: Whether policy is active - optional

    Returns:
        200: Policy updated successfully
        400: Invalid request
        403: Insufficient permissions
        404: Policy not found

    Example:
        PUT /api/v1/license-policies/1
        {
            "action": "block",
            "is_active": false
        }
    """
    db = current_app.db

    # Validate JSON body
    data = request.get_json()
    if error := validate_json_body(data):
        return error

    # Validate resource exists
    policy, error = await validate_resource_exists(
        db.license_policies, id, "License Policy"
    )
    if error:
        return error

    # Validate request with Pydantic
    try:
        req = UpdateLicensePolicyRequest(**data)
    except ValidationError as e:
        errors = [str(err.get("msg", "")) for err in e.errors()]
        return ApiResponse.error(f"Validation error: {', '.join(errors)}", 400)

    # Validate action if provided
    if req.action is not None and req.action not in ["warn", "block"]:
        return ApiResponse.error("action must be 'warn' or 'block'", 400)

    def update():
        update_data = {}
        if req.name is not None:
            update_data["name"] = req.name
        if req.description is not None:
            update_data["description"] = req.description
        if req.allowed_licenses is not None:
            update_data["allowed_licenses"] = req.allowed_licenses
        if req.denied_licenses is not None:
            update_data["denied_licenses"] = req.denied_licenses
        if req.action is not None:
            update_data["action"] = req.action
        if req.is_active is not None:
            update_data["is_active"] = req.is_active

        if update_data:
            db.license_policies[id] = update_data
            db.commit()

        return db.license_policies[id]

    updated_policy = await run_in_threadpool(update)

    policy_dto = LicensePolicyDTO.from_pydal_row(updated_policy)
    logger.info("license_policy_updated", policy_id=id)
    return ApiResponse.success(policy_dto.to_dict())


@bp.route("/<int:id>", methods=["DELETE"])
@login_required
@resource_role_required("maintainer")
async def delete_policy(id: int):
    """
    Delete a license policy.

    Requires maintainer role.

    Path Parameters:
        - id: Policy ID

    Returns:
        204: Policy deleted successfully
        403: Insufficient permissions
        404: Policy not found

    Example:
        DELETE /api/v1/license-policies/1
    """
    db = current_app.db

    # Validate resource exists
    policy, error = await validate_resource_exists(
        db.license_policies, id, "License Policy"
    )
    if error:
        return error

    def delete():
        del db.license_policies[id]
        db.commit()

    await run_in_threadpool(delete)

    logger.info("license_policy_deleted", policy_id=id)
    return ApiResponse.no_content()


@bp.route("/check", methods=["POST"])
@login_required
async def check_components():
    """
    Check components against all active license policies.

    This endpoint checks a list of components against all active license
    policies and returns any violations found.

    Request Body:
        - components: List of component dicts - required
          Each component should have:
          - name: Component name
          - version: Component version
          - license_name: SPDX license identifier
        - organization_id: Organization ID to check policies for - optional

    Returns:
        200: Check results with violations list
        400: Invalid request

    Example:
        POST /api/v1/license-policies/check
        {
            "components": [
                {
                    "name": "flask",
                    "version": "2.0.0",
                    "license_name": "BSD-3-Clause"
                },
                {
                    "name": "some-gpl-package",
                    "version": "1.0.0",
                    "license_name": "GPL-3.0"
                }
            ],
            "organization_id": 1
        }

    Response:
        {
            "violations": [
                {
                    "component_name": "some-gpl-package",
                    "component_version": "1.0.0",
                    "license": "GPL-3.0",
                    "policy_name": "Corporate Open Source Policy",
                    "policy_action": "block",
                    "reason": "License 'GPL-3.0' is denied by policy"
                }
            ],
            "total_components_checked": 2,
            "total_violations": 1
        }
    """
    db = current_app.db

    # Validate JSON body
    data = request.get_json()
    if error := validate_json_body(data):
        return error

    # Validate required fields
    if error := validate_required_fields(data, ["components"]):
        return error

    components = data["components"]
    organization_id = data.get("organization_id")

    if not isinstance(components, list):
        return ApiResponse.error("components must be a list", 400)

    def check():
        # Get active policies
        query = db.license_policies.is_active is True

        if organization_id:
            query &= db.license_policies.organization_id == organization_id

        policies = db(query).select()

        violations = []

        # Check each component against each policy
        for component in components:
            for policy in policies:
                policy_dict = policy.as_dict()
                violation = _check_component_against_policy(component, policy_dict)
                if violation:
                    violations.append(violation)

        return violations

    violations = await run_in_threadpool(check)

    result = {
        "violations": violations,
        "total_components_checked": len(components),
        "total_violations": len(violations),
    }

    logger.info(
        "license_policy_check_completed",
        components_checked=len(components),
        violations_found=len(violations),
        organization_id=organization_id,
    )

    return ApiResponse.success(result)
