"""API v1 package for Elder."""

# flake8: noqa: E501


from quart import Blueprint

# On-call rotations routes - imported early to register blueprints
from apps.api.api.v1 import on_call_rotations

# Create main v1 blueprint
api_v1 = Blueprint("api_v1", __name__)

api_v1.register_blueprint(on_call_rotations.bp, url_prefix="/on-call")
