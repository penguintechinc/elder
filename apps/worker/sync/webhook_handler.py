"""Webhook handler infrastructure for real-time sync.

This module provides a unified webhook handling framework for all supported
platforms (GitHub, GitLab, Jira, Trello, OpenProject).

Webhooks enable real-time synchronization by receiving push notifications
when resources are created, updated, or deleted on external platforms.
"""

# flake8: noqa: E501

import hashlib
import hmac
import json
from collections.abc import Callable
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from apps.worker.sync.base import (
    BaseSyncClient,
    ResourceType,
    SyncDirection,
    SyncOperation,
    SyncResult,
    SyncStatus,
)
from apps.worker.sync.conflict_resolver import ConflictResolver


class WebhookEvent(Enum):
    """Types of webhook events."""

    ISSUE_CREATED = "issue_created"
    ISSUE_UPDATED = "issue_updated"
    ISSUE_DELETED = "issue_deleted"
    ISSUE_CLOSED = "issue_closed"
    ISSUE_REOPENED = "issue_reopened"
    PROJECT_CREATED = "project_created"
    PROJECT_UPDATED = "project_updated"
    PROJECT_DELETED = "project_deleted"
    MILESTONE_CREATED = "milestone_created"
    MILESTONE_UPDATED = "milestone_updated"
    MILESTONE_DELETED = "milestone_deleted"
    MILESTONE_CLOSED = "milestone_closed"
    LABEL_CREATED = "label_created"
    LABEL_UPDATED = "label_updated"
    LABEL_DELETED = "label_deleted"
    COMMENT_CREATED = "comment_created"
    COMMENT_UPDATED = "comment_updated"
    COMMENT_DELETED = "comment_deleted"
    PING = "ping"
    UNKNOWN = "unknown"


class WebhookPayload:
    """Parsed webhook payload.

    Attributes:
        event_type: Type of webhook event
        platform: Source platform (github, gitlab, etc.)
        resource_type: Type of resource (issue, project, etc.)
        resource_id: External resource ID
        action: Action performed (created, updated, deleted)
        data: Full webhook data
        timestamp: Event timestamp
    """

    def __init__(
        self,
        event_type: WebhookEvent,
        platform: str,
        resource_type: ResourceType,
        resource_id: str,
        action: str,
        data: dict[str, Any],
        timestamp: datetime | None = None,
    ):
        """Initialize webhook payload."""
        self.event_type = event_type
        self.platform = platform
        self.resource_type = resource_type
        self.resource_id = resource_id
        self.action = action
        self.data = data
        self.timestamp = timestamp or datetime.now(UTC)


class WebhookHandler:
    """Generic webhook handler for all platforms.

    Handles webhook validation, parsing, and routing to appropriate sync clients.
    """

    def __init__(
        self,
        platform: str,
        secret: str | None,
        sync_client: BaseSyncClient,
        conflict_resolver: ConflictResolver,
        logger: Any,
        require_secret: bool = False,
    ):
        """Initialize webhook handler.

        Args:
            platform: Platform name (github, gitlab, jira, trello, openproject)
            secret: Webhook secret for signature validation
            sync_client: Platform-specific sync client
            conflict_resolver: Conflict resolution engine
            logger: Logger instance
            require_secret: When True, fail closed if no webhook secret is configured
        """
        self.platform = platform.lower()
        self.secret = secret
        self.sync_client = sync_client
        self.conflict_resolver = conflict_resolver
        self.logger = logger
        self.require_secret = require_secret

        # Platform-specific parsers
        self.parsers: dict[str, Callable] = {
            "github": self._parse_github_webhook,
            "gitlab": self._parse_gitlab_webhook,
            "jira": self._parse_jira_webhook,
            "trello": self._parse_trello_webhook,
            "openproject": self._parse_openproject_webhook,
        }

    @staticmethod
    def _get_header(headers: dict[str, str], name: str, default: str = "") -> str:
        """Retrieve a header case-insensitively from an arbitrary mapping."""
        if name in headers:
            return headers[name]
        target = name.lower()
        for key, value in headers.items():
            if key.lower() == target:
                return value
        return default

    def _extract_signature(self, headers: dict[str, str]) -> str:
        """Extract platform-specific signature/token header case-insensitively."""
        if self.platform == "github":
            return self._get_header(headers, "X-Hub-Signature-256")
        if self.platform == "jira":
            return self._get_header(headers, "X-Hub-Signature")
        if self.platform == "gitlab":
            return self._get_header(headers, "X-Gitlab-Token")
        if self.platform == "trello":
            return self._get_header(headers, "X-Trello-Webhook")
        if self.platform == "openproject":
            return self._get_header(headers, "X-OpenProject-Signature")
        return ""

    def validate_signature(
        self,
        payload: bytes,
        signature: str,
    ) -> bool:
        """Validate webhook signature.

        Args:
            payload: Raw webhook payload bytes
            signature: Signature header value

        Returns:
            True if signature is valid, False otherwise
        """
        if not self.secret:
            if self.require_secret:
                self.logger.error(
                    "Webhook secret is required but not configured; rejecting request"
                )
                return False
            self.logger.warning("No webhook secret configured, skipping validation")
            return True

        if not signature:
            return False

        # Platform-specific signature validation
        if self.platform == "github":
            return self._validate_github_signature(payload, signature)
        elif self.platform == "gitlab":
            return self._validate_gitlab_signature(payload, signature)
        elif self.platform == "jira":
            return self._validate_jira_signature(payload, signature)
        elif self.platform == "trello":
            return self._validate_trello_signature(payload, signature)
        elif self.platform == "openproject":
            return self._validate_openproject_signature(payload, signature)

        self.logger.error(f"Unknown platform for signature validation: {self.platform}")
        return False

    def handle_webhook(
        self,
        headers: dict[str, str],
        payload: bytes,
    ) -> SyncResult:
        """Process incoming webhook.

        Args:
            headers: HTTP headers from webhook request
            payload: Raw webhook payload

        Returns:
            Result of processing the webhook
        """
        self.logger.info(
            f"Received webhook from {self.platform}",
            extra={"payload_size": len(payload)},
        )

        # Validate signature
        signature = self._extract_signature(headers)
        if not self.validate_signature(payload, signature):
            self.logger.error("Webhook signature validation failed")
            return SyncResult(
                status=SyncStatus.FAILED,
                operation=SyncOperation(
                    operation_type="webhook",
                    resource_type=ResourceType.ISSUE,  # placeholder
                    direction=SyncDirection.EXTERNAL_TO_ELDER,
                ),
                errors=["Invalid webhook signature"],
            )

        # Parse webhook payload
        try:
            data = json.loads(payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            self.logger.error(f"Failed to parse webhook payload: {e}")
            return SyncResult(
                status=SyncStatus.FAILED,
                operation=SyncOperation(
                    operation_type="webhook",
                    resource_type=ResourceType.ISSUE,
                    direction=SyncDirection.EXTERNAL_TO_ELDER,
                ),
                errors=[f"Invalid JSON payload: {str(e)}"],
            )

        # Parse platform-specific webhook
        parser = self.parsers.get(self.platform)
        if not parser:
            self.logger.error(f"No parser available for platform: {self.platform}")
            return SyncResult(
                status=SyncStatus.FAILED,
                operation=SyncOperation(
                    operation_type="webhook",
                    resource_type=ResourceType.ISSUE,
                    direction=SyncDirection.EXTERNAL_TO_ELDER,
                ),
                errors=[f"Unsupported platform: {self.platform}"],
            )

        webhook_payload = parser(headers, data)

        if webhook_payload.event_type == WebhookEvent.UNKNOWN:
            self.logger.warning("Unknown webhook event, ignoring")
            return SyncResult(
                status=SyncStatus.SUCCESS,
                operation=SyncOperation(
                    operation_type="webhook",
                    resource_type=webhook_payload.resource_type,
                    direction=SyncDirection.EXTERNAL_TO_ELDER,
                ),
                metadata={"event": "unknown", "ignored": True},
            )

        routed_data = dict(webhook_payload.data)
        routed_data["_event"] = (
            self._get_header(headers, "X-GitHub-Event")
            or webhook_payload.data.get("webhookEvent")
            or webhook_payload.event_type.value
        )
        routed_data["_resource_type"] = webhook_payload.resource_type.value
        routed_data["_action"] = webhook_payload.action

        # Route to sync client
        return self.sync_client.handle_webhook(routed_data)

    def _validate_github_signature(
        self,
        payload: bytes,
        signature: str,
    ) -> bool:
        """Validate GitHub webhook signature.

        GitHub uses HMAC-SHA256 with format: sha256=<hash>

        Args:
            payload: Raw payload bytes
            signature: X-Hub-Signature-256 header value

        Returns:
            True if valid, False otherwise
        """
        if not signature.startswith("sha256="):
            return False

        expected_sig = signature[7:]  # Remove "sha256=" prefix
        computed_sig = hmac.new(
            self.secret.encode("utf-8"),
            payload,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(computed_sig, expected_sig)

    def _validate_gitlab_signature(
        self,
        payload: bytes,
        signature: str,
    ) -> bool:
        """Validate GitLab webhook signature.

        GitLab uses X-Gitlab-Token header with plain secret.

        Args:
            payload: Raw payload bytes
            signature: X-Gitlab-Token header value

        Returns:
            True if valid, False otherwise
        """
        return hmac.compare_digest(signature, self.secret)

    def _validate_jira_signature(
        self,
        payload: bytes,
        signature: str,
    ) -> bool:
        """Validate Jira Cloud webhook signature.

        Jira Cloud signs webhook requests with ``X-Hub-Signature: sha256=<hex>``
        computed using HMAC-SHA256 over the raw payload bytes and webhook secret.

        Args:
            payload: Raw payload bytes
            signature: X-Hub-Signature header value

        Returns:
            True if valid, False otherwise
        """
        if not self.secret or not signature or not signature.startswith("sha256="):
            return False

        expected_sig = signature[7:]
        computed_sig = hmac.new(
            self.secret.encode("utf-8"),
            payload,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(computed_sig, expected_sig)

    def _validate_trello_signature(
        self,
        payload: bytes,
        signature: str,
    ) -> bool:
        """Validate Trello webhook signature.

        Trello uses HMAC-SHA1 with base64 encoding.

        Args:
            payload: Raw payload bytes
            signature: X-Trello-Webhook header value

        Returns:
            True if valid, False otherwise
        """
        import base64

        computed_sig = base64.b64encode(
            hmac.new(
                self.secret.encode("utf-8"),
                payload,
                hashlib.sha1,
            ).digest()
        ).decode("utf-8")

        return hmac.compare_digest(computed_sig, signature)

    def _validate_openproject_signature(
        self,
        payload: bytes,
        signature: str,
    ) -> bool:
        """Validate OpenProject webhook signature.

        OpenProject uses HMAC-SHA256.

        Args:
            payload: Raw payload bytes
            signature: X-OpenProject-Signature header value

        Returns:
            True if valid, False otherwise
        """
        computed_sig = hmac.new(
            self.secret.encode("utf-8"),
            payload,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(computed_sig, signature)

    def _parse_github_webhook(
        self,
        headers: dict[str, str],
        data: dict[str, Any],
    ) -> WebhookPayload:
        """Parse GitHub webhook payload.

        Args:
            headers: HTTP headers
            data: Parsed JSON payload

        Returns:
            WebhookPayload
        """
        event = self._get_header(headers, "X-GitHub-Event", "unknown").lower()
        action = (data.get("action") or "unknown").lower()

        if event == "ping":
            return WebhookPayload(
                event_type=WebhookEvent.PING,
                platform="github",
                resource_type=ResourceType.ISSUE,
                resource_id=str(data.get("hook_id", "ping")),
                action="ping",
                data=data,
            )

        # Map GitHub events to our events
        if event == "issues":
            if action == "opened":
                event_type = WebhookEvent.ISSUE_CREATED
            elif action in {
                "edited",
                "labeled",
                "unlabeled",
                "assigned",
                "unassigned",
                "milestoned",
                "demilestoned",
            }:
                event_type = WebhookEvent.ISSUE_UPDATED
            elif action == "closed":
                event_type = WebhookEvent.ISSUE_CLOSED
            elif action == "reopened":
                event_type = WebhookEvent.ISSUE_REOPENED
            elif action == "deleted":
                event_type = WebhookEvent.ISSUE_DELETED
            else:
                event_type = WebhookEvent.UNKNOWN

            return WebhookPayload(
                event_type=event_type,
                platform="github",
                resource_type=ResourceType.ISSUE,
                resource_id=str((data.get("issue") or {}).get("number")),
                action=action,
                data=data,
            )

        elif event == "milestone":
            if action == "created":
                event_type = WebhookEvent.MILESTONE_CREATED
            elif action in {"edited", "opened"}:
                event_type = WebhookEvent.MILESTONE_UPDATED
            elif action == "closed":
                event_type = WebhookEvent.MILESTONE_CLOSED
            elif action == "deleted":
                event_type = WebhookEvent.MILESTONE_DELETED
            else:
                event_type = WebhookEvent.UNKNOWN

            return WebhookPayload(
                event_type=event_type,
                platform="github",
                resource_type=ResourceType.MILESTONE,
                resource_id=str((data.get("milestone") or {}).get("number")),
                action=action,
                data=data,
            )

        elif event == "label":
            if action == "created":
                event_type = WebhookEvent.LABEL_CREATED
            elif action == "edited":
                event_type = WebhookEvent.LABEL_UPDATED
            elif action == "deleted":
                event_type = WebhookEvent.LABEL_DELETED
            else:
                event_type = WebhookEvent.UNKNOWN

            return WebhookPayload(
                event_type=event_type,
                platform="github",
                resource_type=ResourceType.LABEL,
                resource_id=str((data.get("label") or {}).get("name", "unknown")),
                action=action,
                data=data,
            )

        return WebhookPayload(
            event_type=WebhookEvent.UNKNOWN,
            platform="github",
            resource_type=ResourceType.ISSUE,
            resource_id="unknown",
            action=action,
            data=data,
        )

    def _parse_gitlab_webhook(
        self,
        headers: dict[str, str],
        data: dict[str, Any],
    ) -> WebhookPayload:
        """Parse GitLab webhook payload.

        Args:
            headers: HTTP headers
            data: Parsed JSON payload

        Returns:
            WebhookPayload
        """
        object_kind = data.get("object_kind", "unknown")

        if object_kind == "issue":
            action = data.get("object_attributes", {}).get("action", "unknown")

            if action == "open":
                event_type = WebhookEvent.ISSUE_CREATED
            elif action == "update":
                event_type = WebhookEvent.ISSUE_UPDATED
            elif action == "close":
                event_type = WebhookEvent.ISSUE_CLOSED
            elif action == "reopen":
                event_type = WebhookEvent.ISSUE_REOPENED
            else:
                event_type = WebhookEvent.UNKNOWN

            return WebhookPayload(
                event_type=event_type,
                platform="gitlab",
                resource_type=ResourceType.ISSUE,
                resource_id=str(data.get("object_attributes", {}).get("id")),
                action=action,
                data=data,
            )

        return WebhookPayload(
            event_type=WebhookEvent.UNKNOWN,
            platform="gitlab",
            resource_type=ResourceType.ISSUE,
            resource_id="unknown",
            action="unknown",
            data=data,
        )

    def _parse_jira_webhook(
        self,
        headers: dict[str, str],
        data: dict[str, Any],
    ) -> WebhookPayload:
        """Parse Jira webhook payload.

        Args:
            headers: HTTP headers
            data: Parsed JSON payload

        Returns:
            WebhookPayload
        """
        webhook_event = (data.get("webhookEvent") or "unknown").lower()

        if webhook_event.startswith("jira:issue_"):
            action = webhook_event.replace("jira:issue_", "")

            if action == "created":
                event_type = WebhookEvent.ISSUE_CREATED
            elif action == "updated":
                event_type = WebhookEvent.ISSUE_UPDATED
            elif action == "deleted":
                event_type = WebhookEvent.ISSUE_DELETED
            else:
                event_type = WebhookEvent.UNKNOWN

            return WebhookPayload(
                event_type=event_type,
                platform="jira",
                resource_type=ResourceType.ISSUE,
                resource_id=(data.get("issue") or {}).get("key", "unknown"),
                action=action,
                data=data,
            )

        if webhook_event.startswith("jira:version_"):
            action = webhook_event.replace("jira:version_", "")

            if action == "created":
                event_type = WebhookEvent.MILESTONE_CREATED
            elif action in {"updated", "released", "unreleased", "moved", "merged"}:
                event_type = WebhookEvent.MILESTONE_UPDATED
            elif action == "deleted":
                event_type = WebhookEvent.MILESTONE_DELETED
            else:
                event_type = WebhookEvent.UNKNOWN

            return WebhookPayload(
                event_type=event_type,
                platform="jira",
                resource_type=ResourceType.MILESTONE,
                resource_id=str((data.get("version") or {}).get("id", "unknown")),
                action=action,
                data=data,
            )

        if webhook_event.startswith("project_"):
            action = webhook_event.replace("project_", "")

            if action == "created":
                event_type = WebhookEvent.PROJECT_CREATED
            elif action in {
                "updated",
                "soft_deleted",
                "restored_deleted",
                "archived",
                "restored_archived",
            }:
                event_type = WebhookEvent.PROJECT_UPDATED
            elif action == "deleted":
                event_type = WebhookEvent.PROJECT_DELETED
            else:
                event_type = WebhookEvent.UNKNOWN

            proj = data.get("project") or {}
            return WebhookPayload(
                event_type=event_type,
                platform="jira",
                resource_type=ResourceType.PROJECT,
                resource_id=str(proj.get("key") or proj.get("id") or "unknown"),
                action=action,
                data=data,
            )

        return WebhookPayload(
            event_type=WebhookEvent.UNKNOWN,
            platform="jira",
            resource_type=ResourceType.ISSUE,
            resource_id="unknown",
            action="unknown",
            data=data,
        )

    def _parse_trello_webhook(
        self,
        headers: dict[str, str],
        data: dict[str, Any],
    ) -> WebhookPayload:
        """Parse Trello webhook payload.

        Args:
            headers: HTTP headers
            data: Parsed JSON payload

        Returns:
            WebhookPayload
        """
        action_type = data.get("action", {}).get("type", "unknown")
        action_data = data.get("action", {}).get("data", {})

        if action_type == "createCard":
            event_type = WebhookEvent.ISSUE_CREATED
        elif action_type == "updateCard":
            event_type = WebhookEvent.ISSUE_UPDATED
        elif action_type == "deleteCard":
            event_type = WebhookEvent.ISSUE_DELETED
        else:
            event_type = WebhookEvent.UNKNOWN

        return WebhookPayload(
            event_type=event_type,
            platform="trello",
            resource_type=ResourceType.ISSUE,
            resource_id=action_data.get("card", {}).get("id", "unknown"),
            action=action_type,
            data=data,
        )

    def _parse_openproject_webhook(
        self,
        headers: dict[str, str],
        data: dict[str, Any],
    ) -> WebhookPayload:
        """Parse OpenProject webhook payload.

        Args:
            headers: HTTP headers
            data: Parsed JSON payload

        Returns:
            WebhookPayload
        """
        action = data.get("action", "unknown")

        if action == "created":
            event_type = WebhookEvent.ISSUE_CREATED
        elif action == "updated":
            event_type = WebhookEvent.ISSUE_UPDATED
        elif action == "deleted":
            event_type = WebhookEvent.ISSUE_DELETED
        else:
            event_type = WebhookEvent.UNKNOWN

        return WebhookPayload(
            event_type=event_type,
            platform="openproject",
            resource_type=ResourceType.ISSUE,
            resource_id=str(data.get("work_package", {}).get("id", "unknown")),
            action=action,
            data=data,
        )
