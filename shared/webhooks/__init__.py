"""Webhook notification utilities for Elder."""

# flake8: noqa: E501


# Note: This module is in shared/ but will be moved to apps/api/webhooks/
# For now, import from the local module
try:
    from apps.api.webhooks.issue_webhooks import send_issue_created_webhooks
except ImportError:
    # Fallback for backward compatibility
    from .issue_webhooks import send_issue_created_webhooks

__all__ = ["send_issue_created_webhooks"]
