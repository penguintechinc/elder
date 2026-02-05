"""SBOM components management API endpoints for Elder using PyDAL with async/await and shared helpers."""

# flake8: noqa: E501


from dataclasses import asdict

from flask import Blueprint, current_app, jsonify, request

from apps.api.auth.decorators import login_required, resource_role_required
from apps.api.models.dataclasses import (
    PaginatedResponse,
    SBOMComponentDTO,
    from_pydal_row,
    from_pydal_rows,
)
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.pydal_helpers import PaginationParams
from apps.api.utils.validation_helpers import (
    validate_json_body,
    validate_required_fields,
    validate_resource_exists,
)
from apps.api.utils.async_utils import run_in_threadpool

bp = Blueprint("sbom", __name__)


@bp.route("", methods=["GET"])
@login_required
async def list_components():
    """
    List SBOM components with optional filtering.

    Query Parameters:
        - parent_type: Filter by parent type (service, software, sbom_component)
        - parent_id: Filter by parent ID
        - package_type: Filter by package type (pypi, npm, go, maven, nuget, cargo, gem, other)
        - license_id: Filter by license ID
        - direct: Filter by direct dependency (true/false)
        - page: Page number (default: 1)
        - per_page: Items per page (default: 50)
        - search: Search in name and description

    Returns:
        200: List of components with pagination
        400: Invalid parameters

    Example:
        GET /api/v1/sbom/components?parent_type=service&parent_id=1
    """
    db = current_app.db

    # Get pagination params using helper
    pagination = PaginationParams.from_request()

    # Build query
    def get_components():
        query = db.sbom_components.id > 0

        # Apply filters
        if request.args.get("parent_type"):
            query &= db.sbom_components.parent_type == request.args.get("parent_type")

        if request.args.get("parent_id"):
            parent_id = request.args.get("parent_id", type=int)
            query &= db.sbom_components.parent_id == parent_id

        if request.args.get("package_type"):
            query &= db.sbom_components.package_type == request.args.get("package_type")

        if request.args.get("license_id"):
            query &= db.sbom_components.license_id == request.args.get("license_id")

        if request.args.get("direct"):
            direct = request.args.get("direct").lower() == "true"
            query &= db.sbom_components.direct == direct

        if request.args.get("search"):
            search = request.args.get("search")
            search_pattern = f"%{search}%"
            query &= (db.sbom_components.name.ilike(search_pattern)) | (
                db.sbom_components.description.ilike(search_pattern)
            )

        # Get count and rows
        total = db(query).count()
        rows = db(query).select(
            orderby=~db.sbom_components.created_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )

        return total, rows

    total, rows = await run_in_threadpool(get_components)

    # Calculate total pages using helper
    pages = pagination.calculate_pages(total)

    # Convert to DTOs
    items = from_pydal_rows(rows, SBOMComponentDTO)

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
@resource_role_required("viewer")
async def create_component():
    """
    Create a new SBOM component.

    Requires viewer role on the resource.

    Request Body:
        JSON object with component fields

    Returns:
        201: Component created
        400: Invalid request
        403: Insufficient permissions

    Example:
        POST /api/v1/sbom/components
    """
    db = current_app.db

    # Validate JSON body
    data = request.get_json()
    if error := validate_json_body(data):
        return error

    # Validate required fields
    if error := validate_required_fields(
        data, ["parent_type", "parent_id", "name", "package_type"]
    ):
        return error

    # Validate parent exists (service or software)
    def validate_parent():
        parent_type = data["parent_type"]
        parent_id = data["parent_id"]

        if parent_type == "service":
            return db.services[parent_id]
        elif parent_type == "software":
            return db.software[parent_id]
        elif parent_type == "sbom_component":
            return db.sbom_components[parent_id]
        return None

    parent = await run_in_threadpool(validate_parent)
    if not parent:
        return ApiResponse.not_found(data["parent_type"], data["parent_id"])

    def create():
        # Create component
        component_id = db.sbom_components.insert(
            parent_type=data["parent_type"],
            parent_id=data["parent_id"],
            name=data["name"],
            version=data.get("version"),
            purl=data.get("purl"),
            package_type=data["package_type"],
            scope=data.get("scope", "runtime"),
            direct=data.get("direct", True),
            license_id=data.get("license_id"),
            license_name=data.get("license_name"),
            source_file=data.get("source_file"),
            repository_url=data.get("repository_url"),
            homepage_url=data.get("homepage_url"),
            description=data.get("description"),
            hash_sha256=data.get("hash_sha256"),
            hash_sha512=data.get("hash_sha512"),
            metadata=data.get("metadata"),
            is_active=data.get("is_active", True),
        )
        db.commit()

        return db.sbom_components[component_id]

    component = await run_in_threadpool(create)

    component_dto = from_pydal_row(component, SBOMComponentDTO)
    return ApiResponse.created(asdict(component_dto))


@bp.route("/<int:id>", methods=["GET"])
@login_required
async def get_component(id: int):
    """
    Get a single SBOM component by ID.

    Path Parameters:
        - id: Component ID

    Returns:
        200: Component details
        404: Component not found

    Example:
        GET /api/v1/sbom/components/1
    """
    db = current_app.db

    # Validate resource exists using helper
    component, error = await validate_resource_exists(
        db.sbom_components, id, "SBOM Component"
    )
    if error:
        return error

    component_dto = from_pydal_row(component, SBOMComponentDTO)
    return ApiResponse.success(asdict(component_dto))


@bp.route("/<int:id>", methods=["PUT"])
@login_required
@resource_role_required("maintainer")
async def update_component(id: int):
    """
    Update an SBOM component.

    Requires maintainer role.

    Path Parameters:
        - id: Component ID

    Request Body:
        JSON object with fields to update

    Returns:
        200: Component updated
        400: Invalid request
        403: Insufficient permissions
        404: Component not found

    Example:
        PUT /api/v1/sbom/components/1
    """
    db = current_app.db

    # Validate JSON body
    data = request.get_json()
    if error := validate_json_body(data):
        return error

    def update():
        component = db.sbom_components[id]
        if not component:
            return None

        # Update fields
        update_dict = {}
        updateable_fields = [
            "name",
            "version",
            "purl",
            "package_type",
            "scope",
            "direct",
            "license_id",
            "license_name",
            "license_url",
            "source_file",
            "repository_url",
            "homepage_url",
            "description",
            "hash_sha256",
            "hash_sha512",
            "metadata",
            "is_active",
        ]

        for field in updateable_fields:
            if field in data:
                update_dict[field] = data[field]

        if update_dict:
            db(db.sbom_components.id == id).update(**update_dict)
            db.commit()

        return db.sbom_components[id]

    component = await run_in_threadpool(update)

    if not component:
        return ApiResponse.not_found("SBOM Component", id)

    component_dto = from_pydal_row(component, SBOMComponentDTO)
    return ApiResponse.success(asdict(component_dto))


@bp.route("/<int:id>", methods=["DELETE"])
@login_required
@resource_role_required("maintainer")
async def delete_component(id: int):
    """
    Delete an SBOM component.

    Requires maintainer role.

    Path Parameters:
        - id: Component ID

    Returns:
        204: Component deleted
        403: Insufficient permissions
        404: Component not found

    Example:
        DELETE /api/v1/sbom/components/1
    """
    db = current_app.db

    # Validate resource exists using helper
    component, error = await validate_resource_exists(
        db.sbom_components, id, "SBOM Component"
    )
    if error:
        return error

    def delete():
        del db.sbom_components[id]
        db.commit()

    await run_in_threadpool(delete)

    return ApiResponse.no_content()


@bp.route("/<int:id>/vulnerabilities", methods=["GET"])
@login_required
async def get_component_vulnerabilities(id: int):
    """
    Get vulnerabilities affecting a specific SBOM component.

    Path Parameters:
        - id: Component ID

    Query Parameters:
        - status: Filter by status (open, investigating, remediated, false_positive, accepted)
        - severity: Filter by severity (critical, high, medium, low, unknown)

    Returns:
        200: List of vulnerabilities affecting this component
        404: Component not found

    Example:
        GET /api/v1/sbom/components/1/vulnerabilities?status=open&severity=critical
    """
    db = current_app.db

    # Validate component exists
    component, error = await validate_resource_exists(
        db.sbom_components, id, "SBOM Component"
    )
    if error:
        return error

    def get_vulnerabilities():
        # Build query for component vulnerabilities
        query = db.component_vulnerabilities.component_id == id

        # Apply filters
        if request.args.get("status"):
            query &= db.component_vulnerabilities.status == request.args.get("status")

        # Get component vulnerability links
        comp_vuln_links = db(query).select()

        # Get vulnerability IDs
        vuln_ids = [link.vulnerability_id for link in comp_vuln_links]

        if not vuln_ids:
            return []

        # Build vulnerability query
        vuln_query = db.vulnerabilities.id.belongs(vuln_ids)

        # Apply severity filter
        if request.args.get("severity"):
            vuln_query &= (
                db.vulnerabilities.severity == request.args.get("severity").upper()
            )

        # Get vulnerabilities
        vulnerabilities = db(vuln_query).select(orderby=~db.vulnerabilities.cvss_score)

        # Build response with link status
        results = []
        link_map = {link.vulnerability_id: link for link in comp_vuln_links}

        for vuln in vulnerabilities:
            link = link_map.get(vuln.id)
            vuln_dict = dict(vuln)
            vuln_dict["link_status"] = link.status if link else "unknown"
            vuln_dict["link_id"] = link.id if link else None
            vuln_dict["remediation_notes"] = link.remediation_notes if link else None
            vuln_dict["remediated_at"] = link.remediated_at if link else None
            results.append(vuln_dict)

        return results

    results = await run_in_threadpool(get_vulnerabilities)

    return jsonify(results), 200
