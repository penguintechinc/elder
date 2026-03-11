"""
Structured logging package for Elder application.
"""

# flake8: noqa: E501


# Re-export sanitizer availability so callers can check
from .logger import _HAS_SANITIZER as HAS_SANITIZED_LOGGING
from .logger import StructuredLogger, configure_logging_from_env

__all__ = ["StructuredLogger", "configure_logging_from_env", "HAS_SANITIZED_LOGGING"]
