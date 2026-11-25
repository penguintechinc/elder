"""IPAM (IP Address Management) API endpoints for Elder using PyDAL with async/await."""

# flake8: noqa: E501

from dataclasses import asdict
from datetime import UTC, datetime, timezone

from pydantic import ValidationError
from quart import Blueprint, current_app, jsonify, request

from apps.api.auth.decorators import (
    login_required,
    require_scope,
    resource_role_required,
)
from apps.api.common.licensing.enforce import check_limit
from apps.api.models.dataclasses import PaginatedResponse
from apps.api.models.pydantic import (
    CreateIPAMAddressRequest,
    CreateIPAMPrefixRequest,
    CreateIPAMVlanRequest,
    UpdateIPAMAddressRequest,
    UpdateIPAMPrefixRequest,
)
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.pydal_helpers import PaginationParams
from apps.api.utils.quart_validation import ValidationErrorResponse
from apps.api.utils.tenant_scoping import get_current_tenant_id, get_tenant_scoped

bp = Blueprint("ipam", __name__)


def _missing_tenant():
    """Return the 403 response used when the verified token carries no tenant."""
    return jsonify({"error": "Missing tenant claim in token"}), 403


def _scoped(db, table, record_id, tenant_id):
    """Return the ``table`` row with ``record_id`` only if it is in ``tenant_id``.

    IPAM tables carry their own ``tenant_id``; organizations do too. Foreign
    and missing rows both return None so callers answer 404 either way.
    """
    if record_id is None:
        return None
    return get_tenant_scoped(db, table, record_id, tenant_id)


# =============================================================================
# IPAM Prefixes Endpoints
# =============================================================================


@bp.route("/prefixes", methods=["GET"])
@login_required
@require_scope("ipam:read")
async def list_prefixes():
    """
    List IPAM prefixes with optional filtering.

    Query Parameters:
        - organization_id: Filter by organization
        - status: Filter by status (active/reserved/deprecated)
        - parent_id: Filter by parent prefix
        - page: Page number (default: 1)
        - per_page: Items per page (default: 50)
        - search: Search in prefix and description

    Returns:
        200: List of prefixes with pagination
        400: Invalid parameters

    Example:
        GET /api/v1/ipam/prefixes?organization_id=1&status=active
    """
    db = current_app.db
    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return _missing_tenant()

    # Get pagination params
    pagination = PaginationParams.from_request()

    # Build query
    def get_prefixes():
        query = db.ipam_prefixes.tenant_id == tenant_id

        # Apply filters
        if request.args.get("organization_id"):
            org_id = request.args.get("organization_id", type=int)
            query &= db.ipam_prefixes.organization_id == org_id

        if request.args.get("status"):
            query &= db.ipam_prefixes.status == request.args.get("status")

        if request.args.get("parent_id"):
            parent_id = request.args.get("parent_id", type=int)
            query &= db.ipam_prefixes.parent_id == parent_id

        if request.args.get("search"):
            search = request.args.get("search")
            search_pattern = f"%{search}%"
            query &= (db.ipam_prefixes.prefix.ilike(search_pattern)) | (
                db.ipam_prefixes.description.ilike(search_pattern)
            )

        # Get count and rows
        total = db(query).count()
        rows = db(query).select(
            orderby=~db.ipam_prefixes.created_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )

        return total, rows

    total, rows = await run_in_threadpool(get_prefixes)

    # Calculate total pages
    pages = pagination.calculate_pages(total)

    # Convert to dicts
    items = [dict(row) for row in rows]

    # Create paginated response
    response = PaginatedResponse(
        items=items,
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
        pages=pages,
    )

    return jsonify(asdict(response)), 200


@bp.route("/prefixes", methods=["POST"])
@login_required
@require_scope("ipam:write")
async def create_prefix():
    """
    Create a new IPAM prefix.

    Requires viewer role on the resource.

    Request Body:
        {
            "prefix": "10.0.0.0/24",
            "description": "Production network",
            "status": "active",
            "organization_id": 1,
            "parent_id": null,
            "vlan_id": null,
            "is_pool": false
        }

    Returns:
        201: Prefix created
        400: Invalid request
        403: Insufficient permissions

    Example:
        POST /api/v1/ipam/prefixes
    """
    db = current_app.db

    try:
        data = CreateIPAMPrefixRequest.model_validate(await request.get_json() or {})
    except ValidationError as e:
        return ValidationErrorResponse.from_pydantic_error(e)

    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return _missing_tenant()

    # Organization, parent prefix and VLAN must all belong to the caller's tenant
    def get_refs():
        return (
            _scoped(db, db.organizations, data.organization_id, tenant_id),
            data.parent_id is None
            or _scoped(db, db.ipam_prefixes, data.parent_id, tenant_id),
            data.vlan_id is None or _scoped(db, db.ipam_vlans, data.vlan_id, tenant_id),
        )

    org, parent_ok, vlan_ok = await run_in_threadpool(get_refs)
    if not org:
        return ApiResponse.error("Organization not found", 404)
    if not parent_ok:
        return ApiResponse.error("Parent prefix not found", 404)
    if not vlan_ok:
        return ApiResponse.error("VLAN not found", 404)

    def find_existing():
        return (
            db(
                (db.ipam_prefixes.prefix == data.prefix)
                & (db.ipam_prefixes.organization_id == data.organization_id)
            )
            .select()
            .first()
        )

    existing = await run_in_threadpool(find_existing)
    if existing:
        if not data.overwrite:
            return (
                jsonify(
                    {
                        "error": f"IPAM prefix '{data.prefix}' already exists in this organization",
                        "code": "DUPLICATE_RESOURCE",
                        "existing_id": existing.id,
                    }
                ),
                409,
            )

        def overwrite_prefix():
            now = datetime.now(UTC)
            db(db.ipam_prefixes.id == existing.id).update(
                description=data.description,
                status=data.status,
                parent_id=data.parent_id,
                vlan_id=data.vlan_id,
                is_pool=data.is_pool,
                updated_at=now,
            )
            db.commit()
            return _scoped(db, db.ipam_prefixes, existing.id, tenant_id)

        prefix = await run_in_threadpool(overwrite_prefix)
        return jsonify(dict(prefix)), 200

    blocked = await check_limit("object", org.tenant_id)
    if blocked is not None:
        return blocked

    redis_client = getattr(current_app, "redis_client", None)

    def create():
        from apps.api.utils.village_id import mint_village_id

        # Create prefix
        now = datetime.now(UTC)
        prefix_id = db.ipam_prefixes.insert(
            prefix=data.prefix,
            description=data.description,
            status=data.status,
            organization_id=data.organization_id,
            tenant_id=org.tenant_id,
            village_id=mint_village_id(org.tenant_id, redis_client),
            parent_id=data.parent_id,
            vlan_id=data.vlan_id,
            is_pool=data.is_pool,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        return _scoped(db, db.ipam_prefixes, prefix_id, tenant_id)

    prefix = await run_in_threadpool(create)

    return jsonify(dict(prefix)), 201


@bp.route("/prefixes/<int:id>", methods=["GET"])
@login_required
@require_scope("ipam:read")
async def get_prefix(id: int):
    """
    Get a single IPAM prefix by ID.

    Path Parameters:
        - id: Prefix ID

    Returns:
        200: Prefix details
        404: Prefix not found

    Example:
        GET /api/v1/ipam/prefixes/1
    """
    db = current_app.db
    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return _missing_tenant()

    prefix = await run_in_threadpool(
        lambda: _scoped(db, db.ipam_prefixes, id, tenant_id)
    )

    if not prefix:
        return ApiResponse.error("Prefix not found", 404)

    return jsonify(dict(prefix)), 200


@bp.route("/prefixes/<int:id>/tree", methods=["GET"])
@login_required
@require_scope("ipam:read")
async def get_prefix_tree(id: int):
    """
    Get an IPAM prefix with all children (hierarchical tree).

    Path Parameters:
        - id: Prefix ID

    Returns:
        200: Prefix with children tree
        404: Prefix not found

    Example:
        GET /api/v1/ipam/prefixes/1/tree
    """
    db = current_app.db
    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return _missing_tenant()

    def get_tree():
        prefix = _scoped(db, db.ipam_prefixes, id, tenant_id)
        if not prefix:
            return None

        def build_tree(parent_id):
            children = db(
                (db.ipam_prefixes.parent_id == parent_id)
                & (db.ipam_prefixes.tenant_id == tenant_id)
            ).select()
            result = []
            for child in children:
                child_dict = dict(child)
                child_dict["children"] = build_tree(child.id)
                result.append(child_dict)
            return result

        prefix_dict = dict(prefix)
        prefix_dict["children"] = build_tree(id)
        return prefix_dict

    tree = await run_in_threadpool(get_tree)

    if not tree:
        return ApiResponse.error("Prefix not found", 404)

    return jsonify(tree), 200


@bp.route("/prefixes/<int:id>", methods=["PUT"])
@login_required
@require_scope("ipam:write")
@resource_role_required("maintainer")
async def update_prefix(id: int):
    """
    Update an IPAM prefix.

    Requires maintainer role.

    Path Parameters:
        - id: Prefix ID

    Request Body:
        {
            "prefix": "10.0.1.0/24",
            "status": "deprecated"
        }

    Returns:
        200: Prefix updated
        400: Invalid request
        403: Insufficient permissions
        404: Prefix not found

    Example:
        PUT /api/v1/ipam/prefixes/1
    """
    db = current_app.db

    try:
        data = UpdateIPAMPrefixRequest.model_validate(await request.get_json() or {})
    except ValidationError as e:
        return ValidationErrorResponse.from_pydantic_error(e)

    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return _missing_tenant()

    # Any re-pointed organization, parent prefix or VLAN must be in-tenant
    def get_refs():
        return (
            data.organization_id is None
            or _scoped(db, db.organizations, data.organization_id, tenant_id),
            data.parent_id is None
            or _scoped(db, db.ipam_prefixes, data.parent_id, tenant_id),
            data.vlan_id is None or _scoped(db, db.ipam_vlans, data.vlan_id, tenant_id),
        )

    org_ok, parent_ok, vlan_ok = await run_in_threadpool(get_refs)
    if not org_ok:
        return ApiResponse.error("Organization not found", 404)
    if not parent_ok:
        return ApiResponse.error("Parent prefix not found", 404)
    if not vlan_ok:
        return ApiResponse.error("VLAN not found", 404)

    def update():
        prefix = _scoped(db, db.ipam_prefixes, id, tenant_id)
        if not prefix:
            return None

        # Update fields
        update_dict = {}
        if data.prefix is not None:
            update_dict["prefix"] = data.prefix
        if data.description is not None:
            update_dict["description"] = data.description
        if data.status is not None:
            update_dict["status"] = data.status
        if data.parent_id is not None:
            update_dict["parent_id"] = data.parent_id
        if data.vlan_id is not None:
            update_dict["vlan_id"] = data.vlan_id
        if data.is_pool is not None:
            update_dict["is_pool"] = data.is_pool
        if data.organization_id is not None:
            update_dict["organization_id"] = data.organization_id

        if update_dict:
            db(
                (db.ipam_prefixes.id == id) & (db.ipam_prefixes.tenant_id == tenant_id)
            ).update(**update_dict)
            db.commit()

        return _scoped(db, db.ipam_prefixes, id, tenant_id)

    prefix = await run_in_threadpool(update)

    if not prefix:
        return ApiResponse.error("Prefix not found", 404)

    return jsonify(dict(prefix)), 200


@bp.route("/prefixes/<int:id>", methods=["DELETE"])
@login_required
@require_scope("ipam:write")
@resource_role_required("maintainer")
async def delete_prefix(id: int):
    """
    Delete an IPAM prefix.

    Requires maintainer role.

    Path Parameters:
        - id: Prefix ID

    Returns:
        204: Prefix deleted
        403: Insufficient permissions
        404: Prefix not found

    Example:
        DELETE /api/v1/ipam/prefixes/1
    """
    db = current_app.db
    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return _missing_tenant()

    def delete():
        prefix = _scoped(db, db.ipam_prefixes, id, tenant_id)
        if not prefix:
            return False

        db(
            (db.ipam_prefixes.id == prefix.id)
            & (db.ipam_prefixes.tenant_id == tenant_id)
        ).delete()
        db.commit()
        return True

    success = await run_in_threadpool(delete)

    if not success:
        return ApiResponse.error("Prefix not found", 404)

    return "", 204


# =============================================================================
# IPAM Addresses Endpoints
# =============================================================================


@bp.route("/addresses", methods=["GET"])
@login_required
@require_scope("ipam:read")
async def list_addresses():
    """
    List IPAM addresses with optional filtering.

    Query Parameters:
        - prefix_id: Filter by prefix
        - status: Filter by status (active/reserved/deprecated/dhcp)
        - page: Page number (default: 1)
        - per_page: Items per page (default: 50)
        - search: Search in address and description

    Returns:
        200: List of addresses with pagination
        400: Invalid parameters

    Example:
        GET /api/v1/ipam/addresses?prefix_id=1&status=active
    """
    db = current_app.db
    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return _missing_tenant()

    # Get pagination params
    pagination = PaginationParams.from_request()

    # Build query
    def get_addresses():
        query = db.ipam_addresses.tenant_id == tenant_id

        # Apply filters
        if request.args.get("prefix_id"):
            prefix_id = request.args.get("prefix_id", type=int)
            query &= db.ipam_addresses.prefix_id == prefix_id

        if request.args.get("status"):
            query &= db.ipam_addresses.status == request.args.get("status")

        if request.args.get("search"):
            search = request.args.get("search")
            search_pattern = f"%{search}%"
            query &= (db.ipam_addresses.address.ilike(search_pattern)) | (
                db.ipam_addresses.description.ilike(search_pattern)
            )

        # Get count and rows
        total = db(query).count()
        rows = db(query).select(
            orderby=~db.ipam_addresses.created_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )

        return total, rows

    total, rows = await run_in_threadpool(get_addresses)

    # Calculate total pages
    pages = pagination.calculate_pages(total)

    # Convert to dicts
    items = [dict(row) for row in rows]

    # Create paginated response
    response = PaginatedResponse(
        items=items,
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
        pages=pages,
    )

    return jsonify(asdict(response)), 200


@bp.route("/addresses", methods=["POST"])
@login_required
@require_scope("ipam:write")
async def create_address():
    """
    Create a new IPAM address.

    Requires viewer role on the resource.

    Request Body:
        {
            "address": "10.0.0.1/32",
            "description": "Web server",
            "status": "active",
            "prefix_id": 1,
            "dns_name": "web01.example.com"
        }

    Returns:
        201: Address created
        400: Invalid request
        403: Insufficient permissions

    Example:
        POST /api/v1/ipam/addresses
    """
    db = current_app.db

    try:
        data = CreateIPAMAddressRequest.model_validate(await request.get_json() or {})
    except ValidationError as e:
        return ValidationErrorResponse.from_pydantic_error(e)

    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return _missing_tenant()

    # The prefix must belong to the caller's tenant
    prefix = await run_in_threadpool(
        lambda: _scoped(db, db.ipam_prefixes, data.prefix_id, tenant_id)
    )
    if not prefix:
        return ApiResponse.error("Prefix not found", 404)

    def find_existing():
        # Addresses carry no organization column: "same organization" means
        # under any in-tenant prefix of the target prefix's organization.
        org_prefix_ids = [
            row.id
            for row in db(
                (db.ipam_prefixes.organization_id == prefix.organization_id)
                & (db.ipam_prefixes.tenant_id == tenant_id)
            ).select(db.ipam_prefixes.id)
        ]
        return (
            db(
                (db.ipam_addresses.address == data.address)
                & (db.ipam_addresses.tenant_id == tenant_id)
                & (db.ipam_addresses.prefix_id.belongs(org_prefix_ids))
            )
            .select()
            .first()
        )

    existing = await run_in_threadpool(find_existing)
    if existing:
        if not data.overwrite:
            return (
                jsonify(
                    {
                        "error": f"IPAM address '{data.address}' already exists in this organization",
                        "code": "DUPLICATE_RESOURCE",
                        "existing_id": existing.id,
                    }
                ),
                409,
            )

        def overwrite_address():
            now = datetime.now(UTC)
            db(db.ipam_addresses.id == existing.id).update(
                prefix_id=data.prefix_id,
                description=data.description,
                status=data.status,
                dns_name=data.dns_name,
                updated_at=now,
            )
            db.commit()
            return _scoped(db, db.ipam_addresses, existing.id, tenant_id)

        address = await run_in_threadpool(overwrite_address)
        return jsonify(dict(address)), 200

    blocked = await check_limit("object", prefix.tenant_id)
    if blocked is not None:
        return blocked

    redis_client = getattr(current_app, "redis_client", None)

    def create():
        from apps.api.utils.village_id import mint_village_id

        # Create address
        now = datetime.now(UTC)
        address_id = db.ipam_addresses.insert(
            address=data.address,
            description=data.description,
            status=data.status,
            prefix_id=data.prefix_id,
            tenant_id=prefix.tenant_id,
            village_id=mint_village_id(prefix.tenant_id, redis_client),
            dns_name=data.dns_name,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        return _scoped(db, db.ipam_addresses, address_id, tenant_id)

    address = await run_in_threadpool(create)

    return jsonify(dict(address)), 201


@bp.route("/addresses/<int:id>", methods=["GET"])
@login_required
@require_scope("ipam:read")
async def get_address(id: int):
    """
    Get a single IPAM address by ID.

    Path Parameters:
        - id: Address ID

    Returns:
        200: Address details
        404: Address not found

    Example:
        GET /api/v1/ipam/addresses/1
    """
    db = current_app.db

    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return _missing_tenant()

    address = await run_in_threadpool(
        lambda: _scoped(db, db.ipam_addresses, id, tenant_id)
    )

    if not address:
        return ApiResponse.error("Address not found", 404)

    return jsonify(dict(address)), 200


@bp.route("/addresses/<int:id>", methods=["PUT"])
@login_required
@require_scope("ipam:write")
@resource_role_required("maintainer")
async def update_address(id: int):
    """
    Update an IPAM address.

    Requires maintainer role.

    Path Parameters:
        - id: Address ID

    Request Body:
        {
            "address": "10.0.0.2/32",
            "status": "reserved"
        }

    Returns:
        200: Address updated
        400: Invalid request
        403: Insufficient permissions
        404: Address not found

    Example:
        PUT /api/v1/ipam/addresses/1
    """
    db = current_app.db

    try:
        data = UpdateIPAMAddressRequest.model_validate(await request.get_json() or {})
    except ValidationError as e:
        return ValidationErrorResponse.from_pydantic_error(e)

    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return _missing_tenant()

    # A re-pointed prefix must belong to the caller's tenant
    if data.prefix_id is not None:
        prefix = await run_in_threadpool(
            lambda: _scoped(db, db.ipam_prefixes, data.prefix_id, tenant_id)
        )
        if not prefix:
            return ApiResponse.error("Prefix not found", 404)

    def update():
        address = _scoped(db, db.ipam_addresses, id, tenant_id)
        if not address:
            return None

        # Update fields
        update_dict = {}
        if data.address is not None:
            update_dict["address"] = data.address
        if data.description is not None:
            update_dict["description"] = data.description
        if data.status is not None:
            update_dict["status"] = data.status
        if data.prefix_id is not None:
            update_dict["prefix_id"] = data.prefix_id
        if data.dns_name is not None:
            update_dict["dns_name"] = data.dns_name

        if update_dict:
            db(
                (db.ipam_addresses.id == id)
                & (db.ipam_addresses.tenant_id == tenant_id)
            ).update(**update_dict)
            db.commit()

        return _scoped(db, db.ipam_addresses, id, tenant_id)

    address = await run_in_threadpool(update)

    if not address:
        return ApiResponse.error("Address not found", 404)

    return jsonify(dict(address)), 200


@bp.route("/addresses/<int:id>", methods=["DELETE"])
@login_required
@require_scope("ipam:write")
@resource_role_required("maintainer")
async def delete_address(id: int):
    """
    Delete an IPAM address.

    Requires maintainer role.

    Path Parameters:
        - id: Address ID

    Returns:
        204: Address deleted
        403: Insufficient permissions
        404: Address not found

    Example:
        DELETE /api/v1/ipam/addresses/1
    """
    db = current_app.db
    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return _missing_tenant()

    def delete():
        address = _scoped(db, db.ipam_addresses, id, tenant_id)
        if not address:
            return False

        db(
            (db.ipam_addresses.id == address.id)
            & (db.ipam_addresses.tenant_id == tenant_id)
        ).delete()
        db.commit()
        return True

    success = await run_in_threadpool(delete)

    if not success:
        return ApiResponse.error("Address not found", 404)

    return "", 204


# =============================================================================
# IPAM VLANs Endpoints
# =============================================================================


@bp.route("/vlans", methods=["GET"])
@login_required
@require_scope("ipam:read")
async def list_vlans():
    """
    List IPAM VLANs with optional filtering.

    Query Parameters:
        - organization_id: Filter by organization
        - status: Filter by status (active/reserved/deprecated)
        - page: Page number (default: 1)
        - per_page: Items per page (default: 50)
        - search: Search in name and description

    Returns:
        200: List of VLANs with pagination
        400: Invalid parameters

    Example:
        GET /api/v1/ipam/vlans?organization_id=1&status=active
    """
    db = current_app.db
    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return _missing_tenant()

    # Get pagination params
    pagination = PaginationParams.from_request()

    # Build query
    def get_vlans():
        query = db.ipam_vlans.tenant_id == tenant_id

        # Apply filters
        if request.args.get("organization_id"):
            org_id = request.args.get("organization_id", type=int)
            query &= db.ipam_vlans.organization_id == org_id

        if request.args.get("status"):
            query &= db.ipam_vlans.status == request.args.get("status")

        if request.args.get("search"):
            search = request.args.get("search")
            search_pattern = f"%{search}%"
            query &= (db.ipam_vlans.name.ilike(search_pattern)) | (
                db.ipam_vlans.description.ilike(search_pattern)
            )

        # Get count and rows
        total = db(query).count()
        rows = db(query).select(
            orderby=~db.ipam_vlans.created_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )

        return total, rows

    total, rows = await run_in_threadpool(get_vlans)

    # Calculate total pages
    pages = pagination.calculate_pages(total)

    # Convert to dicts
    items = [dict(row) for row in rows]

    # Create paginated response
    response = PaginatedResponse(
        items=items,
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
        pages=pages,
    )

    return jsonify(asdict(response)), 200


@bp.route("/vlans", methods=["POST"])
@login_required
@require_scope("ipam:write")
async def create_vlan():
    """
    Create a new IPAM VLAN.

    Requires viewer role on the resource.

    Request Body:
        {
            "vid": 100,
            "name": "Production",
            "description": "Production network VLAN",
            "status": "active",
            "organization_id": 1
        }

    Returns:
        201: VLAN created
        400: Invalid request
        403: Insufficient permissions

    Example:
        POST /api/v1/ipam/vlans
    """
    db = current_app.db

    try:
        data = CreateIPAMVlanRequest.model_validate(await request.get_json() or {})
    except ValidationError as e:
        return ValidationErrorResponse.from_pydantic_error(e)

    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return _missing_tenant()

    # The organization must belong to the caller's tenant
    org = await run_in_threadpool(
        lambda: _scoped(db, db.organizations, data.organization_id, tenant_id)
    )
    if not org:
        return ApiResponse.error("Organization not found", 404)

    def find_existing():
        return (
            db(
                (db.ipam_vlans.vid == data.vid)
                & (db.ipam_vlans.organization_id == data.organization_id)
            )
            .select()
            .first()
        )

    existing = await run_in_threadpool(find_existing)
    if existing:
        if not data.overwrite:
            return (
                jsonify(
                    {
                        "error": f"IPAM VLAN '{data.vid}' already exists in this organization",
                        "code": "DUPLICATE_RESOURCE",
                        "existing_id": existing.id,
                    }
                ),
                409,
            )

        def overwrite_vlan():
            now = datetime.now(UTC)
            db(db.ipam_vlans.id == existing.id).update(
                name=data.name,
                description=data.description,
                status=data.status,
                updated_at=now,
            )
            db.commit()
            return _scoped(db, db.ipam_vlans, existing.id, tenant_id)

        vlan = await run_in_threadpool(overwrite_vlan)
        return jsonify(dict(vlan)), 200

    blocked = await check_limit("object", org.tenant_id)
    if blocked is not None:
        return blocked

    redis_client = getattr(current_app, "redis_client", None)

    def create():
        from apps.api.utils.village_id import mint_village_id

        # Create VLAN
        now = datetime.now(UTC)
        vlan_id = db.ipam_vlans.insert(
            vid=data.vid,
            name=data.name,
            description=data.description,
            status=data.status,
            organization_id=data.organization_id,
            tenant_id=org.tenant_id,
            village_id=mint_village_id(org.tenant_id, redis_client),
            created_at=now,
            updated_at=now,
        )
        db.commit()

        return _scoped(db, db.ipam_vlans, vlan_id, tenant_id)

    vlan = await run_in_threadpool(create)

    return jsonify(dict(vlan)), 201


@bp.route("/vlans/<int:id>", methods=["GET"])
@login_required
@require_scope("ipam:read")
async def get_vlan(id: int):
    """
    Get a single IPAM VLAN by ID.

    Path Parameters:
        - id: VLAN ID

    Returns:
        200: VLAN details
        404: VLAN not found

    Example:
        GET /api/v1/ipam/vlans/1
    """
    db = current_app.db

    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return _missing_tenant()

    vlan = await run_in_threadpool(lambda: _scoped(db, db.ipam_vlans, id, tenant_id))

    if not vlan:
        return ApiResponse.error("VLAN not found", 404)

    return jsonify(dict(vlan)), 200


@bp.route("/vlans/<int:id>", methods=["PUT"])
@login_required
@require_scope("ipam:write")
@resource_role_required("maintainer")
async def update_vlan(id: int):
    """
    Update an IPAM VLAN.

    Requires maintainer role.

    Path Parameters:
        - id: VLAN ID

    Request Body:
        {
            "name": "Updated VLAN Name",
            "status": "deprecated"
        }

    Returns:
        200: VLAN updated
        400: Invalid request
        403: Insufficient permissions
        404: VLAN not found

    Example:
        PUT /api/v1/ipam/vlans/1
    """
    db = current_app.db

    data = await request.get_json()
    if not data:
        return ApiResponse.bad_request("Request body must be JSON")

    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return _missing_tenant()

    # A re-pointed organization must belong to the caller's tenant
    if "organization_id" in data:
        new_org_id = data["organization_id"]
        if not isinstance(new_org_id, int) or isinstance(new_org_id, bool):
            return ApiResponse.bad_request("organization_id must be an integer")
        org = await run_in_threadpool(
            lambda: _scoped(db, db.organizations, new_org_id, tenant_id)
        )
        if not org:
            return ApiResponse.error("Organization not found", 404)

    def update():
        vlan = _scoped(db, db.ipam_vlans, id, tenant_id)
        if not vlan:
            return None

        # Update fields
        update_dict = {}
        if "vid" in data:
            update_dict["vid"] = data["vid"]
        if "name" in data:
            update_dict["name"] = data["name"]
        if "description" in data:
            update_dict["description"] = data["description"]
        if "status" in data:
            update_dict["status"] = data["status"]
        if "organization_id" in data:
            update_dict["organization_id"] = data["organization_id"]

        if update_dict:
            db(
                (db.ipam_vlans.id == id) & (db.ipam_vlans.tenant_id == tenant_id)
            ).update(**update_dict)
            db.commit()

        return _scoped(db, db.ipam_vlans, id, tenant_id)

    vlan = await run_in_threadpool(update)

    if not vlan:
        return ApiResponse.error("VLAN not found", 404)

    return jsonify(dict(vlan)), 200


@bp.route("/vlans/<int:id>", methods=["DELETE"])
@login_required
@require_scope("ipam:write")
@resource_role_required("maintainer")
async def delete_vlan(id: int):
    """
    Delete an IPAM VLAN.

    Requires maintainer role.

    Path Parameters:
        - id: VLAN ID

    Returns:
        204: VLAN deleted
        403: Insufficient permissions
        404: VLAN not found

    Example:
        DELETE /api/v1/ipam/vlans/1
    """
    db = current_app.db
    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return _missing_tenant()

    def delete():
        vlan = _scoped(db, db.ipam_vlans, id, tenant_id)
        if not vlan:
            return False

        db(
            (db.ipam_vlans.id == vlan.id) & (db.ipam_vlans.tenant_id == tenant_id)
        ).delete()
        db.commit()
        return True

    success = await run_in_threadpool(delete)

    if not success:
        return ApiResponse.error("VLAN not found", 404)

    return "", 204
