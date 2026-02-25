"""Fallback licensing utilities when penguin_licensing module unavailable.

Provides no-op implementations of licensing decorators and clients for
development/community editions where the enterprise licensing module
is not available.
"""

import logging

logger = logging.getLogger(__name__)

# Try to import from penguin_licensing, provide fallbacks if unavailable
try:
    from penguin_licensing import license_required, get_license_client
except ImportError:
    logger.warning("penguin_licensing module not available, using fallback implementations")

    def license_required(*args, **kwargs):
        """Fallback decorator when penguin_licensing not available.

        Acts as a passthrough decorator that doesn't enforce licensing.
        """
        def decorator(f):
            return f
        # Handle both @license_required and @license_required(feature="name")
        if len(args) == 1 and callable(args[0]):
            return args[0]
        return decorator

    def get_license_client():
        """Fallback license client that returns None."""
        return None


__all__ = ["license_required", "get_license_client"]
