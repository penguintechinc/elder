"""SBOM scans management API endpoints for Elder using PyDAL with async/await and shared helpers."""

# flake8: noqa: E501


import fnmatch
from dataclasses import asdict

import structlog
from flask import Blueprint, current_app, jsonify, request
from pydantic import ValidationError

from apps.api.auth.decorators import login_required, resource_role_required
from apps.api.models.dataclasses import (
    PaginatedResponse,
    SBOMScanDTO,
    from_pydal_row,
    from_pydal_rows,
)
from apps.api.services.sbom.parsers import SBOMParser
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.pydal_helpers import PaginationParams
from apps.api.utils.validation_helpers import validate_resource_exists
from apps.api.utils.async_utils import run_in_threadpool
from penguin_libs.pydantic.flask_integration import (
    ValidationErrorResponse,
    validate_body,
)
from apps.api.models.pydantic.sbom import (
    CreateSBOMScanRequest,
    SubmitSBOMResultsRequest,
    UploadSBOMRequest,
)

bp = Blueprint("sbom_scans", __name__)
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


def _resolve_credential(
    db, credential_type: str, credential_id: int, credential_mapping: dict
) -> str:
    """
    Resolve a credential to extract the authentication token.

    Args:
        db: Database connection
        credential_type: Type of credential (only "builtin_secret" is supported)
        credential_id: ID of the credential record
        credential_mapping: Mapping dict to extract token from secret_json (default field: "token")

    Returns:
        Token string if found and active, None otherwise
    """
    # Only handle builtin_secret type
    if credential_type != "builtin_secret" or not credential_id:
        return None

    # Look up the builtin secret
    secret = db.builtin_secrets[credential_id]
    if not secret or not secret.is_active:
        return None

    # Extract token from secret_json using credential_mapping
    if not credential_mapping:
        credential_mapping = {}

    field_name = credential_mapping.get("field", "token")
    secret_json = secret.secret_json or {}

    return secret_json.get(field_name)


def _check_component_against_policy(component: dict, policy: dict) -> dict:
    """
    Check a component against a license policy.

    Args:
        component: Component dict with license_name field
        policy: Policy dict with allowed_licenses, denied_licenses, action

    Returns:
        Violation dict if policy is violated, None otherwise
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
async def list_scans():
    """
    List SBOM scans with optional filtering.

    Query Parameters:
        - parent_type: Filter by parent type (service, software)
        - parent_id: Filter by parent ID
        - status: Filter by status (pending, running, completed, failed)
        - scan_type: Filter by scan type (spdx, cyclonedx, swid, other)
        - page: Page number (default: 1)
        - per_page: Items per page (default: 50)

    Returns:
        200: List of scans with pagination
        400: Invalid parameters

    Example:
        GET /api/v1/sbom/scans?parent_type=service&status=completed
    """
    db = current_app.db

    # Get pagination params using helper
    pagination = PaginationParams.from_request()

    # Build query
    def get_scans():
        query = db.sbom_scans.id > 0

        # Apply filters
        if request.args.get("parent_type"):
            query &= db.sbom_scans.parent_type == request.args.get("parent_type")

        if request.args.get("parent_id"):
            parent_id = request.args.get("parent_id", type=int)
            query &= db.sbom_scans.parent_id == parent_id

        if request.args.get("status"):
            query &= db.sbom_scans.status == request.args.get("status")

        if request.args.get("scan_type"):
            query &= db.sbom_scans.scan_type == request.args.get("scan_type")

        # Get count and rows
        total = db(query).count()
        rows = db(query).select(
            orderby=~db.sbom_scans.created_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )

        return total, rows

    total, rows = await run_in_threadpool(get_scans)

    # Calculate total pages using helper
    pages = pagination.calculate_pages(total)

    # Convert to DTOs
    items = from_pydal_rows(rows, SBOMScanDTO)

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
async def create_scan():
    """
    Create/trigger a new SBOM scan.

    Requires viewer role on the resource.

    Request Body:
        - parent_type: Parent type (service, software) - required
        - parent_id: Parent ID - required
        - scan_type: Scan type (manifest, lockfile, repository, container, binary, source) - required
        - repository_url: Repository URL (optional, will be fetched from parent if not provided)
        - repository_branch: Repository branch (optional)

    Returns:
        201: Scan created with status=pending
        400: Invalid request
        403: Insufficient permissions

    Example:
        POST /api/v1/sbom/scans
    """
    db = current_app.db

    # Validate request body using Pydantic
    try:
        body = validate_body(CreateSBOMScanRequest)
    except ValidationError as e:
        return ValidationErrorResponse.from_pydantic_error(e)

    parent_type = body.parent_type
    parent_id = body.parent_id

    # Validate parent exists (service or software)
    def validate_parent():
        if parent_type == "service":
            return db.services[parent_id]
        elif parent_type == "software":
            return db.software[parent_id]
        return None

    parent = await run_in_threadpool(validate_parent)
    if not parent:
        return ApiResponse.not_found(parent_type, parent_id)

    # Get repository_url from parent if not provided
    repository_url = body.repository_url
    if not repository_url:
        if parent_type == "service" and parent.repository_url:
            repository_url = parent.repository_url
        elif (
            parent_type == "software"
            and hasattr(parent, "repository_url")
            and parent.repository_url
        ):
            repository_url = parent.repository_url

    def create():
        # Create scan record with status=pending
        scan_id = db.sbom_scans.insert(
            parent_type=parent_type,
            parent_id=parent_id,
            scan_type=body.scan_type,
            status="pending",
            repository_url=repository_url,
            repository_branch=body.repository_branch,
            components_found=0,
            components_added=0,
            components_updated=0,
            components_removed=0,
        )
        db.commit()

        return db.sbom_scans[scan_id]

    scan = await run_in_threadpool(create)

    scan_dto = from_pydal_row(scan, SBOMScanDTO)
    return ApiResponse.created(asdict(scan_dto))


@bp.route("/<int:id>", methods=["GET"])
@login_required
async def get_scan(id: int):
    """
    Get a single SBOM scan by ID.

    Path Parameters:
        - id: Scan ID

    Returns:
        200: Scan details
        404: Scan not found

    Example:
        GET /api/v1/sbom/scans/1
    """
    db = current_app.db

    # Validate resource exists using helper
    scan, error = await validate_resource_exists(db.sbom_scans, id, "SBOM Scan")
    if error:
        return error

    scan_dto = from_pydal_row(scan, SBOMScanDTO)
    return ApiResponse.success(asdict(scan_dto))


@bp.route("/<int:id>", methods=["DELETE"])
@login_required
@resource_role_required("maintainer")
async def delete_scan(id: int):
    """
    Delete an SBOM scan record.

    Requires maintainer role.

    Path Parameters:
        - id: Scan ID

    Returns:
        204: Scan deleted
        403: Insufficient permissions
        404: Scan not found

    Example:
        DELETE /api/v1/sbom/scans/1
    """
    db = current_app.db

    # Validate resource exists using helper
    scan, error = await validate_resource_exists(db.sbom_scans, id, "SBOM Scan")
    if error:
        return error

    def delete():
        del db.sbom_scans[id]
        db.commit()

    await run_in_threadpool(delete)

    return ApiResponse.no_content()


@bp.route("/pending", methods=["GET"])
@login_required
async def get_pending_scans():
    """
    Get pending SBOM scans (for scanner worker).

    This endpoint is used by the scanner worker to fetch all pending scans
    that need to be processed. Results are not paginated since the worker
    needs all pending scans to process them efficiently.

    Returns:
        200: List of pending scans (ordered by created_at ascending, oldest first)

    Example:
        GET /api/v1/sbom/scans/pending
    """
    db = current_app.db

    def get_scans():
        query = db.sbom_scans.status == "pending"
        rows = db(query).select(orderby=db.sbom_scans.created_at)
        return rows

    rows = await run_in_threadpool(get_scans)

    # Convert to DTOs
    items = from_pydal_rows(rows, SBOMScanDTO)

    # Resolve credentials for each scan and add to response
    scan_dicts = []
    for item in items:
        scan_dict = asdict(item)

        # Resolve credential if present
        if item.credential_type and item.credential_id:
            token = _resolve_credential(
                db,
                item.credential_type,
                item.credential_id,
                item.credential_mapping or {},
            )
            if token:
                scan_dict["_resolved_token"] = token

        # Remove sensitive credential fields from response
        scan_dict.pop("credential_id", None)
        scan_dict.pop("credential_mapping", None)

        scan_dicts.append(scan_dict)

    # Return list directly (not paginated)
    return jsonify(scan_dicts), 200


@bp.route("/<int:id>/start", methods=["POST"])
@login_required
async def start_scan(id: int):
    """
    Mark an SBOM scan as running (for scanner worker).

    This endpoint is called by the scanner worker when it begins processing
    a scan job.

    Path Parameters:
        - id: Scan ID

    Returns:
        200: Scan marked as running
        404: Scan not found
        400: Scan already running or completed

    Example:
        POST /api/v1/sbom/scans/1/start
    """
    db = current_app.db

    # Validate scan exists
    scan, error = await validate_resource_exists(db.sbom_scans, id, "SBOM Scan")
    if error:
        return error

    # Verify scan is pending
    if scan.status != "pending":
        return ApiResponse.error(f"Scan is not pending (status: {scan.status})", 400)

    def mark_running():
        db.sbom_scans[id] = dict(
            status="running",
            started_at=request.utcnow,
        )
        db.commit()
        return db.sbom_scans[id]

    updated_scan = await run_in_threadpool(mark_running)

    scan_dto = from_pydal_row(updated_scan, SBOMScanDTO)
    return ApiResponse.success(asdict(scan_dto))


@bp.route("/<int:id>/results", methods=["POST"])
@login_required
async def submit_results(id: int):
    """
    Submit SBOM scan results (for scanner worker).

    This endpoint is called by the scanner worker to submit the results
    of a completed scan job.

    Path Parameters:
        - id: Scan ID

    Request Body:
        - success: Boolean indicating if scan succeeded - required
        - components: List of component dicts to insert - optional
        - files_scanned: List of files scanned - optional
        - commit_hash: Git commit hash - optional
        - error_message: Error message if failed - optional
        - scan_duration_ms: Scan duration in milliseconds - optional

    Returns:
        200: Results processed
        400: Invalid request
        404: Scan not found

    Example:
        POST /api/v1/sbom/scans/1/results
        {
            "success": true,
            "components": [...],
            "files_scanned": ["package.json", "requirements.txt"],
            "commit_hash": "abc123",
            "scan_duration_ms": 5432
        }
    """
    db = current_app.db

    # Validate request body using Pydantic
    try:
        body = validate_body(SubmitSBOMResultsRequest)
    except ValidationError as e:
        return ValidationErrorResponse.from_pydantic_error(e)

    # Validate scan exists
    scan, error = await validate_resource_exists(db.sbom_scans, id, "SBOM Scan")
    if error:
        return error

    success = body.success
    components = body.components or []
    files_scanned = body.files_scanned or []
    commit_hash = body.commit_hash
    error_message = body.error_message
    scan_duration_ms = body.scan_duration_ms

    def process_results():
        # Get existing components for this parent
        existing_query = (db.sbom_components.parent_type == scan.parent_type) & (
            db.sbom_components.parent_id == scan.parent_id
        )
        existing_components = db(existing_query).select()
        existing_map = {(c.name, c.version): c for c in existing_components}

        components_added = 0
        components_updated = 0
        component_ids = []

        # Process each component
        for comp in components:
            key = (comp.get("name"), comp.get("version"))

            if key in existing_map:
                # Update existing component
                existing = existing_map[key]
                db.sbom_components[existing.id] = {
                    "package_type": comp.get("package_type"),
                    "purl": comp.get("purl"),
                    "scope": comp.get("scope", "runtime"),
                    "direct": comp.get("direct", True),
                    "license_id": comp.get("license_id"),
                    "license_name": comp.get("license_name"),
                    "source_file": comp.get("source_file"),
                    "repository_url": comp.get("repository_url"),
                    "homepage_url": comp.get("homepage_url"),
                    "description": comp.get("description"),
                    "metadata": comp.get("metadata"),
                }
                components_updated += 1
                component_ids.append(existing.id)
            else:
                # Insert new component
                comp_id = db.sbom_components.insert(
                    parent_type=scan.parent_type,
                    parent_id=scan.parent_id,
                    name=comp["name"],
                    version=comp.get("version"),
                    purl=comp.get("purl"),
                    package_type=comp["package_type"],
                    scope=comp.get("scope", "runtime"),
                    direct=comp.get("direct", True),
                    license_id=comp.get("license_id"),
                    license_name=comp.get("license_name"),
                    source_file=comp.get("source_file"),
                    repository_url=comp.get("repository_url"),
                    homepage_url=comp.get("homepage_url"),
                    description=comp.get("description"),
                    metadata=comp.get("metadata"),
                )
                components_added += 1
                component_ids.append(comp_id)

        # Check components against active license policies
        # Get the organization_id from the parent (service or software)
        parent_org_id = None
        if scan.parent_type == "service":
            parent_row = db.services[scan.parent_id]
            if parent_row:
                parent_org_id = parent_row.organization_id
        elif scan.parent_type == "software":
            parent_row = db.software[scan.parent_id]
            if parent_row:
                parent_org_id = parent_row.organization_id

        # Check policies if we have an organization
        violations = []
        if parent_org_id:
            # Get active policies for this organization
            policy_query = (db.license_policies.organization_id == parent_org_id) & (
                db.license_policies.is_active is True
            )
            policies = db(policy_query).select()

            # Check each component against each policy
            for comp in components:
                for policy in policies:
                    policy_dict = policy.as_dict()
                    violation = _check_component_against_policy(comp, policy_dict)
                    if violation:
                        violations.append(violation)

        # Log violations
        if violations:
            logger.warning(
                "sbom_scan_license_violations",
                scan_id=id,
                parent_type=scan.parent_type,
                parent_id=scan.parent_id,
                violation_count=len(violations),
                violations=violations,
            )

        # Update scan record
        status = "completed" if success else "failed"
        db.sbom_scans[id] = dict(
            status=status,
            files_scanned=files_scanned,
            commit_hash=commit_hash,
            components_found=len(components),
            components_added=components_added,
            components_updated=components_updated,
            error_message=error_message,
            scan_duration_ms=scan_duration_ms,
            completed_at=request.utcnow,
        )

        db.commit()
        return db.sbom_scans[id], component_ids

    updated_scan, component_ids = await run_in_threadpool(process_results)

    # Trigger vulnerability matching for all components (async, non-blocking)
    if success and component_ids:
        # Import here to avoid circular dependency
        from apps.api.services.sbom.vulnerability.matcher import VulnerabilityMatcher

        async def match_vulnerabilities():
            """Background task to match vulnerabilities."""
            try:
                # Build component list for batch matching
                def get_component_data():
                    components_query = db.sbom_components.id.belongs(component_ids)
                    components_rows = db(components_query).select()

                    component_list = []
                    for comp in components_rows:
                        if comp.purl:  # Only match components with PURL
                            component_list.append(
                                {
                                    "id": comp.id,
                                    "purl": comp.purl,
                                    "package_type": comp.package_type,
                                    "name": comp.name,
                                    "version": comp.version,
                                }
                            )
                    return component_list

                component_list = await run_in_threadpool(get_component_data)

                if not component_list:
                    return

                # Perform vulnerability matching
                async with VulnerabilityMatcher() as matcher:
                    results = await matcher.match_components_batch(component_list)

                    # Save results to database
                    def save_vulnerabilities():
                        tenant_id = current_app.config.get("DEFAULT_TENANT_ID", 1)

                        for comp_id, vulns in results.items():
                            for vuln in vulns:
                                # Check if vulnerability already exists
                                existing_vuln = (
                                    db(
                                        (db.vulnerabilities.cve_id == vuln.cve_id)
                                        & (db.vulnerabilities.tenant_id == tenant_id)
                                    )
                                    .select()
                                    .first()
                                )

                                if existing_vuln:
                                    vuln_id = existing_vuln.id
                                else:
                                    # Insert new vulnerability
                                    vuln_id = db.vulnerabilities.insert(
                                        tenant_id=tenant_id,
                                        cve_id=vuln.cve_id,
                                        aliases=vuln.aliases,
                                        severity=vuln.severity,
                                        cvss_score=vuln.cvss_score,
                                        cvss_vector=vuln.cvss_vector,
                                        title=vuln.title,
                                        description=vuln.description,
                                        affected_packages=vuln.affected_packages,
                                        fixed_versions=vuln.fixed_versions,
                                        references=vuln.references,
                                        source=vuln.source,
                                        is_active=True,
                                    )

                                # Check if component-vulnerability link exists
                                existing_link = (
                                    db(
                                        (
                                            db.component_vulnerabilities.component_id
                                            == comp_id
                                        )
                                        & (
                                            db.component_vulnerabilities.vulnerability_id
                                            == vuln_id
                                        )
                                    )
                                    .select()
                                    .first()
                                )

                                if not existing_link:
                                    # Create component-vulnerability link
                                    db.component_vulnerabilities.insert(
                                        tenant_id=tenant_id,
                                        component_id=comp_id,
                                        vulnerability_id=vuln_id,
                                        status="open",
                                    )

                        db.commit()

                    await run_in_threadpool(save_vulnerabilities)

            except Exception as e:
                # Log error but don't fail the scan
                import structlog

                logger = structlog.get_logger(__name__)
                logger.error(
                    "vulnerability_matching_failed",
                    scan_id=id,
                    error=str(e),
                )

        # Schedule vulnerability matching (fire and forget)
        import asyncio

        asyncio.create_task(match_vulnerabilities())

    scan_dto = from_pydal_row(updated_scan, SBOMScanDTO)
    return ApiResponse.success(asdict(scan_dto))


@bp.route("/upload", methods=["POST"])
@login_required
@resource_role_required("viewer")
async def upload_sbom():
    """
    Upload and import an SBOM file (CycloneDX or SPDX).

    Requires viewer role on the resource.

    Request Body:
        - parent_type: Parent type (service, software) - required
        - parent_id: Parent ID - required
        - file_content: SBOM file content as string - required
        - filename: Original filename - required

    Returns:
        201: SBOM imported successfully with scan record
        400: Invalid request or unsupported format
        403: Insufficient permissions
        404: Parent not found

    Example:
        POST /api/v1/sbom/scans/upload
        {
            "parent_type": "service",
            "parent_id": 1,
            "file_content": "...",
            "filename": "cyclonedx.json"
        }
    """
    db = current_app.db

    # Validate request body using Pydantic
    try:
        body = validate_body(UploadSBOMRequest)
    except ValidationError as e:
        return ValidationErrorResponse.from_pydantic_error(e)

    parent_type = body.parent_type
    parent_id = body.parent_id
    file_content = body.file_content
    filename = body.filename

    # Validate parent exists
    def validate_parent():
        if parent_type == "service":
            return db.services[parent_id]
        elif parent_type == "software":
            return db.software[parent_id]
        return None

    parent = await run_in_threadpool(validate_parent)
    if not parent:
        return ApiResponse.not_found(parent_type, parent_id)

    # Parse SBOM file
    parser = SBOMParser()

    if not parser.can_parse(filename):
        return ApiResponse.error(
            f"Unsupported SBOM file format. Filename: {filename}. "
            "Supported formats: CycloneDX (JSON/XML), SPDX (JSON)",
            400,
        )

    try:
        components = parser.parse(file_content, filename)
    except ValueError as e:
        return ApiResponse.error(f"Failed to parse SBOM file: {str(e)}", 400)
    except Exception as e:
        logger.error(
            "sbom_upload_parse_error",
            filename=filename,
            parent_type=parent_type,
            parent_id=parent_id,
            error=str(e),
        )
        return ApiResponse.error(f"Unexpected error parsing SBOM: {str(e)}", 500)

    if not components:
        return ApiResponse.error("No components found in SBOM file", 400)

    # Create scan record and import components
    def import_components():
        # Create scan record
        scan_id = db.sbom_scans.insert(
            parent_type=parent_type,
            parent_id=parent_id,
            scan_type="sbom_import",
            status="running",
            files_scanned=[filename],
            components_found=len(components),
            components_added=0,
            components_updated=0,
            components_removed=0,
            started_at=request.utcnow,
        )

        # Get existing components for this parent
        existing_query = (db.sbom_components.parent_type == parent_type) & (
            db.sbom_components.parent_id == parent_id
        )
        existing_components = db(existing_query).select()
        existing_map = {(c.name, c.version): c for c in existing_components}

        components_added = 0
        components_updated = 0

        # Process each component
        for comp in components:
            key = (comp.get("name"), comp.get("version"))

            if key in existing_map:
                # Update existing component
                existing = existing_map[key]
                db.sbom_components[existing.id] = {
                    "package_type": comp.get("package_type"),
                    "purl": comp.get("purl"),
                    "scope": comp.get("scope", "runtime"),
                    "direct": comp.get("direct", True),
                    "license_id": comp.get("license_id"),
                    "license_name": comp.get("license_name"),
                    "license_url": comp.get("license_url"),
                    "source_file": filename,
                    "repository_url": comp.get("repository_url"),
                    "homepage_url": comp.get("homepage_url"),
                    "description": comp.get("description"),
                    "hash_sha256": comp.get("hash_sha256"),
                    "hash_sha512": comp.get("hash_sha512"),
                    "metadata": comp.get("metadata"),
                }
                components_updated += 1
            else:
                # Insert new component
                db.sbom_components.insert(
                    parent_type=parent_type,
                    parent_id=parent_id,
                    name=comp["name"],
                    version=comp.get("version", "unknown"),
                    purl=comp.get("purl"),
                    package_type=comp.get("package_type", "unknown"),
                    scope=comp.get("scope", "runtime"),
                    direct=comp.get("direct", True),
                    license_id=comp.get("license_id"),
                    license_name=comp.get("license_name"),
                    license_url=comp.get("license_url"),
                    source_file=filename,
                    repository_url=comp.get("repository_url"),
                    homepage_url=comp.get("homepage_url"),
                    description=comp.get("description"),
                    hash_sha256=comp.get("hash_sha256"),
                    hash_sha512=comp.get("hash_sha512"),
                    metadata=comp.get("metadata"),
                )
                components_added += 1

        # Update scan record
        db.sbom_scans[scan_id] = dict(
            status="completed",
            components_added=components_added,
            components_updated=components_updated,
            completed_at=request.utcnow,
        )

        db.commit()
        return db.sbom_scans[scan_id]

    scan = await run_in_threadpool(import_components)

    scan_dto = from_pydal_row(scan, SBOMScanDTO)
    return ApiResponse.created(
        {
            "message": f"SBOM imported successfully. Added {scan.components_added} new components, "
            f"updated {scan.components_updated} existing components.",
            "scan": asdict(scan_dto),
        }
    )
