"""Web UI routes for Elder."""

# flake8: noqa: E501


import os

from flask import Blueprint, flash, redirect, render_template, url_for
from flask_login import current_user, login_required, logout_user

from apps.api.licensing_fallback import get_license_client

bp = Blueprint("web", __name__)


def get_template_context():
    """Get common template context variables."""
    context = {
        "app_version": os.getenv("APP_VERSION", "0.1.0"),
        "current_user": current_user if current_user.is_authenticated else None,
    }

    # Add license tier if user is authenticated
    if current_user.is_authenticated:
        try:
            license_client = get_license_client()
            validation = license_client.validate()
            context["license_tier"] = validation.tier
        except:
            context["license_tier"] = "community"

    return context


# ============================================================================
# Authentication Routes
# ============================================================================


@bp.route("/")
def index():
    """Home page - redirect to dashboard if logged in, else login."""
    if current_user.is_authenticated:
        return redirect(url_for("web.dashboard"))
    return redirect(url_for("web.login"))


@bp.route("/login", methods=["GET"])
def login():
    """Login page."""
    if current_user.is_authenticated:
        return redirect(url_for("web.dashboard"))
    return render_template("auth/login.html", **get_template_context())


@bp.route("/register", methods=["GET"])
def register():
    """Registration page."""
    if current_user.is_authenticated:
        return redirect(url_for("web.dashboard"))
    return render_template("auth/register.html", **get_template_context())


@bp.route("/logout")
@login_required
def logout():
    """Logout user."""
    logout_user()
    flash("You have been logged out successfully.", "success")
    return redirect(url_for("web.login"))


# ============================================================================
# Dashboard & Main Pages
# ============================================================================


@bp.route("/dashboard")
@login_required
def dashboard():
    """Main dashboard."""
    return render_template("dashboard.html", **get_template_context())


@bp.route("/graph")
@login_required
def graph():
    """Graph visualization page."""
    return render_template("graph.html", **get_template_context())


@bp.route("/profile")
@login_required
def profile():
    """User profile page."""
    return render_template("profile.html", **get_template_context())


# ============================================================================
# Organization Routes
# ============================================================================


@bp.route("/organizations")
@login_required
def organizations():
    """Organizations list page."""
    return render_template("organizations/list.html", **get_template_context())


@bp.route("/organizations/new")
@login_required
def create_organization():
    """Create organization page."""
    return render_template(
        "organizations/form.html", mode="create", **get_template_context()
    )


@bp.route("/organizations/<int:id>")
@login_required
def view_organization(id):
    """View organization details."""
    return render_template(
        "organizations/view.html", org_id=id, **get_template_context()
    )


@bp.route("/organizations/<int:id>/edit")
@login_required
def edit_organization(id):
    """Edit organization page."""
    return render_template(
        "organizations/form.html", mode="edit", org_id=id, **get_template_context()
    )


# ============================================================================
# Entity Routes
# ============================================================================


@bp.route("/entities")
@login_required
def entities():
    """Entities list page."""
    return render_template("entities/list.html", **get_template_context())


@bp.route("/entities/new")
@login_required
def create_entity():
    """Create entity page."""
    return render_template(
        "entities/form.html", mode="create", **get_template_context()
    )


@bp.route("/entities/<int:id>")
@login_required
def view_entity(id):
    """View entity details."""
    return render_template("entities/view.html", entity_id=id, **get_template_context())


@bp.route("/entities/<int:id>/edit")
@login_required
def edit_entity(id):
    """Edit entity page."""
    return render_template(
        "entities/form.html", mode="edit", entity_id=id, **get_template_context()
    )


# ============================================================================
# Issues Routes (Enterprise)
# ============================================================================


@bp.route("/issues")
@login_required
def issues():
    """Issues list page (enterprise feature)."""
    context = get_template_context()
    if context.get("license_tier") != "enterprise":
        flash("Issues feature requires an Enterprise license.", "warning")
        return redirect(url_for("web.dashboard"))
    return render_template("issues/list.html", **context)


@bp.route("/issues/new")
@login_required
def create_issue():
    """Create issue page (enterprise feature)."""
    context = get_template_context()
    if context.get("license_tier") != "enterprise":
        flash("Issues feature requires an Enterprise license.", "warning")
        return redirect(url_for("web.dashboard"))
    return render_template("issues/form.html", mode="create", **context)


@bp.route("/issues/<int:id>")
@login_required
def view_issue(id):
    """View issue details (enterprise feature)."""
    context = get_template_context()
    if context.get("license_tier") != "enterprise":
        flash("Issues feature requires an Enterprise license.", "warning")
        return redirect(url_for("web.dashboard"))
    return render_template("issues/view.html", issue_id=id, **context)


# ============================================================================
# Error Handlers
# ============================================================================


@bp.errorhandler(404)
def not_found(error):
    """404 error handler."""
    return render_template("errors/404.html", **get_template_context()), 404


@bp.errorhandler(500)
def internal_error(error):
    """500 error handler."""
    return render_template("errors/500.html", **get_template_context()), 500
