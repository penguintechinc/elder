"""Main Flask application for Elder."""

# flake8: noqa: E501


import logging
import os

import structlog
from asgiref.wsgi import WsgiToAsgi
from flask import Flask, jsonify
from flask_cors import CORS
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from prometheus_flask_exporter import PrometheusMetrics

from apps.api.config import get_config
from apps.api.logging_config import setup_logging
from shared.database import ensure_database_ready, init_db, log_startup_status, run_migrations

# Configure standard library logging
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer(),
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()


def create_app(config_name: str = None) -> Flask:
    """
    Create and configure Flask application.

    Args:
        config_name: Configuration name (development, production, testing)

    Returns:
        Configured Flask application
    """
    # Create Flask app
    app = Flask(__name__)

    # Load configuration
    if config_name is None:
        config_name = os.getenv("FLASK_ENV", "development")

    config = get_config(config_name)
    app.config.from_object(config)
    config.init_app(app)

    # Setup logging (must be after config but before other initializations)
    setup_logging(app)

    # Initialize extensions
    _init_extensions(app)

    # Check database connectivity
    db_status = ensure_database_ready(app)
    log_startup_status(db_status)

    if not db_status["connected"]:
        raise RuntimeError("Cannot start application - database not available")

    # Hybrid Database Initialization:
    # 1. Run SQLAlchemy/Alembic migrations for schema management
    # 2. Initialize PyDAL for runtime queries
    run_migrations(app)
    init_db(app)

    # Initialize license client
    _init_license_client(app)

    # Initialize access review scheduler (v3.1.0)
    _init_access_review_scheduler(app)

    # Register blueprints
    _register_blueprints(app)

    # Register error handlers
    _register_error_handlers(app)

    # Health check endpoint
    @app.route("/healthz")
    def health_check():
        """Health check endpoint."""
        return jsonify({"status": "healthy", "service": "elder"}), 200

    # API status endpoint (for console version check and monitoring)
    @app.route("/api/v1/status")
    def api_status():
        """API status endpoint for console version checks."""
        return jsonify({
            "status": "operational",
            "service": "elder",
            "version": app.config.get("APP_VERSION", "0.0.0"),
            "environment": app.config.get("ENV", "production"),
        }), 200

    logger.info(
        "elder_app_created",
        config=config_name,
        debug=app.config["DEBUG"],
        version=app.config["APP_VERSION"],
    )

    # Wrap Flask WSGI app with ASGI adapter for uvicorn
    return WsgiToAsgi(app)


def _init_extensions(app: Flask) -> None:
    """
    Initialize Flask extensions.

    Args:
        app: Flask application
    """
    # CORS
    CORS(
        app,
        origins=app.config["CORS_ORIGINS"],
        methods=app.config["CORS_METHODS"],
        allow_headers=app.config["CORS_ALLOW_HEADERS"],
        supports_credentials=app.config.get("CORS_SUPPORTS_CREDENTIALS", True),
        expose_headers=app.config.get("CORS_EXPOSE_HEADERS", []),
    )

    # CSRF Protection - Exempt API routes (they use JWT, not cookies)
    csrf = CSRFProtect(app)

    # Exempt API routes from CSRF since they use JWT Bearer tokens
    app.config["WTF_CSRF_CHECK_DEFAULT"] = False

    # Login Manager
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"

    @login_manager.user_loader
    def load_user(user_id):
        """Load user by ID."""
        from apps.api.models import Identity

        return Identity.query.get(int(user_id))

    # Prometheus Metrics
    if app.config.get("METRICS_ENABLED"):
        metrics = PrometheusMetrics(app)
        metrics.info(
            "elder_app_info", "Elder Application", version=app.config["APP_VERSION"]
        )

    logger.info("extensions_initialized")


def _init_license_client(app: Flask) -> None:
    """
    Initialize PenguinTech License Server client.

    Args:
        app: Flask application
    """
    try:
        from penguin_licensing import get_license_client

        client = get_license_client()
        validation = client.validate()
        logger.info(
            "license_client_initialized",
            tier=validation.tier,
            enterprise_features_enabled=(validation.tier == "enterprise"),
        )
    except Exception as e:
        logger.warning(
            "license_client_init_failed",
            error=str(e),
            fallback="community",
        )


def _init_access_review_scheduler(app: Flask) -> None:
    """
    Initialize access review scheduler for periodic reviews.

    Args:
        app: Flask application
    """
    from apps.api.services.access_review.scheduler import init_scheduler

    try:
        init_scheduler(app.db)
        logger.info("access_review_scheduler_initialized")
    except Exception as e:
        logger.warning(
            "access_review_scheduler_init_failed",
            error=str(e),
        )


def _register_blueprints(app: Flask) -> None:
    """
    Register Flask blueprints (async and sync).

    Args:
        app: Flask application
    """
    # Import blueprints (async versions where available)
    from apps.api.api.v1 import access_reviews  # v3.1.0: Access Review System
    from apps.api.api.v1 import audit  # Phase 8: Audit System Enhancement
    from apps.api.api.v1 import audit_enterprise  # v2.2.0: Enhanced Audit & Compliance
    from apps.api.api.v1 import backup  # Phase 10: Backup & Data Management
    from apps.api.api.v1 import builtin_secrets  # v2.0.0: Built-in Secrets Storage
    from apps.api.api.v1 import certificates  # v2.4.0: Certificate Management
    from apps.api.api.v1 import costs  # Cost tracking
    from apps.api.api.v1 import data_stores  # v3.0.0: Data Store Tracking
    from apps.api.api.v1 import discovery  # Phase 5: Cloud Auto-Discovery
    from apps.api.api.v1 import group_membership  # v3.x: Group Membership Management
    from apps.api.api.v1 import iam  # Phase 4: IAM Integration
    from apps.api.api.v1 import ipam  # v2.3.0: IP Address Management
    from apps.api.api.v1 import keys  # Phase 3: Keys Management
    from apps.api.api.v1 import license_policies  # v3.0.0: License Compliance
    from apps.api.api.v1 import logs  # Admin Log Viewer
    from apps.api.api.v1 import networking  # v2.0.0: Networking Resources & Topology
    from apps.api.api.v1 import on_call_rotations  # v3.x: On-Call Rotation Management
    from apps.api.api.v1 import portal_auth  # v2.2.0: Portal User Authentication
    from apps.api.api.v1 import sbom  # v3.0.0: SBOM Component Tracking
    from apps.api.api.v1 import sbom_scans  # v3.0.0: SBOM Scan Management
    from apps.api.api.v1 import sbom_schedules  # v3.0.0: SBOM Scan Schedules
    from apps.api.api.v1 import search  # Phase 10: Advanced Search
    from apps.api.api.v1 import secrets  # Phase 2: Secrets Management
    from apps.api.api.v1 import services  # v2.3.0: Services Tracking
    from apps.api.api.v1 import software  # v2.3.0: Software Tracking
    from apps.api.api.v1 import sso  # v2.2.0: SSO/SAML/SCIM
    from apps.api.api.v1 import tenants  # v2.2.0: Tenant Management
    from apps.api.api.v1 import vulnerabilities  # v3.0.0: Vulnerability Management
    from apps.api.api.v1 import webhooks  # Phase 9: Webhook & Notification System
    from apps.api.api.v1 import (  # Phase 7: Google Workspace Integration
        api_keys,
        auth,
        dependencies,
        entities,
        entity_types,
        google_workspace,
        graph,
        identities,
        issues,
        labels,
        lookup,
        lookup_village_id,
        metadata,
        milestones,
        organization_tree,
        organizations_pydal,
        profile,
        projects,
        resource_roles,
        sync,
        users,
    )
    from apps.api.web import routes as web

    # Register API v1 blueprints
    api_prefix = app.config["API_PREFIX"]

    # Use async organizations_pydal blueprint (PyDAL + async/await)
    app.register_blueprint(
        organizations_pydal.bp, url_prefix=f"{api_prefix}/organizations"
    )
    app.register_blueprint(entities.bp, url_prefix=f"{api_prefix}/entities")
    app.register_blueprint(entity_types.bp, url_prefix=f"{api_prefix}/entity-types")
    app.register_blueprint(dependencies.bp, url_prefix=f"{api_prefix}/dependencies")
    app.register_blueprint(graph.bp, url_prefix=f"{api_prefix}/graph")
    app.register_blueprint(auth.bp, url_prefix=f"{api_prefix}/auth")
    app.register_blueprint(profile.bp, url_prefix=f"{api_prefix}/profile")
    app.register_blueprint(identities.bp, url_prefix=f"{api_prefix}/identities")
    app.register_blueprint(api_keys.bp, url_prefix=f"{api_prefix}/api-keys")
    app.register_blueprint(users.bp, url_prefix=f"{api_prefix}/users")

    # Enterprise feature blueprints
    app.register_blueprint(resource_roles.bp, url_prefix=f"{api_prefix}/resource-roles")
    app.register_blueprint(issues.bp, url_prefix=f"{api_prefix}/issues")
    app.register_blueprint(labels.bp, url_prefix=f"{api_prefix}/labels")
    app.register_blueprint(metadata.bp, url_prefix=f"{api_prefix}/metadata")
    app.register_blueprint(projects.bp, url_prefix=f"{api_prefix}/projects")
    app.register_blueprint(milestones.bp, url_prefix=f"{api_prefix}/milestones")
    app.register_blueprint(organization_tree.bp, url_prefix=f"{api_prefix}")
    app.register_blueprint(sync.bp, url_prefix=f"{api_prefix}/sync")
    app.register_blueprint(
        group_membership.bp, url_prefix=f"{api_prefix}/group-membership"
    )  # v3.x: Group Membership Management
    app.register_blueprint(
        access_reviews.bp, url_prefix=f"{api_prefix}"
    )  # v3.1.0: Access Review System

    # v1.2.0 Feature blueprints
    app.register_blueprint(secrets.bp, url_prefix=f"{api_prefix}/secrets")  # Phase 2
    app.register_blueprint(keys.bp, url_prefix=f"{api_prefix}/keys")  # Phase 3
    app.register_blueprint(iam.bp, url_prefix=f"{api_prefix}/iam")  # Phase 4
    app.register_blueprint(
        discovery.bp, url_prefix=f"{api_prefix}/discovery"
    )  # Phase 5
    app.register_blueprint(audit.bp, url_prefix=f"{api_prefix}/audit")  # Phase 8
    app.register_blueprint(logs.bp, url_prefix=f"{api_prefix}/logs")  # Admin Log Viewer
    app.register_blueprint(webhooks.bp, url_prefix=f"{api_prefix}/webhooks")  # Phase 9
    app.register_blueprint(search.bp, url_prefix=f"{api_prefix}/search")  # Phase 10
    app.register_blueprint(backup.bp, url_prefix=f"{api_prefix}/backup")  # Phase 10
    app.register_blueprint(
        google_workspace.bp, url_prefix=f"{api_prefix}/google-workspace"
    )  # Phase 7

    # v2.0.0 Feature blueprints
    app.register_blueprint(
        networking.bp
    )  # Networking already has /api/v1/networking prefix
    app.register_blueprint(
        builtin_secrets.bp
    )  # Built-in secrets already has /api/v1/builtin-secrets prefix

    # v2.2.0 Enterprise Edition blueprints
    app.register_blueprint(
        portal_auth.bp, url_prefix=f"{api_prefix}/portal-auth"
    )  # Portal user authentication
    app.register_blueprint(sso.bp, url_prefix=f"{api_prefix}/sso")  # SSO/SAML/SCIM
    app.register_blueprint(
        audit_enterprise.bp, url_prefix=f"{api_prefix}/audit-enterprise"
    )  # Enhanced audit & compliance
    app.register_blueprint(
        tenants.bp, url_prefix=f"{api_prefix}/tenants"
    )  # Tenant management

    # v2.3.0 Feature blueprints
    app.register_blueprint(
        software.bp, url_prefix=f"{api_prefix}/software"
    )  # Software tracking
    app.register_blueprint(
        services.bp, url_prefix=f"{api_prefix}/services"
    )  # Services tracking
    app.register_blueprint(
        ipam.bp, url_prefix=f"{api_prefix}/ipam"
    )  # IP Address Management
    app.register_blueprint(
        data_stores.bp, url_prefix=f"{api_prefix}/data-stores"
    )  # v3.0.0: Data Store Tracking (Community)
    app.register_blueprint(
        sbom.bp, url_prefix=f"{api_prefix}/sbom/components"
    )  # v3.0.0: SBOM Component Tracking
    app.register_blueprint(
        sbom_scans.bp, url_prefix=f"{api_prefix}/sbom/scans"
    )  # v3.0.0: SBOM Scan Management
    app.register_blueprint(
        sbom_schedules.bp, url_prefix=f"{api_prefix}/sbom/schedules"
    )  # v3.0.0: SBOM Scan Schedules
    app.register_blueprint(
        vulnerabilities.bp, url_prefix=f"{api_prefix}/vulnerabilities"
    )  # v3.0.0: Vulnerability Management
    app.register_blueprint(
        license_policies.bp, url_prefix=f"{api_prefix}/license-policies"
    )  # v3.0.0: License Compliance

    # v2.4.0 Feature blueprints
    app.register_blueprint(
        certificates.bp, url_prefix=f"{api_prefix}/certificates"
    )  # Certificate management
    app.register_blueprint(
        costs.bp, url_prefix=f"{api_prefix}/costs"
    )  # Cost tracking

    # v3.x Feature blueprints
    app.register_blueprint(
        on_call_rotations.bp, url_prefix=f"{api_prefix}/on-call"
    )  # On-Call Rotation Management

    # Public lookup endpoint (no /api/v1 prefix for cleaner URLs)
    app.register_blueprint(lookup.bp, url_prefix="/lookup")

    # Village ID lookup endpoint (no prefix - accessible at /id/{village_id})
    app.register_blueprint(lookup_village_id.bp, url_prefix="")

    # Web UI blueprint (root routes)
    app.register_blueprint(web.bp, url_prefix="")

    logger.info(
        "blueprints_registered",
        api_prefix=api_prefix,
        blueprints=[
            "organizations (async PyDAL)",
            "entities",
            "dependencies",
            "graph",
            "auth",
            "identities",
            "resource_roles",
            "issues",
            "metadata",
            "lookup",
            "web",
        ],
    )


def _register_error_handlers(app: Flask) -> None:
    """
    Register error handlers.

    Args:
        app: Flask application
    """

    @app.errorhandler(400)
    def bad_request(error):
        """Handle 400 Bad Request."""
        logger.warning("bad_request", error=str(error))
        return jsonify({"error": "Bad Request", "message": "Invalid request"}), 400

    @app.errorhandler(401)
    def unauthorized(error):
        """Handle 401 Unauthorized."""
        return (
            jsonify({"error": "Unauthorized", "message": "Authentication required"}),
            401,
        )

    @app.errorhandler(403)
    def forbidden(error):
        """Handle 403 Forbidden."""
        return (
            jsonify({"error": "Forbidden", "message": "Insufficient permissions"}),
            403,
        )

    @app.errorhandler(404)
    def not_found(error):
        """Handle 404 Not Found."""
        return jsonify({"error": "Not Found", "message": "Resource not found"}), 404

    @app.errorhandler(429)
    def rate_limit_exceeded(error):
        """Handle 429 Rate Limit Exceeded."""
        return (
            jsonify({"error": "Rate Limit Exceeded", "message": "Too many requests"}),
            429,
        )

    @app.errorhandler(500)
    def internal_server_error(error):
        """Handle 500 Internal Server Error."""
        logger.error("internal_server_error", error=str(error))
        return (
            jsonify({"error": "Internal Server Error", "message": "An error occurred"}),
            500,
        )

    logger.info("error_handlers_registered")


if __name__ == "__main__":
    # Create and run application directly with Flask dev server
    # Note: create_app() returns ASGI app, so we need to unwrap it
    import uvicorn

    asgi_app = create_app()
    uvicorn.run(
        asgi_app,
        host=os.getenv("FLASK_HOST", "0.0.0.0"),
        port=int(os.getenv("FLASK_PORT", 5000)),
    )
