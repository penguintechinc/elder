"""IPAM (IP Address Management) API endpoints for Elder using PyDAL with async/await."""

# flake8: noqa: E501


from dataclasses import asdict

from flask import Blueprint, current_app, jsonify, request
from penguin_libs.pydantic.flask_integration import ValidationErrorResponse
from apps.api.models.pydantic import (
    CreateIPAMAddressRequest,
    CreateIPAMPrefixRequest,
    CreateIPAMVlanRequest,
    UpdateIPAMAddressRequest,
    UpdateIPAMPrefixRequest,
)
from pydantic import ValidationError

from apps.api.auth.decorators import login_required, resource_role_required
from apps.api.models.dataclasses import PaginatedResponse
from apps.api.utils.async_utils import run_in_threadpool

bp = Blueprint("ipam", __name__)


# =============================================================================
# IPAM Prefixes Endpoints
# =============================================================================


@bp.route("/prefixes", methods=["GET"])
@login_required
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

    # Get pagination params
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 50, type=int), 1000)

    # Build query
    def get_prefixes():
        query = db.ipam_prefixes.id > 0

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

        # Calculate pagination
        offset = (page - 1) * per_page

        # Get count and rows
        total = db(query).count()
        rows = db(query).select(
            orderby=~db.ipam_prefixes.created_at, limitby=(offset, offset + per_page)
        )

        return total, rows

    total, rows = await run_in_threadpool(get_prefixes)

    # Calculate total pages
    pages = (total + per_page - 1) // per_page if total > 0 else 0

    # Convert to dicts
    items = [dict(row) for row in rows]

    # Create paginated response
    response = PaginatedResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )

    return jsonify(asdict(response)), 200


@bp.route("/prefixes", methods=["POST"])
@login_required
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
        data = CreateIPAMPrefixRequest.model_validate(request.get_json() or {})
    except ValidationError as e:
        return ValidationErrorResponse.from_pydantic_error(e)

    # Get organization to derive tenant_id
    def get_org():
        return db.organizations[data.organization_id]

    org = await run_in_threadpool(get_org)
    if not org:
        return jsonify({"error": "Organization not found"}), 404
    if not org.tenant_id:
        return jsonify({"error": "Organization must have a tenant"}), 400

    def create():
        # Create prefix
        prefix_id = db.ipam_prefixes.insert(
            prefix=data.prefix,
            description=data.description,
            status=data.status,
            organization_id=data.organization_id,
            tenant_id=org.tenant_id,
            parent_id=data.parent_id,
            vlan_id=data.vlan_id,
            is_pool=data.is_pool,
        )
        db.commit()

        return db.ipam_prefixes[prefix_id]

    prefix = await run_in_threadpool(create)

    return jsonify(dict(prefix)), 201


@bp.route("/prefixes/<int:id>", methods=["GET"])
@login_required
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

    prefix = await run_in_threadpool(lambda: db.ipam_prefixes[id])

    if not prefix:
        return jsonify({"error": "Prefix not found"}), 404

    return jsonify(dict(prefix)), 200


@bp.route("/prefixes/<int:id>/tree", methods=["GET"])
@login_required
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

    def get_tree():
        prefix = db.ipam_prefixes[id]
        if not prefix:
            return None

        def build_tree(parent_id):
            children = db(db.ipam_prefixes.parent_id == parent_id).select()
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
        return jsonify({"error": "Prefix not found"}), 404

    return jsonify(tree), 200


@bp.route("/prefixes/<int:id>", methods=["PUT"])
@login_required
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
        data = UpdateIPAMPrefixRequest.model_validate(request.get_json() or {})
    except ValidationError as e:
        return ValidationErrorResponse.from_pydantic_error(e)

    # If organization is being changed, validate and get tenant
    org_tenant_id = None
    if data.organization_id is not None:

        def get_org():
            return db.organizations[data.organization_id]

        org = await run_in_threadpool(get_org)
        if not org:
            return jsonify({"error": "Organization not found"}), 404
        if not org.tenant_id:
            return jsonify({"error": "Organization must have a tenant"}), 400
        org_tenant_id = org.tenant_id

    def update():
        prefix = db.ipam_prefixes[id]
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
            update_dict["tenant_id"] = org_tenant_id

        if update_dict:
            db(db.ipam_prefixes.id == id).update(**update_dict)
            db.commit()

        return db.ipam_prefixes[id]

    prefix = await run_in_threadpool(update)

    if not prefix:
        return jsonify({"error": "Prefix not found"}), 404

    return jsonify(dict(prefix)), 200


@bp.route("/prefixes/<int:id>", methods=["DELETE"])
@login_required
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

    def delete():
        prefix = db.ipam_prefixes[id]
        if not prefix:
            return False

        del db.ipam_prefixes[id]
        db.commit()
        return True

    success = await run_in_threadpool(delete)

    if not success:
        return jsonify({"error": "Prefix not found"}), 404

    return "", 204


# =============================================================================
# IPAM Addresses Endpoints
# =============================================================================


@bp.route("/addresses", methods=["GET"])
@login_required
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

    # Get pagination params
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 50, type=int), 1000)

    # Build query
    def get_addresses():
        query = db.ipam_addresses.id > 0

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

        # Calculate pagination
        offset = (page - 1) * per_page

        # Get count and rows
        total = db(query).count()
        rows = db(query).select(
            orderby=~db.ipam_addresses.created_at, limitby=(offset, offset + per_page)
        )

        return total, rows

    total, rows = await run_in_threadpool(get_addresses)

    # Calculate total pages
    pages = (total + per_page - 1) // per_page if total > 0 else 0

    # Convert to dicts
    items = [dict(row) for row in rows]

    # Create paginated response
    response = PaginatedResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )

    return jsonify(asdict(response)), 200


@bp.route("/addresses", methods=["POST"])
@login_required
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
        data = CreateIPAMAddressRequest.model_validate(request.get_json() or {})
    except ValidationError as e:
        return ValidationErrorResponse.from_pydantic_error(e)

    # Get prefix to derive tenant_id and organization_id
    def get_prefix():
        return db.ipam_prefixes[data.prefix_id]

    prefix = await run_in_threadpool(get_prefix)
    if not prefix:
        return jsonify({"error": "Prefix not found"}), 404

    def create():
        # Create address
        address_id = db.ipam_addresses.insert(
            address=data.address,
            description=data.description,
            status=data.status,
            prefix_id=data.prefix_id,
            tenant_id=prefix.tenant_id,
            dns_name=data.dns_name,
        )
        db.commit()

        return db.ipam_addresses[address_id]

    address = await run_in_threadpool(create)

    return jsonify(dict(address)), 201


@bp.route("/addresses/<int:id>", methods=["GET"])
@login_required
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

    address = await run_in_threadpool(lambda: db.ipam_addresses[id])

    if not address:
        return jsonify({"error": "Address not found"}), 404

    return jsonify(dict(address)), 200


@bp.route("/addresses/<int:id>", methods=["PUT"])
@login_required
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
        data = UpdateIPAMAddressRequest.model_validate(request.get_json() or {})
    except ValidationError as e:
        return ValidationErrorResponse.from_pydantic_error(e)

    # If prefix is being changed, validate it
    if data.prefix_id is not None:

        def get_prefix():
            return db.ipam_prefixes[data.prefix_id]

        prefix = await run_in_threadpool(get_prefix)
        if not prefix:
            return jsonify({"error": "Prefix not found"}), 404

    def update():
        address = db.ipam_addresses[id]
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
            db(db.ipam_addresses.id == id).update(**update_dict)
            db.commit()

        return db.ipam_addresses[id]

    address = await run_in_threadpool(update)

    if not address:
        return jsonify({"error": "Address not found"}), 404

    return jsonify(dict(address)), 200


@bp.route("/addresses/<int:id>", methods=["DELETE"])
@login_required
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

    def delete():
        address = db.ipam_addresses[id]
        if not address:
            return False

        del db.ipam_addresses[id]
        db.commit()
        return True

    success = await run_in_threadpool(delete)

    if not success:
        return jsonify({"error": "Address not found"}), 404

    return "", 204


# =============================================================================
# IPAM VLANs Endpoints
# =============================================================================


@bp.route("/vlans", methods=["GET"])
@login_required
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

    # Get pagination params
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 50, type=int), 1000)

    # Build query
    def get_vlans():
        query = db.ipam_vlans.id > 0

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

        # Calculate pagination
        offset = (page - 1) * per_page

        # Get count and rows
        total = db(query).count()
        rows = db(query).select(
            orderby=~db.ipam_vlans.created_at, limitby=(offset, offset + per_page)
        )

        return total, rows

    total, rows = await run_in_threadpool(get_vlans)

    # Calculate total pages
    pages = (total + per_page - 1) // per_page if total > 0 else 0

    # Convert to dicts
    items = [dict(row) for row in rows]

    # Create paginated response
    response = PaginatedResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )

    return jsonify(asdict(response)), 200


@bp.route("/vlans", methods=["POST"])
@login_required
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
        data = CreateIPAMVlanRequest.model_validate(request.get_json() or {})
    except ValidationError as e:
        return ValidationErrorResponse.from_pydantic_error(e)

    # Get organization to derive tenant_id
    def get_org():
        return db.organizations[data.organization_id]

    org = await run_in_threadpool(get_org)
    if not org:
        return jsonify({"error": "Organization not found"}), 404
    if not org.tenant_id:
        return jsonify({"error": "Organization must have a tenant"}), 400

    def create():
        # Create VLAN
        vlan_id = db.ipam_vlans.insert(
            vid=data.vid,
            name=data.name,
            description=data.description,
            status=data.status,
            organization_id=data.organization_id,
            tenant_id=org.tenant_id,
        )
        db.commit()

        return db.ipam_vlans[vlan_id]

    vlan = await run_in_threadpool(create)

    return jsonify(dict(vlan)), 201


@bp.route("/vlans/<int:id>", methods=["GET"])
@login_required
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

    vlan = await run_in_threadpool(lambda: db.ipam_vlans[id])

    if not vlan:
        return jsonify({"error": "VLAN not found"}), 404

    return jsonify(dict(vlan)), 200


@bp.route("/vlans/<int:id>", methods=["PUT"])
@login_required
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

    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    # If organization is being changed, validate and get tenant
    org_tenant_id = None
    if "organization_id" in data:

        def get_org():
            return db.organizations[data["organization_id"]]

        org = await run_in_threadpool(get_org)
        if not org:
            return jsonify({"error": "Organization not found"}), 404
        if not org.tenant_id:
            return jsonify({"error": "Organization must have a tenant"}), 400
        org_tenant_id = org.tenant_id

    def update():
        vlan = db.ipam_vlans[id]
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
            update_dict["tenant_id"] = org_tenant_id

        if update_dict:
            db(db.ipam_vlans.id == id).update(**update_dict)
            db.commit()

        return db.ipam_vlans[id]

    vlan = await run_in_threadpool(update)

    if not vlan:
        return jsonify({"error": "VLAN not found"}), 404

    return jsonify(dict(vlan)), 200


@bp.route("/vlans/<int:id>", methods=["DELETE"])
@login_required
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

    def delete():
        vlan = db.ipam_vlans[id]
        if not vlan:
            return False

        del db.ipam_vlans[id]
        db.commit()
        return True

    success = await run_in_threadpool(delete)

    if not success:
        return jsonify({"error": "VLAN not found"}), 404

    return "", 204
