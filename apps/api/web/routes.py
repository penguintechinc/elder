"""Web UI routes for Elder."""

# flake8: noqa: E501


import os

from quart import Blueprint, flash, redirect, render_template, url_for

from apps.api.auth.web_auth import clear_session_cookie, get_web_user, web_login_required
from apps.api.licensing_fallback import get_license_client

bp = Blueprint("web", __name__)


def get_template_context():
    """Get common template context variables."""
    user = get_web_user()
    context = {
        "app_version": os.getenv("APP_VERSION", "0.1.0"),
        "current_user": user,
    }

    if user is not None:
        try:
            license_client = get_license_client()
            validation = license_client.validate()
            context["license_tier"] = validation.tier
        except Exception:
            context["license_tier"] = "community"

    return context


# ============================================================================
# Authentication Routes
# ============================================================================


@bp.route("/")
async def index():
    """Home page - redirect to dashboard if logged in, else login."""
    if get_web_user() is not None:
        return redirect(url_for("web.dashboard"))
    return redirect(url_for("web.login"))


@bp.route("/login", methods=["GET"])
async def login():
    """Login page."""
    if get_web_user() is not None:
        return redirect(url_for("web.dashboard"))
    return await render_template("auth/login.html", **get_template_context())


@bp.route("/register", methods=["GET"])
async def register():
    """Registration page."""
    if get_web_user() is not None:
        return redirect(url_for("web.dashboard"))
    return await render_template("auth/register.html", **get_template_context())


@bp.route("/logout")
@web_login_required
async def logout():
    """Logout user."""
    flash("You have been logged out successfully.", "success")
    response = redirect(url_for("web.login"))
    return clear_session_cookie(response)


# ============================================================================
# Dashboard & Main Pages
# ============================================================================


@bp.route("/dashboard")
@web_login_required
async def dashboard():
    """Main dashboard."""
    return await render_template("dashboard.html", **get_template_context())


@bp.route("/graph")
@web_login_required
async def graph():
    """Graph visualization page."""
    return await render_template("graph.html", **get_template_context())


@bp.route("/profile")
@web_login_required
async def profile():
    """User profile page."""
    return await render_template("profile.html", **get_template_context())


# ============================================================================
# Organization Routes
# ============================================================================


@bp.route("/organizations")
@web_login_required
async def organizations():
    """Organizations list page."""
    return await render_template("organizations/list.html", **get_template_context())


@bp.route("/organizations/new")
@web_login_required
async def create_organization():
    """Create organization page."""
    return await render_template(
        "organizations/form.html", mode="create", **get_template_context()
    )


@bp.route("/organizations/<int:id>")
@web_login_required
async def view_organization(id):
    """View organization details."""
    return await render_template(
        "organizations/view.html", org_id=id, **get_template_context()
    )


@bp.route("/organizations/<int:id>/edit")
@web_login_required
async def edit_organization(id):
    """Edit organization page."""
    return await render_template(
        "organizations/form.html", mode="edit", org_id=id, **get_template_context()
    )


# ============================================================================
# Entity Routes
# ============================================================================


@bp.route("/entities")
@web_login_required
async def entities():
    """Entities list page."""
    return await render_template("entities/list.html", **get_template_context())


@bp.route("/entities/new")
@web_login_required
async def create_entity():
    """Create entity page."""
    return await render_template(
        "entities/form.html", mode="create", **get_template_context()
    )


@bp.route("/entities/<int:id>")
@web_login_required
async def view_entity(id):
    """View entity details."""
    return await render_template("entities/view.html", entity_id=id, **get_template_context())


@bp.route("/entities/<int:id>/edit")
@web_login_required
async def edit_entity(id):
    """Edit entity page."""
    return await render_template(
        "entities/form.html", mode="edit", entity_id=id, **get_template_context()
    )


# ============================================================================
# Issues Routes (Enterprise)
# ============================================================================


@bp.route("/issues")
@web_login_required
async def issues():
    """Issues list page (enterprise feature)."""
    context = get_template_context()
    if context.get("license_tier") != "enterprise":
        flash("Issues feature requires an Enterprise license.", "warning")
        return redirect(url_for("web.dashboard"))
    return await render_template("issues/list.html", **context)


@bp.route("/issues/new")
@web_login_required
async def create_issue():
    """Create issue page (enterprise feature)."""
    context = get_template_context()
    if context.get("license_tier") != "enterprise":
        flash("Issues feature requires an Enterprise license.", "warning")
        return redirect(url_for("web.dashboard"))
    return await render_template("issues/form.html", mode="create", **context)


@bp.route("/issues/<int:id>")
@web_login_required
async def view_issue(id):
    """View issue details (enterprise feature)."""
    context = get_template_context()
    if context.get("license_tier") != "enterprise":
        flash("Issues feature requires an Enterprise license.", "warning")
        return redirect(url_for("web.dashboard"))
    return await render_template("issues/view.html", issue_id=id, **context)


# ============================================================================
# SPA Catch-All Route
# ============================================================================


@bp.route("/<path:path>")
async def spa_catch_all(path):
    """Catch-all route for SPA client-side routing."""
    if path.startswith("api/"):
        from quart import jsonify
        return jsonify({"error": "Not found"}), 404
    return await render_template("dashboard.html", **get_template_context())


# ============================================================================
# Error Handlers
# ============================================================================


@bp.errorhandler(404)
async def not_found(error):
    """404 error handler."""
    from quart import request
    if request.path.startswith("/api/"):
        from quart import jsonify
        return jsonify({"error": "Not found"}), 404
    return await render_template("dashboard.html", **get_template_context()), 404


@bp.errorhandler(500)
async def internal_error(error):
    """500 error handler."""
    from quart import request, jsonify
    if request.path.startswith("/api/"):
        return jsonify({"error": "Internal server error"}), 500
    return await render_template("dashboard.html", **get_template_context()), 500
