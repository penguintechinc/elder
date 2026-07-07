"""On-call rotation management API blueprint router.

This module aggregates all on-call rotation endpoints from separate modules.
"""

# flake8: noqa: E501


from quart import Blueprint

from apps.api.modules.services_oncall.routes import (
    on_call_rotations_crud,
    on_call_rotations_history,
    on_call_rotations_participants,
    on_call_webhooks,
)

# Create main blueprint
bp = Blueprint("on_call_rotations", __name__)

# Sub-blueprints imported at module level
# Register all sub-blueprints
bp.register_blueprint(on_call_rotations_crud.bp, url_prefix="/rotations")
bp.register_blueprint(on_call_rotations_participants.bp, url_prefix="/rotations")
bp.register_blueprint(on_call_rotations_history.bp, url_prefix="/rotations")
bp.register_blueprint(on_call_webhooks.bp, url_prefix="/webhooks")
