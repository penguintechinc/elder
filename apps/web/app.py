"""
py4web application with license integration
"""

# flake8: noqa: E501


import logging
import os
import sys

from py4web import DAL, URL, Field, action, redirect, request, response
from py4web.utils.auth import Auth
from py4web.utils.cors import CORS
from py4web.utils.form import Form, FormStyleBulma
from py4web.utils.mailer import Mailer
from pydal.validators import IS_EMAIL, IS_IN_SET, IS_NOT_EMPTY

# Add shared modules to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from penguin_licensing import (  # noqa: E402
    FeatureNotAvailableError,
    LicenseValidationError,
    get_client,
    initialize_licensing,
    requires_feature,
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Database configuration
DB_URI = os.getenv("DATABASE_URL", "sqlite://storage.db")

# Initialize database
db = DAL(DB_URI, pool_size=10, migrate=True, fake_migrate=False, check_reserved=["all"])

# Define database tables
db.define_table(
    "users",
    Field("username", "string", requires=IS_NOT_EMPTY(), unique=True),
    Field("email", "string", requires=IS_EMAIL(), unique=True),
    Field("first_name", "string", requires=IS_NOT_EMPTY()),
    Field("last_name", "string", requires=IS_NOT_EMPTY()),
    Field("role", "string", default="user", requires=IS_IN_SET(["user", "admin"])),
    Field("created_on", "datetime", default=request.now),
    Field("is_active", "boolean", default=True),
)

db.define_table(
    "license_usage",
    Field("feature_name", "string", requires=IS_NOT_EMPTY()),
    Field("user_id", "reference users"),
    Field("usage_count", "integer", default=1),
    Field("last_used", "datetime", default=request.now),
)

# Initialize authentication
# Note: session is provided by py4web framework at runtime
auth = Auth(db, session=None)  # Session injected by @action.uses

# Initialize mailer (if needed)
mailer = Mailer(
    server=os.getenv("SMTP_SERVER", "localhost"),
    sender=os.getenv("SMTP_SENDER", "noreply@localhost"),
    login=os.getenv("SMTP_LOGIN"),
    password=os.getenv("SMTP_PASSWORD"),
    tls=True,
)

# Initialize licensing
try:
    license_validation = initialize_licensing()
    logger.info("License validation successful")
except LicenseValidationError as e:
    logger.error(f"License validation failed: {e}")
    license_validation = None


# Helper functions
def track_feature_usage(feature_name: str):
    """Track feature usage for analytics."""
    if not auth.user:
        return

    user_id = auth.user["id"]

    # Check if record exists
    existing = (
        db(
            (db.license_usage.feature_name == feature_name)
            & (db.license_usage.user_id == user_id)
        )
        .select()
        .first()
    )

    if existing:
        # Update existing record
        existing.update_record(
            usage_count=existing.usage_count + 1, last_used=request.now
        )
    else:
        # Create new record
        db.license_usage.insert(
            feature_name=feature_name,
            user_id=user_id,
            usage_count=1,
            last_used=request.now,
        )

    db.commit()


def get_license_info():
    """Get current license information."""
    client = get_client()
    if not client:
        return None

    try:
        return client.validate()
    except LicenseValidationError:
        return None


# Routes
@action("index")
@action.uses("index.html", auth, db)
def index():
    """Main dashboard."""
    license_info = get_license_info()
    features = {}

    if license_info:
        # Get available features
        client = get_client()
        if client:
            features = client.get_all_features()

    return dict(user=auth.user, license_info=license_info, features=features)


@action("api/health")
@CORS()
def api_health():
    """Health check endpoint."""
    return dict(
        status="healthy",
        version=os.getenv("VERSION", "development"),
        database="connected" if db else "disconnected",
        license="valid" if license_validation else "invalid",
    )


@action("api/license")
@action.uses(auth.user)
@CORS()
def api_license():
    """Get license information."""
    license_info = get_license_info()
    if not license_info:
        response.status = 500
        return dict(error="License validation failed")

    return dict(
        customer=license_info.get("customer"),
        tier=license_info.get("tier"),
        features=[f for f in license_info.get("features", []) if f.get("entitled")],
        expires_at=license_info.get("expires_at"),
    )


@action("api/features")
@action.uses(auth.user)
@CORS()
def api_features():
    """Get available features."""
    client = get_client()
    if not client:
        response.status = 500
        return dict(error="License client not available")

    features = client.get_all_features()
    return dict(features=features)


@action("analytics")
@action.uses("analytics.html", auth.user, db)
@requires_feature("advanced_analytics")
def analytics():
    """Advanced analytics page (requires license)."""
    track_feature_usage("advanced_analytics")

    # Get usage statistics
    usage_stats = db(db.license_usage.user_id == auth.user["id"]).select()

    # Generate some mock analytics data
    analytics_data = {
        "total_users": db(db.users).count(),
        "active_features": len(
            [f for f in get_client().get_all_features().values() if f]
        ),
        "usage_by_feature": {},
        "user_usage": usage_stats,
    }

    # Calculate usage by feature
    for stat in usage_stats:
        feature = stat.feature_name
        if feature not in analytics_data["usage_by_feature"]:
            analytics_data["usage_by_feature"][feature] = 0
        analytics_data["usage_by_feature"][feature] += stat.usage_count

    return dict(analytics=analytics_data, user=auth.user)


@action("enterprise")
@action.uses("enterprise.html", auth.user, db)
@requires_feature("enterprise_features")
def enterprise():
    """Enterprise features page."""
    track_feature_usage("enterprise_features")

    # Get all users (enterprise feature)
    users = db(db.users).select()

    # Get system-wide usage statistics
    usage_stats = db().select(
        db.license_usage.feature_name,
        db.license_usage.usage_count.sum().with_alias("total_usage"),
        groupby=db.license_usage.feature_name,
    )

    return dict(users=users, usage_stats=usage_stats, user=auth.user)


@action("admin")
@action.uses("admin.html", auth.user, db)
def admin():
    """Admin panel."""
    if not auth.user or auth.user.get("role") != "admin":
        redirect(URL("index"))

    # Get all users and system stats
    users = db(db.users).select()
    license_info = get_license_info()

    # System statistics
    stats = {
        "total_users": db(db.users).count(),
        "active_users": db(db.users.is_active is True).count(),
        "total_feature_usage": db(db.license_usage).count(),
        "license_tier": license_info.get("tier") if license_info else "Unknown",
    }

    return dict(users=users, stats=stats, license_info=license_info, user=auth.user)


@action("profile")
@action.uses("profile.html", auth.user, db)
def profile():
    """User profile page."""
    form = Form(
        db.users,
        record=auth.user,
        formstyle=FormStyleBulma,
        readonly=["username", "email"],  # Don't allow changing these
    )

    if form.accepted:
        auth.user.update(form.vars)
        db.commit()
        response.flash = "Profile updated successfully"
        redirect(URL("profile"))

    return dict(form=form, user=auth.user)


# Error handlers
@action("error/license")
@action.uses("error.html")
def license_error():
    """License error page."""
    return dict(
        error="License Required",
        message="This feature requires a license upgrade. Please contact sales for more information.",
        contact="sales@penguintech.io",
    )


# Feature gate decorator for py4web actions
def feature_required(feature_name: str):
    """Decorator to require a feature for py4web actions."""

    def decorator(func):
        def wrapper(*args, **kwargs):
            try:
                # Check if feature is available
                if not requires_feature(feature_name):
                    redirect(URL("error/license"))
                return func(*args, **kwargs)
            except FeatureNotAvailableError:
                redirect(URL("error/license"))

        wrapper.__name__ = func.__name__
        return wrapper

    return decorator


# Startup tasks
def _startup():
    """Startup tasks."""
    if license_validation:
        logger.info("Application started with valid license")
    else:
        logger.warning(
            "Application started without valid license - some features may be unavailable"
        )


# Run startup tasks
_startup()
