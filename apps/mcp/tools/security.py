"""MCP tools for Elder vulnerabilities, SBOM, services, software, certificates, secrets metadata, KMS, and access reviews."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from apps.mcp.client import ElderClient
else:
    try:
        from apps.mcp.client import ElderClient
    except ImportError:
        from client import ElderClient


async def list_vulnerabilities(
    client: ElderClient,
    severity: str | None = None,
    cve_id: str | None = None,
    source: str | None = None,
    search: str | None = None,
    limit: int = 50,
    page: int = 1,
) -> Any:
    """List tracked CVEs/vulnerabilities filtered by severity, CVE ID, source, or keyword."""
    params: dict[str, Any] = {
        "severity": severity,
        "cve_id": cve_id,
        "source": source,
        "search": search,
        "per_page": limit,
        "page": page,
    }
    return await client.get("/api/v1/vulnerabilities", params=params)


async def get_vulnerability(client: ElderClient, vulnerability_id: int) -> Any:
    """Get full details and affected components for a vulnerability by ID."""
    return await client.get(f"/api/v1/vulnerabilities/{vulnerability_id}")


async def get_vulnerability_dashboard(client: ElderClient) -> Any:
    """Get aggregate vulnerability metrics and severity breakdown."""
    return await client.get("/api/v1/vulnerabilities/dashboard")


async def list_sbom_components(
    client: ElderClient,
    parent_type: str | None = None,
    parent_id: int | None = None,
    package_type: str | None = None,
    license_id: str | None = None,
    direct: bool | None = None,
    search: str | None = None,
    limit: int = 50,
    page: int = 1,
) -> Any:
    """List SBOM software components filtered by parent resource, package ecosystem, or license."""
    params: dict[str, Any] = {
        "parent_type": parent_type,
        "parent_id": parent_id,
        "package_type": package_type,
        "license_id": license_id,
        "direct": str(direct).lower() if direct is not None else None,
        "search": search,
        "per_page": limit,
        "page": page,
    }
    return await client.get("/api/v1/sbom/components", params=params)


async def list_sbom_scans(
    client: ElderClient,
    parent_type: str | None = None,
    parent_id: int | None = None,
    status: str | None = None,
    scan_type: str | None = None,
    limit: int = 50,
    page: int = 1,
) -> Any:
    """List SBOM scan jobs filtered by parent resource, status, or scan type."""
    params: dict[str, Any] = {
        "parent_type": parent_type,
        "parent_id": parent_id,
        "status": status,
        "scan_type": scan_type,
        "per_page": limit,
        "page": page,
    }
    return await client.get("/api/v1/sbom/scans", params=params)


async def search_services(
    client: ElderClient,
    organization_id: int | None = None,
    status: str | None = None,
    search: str | None = None,
    limit: int = 50,
    page: int = 1,
) -> Any:
    """List/search services registered in Elder."""
    params: dict[str, Any] = {
        "organization_id": organization_id,
        "status": status,
        "search": search,
        "per_page": limit,
        "page": page,
    }
    return await client.get("/api/v1/services", params=params)


async def get_service(client: ElderClient, service_id: int) -> Any:
    """Get full details for a single service by ID."""
    return await client.get(f"/api/v1/services/{service_id}")


async def get_service_sbom(client: ElderClient, service_id: int) -> Any:
    """Get SBOM components and dependencies for a specific service."""
    return await client.get(f"/api/v1/services/{service_id}/sbom")


async def list_software(
    client: ElderClient,
    organization_id: int | None = None,
    software_type: str | None = None,
    is_active: bool | None = None,
    search: str | None = None,
    limit: int = 50,
    page: int = 1,
) -> Any:
    """List software catalog items with optional organization and search filters."""
    params: dict[str, Any] = {
        "organization_id": organization_id,
        "software_type": software_type,
        "is_active": str(is_active).lower() if is_active is not None else None,
        "search": search,
        "per_page": limit,
        "page": page,
    }
    return await client.get("/api/v1/software", params=params)


async def get_software(client: ElderClient, software_id: int) -> Any:
    """Get details for a specific software catalog item by ID."""
    return await client.get(f"/api/v1/software/{software_id}")


async def list_license_policies(
    client: ElderClient,
    organization_id: int | None = None,
    is_active: bool | None = None,
) -> Any:
    """List SBOM OSS license compliance policies."""
    params: dict[str, Any] = {
        "organization_id": organization_id,
        "is_active": str(is_active).lower() if is_active is not None else None,
    }
    return await client.get("/api/v1/license-policies", params=params)


async def list_certificates(
    client: ElderClient,
    organization_id: int | None = None,
    status: str | None = None,
    cert_type: str | None = None,
    expiring_soon: bool | None = None,
    search: str | None = None,
    limit: int = 50,
    page: int = 1,
) -> Any:
    """List TLS/PKI certificates with optional expiry and status filters."""
    params: dict[str, Any] = {
        "organization_id": organization_id,
        "status": status,
        "cert_type": cert_type,
        "expiring_soon": "true" if expiring_soon else None,
        "search": search,
        "per_page": limit,
        "page": page,
    }
    return await client.get("/api/v1/certificates", params=params)


async def list_secrets_metadata(
    client: ElderClient,
    organization_id: int | None = None,
    provider_id: int | None = None,
    secret_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Any:
    """List managed secret metadata only (never exposes or unmasks secret values)."""
    params: dict[str, Any] = {
        "organization_id": organization_id,
        "provider_id": provider_id,
        "secret_type": secret_type,
        "limit": limit,
        "offset": offset,
    }
    return await client.get("/api/v1/secrets", params=params)


async def list_kms_keys(
    client: ElderClient,
    provider_id: int | None = None,
    key_type: str | None = None,
    enabled_only: bool = False,
) -> Any:
    """List KMS encryption keys metadata."""
    params: dict[str, Any] = {
        "provider_id": provider_id,
        "key_type": key_type,
        "enabled_only": "true" if enabled_only else None,
    }
    return await client.get("/api/v1/keys", params=params)


async def list_access_reviews(
    client: ElderClient,
    status: str | None = None,
    group_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Any:
    """List access review campaigns filtered by status or group."""
    params: dict[str, Any] = {
        "status": status,
        "group_id": group_id,
        "limit": limit,
        "offset": offset,
    }
    return await client.get("/api/v1/access-reviews", params=params)


async def get_access_review(client: ElderClient, review_id: int) -> Any:
    """Get details for a specific access review campaign by ID."""
    return await client.get(f"/api/v1/access-reviews/{review_id}")


async def list_pending_group_requests(
    client: ElderClient,
    limit: int = 50,
    offset: int = 0,
) -> Any:
    """List pending group membership access requests."""
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    return await client.get("/api/v1/group-membership/requests/pending", params=params)


async def get_entity_resource_roles(client: ElderClient, entity_id: int) -> Any:
    """List resource-level RBAC role assignments for a specific entity."""
    return await client.get(f"/api/v1/resource-roles/entities/{entity_id}/roles")
