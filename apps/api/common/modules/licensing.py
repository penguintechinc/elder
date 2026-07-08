"""Module licensing helper with graceful degradation.

Provides runtime license checking for feature modules with fail-soft behavior.
If the license client is unavailable or raises, gracefully degrades to allowing
the module (never crashes, never hard-denies on infra failure).
"""

from __future__ import annotations

import structlog
from typing import Any

logger = structlog.get_logger()


def module_licensed(app: Any, manifest: Any) -> bool:
    """Check if a module is licensed to run.

    Returns True if:
    - manifest.license_feature is None (always available)
    - license client is not available (graceful degradation)
    - license client confirms the feature is licensed

    Returns False if:
    - manifest.license_feature is set AND license client says it's NOT licensed

    On any error (license client unavailable, check fails, etc.):
    - Logs warning, defaults to True (allow the module)
    - Never crashes, never hard-denies on infra failure

    Args:
        app: Quart application with license_client in app.extensions
        manifest: ModuleManifest with optional .license_feature field

    Returns:
        bool: True if module is licensed (or license check unavailable), False if explicitly unlicensed
    """
    # Module doesn't require a license feature
    if manifest.license_feature is None:
        return True

    try:
        license_client = app.extensions.get("license_client")

        # No license client available (licensing disabled or failed at init)
        if license_client is None:
            logger.warning(
                "license_check_skipped_no_client",
                module=manifest.name,
                feature=manifest.license_feature,
                fallback="allow",
            )
            return True

        # Check if the feature is licensed
        is_licensed = license_client.check_feature(manifest.license_feature)
        logger.debug(
            "license_checked",
            module=manifest.name,
            feature=manifest.license_feature,
            licensed=is_licensed,
        )
        return is_licensed

    except Exception as e:
        # Graceful degradation on any error
        logger.warning(
            "license_check_failed",
            module=manifest.name,
            feature=getattr(manifest, "license_feature", None),
            error=str(e),
            fallback="allow",
        )
        return True  # Default to allowing the module
