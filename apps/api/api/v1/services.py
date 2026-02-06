"""Services management API endpoints for Elder using PyDAL with async/await and shared helpers."""

# flake8: noqa: E501


from dataclasses import asdict

from flask import Blueprint, Response, current_app, jsonify, request
from apps.api.models.pydantic.service import CreateServiceRequest, UpdateServiceRequest
from penguin_libs.pydantic.flask_integration import validated_request

from apps.api.auth.decorators import login_required, resource_role_required
from apps.api.models.dataclasses import (
    PaginatedResponse,
    SBOMComponentDTO,
    ServiceDTO,
    from_pydal_row,
    from_pydal_rows,
)
from apps.api.services.sbom.exporters import CycloneDXExporter, SPDXExporter
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.pydal_helpers import PaginationParams
from apps.api.utils.validation_helpers import (
    validate_organization_and_get_tenant,
    validate_resource_exists,
)
from apps.api.utils.async_utils import run_in_threadpool

bp = Blueprint("services", __name__)


@bp.route("", methods=["GET"])
@login_required
async def list_services():
    """
    List services with optional filtering.

    Query Parameters:
        - organization_id: Filter by organization
        - language: Filter by language (rust, go, python, nodejs, etc.)
        - deployment_method: Filter by deployment method
        - status: Filter by status (active/deprecated/maintenance/inactive)
        - page: Page number (default: 1)
        - per_page: Items per page (default: 50)
        - search: Search in name and description

    Returns:
        200: List of services with pagination
        400: Invalid parameters

    Example:
        GET /api/v1/services?organization_id=1&status=active
    """
    db = current_app.db

    # Get pagination params using helper
    pagination = PaginationParams.from_request()

    # Build query
    def get_services():
        query = db.services.id > 0

        # Apply filters
        if request.args.get("organization_id"):
            org_id = request.args.get("organization_id", type=int)
            query &= db.services.organization_id == org_id

        if request.args.get("language"):
            query &= db.services.language == request.args.get("language")

        if request.args.get("deployment_method"):
            query &= db.services.deployment_method == request.args.get(
                "deployment_method"
            )

        if request.args.get("status"):
            query &= db.services.status == request.args.get("status")

        if request.args.get("search"):
            search = request.args.get("search")
            search_pattern = f"%{search}%"
            query &= (db.services.name.ilike(search_pattern)) | (
                db.services.description.ilike(search_pattern)
            )

        # Get count and rows
        total = db(query).count()
        rows = db(query).select(
            orderby=~db.services.created_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )

        return total, rows

    total, rows = await run_in_threadpool(get_services)

    # Calculate total pages using helper
    pages = pagination.calculate_pages(total)

    # Convert to DTOs
    items = from_pydal_rows(rows, ServiceDTO)

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
@validated_request(body_model=CreateServiceRequest)
async def create_service(body: CreateServiceRequest):
    """
    Create a new service.

    Requires viewer role on the resource.

    Request Body:
        CreateServiceRequest: Service creation parameters with validation

    Returns:
        201: Service created
        400: Invalid request
        403: Insufficient permissions

    Example:
        POST /api/v1/services
    """
    db = current_app.db

    # Get organization to derive tenant_id using helper
    org, tenant_id, error = await validate_organization_and_get_tenant(
        body.organization_id
    )
    if error:
        return error

    # Validate poc_identity_id if provided
    if body.poc_identity_id:

        def get_identity():
            return db.identities[body.poc_identity_id]

        identity = await run_in_threadpool(get_identity)
        if not identity:
            return ApiResponse.not_found("POC identity", body.poc_identity_id)

    def create():
        # Create service
        service_id = db.services.insert(
            name=body.name,
            description=body.description,
            organization_id=body.organization_id,
            tenant_id=tenant_id,
            domains=body.domains or [],
            paths=body.paths or [],
            poc_identity_id=body.poc_identity_id,
            language=body.language,
            deployment_method=body.deployment_method,
            deployment_type=body.deployment_type,
            is_public=body.is_public,
            port=body.port,
            health_endpoint=body.health_endpoint,
            repository_url=body.repository_url,
            documentation_url=body.documentation_url,
            sla_uptime=body.sla_uptime,
            sla_response_time_ms=body.sla_response_time_ms,
            notes=body.notes,
            tags=body.tags,
            status=body.status,
        )
        db.commit()

        # Auto-create SBOM scan if repository_url is provided
        if body.repository_url:
            db.sbom_scans.insert(
                parent_type="service",
                parent_id=service_id,
                scan_type="git_clone",
                status="pending",
                repository_url=body.repository_url,
                repository_branch="main",
                components_found=0,
                components_added=0,
                components_updated=0,
                components_removed=0,
            )
            db.commit()

        return db.services[service_id]

    service = await run_in_threadpool(create)

    service_dto = from_pydal_row(service, ServiceDTO)
    return ApiResponse.created(asdict(service_dto))


@bp.route("/<int:id>", methods=["GET"])
@login_required
async def get_service(id: int):
    """
    Get a single service by ID.

    Path Parameters:
        - id: Service ID

    Returns:
        200: Service details
        404: Service not found

    Example:
        GET /api/v1/services/1
    """
    db = current_app.db

    # Validate resource exists using helper
    service, error = await validate_resource_exists(db.services, id, "Service")
    if error:
        return error

    service_dto = from_pydal_row(service, ServiceDTO)
    return ApiResponse.success(asdict(service_dto))


@bp.route("/<int:id>", methods=["PUT"])
@login_required
@resource_role_required("maintainer")
@validated_request(body_model=UpdateServiceRequest)
async def update_service(id: int, body: UpdateServiceRequest):
    """
    Update a service.

    Requires maintainer role.

    Path Parameters:
        - id: Service ID

    Request Body:
        UpdateServiceRequest: Service update parameters with validation

    Returns:
        200: Service updated
        400: Invalid request
        403: Insufficient permissions
        404: Service not found

    Example:
        PUT /api/v1/services/1
    """
    db = current_app.db

    # If organization is being changed, validate and get tenant
    org_tenant_id = None
    if body.organization_id is not None:
        org, org_tenant_id, error = await validate_organization_and_get_tenant(
            body.organization_id
        )
        if error:
            return error

    # Validate poc_identity_id if provided
    if body.poc_identity_id is not None and body.poc_identity_id:

        def get_identity():
            return db.identities[body.poc_identity_id]

        identity = await run_in_threadpool(get_identity)
        if not identity:
            return ApiResponse.not_found("POC identity", body.poc_identity_id)

    def update():
        service = db.services[id]
        if not service:
            return None

        # Build update dictionary from non-None fields
        update_dict = {}
        for field, value in body.model_dump(exclude_none=True).items():
            update_dict[field] = value

        if body.organization_id is not None:
            update_dict["tenant_id"] = org_tenant_id

        if update_dict:
            db(db.services.id == id).update(**update_dict)
            db.commit()

        return db.services[id]

    service = await run_in_threadpool(update)

    if not service:
        return ApiResponse.not_found("Service", id)

    service_dto = from_pydal_row(service, ServiceDTO)
    return ApiResponse.success(asdict(service_dto))


@bp.route("/<int:id>", methods=["DELETE"])
@login_required
@resource_role_required("maintainer")
async def delete_service(id: int):
    """
    Delete a service.

    Requires maintainer role.

    Path Parameters:
        - id: Service ID

    Returns:
        204: Service deleted
        403: Insufficient permissions
        404: Service not found

    Example:
        DELETE /api/v1/services/1
    """
    db = current_app.db

    # Validate resource exists using helper
    service, error = await validate_resource_exists(db.services, id, "Service")
    if error:
        return error

    def delete():
        del db.services[id]
        db.commit()

    await run_in_threadpool(delete)

    return ApiResponse.no_content()


@bp.route("/<int:id>/sbom", methods=["GET"])
@login_required
async def get_service_sbom(id: int):
    """
    Get SBOM components for a service.

    Path Parameters:
        - id: Service ID

    Returns:
        200: List of SBOM components
        404: Service not found

    Example:
        GET /api/v1/services/1/sbom
    """
    db = current_app.db

    # Validate service exists
    service, error = await validate_resource_exists(db.services, id, "Service")
    if error:
        return error

    def get_components():
        query = (db.sbom_components.parent_type == "service") & (
            db.sbom_components.parent_id == id
        )
        rows = db(query).select(orderby=db.sbom_components.name)
        return rows

    rows = await run_in_threadpool(get_components)

    # Convert to DTOs
    items = from_pydal_rows(rows, SBOMComponentDTO)

    return jsonify([asdict(item) for item in items]), 200


@bp.route("/<int:id>/sbom/scan", methods=["POST"])
@login_required
@resource_role_required("viewer")
async def trigger_service_sbom_scan(id: int):
    """
    Trigger an SBOM scan for a service.

    Requires viewer role on the resource.

    Path Parameters:
        - id: Service ID

    Request Body (optional):
        - scan_type: Scan type (git_clone, git_api, etc.) - default: git_clone
        - repository_branch: Repository branch - optional

    Returns:
        201: Scan job created
        400: Invalid request (e.g., no repository URL)
        403: Insufficient permissions
        404: Service not found

    Example:
        POST /api/v1/services/1/sbom/scan
    """
    db = current_app.db

    # Validate service exists
    service, error = await validate_resource_exists(db.services, id, "Service")
    if error:
        return error

    # Verify service has repository URL
    if not service.repository_url:
        return ApiResponse.error(
            "Service does not have a repository URL configured", 400
        )

    # Get request data if provided
    data = request.get_json() or {}
    scan_type = data.get("scan_type", "git_clone")
    repository_branch = data.get("repository_branch")

    def create_scan():
        scan_id = db.sbom_scans.insert(
            parent_type="service",
            parent_id=id,
            scan_type=scan_type,
            status="pending",
            repository_url=service.repository_url,
            repository_branch=repository_branch or "main",
            components_found=0,
            components_added=0,
            components_updated=0,
            components_removed=0,
        )
        db.commit()
        return db.sbom_scans[scan_id]

    scan = await run_in_threadpool(create_scan)

    return (
        jsonify(
            {
                "message": "SBOM scan triggered successfully",
                "scan_id": scan.id,
                "status": scan.status,
            }
        ),
        201,
    )


@bp.route("/endpoints", methods=["GET"])
@login_required
async def list_service_endpoints():
    """
    List all service endpoints across services.

    Query Parameters:
        - search: Filter endpoints by path (substring match)
        - method: Filter endpoints by HTTP method if stored
        - page: Page number (default: 1)
        - per_page: Items per page (default: 50)

    Returns:
        200: List of service endpoints with pagination
        400: Invalid parameters

    Example:
        GET /api/v1/services/endpoints?search=/api&method=GET
    """
    db = current_app.db

    # Get pagination params using helper
    pagination = PaginationParams.from_request()

    # Build query
    def get_endpoints():
        # Query services with non-empty paths
        query = (
            (db.services.id > 0)
            & (db.services.paths is not None)
            & (db.services.paths != "")
        )

        # Get count and rows
        total_services = db(query).count()
        services = db(query).select(
            orderby=~db.services.created_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )

        # Transform service paths into endpoint objects
        endpoints = []
        search_term = request.args.get("search", "").lower()
        method_filter = request.args.get("method", "").upper()

        for service in services:
            if service.paths and isinstance(service.paths, list):
                for path_item in service.paths:
                    # Handle both string paths and object paths with method
                    if isinstance(path_item, dict):
                        path = path_item.get("path", "")
                        method = (
                            path_item.get("method", "").upper()
                            if path_item.get("method")
                            else ""
                        )
                    else:
                        path = str(path_item)
                        method = ""

                    # Apply filters
                    if search_term and search_term not in path.lower():
                        continue
                    if method_filter and method and method != method_filter:
                        continue

                    endpoints.append(
                        {
                            "path": path,
                            "method": method,
                            "service_id": service.id,
                            "service_name": service.name,
                        }
                    )

        return total_services, endpoints

    total_services, endpoints = await run_in_threadpool(get_endpoints)

    # Calculate total pages using helper
    pages = pagination.calculate_pages(total_services)

    # Create paginated response
    response = PaginatedResponse(
        items=endpoints,
        total=total_services,
        page=pagination.page,
        per_page=pagination.per_page,
        pages=pages,
    )

    return jsonify(asdict(response)), 200


@bp.route("/<int:id>/sbom/export", methods=["GET"])
@login_required
async def export_service_sbom(id: int):
    """
    Export service SBOM in standard format.

    Path Parameters:
        - id: Service ID

    Query Parameters:
        - format: Export format (cyclonedx_json, cyclonedx_xml, spdx) - default: cyclonedx_json

    Returns:
        200: SBOM file content
        400: Invalid format or no components
        404: Service not found

    Example:
        GET /api/v1/services/1/sbom/export?format=cyclonedx_json
        GET /api/v1/services/1/sbom/export?format=cyclonedx_xml
        GET /api/v1/services/1/sbom/export?format=spdx
    """
    db = current_app.db

    # Validate service exists
    service, error = await validate_resource_exists(db.services, id, "Service")
    if error:
        return error

    # Get format parameter
    export_format = request.args.get("format", "cyclonedx_json")
    supported_formats = ["cyclonedx_json", "cyclonedx_xml", "spdx"]

    if export_format not in supported_formats:
        return ApiResponse.error(
            f"Invalid format. Supported: {', '.join(supported_formats)}", 400
        )

    def get_components():
        query = (db.sbom_components.parent_type == "service") & (
            db.sbom_components.parent_id == id
        )
        rows = db(query).select(orderby=db.sbom_components.name)
        # Convert rows to dictionaries
        return [dict(row) for row in rows]

    components = await run_in_threadpool(get_components)

    if not components:
        return ApiResponse.error("No SBOM components found for this service", 400)

    # Build metadata
    metadata = {
        "name": service.name,
        "version": getattr(service, "version", "unknown"),
        "description": service.description,
    }

    # Export based on format
    try:
        if export_format == "cyclonedx_json":
            exporter = CycloneDXExporter()
            content = exporter.export_json(components, metadata)
            mimetype = "application/json"
            filename = f"{service.name.replace(' ', '_')}_sbom_cyclonedx.json"

        elif export_format == "cyclonedx_xml":
            exporter = CycloneDXExporter()
            content = exporter.export_xml(components, metadata)
            mimetype = "application/xml"
            filename = f"{service.name.replace(' ', '_')}_sbom_cyclonedx.xml"

        else:  # spdx
            exporter = SPDXExporter()
            content = exporter.export_json(components, metadata)
            mimetype = "application/json"
            filename = f"{service.name.replace(' ', '_')}_sbom_spdx.json"

        # Return as downloadable file
        return Response(
            content,
            mimetype=mimetype,
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    except Exception as e:
        return ApiResponse.error(f"Failed to export SBOM: {str(e)}", 500)
