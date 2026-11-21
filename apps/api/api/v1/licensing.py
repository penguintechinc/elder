"""Licensing status API endpoint for Elder.

Exposes the effective deployment tier, domain-bypass state, resolved scale
limits, and feature entitlements for authenticated callers (used by Admin
Settings and client feature-gating views).
"""

from __future__ import annotations

import structlog
from pydantic import BaseModel
from quart import Blueprint, current_app
from quart_schema import validate_response

from apps.api.auth.decorators import login_required
from apps.api.common.licensing.limits import resolve_limits
from apps.api.common.licensing.tier_gate import get_tier
from apps.api.common.modules.licensing import (
    get_matched_bypass_domain,
    is_bypass_domain,
)

logger = structlog.get_logger(__name__)

bp = Blueprint("licensing", __name__)


class LicenseLimitsDTO(BaseModel):
    """Resolved scale limits (``None`` indicates unlimited)."""

    max_global_admins: int | None
    max_tenant_admins: int | None
    max_teams: int | None
    max_tenants: int | None
    max_objects: int | None
    max_nodes_per_type: int | None


class LicenseFeaturesDTO(BaseModel):
    """Feature entitlement flags derived from the resolved deployment tier."""

    multi_tenancy: bool
    portal_users: bool
    saml_sso: bool
    scim_provisioning: bool
    audit_logging: bool
    compliance_reports: bool
    access_reviews: bool
    enterprise_integrations: bool
    google_oauth_sso: bool


class LicenseStatusResponse(BaseModel):
    """Response DTO for ``GET /api/v1/licensing/status``."""

    tier: str
    status: str
    domain_bypass: bool
    bypass_domain: str | None
    limits: LicenseLimitsDTO
    features: LicenseFeaturesDTO


@bp.route("/status", methods=["GET"])
@login_required
@validate_response(LicenseStatusResponse)
async def get_licensing_status() -> tuple[LicenseStatusResponse, int]:
    """Return the effective license tier, domain-bypass state, limits, and features."""
    tier = get_tier()
    bypassed = is_bypass_domain()
    bypass_domain = get_matched_bypass_domain() if bypassed else None
    _, limits = resolve_limits(current_app.extensions.get("license_client"))
    is_ent = tier == "enterprise"

    logger.debug(
        "licensing_status_resolved",
        tier=tier,
        domain_bypass=bypassed,
    )

    response = LicenseStatusResponse(
        tier=tier,
        status="active",
        domain_bypass=bypassed,
        bypass_domain=bypass_domain,
        limits=LicenseLimitsDTO(
            max_global_admins=limits.max_global_admins,
            max_tenant_admins=limits.max_tenant_admins,
            max_teams=limits.max_teams,
            max_tenants=limits.max_tenants,
            max_objects=limits.max_objects,
            max_nodes_per_type=limits.max_nodes_per_type,
        ),
        features=LicenseFeaturesDTO(
            multi_tenancy=is_ent,
            portal_users=is_ent,
            saml_sso=is_ent,
            scim_provisioning=is_ent,
            audit_logging=is_ent,
            compliance_reports=is_ent,
            access_reviews=is_ent,
            enterprise_integrations=is_ent,
            google_oauth_sso=tier in ("professional", "enterprise"),
        ),
    )
    return response, 200
