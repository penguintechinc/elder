"""Vulnerabilities management API endpoints for Elder using PyDAL with async/await and shared helpers."""

# flake8: noqa: E501

import math
from dataclasses import asdict
from datetime import UTC, datetime

from pydantic import ValidationError
from quart import Blueprint, current_app, g, jsonify, request

from apps.api.auth.decorators import (
    login_required,
    require_scope,
    resource_role_required,
    role_required,
)
from apps.api.models.dataclasses import (
    ComponentVulnerabilityDTO,
    PaginatedResponse,
    VulnerabilityDTO,
    from_pydal_row,
    from_pydal_rows,
)
from apps.api.models.pydantic.vulnerability import (
    AssignVulnerabilityRequest,
    NVDSyncRequest,
    SyncVulnerabilitiesRequest,
    UpdateComponentVulnerabilityRequest,
)
from apps.api.services.sbom.vulnerability.matcher import VulnerabilityMatcher
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.pydal_helpers import PaginationParams
from apps.api.utils.quart_validation import ValidationErrorResponse
from apps.api.utils.tenant_scoping import get_current_tenant_id, get_tenant_scoped
from apps.api.utils.validation_helpers import validate_resource_exists

bp = Blueprint("vulnerabilities", __name__)

_SEVERITY_TO_ISSUE_PRIORITY = {
    "CRITICAL": "CRITICAL",
    "HIGH": "HIGH",
    "MEDIUM": "MEDIUM",
    "LOW": "LOW",
}


def _parse_optional_positive_int(
    raw_value, field_name: str
) -> tuple[int | None, str | None]:
    """Parse an optional positive integer ID (None or '' clears the field)."""
    if raw_value is None or raw_value == "":
        return None, None
    if isinstance(raw_value, bool):
        return None, f"{field_name} must be a positive integer"
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        return None, f"{field_name} must be a positive integer"
    if parsed <= 0:
        return None, f"{field_name} must be a positive integer"
    return parsed, None


def _parse_optional_cvss_score(raw_value) -> tuple[float | None, str | None]:
    """Parse an optional CVSS score in [0.0, 10.0] (None or '' clears the field)."""
    if raw_value is None or raw_value == "":
        return None, None
    if isinstance(raw_value, bool):
        return None, "cvss_score must be a valid number"
    try:
        parsed = float(raw_value)
    except (TypeError, ValueError):
        return None, "cvss_score must be a valid number"
    if not math.isfinite(parsed) or parsed < 0.0 or parsed > 10.0:
        return None, "cvss_score must be between 0.0 and 10.0"
    return parsed, None


def _parse_optional_adjusted_risk_score(raw_value) -> tuple[str | None, str | None]:
    """Parse an optional free-text adjusted risk score (trimmed, max 64 chars)."""
    if raw_value is None:
        return None, None
    if isinstance(raw_value, bool) or not isinstance(raw_value, (str, int, float)):
        return None, "adjusted_risk_score must be a string (max 64 chars)"
    trimmed = str(raw_value).strip()
    if not trimmed:
        return None, None
    if len(trimmed) > 64:
        return None, "adjusted_risk_score must be at most 64 characters"
    return trimmed, None


def _serialize_issue_summary(issue_row) -> dict | None:
    """Return a compact remediation issue summary dict."""
    if not issue_row:
        return None
    raw_status = getattr(issue_row, "status", None)
    raw_priority = getattr(issue_row, "priority", None)
    return {
        "id": issue_row.id,
        "title": issue_row.title,
        "status": str(raw_status).lower() if raw_status is not None else "open",
        "priority": str(raw_priority).lower() if raw_priority is not None else "medium",
        "village_id": getattr(issue_row, "village_id", None),
    }


def _validate_ownership_refs(
    db,
    tenant_id: int,
    *,
    owner_organization_id: int | None = None,
    owner_entity_id: int | None = None,
    remediation_issue_id: int | None = None,
) -> tuple[str | None, int | None]:
    """Validate that non-null ownership and remediation issue IDs belong to tenant_id."""
    if owner_organization_id is not None:
        org = get_tenant_scoped(db, db.organizations, owner_organization_id, tenant_id)
        if not org:
            return "Owner organization not found", 404
    if owner_entity_id is not None:
        entity = get_tenant_scoped(
            db, db.entities, owner_entity_id, tenant_id, org_fk="organization_id"
        )
        if not entity:
            return "Owner entity not found", 404
    if remediation_issue_id is not None:
        issue = get_tenant_scoped(db, db.issues, remediation_issue_id, tenant_id)
        if not issue:
            return "Remediation issue not found", 404
    return None, None


def _enrich_vulnerability_dicts(
    db,
    items: list[VulnerabilityDTO],
    tenant_id: int | None,
    affected_map: dict[int, list[dict]] | None = None,
) -> list[dict]:
    """Enrich VulnerabilityDTO items with tenant-scoped owner names and remediation issue summaries."""
    org_ids = {
        item.owner_organization_id
        for item in items
        if item.owner_organization_id is not None
    }
    entity_ids = {
        item.owner_entity_id for item in items if item.owner_entity_id is not None
    }
    issue_ids = {
        item.remediation_issue_id
        for item in items
        if item.remediation_issue_id is not None
    }

    org_names: dict[int, str] = {}
    entity_names: dict[int, str] = {}
    issue_summaries: dict[int, dict] = {}

    if tenant_id is not None:
        if org_ids:
            org_rows = db(
                db.organizations.id.belongs(list(org_ids))
                & (db.organizations.tenant_id == tenant_id)
            ).select()
            org_names = {row.id: row.name for row in org_rows}

        if entity_ids:
            entity_rows = db(db.entities.id.belongs(list(entity_ids))).select()
            entity_org_ids = {
                row.organization_id
                for row in entity_rows
                if getattr(row, "organization_id", None) is not None
            }
            allowed_org_ids: set[int] = set()
            if entity_org_ids:
                allowed_orgs = db(
                    db.organizations.id.belongs(list(entity_org_ids))
                    & (db.organizations.tenant_id == tenant_id)
                ).select()
                allowed_org_ids = {row.id for row in allowed_orgs}
            entity_names = {
                row.id: row.name
                for row in entity_rows
                if getattr(row, "organization_id", None) in allowed_org_ids
            }

        if issue_ids:
            issue_rows = db(
                db.issues.id.belongs(list(issue_ids))
                & (db.issues.tenant_id == tenant_id)
            ).select()
            issue_summaries = {
                row.id: _serialize_issue_summary(row) for row in issue_rows
            }

    enriched: list[dict] = []
    for item in items:
        item_dict = asdict(item)
        if item_dict.get("cvss_score") is not None:
            item_dict["cvss_score"] = float(item_dict["cvss_score"])
        item_dict["owner_organization_name"] = (
            org_names.get(item.owner_organization_id)
            if item.owner_organization_id is not None
            else None
        )
        item_dict["owner_entity_name"] = (
            entity_names.get(item.owner_entity_id)
            if item.owner_entity_id is not None
            else None
        )
        item_dict["remediation_issue"] = (
            issue_summaries.get(item.remediation_issue_id)
            if item.remediation_issue_id is not None
            else None
        )
        if affected_map is not None:
            item_dict["affected_entities"] = affected_map.get(item.id, [])
        enriched.append(item_dict)

    return enriched


@bp.route("", methods=["GET"])
@login_required
@require_scope("sbom:read")
async def list_vulnerabilities():
    """
    List vulnerabilities with optional filtering.

    Query Parameters:
        - severity: Filter by severity (critical, high, medium, low, unknown)
        - cve_id: Filter by CVE ID
        - source: Filter by source (osv, nvd, github_advisory, manual)
        - owner_organization_id: Filter by owning organization (OU) ID
        - owner_entity_id: Filter by owning resource (entity) ID
        - page: Page number (default: 1)
        - per_page: Items per page (default: 50)
        - search: Search in CVE ID, title, and description

    Returns:
        200: List of vulnerabilities with pagination
        400: Invalid parameters

    Example:
        GET /api/v1/vulnerabilities?severity=critical
    """
    db = current_app.db
    tenant_id = get_current_tenant_id()

    owner_org_filter, org_filter_err = _parse_optional_positive_int(
        request.args.get("owner_organization_id"), "owner_organization_id"
    )
    if org_filter_err:
        return ApiResponse.error(org_filter_err, 400)

    owner_entity_filter, entity_filter_err = _parse_optional_positive_int(
        request.args.get("owner_entity_id"), "owner_entity_id"
    )
    if entity_filter_err:
        return ApiResponse.error(entity_filter_err, 400)

    # Get pagination params using helper
    pagination = PaginationParams.from_request()

    # Build query
    def get_vulnerabilities():
        query = db.vulnerabilities.id > 0

        # Apply filters
        if request.args.get("severity"):
            query &= db.vulnerabilities.severity == request.args.get("severity").upper()

        if request.args.get("cve_id"):
            query &= db.vulnerabilities.cve_id == request.args.get("cve_id").upper()

        if request.args.get("source"):
            query &= db.vulnerabilities.source == request.args.get("source")

        if owner_org_filter is not None:
            query &= db.vulnerabilities.owner_organization_id == owner_org_filter

        if owner_entity_filter is not None:
            query &= db.vulnerabilities.owner_entity_id == owner_entity_filter

        if request.args.get("search"):
            search = request.args.get("search")
            search_pattern = f"%{search}%"
            query &= (
                (db.vulnerabilities.cve_id.ilike(search_pattern))
                | (db.vulnerabilities.title.ilike(search_pattern))
                | (db.vulnerabilities.description.ilike(search_pattern))
            )

        # Get count and rows
        total = db(query).count()
        rows = db(query).select(
            orderby=~db.vulnerabilities.created_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )

        return total, rows

    total, rows = await run_in_threadpool(get_vulnerabilities)

    # Calculate total pages using helper
    pages = pagination.calculate_pages(total)

    # Convert to DTOs
    items = from_pydal_rows(rows, VulnerabilityDTO)

    # Batch-query affected entities, owner names, and remediation issues
    vuln_ids = [item.id for item in items]

    def enrich_list():
        affected = {}
        if vuln_ids:
            # Query component_vulnerabilities for these vulnerability IDs,
            # scoped to the caller's tenant (gh-237) -- without this filter,
            # every tenant's linked sbom_components (and the service/software
            # names they belong to) leaked through this enrichment step.
            cv_rows = db(
                db.component_vulnerabilities.vulnerability_id.belongs(vuln_ids)
                & (db.component_vulnerabilities.tenant_id == tenant_id)
            ).select()

            for cv in cv_rows:
                vid = cv.vulnerability_id
                comp_id = cv.component_id

                # Look up the sbom_component to get parent info, scoped to
                # caller's tenant (gh-237)
                comp = get_tenant_scoped(db, db.sbom_components, comp_id, tenant_id)
                if not comp:
                    continue

                parent_type = comp.parent_type
                parent_id = comp.parent_id
                source_file = comp.source_file if hasattr(comp, "source_file") else None
                parent_name = None

                if parent_type == "service" and parent_id:
                    svc = get_tenant_scoped(db, db.services, parent_id, tenant_id)
                    parent_name = svc.name if svc else None
                elif parent_type == "software" and parent_id:
                    sw = get_tenant_scoped(db, db.software, parent_id, tenant_id)
                    parent_name = sw.name if sw else None

                if parent_name:
                    if vid not in affected:
                        affected[vid] = []
                    affected[vid].append(
                        {
                            "parent_type": parent_type,
                            "parent_name": parent_name,
                            "source_file": source_file,
                        }
                    )

        return _enrich_vulnerability_dicts(db, items, tenant_id, affected_map=affected)

    enriched_items = await run_in_threadpool(enrich_list)

    # Create paginated response
    response = PaginatedResponse(
        items=enriched_items,
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
        pages=pages,
    )

    return jsonify(asdict(response)), 200


@bp.route("", methods=["POST"])
@login_required
@require_scope("sbom:write")
async def create_vulnerability():
    """Manually create a vulnerability entry."""
    db = current_app.db
    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return ApiResponse.error("Tenant context is required", 403)

    data = await request.get_json()
    if not isinstance(data, dict):
        return ApiResponse.error("Invalid JSON body", 400)

    cve_id = (data.get("cve_id") or "").strip().upper()
    title = (data.get("title") or "").strip()
    severity = (data.get("severity") or "").strip().upper()

    if not cve_id or len(cve_id) > 50:
        return ApiResponse.error("cve_id is required (max 50 chars)", 400)
    if not title or len(title) > 512:
        return ApiResponse.error("title is required (max 512 chars)", 400)
    if severity not in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"):
        return ApiResponse.error(
            "severity must be one of: critical, high, medium, low, info", 400
        )

    cvss_score, cvss_err = _parse_optional_cvss_score(data.get("cvss_score"))
    if cvss_err:
        return ApiResponse.error(cvss_err, 400)

    adjusted_risk_score, risk_err = _parse_optional_adjusted_risk_score(
        data.get("adjusted_risk_score")
    )
    if risk_err:
        return ApiResponse.error(risk_err, 400)

    owner_organization_id, org_err = _parse_optional_positive_int(
        data.get("owner_organization_id"), "owner_organization_id"
    )
    if org_err:
        return ApiResponse.error(org_err, 400)

    owner_entity_id, entity_err = _parse_optional_positive_int(
        data.get("owner_entity_id"), "owner_entity_id"
    )
    if entity_err:
        return ApiResponse.error(entity_err, 400)

    remediation_issue_id, issue_err = _parse_optional_positive_int(
        data.get("remediation_issue_id"), "remediation_issue_id"
    )
    if issue_err:
        return ApiResponse.error(issue_err, 400)

    published_at = None
    if data.get("published_at"):
        try:
            raw_pub = str(data["published_at"]).strip()
            published_at = datetime.fromisoformat(raw_pub.replace("Z", "+00:00"))
        except ValueError:
            return ApiResponse.error(
                "published_at must be a valid ISO-8601 date or datetime", 400
            )

    redis_client = getattr(current_app, "redis_client", None)

    def create():
        from apps.api.utils.village_id import mint_village_id

        ref_err, ref_status = _validate_ownership_refs(
            db,
            tenant_id,
            owner_organization_id=owner_organization_id,
            owner_entity_id=owner_entity_id,
            remediation_issue_id=remediation_issue_id,
        )
        if ref_err:
            return None, ref_err, ref_status

        existing = db(db.vulnerabilities.cve_id == cve_id).select().first()
        if existing:
            return None, f"Vulnerability '{cve_id}' already exists", 409

        now = datetime.now(UTC)
        vuln_id = db.vulnerabilities.insert(
            tenant_id=tenant_id,
            village_id=mint_village_id(tenant_id, redis_client),
            cve_id=cve_id,
            title=title,
            severity=severity,
            cvss_score=cvss_score,
            adjusted_risk_score=adjusted_risk_score,
            owner_organization_id=owner_organization_id,
            owner_entity_id=owner_entity_id,
            remediation_issue_id=remediation_issue_id,
            description=(data.get("description") or "").strip() or None,
            affected_packages=data.get("affected_packages")
            if isinstance(data.get("affected_packages"), list)
            else None,
            fixed_versions=data.get("fixed_versions")
            if isinstance(data.get("fixed_versions"), list)
            else None,
            references=data.get("references")
            if isinstance(data.get("references"), list)
            else None,
            published_at=published_at,
            source=(data.get("source") or "manual").strip()[:50],
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db.commit()
        row = db.vulnerabilities[vuln_id]  # tenant-scope-exempt
        dto = from_pydal_row(row, VulnerabilityDTO)
        enriched = _enrich_vulnerability_dicts(db, [dto], tenant_id)[0]
        return enriched, None, 201

    enriched, err_msg, status_code = await run_in_threadpool(create)
    if err_msg:
        return ApiResponse.error(err_msg, status_code)

    return jsonify(enriched), 201


@bp.route("/<int:id>", methods=["GET"])
@login_required
@require_scope("sbom:read")
async def get_vulnerability(id: int):
    """
    Get a single vulnerability by ID.

    Path Parameters:
        - id: Vulnerability ID

    Returns:
        200: Vulnerability details
        404: Vulnerability not found

    Example:
        GET /api/v1/vulnerabilities/1
    """
    db = current_app.db
    tenant_id = get_current_tenant_id()

    # tenant-scope-exempt: vulnerabilities is a shared CVE reference
    # dataset (OSV/NVD/GitHub Advisory sourced) -- not tenant-partitioned
    # data, consistent with list_vulnerabilities/get_dashboard/nvd-sync
    # treating it as global. Tenant-owned linkage lives in
    # component_vulnerabilities (see update_component_vulnerability).
    vulnerability, error = await validate_resource_exists(
        db.vulnerabilities, id, "Vulnerability"
    )
    if error:
        return error

    vulnerability_dto = from_pydal_row(vulnerability, VulnerabilityDTO)
    enriched = await run_in_threadpool(
        lambda: _enrich_vulnerability_dicts(db, [vulnerability_dto], tenant_id)[0]
    )
    return ApiResponse.success(enriched)


@bp.route("/<int:id>", methods=["PATCH", "PUT"])
@login_required
@require_scope("sbom:write")
async def update_vulnerability(id: int):
    """
    Update a vulnerability's editable metadata, ownership, CVSS, adjusted risk score, or remediation issue.

    Path Parameters:
        - id: Vulnerability ID

    Request Body (all optional):
        - title: Vulnerability title (max 512 chars)
        - severity: critical, high, medium, low, info, unknown
        - description: Vulnerability description (nullable)
        - cvss_score: Float 0.0-10.0 or null/""
        - adjusted_risk_score: Free-text adjusted risk score (max 64 chars) or null/""
        - owner_organization_id: Owning OU (organization) ID or null/""
        - owner_entity_id: Owning resource (entity) ID or null/""
        - remediation_issue_id: Linked remediation issue ID or null/""
        - published_at: ISO-8601 date/datetime or null/""

    Returns:
        200: Updated vulnerability details
        400: Validation error
        404: Vulnerability or referenced owner/issue not found
    """
    db = current_app.db
    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return ApiResponse.error("Tenant context is required", 403)

    data = await request.get_json()
    if not isinstance(data, dict):
        return ApiResponse.error("Invalid JSON body", 400)

    vulnerability, error = await validate_resource_exists(
        db.vulnerabilities, id, "Vulnerability"
    )
    if error:
        return error

    update_fields: dict = {}

    if "title" in data:
        title = (data.get("title") or "").strip()
        if not title or len(title) > 512:
            return ApiResponse.error("title must be 1-512 characters", 400)
        update_fields["title"] = title

    if "severity" in data:
        severity = (data.get("severity") or "").strip().upper()
        if severity not in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO", "UNKNOWN"):
            return ApiResponse.error(
                "severity must be one of: critical, high, medium, low, info, unknown",
                400,
            )
        update_fields["severity"] = severity

    if "description" in data:
        raw_desc = data.get("description")
        if raw_desc is None:
            update_fields["description"] = None
        else:
            update_fields["description"] = str(raw_desc).strip() or None

    if "cvss_score" in data:
        cvss_score, cvss_err = _parse_optional_cvss_score(data.get("cvss_score"))
        if cvss_err:
            return ApiResponse.error(cvss_err, 400)
        update_fields["cvss_score"] = cvss_score

    if "adjusted_risk_score" in data:
        adjusted_risk, risk_err = _parse_optional_adjusted_risk_score(
            data.get("adjusted_risk_score")
        )
        if risk_err:
            return ApiResponse.error(risk_err, 400)
        update_fields["adjusted_risk_score"] = adjusted_risk

    owner_org_id_to_check = None
    if "owner_organization_id" in data:
        owner_org_id, org_err = _parse_optional_positive_int(
            data.get("owner_organization_id"), "owner_organization_id"
        )
        if org_err:
            return ApiResponse.error(org_err, 400)
        update_fields["owner_organization_id"] = owner_org_id
        owner_org_id_to_check = owner_org_id

    owner_entity_id_to_check = None
    if "owner_entity_id" in data:
        owner_entity_id, entity_err = _parse_optional_positive_int(
            data.get("owner_entity_id"), "owner_entity_id"
        )
        if entity_err:
            return ApiResponse.error(entity_err, 400)
        update_fields["owner_entity_id"] = owner_entity_id
        owner_entity_id_to_check = owner_entity_id

    remediation_issue_id_to_check = None
    if "remediation_issue_id" in data:
        rem_issue_id, issue_err = _parse_optional_positive_int(
            data.get("remediation_issue_id"), "remediation_issue_id"
        )
        if issue_err:
            return ApiResponse.error(issue_err, 400)
        update_fields["remediation_issue_id"] = rem_issue_id
        remediation_issue_id_to_check = rem_issue_id

    if "published_at" in data:
        raw_pub = data.get("published_at")
        if raw_pub is None or raw_pub == "":
            update_fields["published_at"] = None
        else:
            try:
                update_fields["published_at"] = datetime.fromisoformat(
                    str(raw_pub).strip().replace("Z", "+00:00")
                )
            except ValueError:
                return ApiResponse.error(
                    "published_at must be a valid ISO-8601 date or datetime", 400
                )

    def apply_update():
        ref_err, ref_status = _validate_ownership_refs(
            db,
            tenant_id,
            owner_organization_id=owner_org_id_to_check,
            owner_entity_id=owner_entity_id_to_check,
            remediation_issue_id=remediation_issue_id_to_check,
        )
        if ref_err:
            return None, ref_err, ref_status

        if update_fields:
            update_fields["updated_at"] = datetime.now(UTC)
            db(db.vulnerabilities.id == id).update(**update_fields)
            db.commit()

        row = db.vulnerabilities[id]  # tenant-scope-exempt
        dto = from_pydal_row(row, VulnerabilityDTO)
        enriched = _enrich_vulnerability_dicts(db, [dto], tenant_id)[0]
        return enriched, None, 200

    enriched, err_msg, status_code = await run_in_threadpool(apply_update)
    if err_msg:
        return ApiResponse.error(err_msg, status_code)

    return jsonify(enriched), 200


@bp.route("/<int:id>/create-issue", methods=["POST"])
@login_required
@require_scope("sbom:write")
async def create_vulnerability_issue(id: int):
    """
    Create a remediation issue assigned to the vulnerability's owning OU and link it.

    Path Parameters:
        - id: Vulnerability ID

    Request Body (optional):
        - organization_id: Override/set the owning OU (organization) ID if not already set
        - title: Custom issue title (defaults to "[<cve_id>] Remediate <title>")
        - description: Custom issue description
        - priority: Issue priority (low, medium, high, urgent, critical)

    Returns:
        201: Created issue summary and updated vulnerability
        400: Missing owning OU or invalid parameters
        404: Vulnerability or owning OU not found
        409: Vulnerability already has a linked remediation issue
    """
    db = current_app.db
    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return ApiResponse.error("Tenant context is required", 403)

    data = await request.get_json(silent=True)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return ApiResponse.error("Invalid JSON body", 400)

    vulnerability, error = await validate_resource_exists(
        db.vulnerabilities, id, "Vulnerability"
    )
    if error:
        return error

    org_override, org_err = _parse_optional_positive_int(
        data.get("organization_id"), "organization_id"
    )
    if org_err:
        return ApiResponse.error(org_err, 400)

    raw_priority = data.get("priority")
    if raw_priority is not None and str(raw_priority).strip():
        priority_upper = str(raw_priority).strip().upper()
        if priority_upper not in ("LOW", "MEDIUM", "HIGH", "URGENT", "CRITICAL"):
            return ApiResponse.error(
                "priority must be one of: low, medium, high, urgent, critical", 400
            )
    else:
        vuln_sev = str(getattr(vulnerability, "severity", "") or "").upper()
        priority_upper = _SEVERITY_TO_ISSUE_PRIORITY.get(vuln_sev, "MEDIUM")

    custom_title = (data.get("title") or "").strip() if data.get("title") else None
    custom_description = (
        str(data.get("description")).strip()
        if data.get("description") is not None and str(data.get("description")).strip()
        else None
    )

    current_user = getattr(g, "current_user", None)
    current_user_id = getattr(current_user, "id", None)
    redis_client = getattr(current_app, "redis_client", None)

    def create_and_link():
        from apps.api.utils.village_id import mint_village_id

        existing_issue_id = getattr(vulnerability, "remediation_issue_id", None)
        if existing_issue_id:
            existing_issue = get_tenant_scoped(
                db, db.issues, existing_issue_id, tenant_id
            )
            if existing_issue:
                return (
                    None,
                    None,
                    "Vulnerability already has a linked remediation issue",
                    409,
                )

        target_org_id = org_override or getattr(
            vulnerability, "owner_organization_id", None
        )
        if not target_org_id:
            return (
                None,
                None,
                "An owning OU (owner_organization_id) is required to create a remediation issue",
                400,
            )

        org = get_tenant_scoped(db, db.organizations, target_org_id, tenant_id)
        if not org:
            return None, None, "Owner organization not found", 404

        owner_entity_name = None
        owner_entity_id = getattr(vulnerability, "owner_entity_id", None)
        if owner_entity_id:
            ent = get_tenant_scoped(
                db, db.entities, owner_entity_id, tenant_id, org_fk="organization_id"
            )
            if ent:
                owner_entity_name = ent.name

        cve_id = getattr(vulnerability, "cve_id", "") or f"VULN-{id}"
        vuln_title = getattr(vulnerability, "title", None) or cve_id
        issue_title = (custom_title or f"[{cve_id}] Remediate {vuln_title}")[:255]

        if custom_description:
            issue_description = custom_description
        else:
            lines = [
                f"Remediation tracking issue for **{cve_id}**.",
                "",
                f"- **Severity:** {str(getattr(vulnerability, 'severity', 'unknown')).upper()}",
            ]
            cvss_val = getattr(vulnerability, "cvss_score", None)
            if cvss_val is not None:
                lines.append(f"- **CVSS Score:** {float(cvss_val):.1f}")
            adj_val = getattr(vulnerability, "adjusted_risk_score", None)
            if adj_val:
                lines.append(f"- **Adjusted Risk Score:** {adj_val}")
            lines.append(f"- **Owning OU:** {org.name}")
            if owner_entity_name:
                lines.append(f"- **Owning Resource:** {owner_entity_name}")
            vuln_desc = getattr(vulnerability, "description", None)
            if vuln_desc:
                lines.extend(["", "### Vulnerability Details", vuln_desc])
            issue_description = "\n".join(lines)

        now = datetime.now(UTC)
        issue_id = db.issues.insert(
            tenant_id=tenant_id,
            village_id=mint_village_id(tenant_id, redis_client),
            title=issue_title,
            description=issue_description,
            status="OPEN",
            priority=priority_upper,
            issue_type="SECURITY",
            resource_type="organization",
            resource_id=target_org_id,
            is_incident=0,
            reporter_id=current_user_id,
            assignee_id=target_org_id,
            assignee_type="org_unit",
            created_at=now,
            updated_at=now,
        )

        vuln_updates = {
            "remediation_issue_id": issue_id,
            "updated_at": now,
        }
        if not getattr(vulnerability, "owner_organization_id", None):
            vuln_updates["owner_organization_id"] = target_org_id

        db(db.vulnerabilities.id == id).update(**vuln_updates)
        db.commit()

        issue_row = get_tenant_scoped(db, db.issues, issue_id, tenant_id)
        vuln_row = db.vulnerabilities[id]  # tenant-scope-exempt
        vuln_dto = from_pydal_row(vuln_row, VulnerabilityDTO)
        enriched_vuln = _enrich_vulnerability_dicts(db, [vuln_dto], tenant_id)[0]
        return _serialize_issue_summary(issue_row), enriched_vuln, None, 201

    issue_summary, enriched_vuln, err_msg, status_code = await run_in_threadpool(
        create_and_link
    )
    if err_msg:
        return ApiResponse.error(err_msg, status_code)

    return jsonify({"issue": issue_summary, "vulnerability": enriched_vuln}), 201


@bp.route("/sync", methods=["POST"])
@login_required
@require_scope("sbom:write")
@resource_role_required("maintainer")
async def sync_vulnerabilities():
    """
    Trigger vulnerability sync from OSV.dev and NVD.

    Syncs vulnerabilities for all SBOM components in the system.
    This is a long-running operation that runs asynchronously.

    Requires maintainer role.

    Request Body:
        - component_ids: Optional list of component IDs to sync (syncs all if not provided)
        - force: Force re-sync even if recently synced (default: false)

    Returns:
        202: Sync initiated
        400: Invalid request
        403: Insufficient permissions

    Example:
        POST /api/v1/vulnerabilities/sync
        {
            "component_ids": [1, 2, 3],
            "force": false
        }
    """
    db = current_app.db

    # Validate request using pydantic
    try:
        data = await request.get_json() or {}
        validated_req = SyncVulnerabilitiesRequest(**data)
    except ValidationError as e:
        return ValidationErrorResponse.from_pydantic_error(e)

    component_ids = validated_req.component_ids
    # TODO: Implement force re-sync functionality
    validated_req.force

    def get_components():
        query = db.sbom_components.id > 0

        if component_ids:
            query &= db.sbom_components.id.belongs(component_ids)

        # Only sync components with PURL
        query &= db.sbom_components.purl != None  # noqa: E711
        query &= db.sbom_components.purl != ""

        return db(query).select()

    components = await run_in_threadpool(get_components)

    if not components:
        return ApiResponse.error("No components found to sync", 400)

    # Build component list for batch matching
    component_list = []
    for comp in components:
        component_list.append(
            {
                "id": comp.id,
                "purl": comp.purl,
                "package_type": comp.package_type,
                "name": comp.name,
                "version": comp.version,
            }
        )

    # Perform vulnerability matching in background
    # (In production, this would be a background task/worker)
    async def sync_task():
        async with VulnerabilityMatcher() as matcher:
            results = await matcher.match_components_batch(component_list)

            # Sync results to database
            def save_results():
                tenant_id = current_app.config.get("DEFAULT_TENANT_ID", 1)
                total_vulns = 0
                total_links = 0

                for comp_id, vulns in results.items():
                    for vuln in vulns:
                        from apps.api.utils.village_id import mint_village_id

                        # Sync vulnerability to DB
                        vuln_id = db.vulnerabilities.insert(
                            tenant_id=tenant_id,
                            village_id=mint_village_id(tenant_id),
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
                        total_vulns += 1

                        # Check if link exists
                        existing_link = (
                            db(
                                (db.component_vulnerabilities.component_id == comp_id)
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
                            total_links += 1

                db.commit()
                return total_vulns, total_links

            total_vulns, total_links = await run_in_threadpool(save_results)

            return {
                "components_scanned": len(component_list),
                "vulnerabilities_found": total_vulns,
                "links_created": total_links,
            }

    # Run sync task
    result = await sync_task()

    return jsonify(result), 202


@bp.route("/dashboard", methods=["GET"])
@login_required
@require_scope("sbom:read")
async def get_dashboard():
    """
    Get vulnerability dashboard summary statistics.

    Returns:
        200: Dashboard statistics

    Example:
        GET /api/v1/vulnerabilities/dashboard
    """
    db = current_app.db

    def get_stats():
        # Count vulnerabilities by severity
        critical_count = db(db.vulnerabilities.severity == "CRITICAL").count()
        high_count = db(db.vulnerabilities.severity == "HIGH").count()
        medium_count = db(db.vulnerabilities.severity == "MEDIUM").count()
        low_count = db(db.vulnerabilities.severity == "LOW").count()
        unknown_count = db(db.vulnerabilities.severity == "UNKNOWN").count()

        # Count component vulnerabilities by status
        open_count = db(db.component_vulnerabilities.status == "open").count()
        investigating_count = db(
            db.component_vulnerabilities.status == "investigating"
        ).count()
        remediated_count = db(
            db.component_vulnerabilities.status == "remediated"
        ).count()
        false_positive_count = db(
            db.component_vulnerabilities.status == "false_positive"
        ).count()
        accepted_count = db(db.component_vulnerabilities.status == "accepted").count()

        # Count affected components
        affected_components = db(
            (db.component_vulnerabilities.id > 0)
            & (db.component_vulnerabilities.status == "open")
        ).count()

        # Total vulnerabilities
        total_vulns = db(db.vulnerabilities.id > 0).count()

        return {
            "vulnerabilities_by_severity": {
                "critical": critical_count,
                "high": high_count,
                "medium": medium_count,
                "low": low_count,
                "unknown": unknown_count,
            },
            "component_vulnerabilities_by_status": {
                "open": open_count,
                "investigating": investigating_count,
                "remediated": remediated_count,
                "false_positive": false_positive_count,
                "accepted": accepted_count,
            },
            "total_vulnerabilities": total_vulns,
            "affected_components": affected_components,
        }

    stats = await run_in_threadpool(get_stats)

    return ApiResponse.success(stats)


@bp.route("/component-vulnerabilities/<int:id>", methods=["PATCH"])
@login_required
@require_scope("sbom:write")
@resource_role_required("maintainer")
async def update_component_vulnerability(id: int):
    """
    Update component-vulnerability link status.

    Requires maintainer role.

    Path Parameters:
        - id: Component vulnerability link ID

    Request Body:
        - status: New status (open, investigating, remediated, false_positive, accepted)
        - remediation_notes: Optional remediation notes
        - remediated_by_id: Optional user ID who remediated

    Returns:
        200: Component vulnerability updated
        400: Invalid request
        403: Insufficient permissions
        404: Component vulnerability not found

    Example:
        PATCH /api/v1/vulnerabilities/component-vulnerabilities/1
        {
            "status": "remediated",
            "remediation_notes": "Updated to version 2.1.0"
        }
    """
    db = current_app.db
    tenant_id = get_current_tenant_id()

    # Validate request using pydantic
    try:
        data = await request.get_json()
        if not data:
            return ApiResponse.error("Request body is required", 400)
        validated_req = UpdateComponentVulnerabilityRequest(**data)
    except ValidationError as e:
        return ValidationErrorResponse.from_pydantic_error(e)

    # Validate resource exists and belongs to caller's tenant (gh-237)
    comp_vuln = await run_in_threadpool(
        lambda: get_tenant_scoped(db, db.component_vulnerabilities, id, tenant_id)
    )
    if not comp_vuln:
        return ApiResponse.not_found("Component Vulnerability", id)

    def update():
        update_dict = {}

        if validated_req.status is not None:
            update_dict["status"] = validated_req.status

            # Set remediated_at if status is remediated
            if validated_req.status == "remediated":
                from datetime import datetime, timezone

                update_dict["remediated_at"] = datetime.now(UTC)

        if validated_req.remediation_notes is not None:
            update_dict["remediation_notes"] = validated_req.remediation_notes

        if validated_req.remediated_by_id is not None:
            update_dict["remediated_by_id"] = validated_req.remediated_by_id

        if update_dict:
            db(db.component_vulnerabilities.id == id).update(**update_dict)
            db.commit()

        return db.component_vulnerabilities[id], None  # tenant-scope-exempt

    updated, error_msg = await run_in_threadpool(update)

    if error_msg:
        return ApiResponse.error(error_msg, 400)

    if not updated:
        return ApiResponse.not_found("Component Vulnerability", id)

    comp_vuln_dto = from_pydal_row(updated, ComponentVulnerabilityDTO)
    return ApiResponse.success(asdict(comp_vuln_dto))


@bp.route("/nvd-sync", methods=["POST"])
@login_required
@require_scope("sbom:write")
@role_required("admin", allow_service_worker_scopes=("vulnerabilities:write",))
async def trigger_nvd_sync():
    """
    Trigger NVD sync to enrich vulnerability CVSS data.

    This endpoint triggers a background sync of CVE data from NVD (NIST).
    Only vulnerabilities that haven't been synced in the last 24 hours
    will be processed. Rate limits are respected (6 req/min without API key,
    50 req/30s with API key).

    Requires maintainer role.

    Request Body (optional):
        - max_vulns: Maximum vulnerabilities to sync (default: 500)
        - force_refresh: Force refresh all CVEs (default: false)

    Returns:
        202: Sync started with statistics
        403: Insufficient permissions

    Example:
        POST /api/v1/vulnerabilities/nvd-sync
        {"max_vulns": 100}
    """
    from apps.api.services.sbom.vulnerability.nvd_sync import NVDSyncService

    db = current_app.db

    # Validate request using pydantic
    try:
        data = await request.get_json() or {}
        validated_req = NVDSyncRequest(**data)
    except ValidationError as e:
        return ValidationErrorResponse.from_pydantic_error(e)

    max_vulns = validated_req.max_vulns
    force_refresh = validated_req.force_refresh

    # Get NVD API key from config if available
    nvd_api_key = current_app.config.get("NVD_API_KEY")

    # Run the sync - let exceptions propagate for proper error handling (500 response)
    service = NVDSyncService(db, nvd_api_key)
    stats = await service.sync_vulnerabilities(
        max_vulns=max_vulns,
        force_refresh=force_refresh,
    )

    return (
        jsonify(
            {
                "message": "NVD sync completed",
                "stats": stats,
            }
        ),
        202,
    )


@bp.route("/nvd-sync/status", methods=["GET"])
@login_required
@require_scope("sbom:read")
async def get_nvd_sync_status():
    """
    Get NVD sync status - how many vulnerabilities need syncing.

    Returns:
        200: Sync status with counts

    Example:
        GET /api/v1/vulnerabilities/nvd-sync/status
    """
    from datetime import datetime, timedelta, timezone

    db = current_app.db

    def get_status():
        cutoff_time = datetime.now(UTC) - timedelta(hours=24)

        # Count CVEs that need syncing
        total_cves = db(db.vulnerabilities.cve_id.startswith("CVE-")).count()
        never_synced = db(
            (db.vulnerabilities.cve_id.startswith("CVE-"))
            & (db.vulnerabilities.nvd_last_sync == None)  # noqa: E711
        ).count()
        stale_sync = db(
            (db.vulnerabilities.cve_id.startswith("CVE-"))
            & (db.vulnerabilities.nvd_last_sync != None)  # noqa: E711
            & (db.vulnerabilities.nvd_last_sync < cutoff_time)
        ).count()
        recently_synced = db(
            (db.vulnerabilities.cve_id.startswith("CVE-"))
            & (db.vulnerabilities.nvd_last_sync >= cutoff_time)
        ).count()

        return {
            "total_cves": total_cves,
            "never_synced": never_synced,
            "stale_sync": stale_sync,
            "recently_synced": recently_synced,
            "needs_sync": never_synced + stale_sync,
        }

    status = await run_in_threadpool(get_status)
    return jsonify(status), 200


@bp.route("/<int:id>/assign", methods=["POST"])
@login_required
@require_scope("sbom:write")
@resource_role_required("maintainer")
async def assign_vulnerability(id: int):
    """
    Assign a vulnerability to a service or software.

    Requires maintainer role.

    Path Parameters:
        - id: Vulnerability ID

    Request Body:
        - parent_type: "service" or "software" (required)
        - parent_id: ID of the service or software (required)
        - notes: Optional assignment notes (max 5000 chars)

    Returns:
        201: Assignment created
        400: Invalid request or duplicate assignment
        404: Vulnerability or parent not found
    """
    db = current_app.db
    tenant_id = get_current_tenant_id()

    # Validate request
    try:
        data = await request.get_json()
        if not data:
            return ApiResponse.error("Request body is required", 400)
        validated_req = AssignVulnerabilityRequest(**data)
    except ValidationError as e:
        return ValidationErrorResponse.from_pydantic_error(e)

    # Validate vulnerability exists (tenant-scope-exempt: vulnerabilities is
    # a shared CVE reference dataset, see get_vulnerability)
    vulnerability, error = await validate_resource_exists(
        db.vulnerabilities, id, "Vulnerability"
    )
    if error:
        return error

    parent_type = validated_req.parent_type
    parent_id = validated_req.parent_id

    # Validate parent resource exists and belongs to caller's tenant
    # (gh-237) -- previously any authenticated caller could assign a
    # vulnerability onto another tenant's service/software by id.
    if parent_type == "service":
        parent = await run_in_threadpool(
            lambda: get_tenant_scoped(db, db.services, parent_id, tenant_id)
        )
    else:
        parent = await run_in_threadpool(
            lambda: get_tenant_scoped(db, db.software, parent_id, tenant_id)
        )
    if not parent:
        return ApiResponse.not_found(parent_type.title(), parent_id)

    def create_assignment():
        # Find or create sbom_component for this parent
        existing_comp = (
            db(
                (db.sbom_components.parent_type == parent_type)
                & (db.sbom_components.parent_id == parent_id)
                & (db.sbom_components.package_type == "other")
                & (db.sbom_components.name == parent.name)
            )
            .select()
            .first()
        )

        if existing_comp:
            comp_id = existing_comp.id
        else:
            from apps.api.utils.village_id import mint_village_id

            comp_id = db.sbom_components.insert(
                tenant_id=tenant_id,
                village_id=mint_village_id(tenant_id),
                parent_type=parent_type,
                parent_id=parent_id,
                name=parent.name,
                package_type="other",
            )

        # Check for duplicate
        existing_link = (
            db(
                (db.component_vulnerabilities.component_id == comp_id)
                & (db.component_vulnerabilities.vulnerability_id == id)
            )
            .select()
            .first()
        )

        if existing_link:
            return None, "Vulnerability is already assigned to this resource"

        cv_id = db.component_vulnerabilities.insert(
            tenant_id=tenant_id,
            component_id=comp_id,
            vulnerability_id=id,
            status="open",
            remediation_notes=validated_req.notes,
        )
        db.commit()

        return db.component_vulnerabilities[cv_id], None  # tenant-scope-exempt

    result, error_msg = await run_in_threadpool(create_assignment)

    if error_msg:
        return ApiResponse.error(error_msg, 400)

    comp_vuln_dto = from_pydal_row(result, ComponentVulnerabilityDTO)
    return jsonify(asdict(comp_vuln_dto)), 201
