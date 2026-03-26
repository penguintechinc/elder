"""Verify every frontend API path maps to an existing backend route.

A 404 means the route doesn't exist (path mismatch between frontend and backend).
Any other status (200, 401, 403, 405, 422, 500) means the route is registered.

This test catches the class of bug where the frontend calls /sso/idp-configs
but the backend route is /sso/idp — the request silently returns 404 and
the UI shows empty data with no visible error.

Run: pytest tests/api/test_route_existence.py -v
"""

import os

import pytest

os.environ["FLASK_ENV"] = "testing"
os.environ["DATABASE_URL"] = "sqlite:////tmp/elder_test_routes.db"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-route-existence")

# Every path the frontend api.ts client calls, with dummy IDs for parameterized routes.
# Format: (HTTP_METHOD, path_relative_to_/api/v1)
# Paths use dummy integer IDs (1) for path params like {id}, {tenantId}, etc.
FRONTEND_ROUTES = [
    # Auth & Portal Auth
    ("POST", "/portal-auth/refresh"),
    ("GET", "/auth/guest-enabled"),
    ("POST", "/auth/login"),
    ("POST", "/auth/register"),
    ("GET", "/portal-auth/me"),
    ("PATCH", "/portal-auth/me"),
    ("POST", "/portal-auth/password/change"),
    ("POST", "/portal-auth/login"),
    ("POST", "/portal-auth/register"),
    ("POST", "/portal-auth/mfa/verify"),
    ("POST", "/portal-auth/mfa/enable"),
    ("POST", "/portal-auth/mfa/disable"),
    # Organizations
    ("GET", "/organizations"),
    ("GET", "/organizations/1"),
    ("POST", "/organizations"),
    ("PUT", "/organizations/1"),
    ("DELETE", "/organizations/1"),
    ("GET", "/organizations/1/tree-stats"),
    ("GET", "/organizations/1/graph"),
    # Entities
    ("GET", "/entities"),
    ("GET", "/entities/1"),
    ("POST", "/entities"),
    ("PUT", "/entities/1"),
    ("DELETE", "/entities/1"),
    # Entity Types
    ("GET", "/entity-types/"),
    ("GET", "/entity-types/1"),
    # Entity types: backend only has GET routes and POST /validate (no create/update/delete)
    ("POST", "/entity-types/validate"),
    # Dependencies
    ("GET", "/dependencies"),
    ("POST", "/dependencies"),
    ("DELETE", "/dependencies/1"),
    ("DELETE", "/dependencies/bulk"),
    # Graph
    ("GET", "/graph"),
    ("GET", "/graph/map"),
    # Identities
    ("GET", "/identities"),
    ("GET", "/identities/1"),
    ("POST", "/identities"),
    ("PUT", "/identities/1"),
    ("GET", "/identities/groups"),
    ("POST", "/identities/groups"),
    # Group Membership
    ("GET", "/group-membership/groups"),
    ("GET", "/group-membership/groups/1"),
    ("PATCH", "/group-membership/groups/1"),
    ("POST", "/group-membership/groups/1/requests"),
    ("GET", "/group-membership/groups/1/requests"),
    ("GET", "/group-membership/requests/pending"),
    ("POST", "/group-membership/requests/1/approve"),
    ("POST", "/group-membership/requests/1/deny"),
    ("DELETE", "/group-membership/requests/1"),
    ("POST", "/group-membership/requests/bulk-approve"),
    ("GET", "/group-membership/groups/1/members"),
    ("POST", "/group-membership/groups/1/members"),
    ("DELETE", "/group-membership/groups/1/members/1"),
    # Access Reviews
    ("GET", "/access-reviews"),
    ("GET", "/access-reviews/1"),
    ("GET", "/access-reviews/1/items"),
    ("POST", "/access-reviews/1/decisions"),
    ("POST", "/access-reviews/1/complete"),
    ("GET", "/access-reviews/my-reviews"),
    ("POST", "/access-reviews"),
    # Issues
    ("GET", "/issues"),
    ("GET", "/issues/1"),
    ("POST", "/issues"),
    ("PATCH", "/issues/1"),
    ("DELETE", "/issues/1"),
    # POST /issues/1/close — no backend route (not implemented)
    ("GET", "/issues/1/comments"),
    ("POST", "/issues/1/comments"),
    ("DELETE", "/issues/1/comments/1"),
    ("GET", "/issues/1/labels"),
    ("POST", "/issues/1/labels"),
    ("DELETE", "/issues/1/labels/1"),
    # GET /issues/1/subtasks — no backend route (not implemented)
    # Issue-Entity Links (backend uses /links, not /entities)
    ("GET", "/issues/1/links"),
    ("POST", "/issues/1/links"),
    ("DELETE", "/issues/1/links/1"),
    ("DELETE", "/issues/1/links/by-entity/1"),
    ("POST", "/issues/1/projects"),
    ("DELETE", "/issues/1/projects/1"),
    ("POST", "/issues/1/milestones"),
    ("DELETE", "/issues/1/milestones/1"),
    # Labels
    ("GET", "/labels"),
    ("GET", "/labels/1"),
    ("POST", "/labels"),
    ("PUT", "/labels/1"),
    ("DELETE", "/labels/1"),
    # Projects
    ("GET", "/projects"),
    ("GET", "/projects/1"),
    ("POST", "/projects"),
    ("PUT", "/projects/1"),
    ("DELETE", "/projects/1"),
    # Milestones
    ("GET", "/milestones"),
    ("GET", "/milestones/1"),
    ("POST", "/milestones"),
    ("PUT", "/milestones/1"),
    ("DELETE", "/milestones/1"),
    ("GET", "/milestones/1/issues"),
    # Software
    ("GET", "/software"),
    ("GET", "/software/1"),
    ("POST", "/software"),
    ("PUT", "/software/1"),
    ("DELETE", "/software/1"),
    # Resource Roles
    ("GET", "/resource-roles"),
    ("POST", "/resource-roles"),
    ("DELETE", "/resource-roles/1"),
    # Metadata
    ("GET", "/metadata/organizations/1/metadata"),
    ("POST", "/metadata/organizations/1/metadata"),
    ("PATCH", "/metadata/organizations/1/metadata/testkey"),
    ("DELETE", "/metadata/organizations/1/metadata/testkey"),
    ("GET", "/metadata/entities/1/metadata"),
    ("POST", "/metadata/entities/1/metadata"),
    ("PATCH", "/metadata/entities/1/metadata/testkey"),
    ("DELETE", "/metadata/entities/1/metadata/testkey"),
    # Secrets
    ("GET", "/secrets/providers"),
    ("GET", "/secrets/providers/1"),
    ("POST", "/secrets/providers"),
    ("PUT", "/secrets/providers/1"),
    ("DELETE", "/secrets/providers/1"),
    ("POST", "/secrets/providers/1/test"),
    # Secrets are flat (not nested under providers)
    ("GET", "/secrets"),
    ("GET", "/secrets/1"),
    # Keys
    ("GET", "/keys/providers"),
    ("GET", "/keys/providers/1"),
    ("POST", "/keys/providers"),
    ("PUT", "/keys/providers/1"),
    ("DELETE", "/keys/providers/1"),
    ("POST", "/keys/providers/1/test"),
    # Encrypt/decrypt are on keys directly, not nested under providers
    ("POST", "/keys/1/encrypt"),
    ("POST", "/keys/1/decrypt"),
    # IAM
    ("GET", "/iam/providers"),
    ("GET", "/iam/providers/1"),
    ("POST", "/iam/providers"),
    ("PUT", "/iam/providers/1"),
    ("DELETE", "/iam/providers/1"),
    ("GET", "/iam/providers/1/users"),
    ("GET", "/iam/providers/1/roles"),
    ("GET", "/iam/providers/1/policies"),
    # Discovery
    ("GET", "/discovery/jobs"),
    ("GET", "/discovery/jobs/1"),
    ("POST", "/discovery/jobs"),
    ("PUT", "/discovery/jobs/1"),
    ("DELETE", "/discovery/jobs/1"),
    ("POST", "/discovery/jobs/1/run"),
    ("GET", "/discovery/jobs/1/history"),
    # Google Workspace
    ("GET", "/google-workspace/providers"),
    ("GET", "/google-workspace/providers/1"),
    ("POST", "/google-workspace/providers"),
    ("PUT", "/google-workspace/providers/1"),
    ("DELETE", "/google-workspace/providers/1"),
    ("POST", "/google-workspace/providers/1/test"),
    ("GET", "/google-workspace/providers/1/users"),
    ("GET", "/google-workspace/providers/1/groups"),
    # Webhooks
    ("GET", "/webhooks"),
    ("GET", "/webhooks/1"),
    ("POST", "/webhooks"),
    ("PUT", "/webhooks/1"),
    ("DELETE", "/webhooks/1"),
    ("POST", "/webhooks/1/test"),
    ("GET", "/webhooks/1/deliveries"),
    # Backup
    ("GET", "/backup/jobs"),
    ("GET", "/backup/jobs/1"),
    ("POST", "/backup/jobs"),
    ("PUT", "/backup/jobs/1"),
    ("DELETE", "/backup/jobs/1"),
    ("POST", "/backup/jobs/1/run"),
    ("GET", "/backup"),
    ("GET", "/backup/1"),
    ("DELETE", "/backup/1"),
    ("POST", "/backup/1/restore"),
    # Networking
    ("GET", "/networking/networks"),
    ("GET", "/networking/networks/1"),
    ("POST", "/networking/networks"),
    ("PUT", "/networking/networks/1"),
    ("DELETE", "/networking/networks/1"),
    ("GET", "/networking/topology/connections"),
    ("GET", "/networking/topology/connections/1"),
    ("POST", "/networking/topology/connections"),
    ("DELETE", "/networking/topology/connections/1"),
    ("GET", "/networking/mappings"),
    ("POST", "/networking/mappings"),
    ("DELETE", "/networking/mappings/1"),
    ("GET", "/networking/topology/graph"),
    # Builtin Secrets
    ("GET", "/builtin-secrets"),
    ("GET", "/builtin-secrets/test-path"),
    ("POST", "/builtin-secrets"),
    ("PUT", "/builtin-secrets/test-path"),
    ("DELETE", "/builtin-secrets/test-path"),
    ("POST", "/builtin-secrets/test-connection"),
    # Tenants
    ("GET", "/tenants"),
    ("GET", "/tenants/1"),
    ("POST", "/tenants"),
    ("PUT", "/tenants/1"),
    ("DELETE", "/tenants/1"),
    ("GET", "/tenants/1/users"),
    ("PUT", "/tenants/1/users/1"),
    ("DELETE", "/tenants/1/users/1"),
    ("GET", "/tenants/1/stats"),
    # SSO - IdP (fixed paths: /sso/idp, not /sso/idp-configs)
    ("GET", "/sso/idp"),
    ("POST", "/sso/idp"),
    ("PUT", "/sso/idp/1"),
    ("DELETE", "/sso/idp/1"),
    # SSO - SCIM Config (fixed paths: /sso/scim/config, not /sso/scim)
    ("GET", "/sso/scim/config/1"),
    ("POST", "/sso/scim/config"),
    ("POST", "/sso/scim/config/1/regenerate-token"),
    # SSO - SAML
    ("GET", "/sso/saml/metadata"),
    # Audit — enterprise audit endpoints are at /audit-enterprise
    ("GET", "/audit-enterprise/logs"),
    ("GET", "/audit-enterprise/reports/compliance"),
    ("GET", "/audit-enterprise/retention"),
    ("POST", "/audit-enterprise/cleanup"),
    ("GET", "/audit-enterprise/export"),
    # IPAM
    ("GET", "/ipam/prefixes"),
    ("GET", "/ipam/prefixes/1/tree"),
    ("GET", "/ipam/prefixes/1"),
    ("POST", "/ipam/prefixes"),
    ("PUT", "/ipam/prefixes/1"),
    ("DELETE", "/ipam/prefixes/1"),
    ("GET", "/ipam/addresses"),
    ("GET", "/ipam/addresses/1"),
    ("POST", "/ipam/addresses"),
    ("PUT", "/ipam/addresses/1"),
    ("DELETE", "/ipam/addresses/1"),
    ("GET", "/ipam/vlans"),
    ("GET", "/ipam/vlans/1"),
    ("POST", "/ipam/vlans"),
    ("PUT", "/ipam/vlans/1"),
    ("DELETE", "/ipam/vlans/1"),
    # Data Stores
    ("GET", "/data-stores"),
    ("GET", "/data-stores/1"),
    ("POST", "/data-stores"),
    ("PUT", "/data-stores/1"),
    ("DELETE", "/data-stores/1"),
    ("GET", "/data-stores/1/labels"),
    ("POST", "/data-stores/1/labels"),
    ("DELETE", "/data-stores/1/labels/1"),
    # Services
    ("GET", "/services"),
    ("GET", "/services/1"),
    ("POST", "/services"),
    ("PUT", "/services/1"),
    ("DELETE", "/services/1"),
    # Certificates
    ("GET", "/certificates"),
    ("GET", "/certificates/1"),
    ("POST", "/certificates"),
    ("PUT", "/certificates/1"),
    ("DELETE", "/certificates/1"),
    # Logs
    ("GET", "/logs"),
    ("GET", "/logs/search"),
    # SBOM
    ("GET", "/sbom/schedules"),
    ("GET", "/sbom/schedules/1"),
    ("POST", "/sbom/schedules"),
    ("PUT", "/sbom/schedules/1"),
    ("DELETE", "/sbom/schedules/1"),
    ("POST", "/sbom/scans"),
    ("GET", "/sbom/scans"),
    # License Policies (fixed path: /license-policies, not /admin/license-policies)
    ("GET", "/license-policies"),
    ("GET", "/license-policies/1"),
    ("POST", "/license-policies"),
    ("PUT", "/license-policies/1"),
    ("DELETE", "/license-policies/1"),
    # Vulnerabilities
    ("GET", "/vulnerabilities"),
    ("GET", "/vulnerabilities/1"),
    ("PATCH", "/vulnerabilities/component-vulnerabilities/1"),
    ("POST", "/vulnerabilities/1/assign"),
    # On-Call Rotations
    ("GET", "/on-call/rotations"),
    ("GET", "/on-call/rotations/1"),
    ("POST", "/on-call/rotations"),
    ("PUT", "/on-call/rotations/1"),
    ("DELETE", "/on-call/rotations/1"),
    ("GET", "/on-call/rotations/current/organization/1"),
    ("GET", "/on-call/rotations/1/participants"),
    ("POST", "/on-call/rotations/1/participants"),
    ("PUT", "/on-call/rotations/1/participants/1"),
    ("DELETE", "/on-call/rotations/1/participants/1"),
    ("GET", "/on-call/rotations/1/overrides"),
    ("POST", "/on-call/rotations/1/overrides"),
    ("PUT", "/on-call/rotations/overrides/1"),
    ("DELETE", "/on-call/rotations/overrides/1"),
    ("GET", "/on-call/rotations/1/history"),
    # Escalation policies are at /rotations/<id>/escalations and /rotations/escalations/<id>
    ("GET", "/on-call/rotations/1/escalations"),
    ("POST", "/on-call/rotations/1/escalations"),
    ("PUT", "/on-call/rotations/escalations/1"),
    ("DELETE", "/on-call/rotations/escalations/1"),
    # Costs
    ("GET", "/costs/entity/1"),
    ("POST", "/costs/entity/1"),
]


@pytest.fixture(scope="module")
def route_app():
    """Create Flask app for route existence checks only.

    create_app() returns a WsgiToAsgi wrapper; unwrap to get the Flask app.
    """
    from apps.api.main import create_app

    asgi_app = create_app("testing")
    # Unwrap ASGI adapter to get the underlying Flask app
    flask_app = getattr(asgi_app, "wsgi_application", asgi_app)
    flask_app.config["TESTING"] = True
    return flask_app


@pytest.fixture(scope="module")
def url_adapter(route_app):
    """Werkzeug URL map adapter for pure route-matching checks.

    Uses the URL map directly instead of making HTTP requests,
    so handler-level 404s (resource not found in DB) don't cause
    false failures.
    """
    return route_app.url_map.bind("")


@pytest.mark.parametrize("method,path", FRONTEND_ROUTES, ids=[f"{m} {p}" for m, p in FRONTEND_ROUTES])
def test_route_exists(url_adapter, method, path):
    """Frontend path must have a matching URL rule in Flask's URL map.

    This checks the URL map directly rather than making HTTP requests,
    avoiding false failures from handlers that return 404 for missing
    DB records. A MethodNotAllowed exception still means the route is
    registered (just for different HTTP methods).
    """
    from werkzeug.exceptions import MethodNotAllowed, NotFound
    from werkzeug.routing import RequestRedirect

    full_path = f"/api/v1{path}"

    try:
        url_adapter.match(full_path, method=method)
    except RequestRedirect:
        pass  # Route exists, just redirects (e.g. trailing slash)
    except MethodNotAllowed:
        pytest.fail(
            f"{method} {full_path} — route exists but method not allowed. "
            f"Check that the backend registers this HTTP method."
        )
    except NotFound:
        pytest.fail(
            f"{method} {full_path} — no matching URL rule in Flask. "
            f"Check that the frontend path matches the backend blueprint route."
        )
