"""Main Quart application for Elder."""

# flake8: noqa: E501


import logging
import os

import structlog
from penguin_aaa.audit.emitter import Emitter
from penguin_aaa.audit.sinks import StdoutSink
from penguin_aaa.middleware.asgi import AuditMiddleware
from penguin_aaa.middleware.tenant import TenantMiddleware
from quart import Quart, g, jsonify, make_response
from quart_cors import cors

from apps.api.config import get_config
from apps.api.logging_config import setup_logging
from shared.database import (
    ensure_database_ready,
    init_db,
    init_sqlalchemy_tables,
    log_startup_status,
)
from shared.observability import auto_instrument_app, init_telemetry

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


def create_app(config_name: str = None) -> Quart:
    """
    Create and configure Quart application.

    Args:
        config_name: Configuration name (development, production, testing)

    Returns:
        Configured Quart application (native ASGI — no adapter needed)
    """
    app = Quart(__name__)

    # Load configuration
    if config_name is None:
        config_name = os.getenv("FLASK_ENV", "development")

    config = get_config(config_name)
    app.config.from_object(config)
    config.init_app(app)

    # Initialize OpenTelemetry (Phase 0.5) — must be before logging/DB init
    otel = init_telemetry(app.config.get("OTEL_SERVICE_NAME", "elder-api"))
    app.extensions["otel"] = otel

    # Setup logging (must be after OTel but before other initializations)
    setup_logging(app)

    # Initialize extensions
    _init_extensions(app)

    # Check database connectivity
    db_status = ensure_database_ready(app)
    log_startup_status(db_status)

    if not db_status["connected"]:
        raise RuntimeError("Cannot start application - database not available")

    # Database Initialization:
    # 1. SQLAlchemy create_all() — idempotent, creates missing tables at startup
    # 2. Initialize PyDAL with migrate=False — connects to existing tables for queries
    # NOTE: For schema migrations on existing databases, run: ./scripts/migrate.sh
    init_sqlalchemy_tables(app)
    init_db(app)

    # Initialize license client
    _init_license_client(app)

    # Initialize access review scheduler (v3.1.0)
    _init_access_review_scheduler(app)

    # Register core blueprints and load feature modules
    _register_blueprints(app)
    _load_modules(app)

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
        return (
            jsonify(
                {
                    "status": "operational",
                    "service": "elder",
                    "version": app.config.get("APP_VERSION", "0.0.0"),
                    "environment": app.config.get("ENV", "production"),
                }
            ),
            200,
        )

    logger.info(
        "elder_app_created",
        config=config_name,
        debug=app.config["DEBUG"],
        version=app.config["APP_VERSION"],
    )

    _register_before_request(app)

    # Install OTel auto-instrumentation (ASGI, Redis, psycopg, httpx)
    auto_instrument_app(app)

    return app


def create_asgi_app(config_name: str = None):
    """Return a production-ready ASGI callable wrapped with penguin-aaa middleware.

    Layer order (outermost first):
      AuditMiddleware → TenantMiddleware → Quart app

    Use this as the uvicorn entry point. Tests can use ``create_app()`` directly
    to get the bare Quart app and call ``app.test_client()``.
    """
    quart_app = create_app(config_name)
    emitter = Emitter(StdoutSink())
    asgi = TenantMiddleware(quart_app, required=False)
    asgi = AuditMiddleware(asgi, emitter)
    return asgi


def _register_before_request(app: Quart) -> None:
    """Populate g.claims from the JWT payload on every authenticated request.

    This bridges Elder's Bearer-token auth into the penguin-aaa claims format
    so that @require_scope / @require_role decorators work without an extra DB
    round-trip — the scopes and roles are embedded in the token itself.
    """

    @app.before_request
    async def populate_claims():
        from apps.api.auth.jwt_handler import get_token_from_header, verify_token

        token = get_token_from_header()
        if not token:
            return
        payload = verify_token(token)
        if not payload:
            return
        g.claims = {
            "sub": payload.get("sub", ""),
            "tenant": payload.get("tenant", ""),
            "roles": payload.get("roles", []),
            "scope": payload.get("scope", []),
        }


def _init_extensions(app: Quart) -> None:
    """Initialize Quart extensions."""
    cors(
        app,
        allow_origin=app.config["CORS_ORIGINS"],
        allow_methods=app.config["CORS_METHODS"],
        allow_headers=app.config["CORS_ALLOW_HEADERS"],
        allow_credentials=app.config.get("CORS_SUPPORTS_CREDENTIALS", True),
        expose_headers=app.config.get("CORS_EXPOSE_HEADERS", []),
    )

    logger.info("extensions_initialized")


def _init_license_client(app: Quart) -> None:
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


def _init_access_review_scheduler(app: Quart) -> None:
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


def _register_blueprints(app: Quart) -> None:
    """Register CORE (always-on) blueprints.

    Phase 0: Core blueprints that are always available. Feature modules
    are registered separately via _load_modules().
    """
    # Lookup endpoints with special prefixes
    from apps.api.api.v1 import (
        api_keys,
        audit,
        audit_enterprise,
        auth,
        backup,
        identities,
        logs,
        lookup,
        lookup_village_id,
        modules,
        portal_auth,
        profile,
        refs,
        search,
        sso,
        tenant_modules,
        tenants,
        users,
    )
    from apps.api.web import routes as web

    api_prefix = app.config["API_PREFIX"]

    # Core authentication and identity
    app.register_blueprint(auth.bp, url_prefix=f"{api_prefix}/auth")
    app.register_blueprint(profile.bp, url_prefix=f"{api_prefix}/profile")
    app.register_blueprint(identities.bp, url_prefix=f"{api_prefix}/identities")
    app.register_blueprint(api_keys.bp, url_prefix=f"{api_prefix}/api-keys")
    app.register_blueprint(users.bp, url_prefix=f"{api_prefix}/users")

    # Tenancy and administration
    app.register_blueprint(tenants.bp, url_prefix=f"{api_prefix}/tenants")
    app.register_blueprint(tenant_modules.bp, url_prefix=f"{api_prefix}")
    app.register_blueprint(portal_auth.bp, url_prefix=f"{api_prefix}/portal-auth")
    app.register_blueprint(sso.bp, url_prefix=f"{api_prefix}/sso")

    # Audit and logging
    app.register_blueprint(audit.bp, url_prefix=f"{api_prefix}/audit")
    app.register_blueprint(
        audit_enterprise.bp, url_prefix=f"{api_prefix}/audit-enterprise"
    )
    app.register_blueprint(logs.bp, url_prefix=f"{api_prefix}/logs")

    # Search and backup
    app.register_blueprint(search.bp, url_prefix=f"{api_prefix}/search")
    app.register_blueprint(backup.bp, url_prefix=f"{api_prefix}/backup")

    # Module registry endpoint
    app.register_blueprint(modules.bp, url_prefix=f"{api_prefix}")

    # Cross-reference resolution
    app.register_blueprint(refs.bp, url_prefix=f"{api_prefix}")

    # Special-prefix endpoints
    app.register_blueprint(lookup.bp, url_prefix="/lookup")
    app.register_blueprint(lookup_village_id.bp, url_prefix="")

    # Web UI blueprint (root routes)
    app.register_blueprint(web.bp, url_prefix="")

    logger.info(
        "core_blueprints_registered",
        api_prefix=api_prefix,
        count=14,
    )


def _load_modules(app: Quart) -> None:
    """Load and mount feature modules based on environment configuration.

    Resolves enabled modules from ELDER_MODULES_ENABLED env var and per-module
    overrides, then registers their blueprints. Phase 0: all modules re-export
    existing blueprints from apps.api.api.v1 with zero file moves.
    """
    from apps.api.modules.registry import mount, resolve_enabled

    try:
        enabled_modules = resolve_enabled(os.environ)
        mount(app, enabled_modules)
        logger.info(
            "feature_modules_loaded",
            module_count=len(enabled_modules),
            modules=[m.name for m in enabled_modules],
        )
    except Exception as e:
        logger.error("feature_modules_load_failed", error=str(e))
        raise


def _register_error_handlers(app: Quart) -> None:
    """Register error handlers."""

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
    import uvicorn

    uvicorn.run(
        create_asgi_app(),
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", 5000)),
    )
