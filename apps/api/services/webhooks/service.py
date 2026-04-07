"""Webhook & Notification Service for Elder v1.2.0 (Phase 9)."""

# flake8: noqa: E501


import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from penguin_dal import DAL


class WebhookService:
    """Service for managing webhooks and notification rules."""

    def __init__(self, db: DAL):
        """
        Initialize WebhookService.

        Args:
            db: penguin-dal database instance
        """
        self.db = db

    # ===========================
    # Webhook Management Methods
    # ===========================

    def list_webhooks(
        self, organization_id: Optional[int] = None, enabled: Optional[bool] = None
    ) -> List[Dict[str, Any]]:
        """
        List all webhooks with optional filtering.

        Args:
            organization_id: Filter by organization
            enabled: Filter by enabled status

        Returns:
            List of webhook dictionaries
        """
        query = self.db.webhooks.id > 0

        if organization_id is not None:
            query &= self.db.webhooks.organization_id == organization_id

        if enabled is not None:
            query &= self.db.webhooks.enabled == enabled

        webhooks = self.db(query).select(orderby=self.db.webhooks.created_at)

        return [self._sanitize_webhook(w.as_dict()) for w in webhooks]

    def get_webhook(self, webhook_id: int) -> Dict[str, Any]:
        """
        Get webhook details by ID.

        Args:
            webhook_id: Webhook ID

        Returns:
            Webhook dictionary

        Raises:
            Exception: If webhook not found
        """
        webhook = self.db.webhooks[webhook_id]

        if not webhook:
            raise Exception(f"Webhook {webhook_id} not found")

        return self._sanitize_webhook(webhook.as_dict())

    def create_webhook(
        self,
        name: str,
        url: str,
        events: List[str],
        organization_id: int,
        secret: Optional[str] = None,
        description: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Create a new webhook.

        Args:
            name: Webhook name
            url: Target URL for webhook deliveries
            events: List of event types to subscribe to
            organization_id: Organization ID
            secret: Optional shared secret for HMAC signatures
            description: Optional description
            headers: Optional custom headers to send

        Returns:
            Created webhook dictionary
        """
        # Validate URL
        if not url.startswith(("http://", "https://")):
            raise Exception("Webhook URL must start with http:// or https://")

        # Validate events
        if not events or not isinstance(events, list):
            raise Exception("Events must be a non-empty list")

        now = datetime.now(timezone.utc)
        webhook_id = self.db.webhooks.insert(
            name=name,
            url=url,
            events_json=json.dumps(events),
            secret=secret,
            organization_id=organization_id,
            description=description,
            headers_json=json.dumps(headers) if headers else None,
            enabled=True,
            created_at=now,
            updated_at=now,
        )

        self.db.commit()

        webhook = self.db.webhooks[webhook_id]
        return self._sanitize_webhook(webhook.as_dict())

    def update_webhook(
        self,
        webhook_id: int,
        name: Optional[str] = None,
        url: Optional[str] = None,
        events: Optional[List[str]] = None,
        secret: Optional[str] = None,
        description: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        enabled: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Update webhook configuration.

        Args:
            webhook_id: Webhook ID
            name: New name
            url: New URL
            events: New event list
            secret: New secret
            description: New description
            headers: New headers
            enabled: New enabled status

        Returns:
            Updated webhook dictionary

        Raises:
            Exception: If webhook not found
        """
        webhook = self.db.webhooks[webhook_id]

        if not webhook:
            raise Exception(f"Webhook {webhook_id} not found")

        update_data = {"updated_at": datetime.now(timezone.utc)}

        if name is not None:
            update_data["name"] = name

        if url is not None:
            if not url.startswith(("http://", "https://")):
                raise Exception("Webhook URL must start with http:// or https://")
            update_data["url"] = url

        if events is not None:
            if not isinstance(events, list):
                raise Exception("Events must be a list")
            update_data["events_json"] = json.dumps(events)

        if secret is not None:
            update_data["secret"] = secret

        if description is not None:
            update_data["description"] = description

        if headers is not None:
            update_data["headers_json"] = json.dumps(headers)

        if enabled is not None:
            update_data["enabled"] = enabled

        self.db(self.db.webhooks.id == webhook_id).update(**update_data)
        self.db.commit()

        webhook = self.db.webhooks[webhook_id]
        return self._sanitize_webhook(webhook.as_dict())

    def delete_webhook(self, webhook_id: int) -> Dict[str, str]:
        """
        Delete a webhook.

        Args:
            webhook_id: Webhook ID

        Returns:
            Success message

        Raises:
            Exception: If webhook not found
        """
        webhook = self.db.webhooks[webhook_id]

        if not webhook:
            raise Exception(f"Webhook {webhook_id} not found")

        # Delete associated deliveries
        self.db(self.db.webhook_deliveries.webhook_id == webhook_id).delete()

        # Delete webhook
        self.db(self.db.webhooks.id == webhook_id).delete()
        self.db.commit()

        return {"message": "Webhook deleted successfully"}

    # ===========================
    # Webhook Delivery Methods
    # ===========================

    def deliver_webhook(
        self, webhook_id: int, event_type: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Deliver a webhook event.

        Args:
            webhook_id: Webhook ID
            event_type: Event type (e.g., "entity.created")
            payload: Event payload

        Returns:
            Delivery result dictionary
        """
        webhook = self.db.webhooks[webhook_id]

        if not webhook:
            raise Exception(f"Webhook {webhook_id} not found")

        if not webhook.enabled:
            raise Exception(f"Webhook {webhook_id} is disabled")

        # Check if webhook subscribes to this event
        events = json.loads(webhook.events_json)
        if event_type not in events:
            raise Exception(
                f"Webhook {webhook_id} does not subscribe to event {event_type}"
            )

        # Prepare payload
        delivery_payload = {
            "event": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": payload,
        }

        # Create delivery record
        now = datetime.now(timezone.utc)
        delivery_id = self.db.webhook_deliveries.insert(
            webhook_id=webhook_id,
            event_type=event_type,
            payload_json=json.dumps(delivery_payload),
            attempts=0,
            created_at=now,
            updated_at=now,
        )
        self.db.commit()

        # Attempt delivery
        return self._attempt_delivery(delivery_id, webhook, delivery_payload)

    def _attempt_delivery(
        self, delivery_id: int, webhook: Any, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Attempt to deliver a webhook.

        Args:
            delivery_id: Delivery ID
            webhook: Webhook record
            payload: Delivery payload

        Returns:
            Delivery result
        """
        try:
            # Prepare headers
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "Elder-Webhook/1.2.0",
            }

            # Add custom headers if configured
            if webhook.headers_json:
                custom_headers = json.loads(webhook.headers_json)
                headers.update(custom_headers)

            # Generate HMAC signature if secret is configured
            if webhook.secret:
                signature = self._generate_signature(
                    webhook.secret, json.dumps(payload)
                )
                headers["X-Elder-Signature"] = signature

            # Send webhook
            start_time = time.time()
            response = requests.post(
                webhook.url, json=payload, headers=headers, timeout=30
            )
            duration_ms = int((time.time() - start_time) * 1000)

            # Update delivery record
            success = 200 <= response.status_code < 300

            self.db(self.db.webhook_deliveries.id == delivery_id).update(
                attempts=self.db.webhook_deliveries.attempts + 1,
                success=success,
                status_code=response.status_code,
                response_body=response.text[:1000],  # Truncate response
                duration_ms=duration_ms,
                delivered_at=datetime.now(timezone.utc) if success else None,
            )
            self.db.commit()

            return {
                "delivery_id": delivery_id,
                "success": success,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            }

        except Exception as e:
            # Update delivery record with error
            self.db(self.db.webhook_deliveries.id == delivery_id).update(
                attempts=self.db.webhook_deliveries.attempts + 1,
                success=False,
                error_message=str(e)[:500],  # Truncate error
            )
            self.db.commit()

            return {"delivery_id": delivery_id, "success": False, "error": str(e)}

    def redeliver_webhook(self, webhook_id: int, delivery_id: int) -> Dict[str, Any]:
        """
        Retry a failed webhook delivery.

        Args:
            webhook_id: Webhook ID
            delivery_id: Delivery ID

        Returns:
            Redelivery result

        Raises:
            Exception: If webhook or delivery not found
        """
        webhook = self.db.webhooks[webhook_id]
        if not webhook:
            raise Exception(f"Webhook {webhook_id} not found")

        delivery = self.db.webhook_deliveries[delivery_id]
        if not delivery:
            raise Exception(f"Delivery {delivery_id} not found")

        if delivery.webhook_id != webhook_id:
            raise Exception(
                f"Delivery {delivery_id} does not belong to webhook {webhook_id}"
            )

        # Parse original payload
        payload = json.loads(delivery.payload_json)

        # Attempt redelivery
        return self._attempt_delivery(delivery_id, webhook, payload)

    def get_webhook_deliveries(
        self, webhook_id: int, limit: int = 50, success: Optional[bool] = None
    ) -> List[Dict[str, Any]]:
        """
        Get webhook delivery history.

        Args:
            webhook_id: Webhook ID
            limit: Maximum number of deliveries to return
            success: Filter by success status

        Returns:
            List of delivery dictionaries
        """
        query = self.db.webhook_deliveries.webhook_id == webhook_id

        if success is not None:
            query &= self.db.webhook_deliveries.success == success

        deliveries = self.db(query).select(
            orderby=~self.db.webhook_deliveries.created_at, limitby=(0, limit)
        )

        return [d.as_dict() for d in deliveries]

    def test_webhook(self, webhook_id: int) -> Dict[str, Any]:
        """
        Send a test event to webhook.

        Args:
            webhook_id: Webhook ID

        Returns:
            Test delivery result

        Raises:
            Exception: If webhook not found
        """
        webhook = self.db.webhooks[webhook_id]

        if not webhook:
            raise Exception(f"Webhook {webhook_id} not found")

        # Create test payload
        test_payload = {
            "test": True,
            "webhook_id": webhook_id,
            "message": "This is a test webhook delivery from Elder",
        }

        # Use first subscribed event for test
        events = json.loads(webhook.events_json)
        event_type = events[0] if events else "test.event"

        return self.deliver_webhook(webhook_id, event_type, test_payload)

    # ===========================
    # Notification Rule Methods
    # ===========================

    def list_notification_rules(
        self, organization_id: Optional[int] = None, channel: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        List notification rules with optional filtering.

        Args:
            organization_id: Filter by organization
            channel: Filter by channel type

        Returns:
            List of notification rule dictionaries
        """
        query = self.db.notification_rules.id > 0

        if organization_id is not None:
            query &= self.db.notification_rules.organization_id == organization_id

        if channel is not None:
            query &= self.db.notification_rules.channel == channel

        rules = self.db(query).select(orderby=self.db.notification_rules.created_at)

        return [r.as_dict() for r in rules]

    def get_notification_rule(self, rule_id: int) -> Dict[str, Any]:
        """
        Get notification rule by ID.

        Args:
            rule_id: Notification rule ID

        Returns:
            Notification rule dictionary

        Raises:
            Exception: If rule not found
        """
        rule = self.db.notification_rules[rule_id]

        if not rule:
            raise Exception(f"Notification rule {rule_id} not found")

        return rule.as_dict()

    def create_notification_rule(
        self,
        name: str,
        channel: str,
        events: List[str],
        config: Dict[str, Any],
        organization_id: int,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a notification rule.

        Args:
            name: Rule name
            channel: Notification channel (email, slack, teams, pagerduty)
            events: List of event types
            config: Channel-specific configuration
            organization_id: Organization ID
            description: Optional description

        Returns:
            Created notification rule dictionary
        """
        # Validate channel
        valid_channels = ["email", "slack", "teams", "pagerduty"]
        if channel not in valid_channels:
            raise Exception(
                f"Invalid channel. Must be one of: {', '.join(valid_channels)}"
            )

        # Validate events
        if not events or not isinstance(events, list):
            raise Exception("Events must be a non-empty list")

        # Validate config
        if not config or not isinstance(config, dict):
            raise Exception("Config must be a non-empty dictionary")

        now = datetime.now(timezone.utc)
        rule_id = self.db.notification_rules.insert(
            name=name,
            channel=channel,
            events_json=json.dumps(events),
            config_json=json.dumps(config),
            organization_id=organization_id,
            description=description,
            enabled=True,
            created_at=now,
            updated_at=now,
        )

        self.db.commit()

        rule = self.db.notification_rules[rule_id]
        return rule.as_dict()

    def update_notification_rule(
        self,
        rule_id: int,
        name: Optional[str] = None,
        events: Optional[List[str]] = None,
        config: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Update notification rule.

        Args:
            rule_id: Rule ID
            name: New name
            events: New event list
            config: New configuration
            description: New description
            enabled: New enabled status

        Returns:
            Updated notification rule dictionary

        Raises:
            Exception: If rule not found
        """
        rule = self.db.notification_rules[rule_id]

        if not rule:
            raise Exception(f"Notification rule {rule_id} not found")

        update_data = {"updated_at": datetime.now(timezone.utc)}

        if name is not None:
            update_data["name"] = name

        if events is not None:
            if not isinstance(events, list):
                raise Exception("Events must be a list")
            update_data["events_json"] = json.dumps(events)

        if config is not None:
            if not isinstance(config, dict):
                raise Exception("Config must be a dictionary")
            update_data["config_json"] = json.dumps(config)

        if description is not None:
            update_data["description"] = description

        if enabled is not None:
            update_data["enabled"] = enabled

        self.db(self.db.notification_rules.id == rule_id).update(**update_data)
        self.db.commit()

        rule = self.db.notification_rules[rule_id]
        return rule.as_dict()

    def delete_notification_rule(self, rule_id: int) -> Dict[str, str]:
        """
        Delete a notification rule.

        Args:
            rule_id: Rule ID

        Returns:
            Success message

        Raises:
            Exception: If rule not found
        """
        rule = self.db.notification_rules[rule_id]

        if not rule:
            raise Exception(f"Notification rule {rule_id} not found")

        self.db(self.db.notification_rules.id == rule_id).delete()
        self.db.commit()

        return {"message": "Notification rule deleted successfully"}

    def test_notification_rule(self, rule_id: int) -> Dict[str, Any]:
        """
        Send a test notification for a rule.

        Args:
            rule_id: Rule ID

        Returns:
            Test result

        Raises:
            Exception: If rule not found
        """
        rule = self.db.notification_rules[rule_id]

        if not rule:
            raise Exception(f"Notification rule {rule_id} not found")

        # Create test notification
        test_payload = {
            "test": True,
            "rule_id": rule_id,
            "message": f"This is a test notification from Elder via {rule.channel}",
        }

        return self._send_notification(rule, test_payload)

    def _send_notification(self, rule: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send notification via configured channel.

        Args:
            rule: Notification rule record
            payload: Notification payload

        Returns:
            Send result
        """
        try:
            config = json.loads(rule.config_json)

            if rule.channel == "email":
                return self._send_email_notification(config, payload)
            elif rule.channel == "slack":
                return self._send_slack_notification(config, payload)
            elif rule.channel == "teams":
                return self._send_teams_notification(config, payload)
            elif rule.channel == "pagerduty":
                return self._send_pagerduty_notification(config, payload)
            else:
                raise Exception(f"Unsupported channel: {rule.channel}")

        except Exception as e:
            return {"success": False, "error": str(e)}

    def _send_email_notification(
        self, config: Dict[str, Any], payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Send email notification."""
        # NOTE: In production, integrate with SMTP or email service (SendGrid, SES, etc.)
        # For now, return success indicating email would be sent
        return {
            "success": True,
            "channel": "email",
            "recipients": config.get("recipients", []),
            "message": "Email notification would be sent in production",
        }

    def _send_slack_notification(
        self, config: Dict[str, Any], payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Send Slack notification."""
        webhook_url = config.get("webhook_url")
        if not webhook_url:
            raise Exception("Slack webhook_url is required in config")

        try:
            response = requests.post(
                webhook_url, json={"text": json.dumps(payload, indent=2)}, timeout=10
            )
            return {
                "success": response.status_code == 200,
                "channel": "slack",
                "status_code": response.status_code,
            }
        except Exception as e:
            return {"success": False, "channel": "slack", "error": str(e)}

    def _send_teams_notification(
        self, config: Dict[str, Any], payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Send Microsoft Teams notification."""
        webhook_url = config.get("webhook_url")
        if not webhook_url:
            raise Exception("Teams webhook_url is required in config")

        try:
            response = requests.post(
                webhook_url, json={"text": json.dumps(payload, indent=2)}, timeout=10
            )
            return {
                "success": response.status_code == 200,
                "channel": "teams",
                "status_code": response.status_code,
            }
        except Exception as e:
            return {"success": False, "channel": "teams", "error": str(e)}

    def _send_pagerduty_notification(
        self, config: Dict[str, Any], payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Send PagerDuty notification."""
        routing_key = config.get("routing_key")
        if not routing_key:
            raise Exception("PagerDuty routing_key is required in config")

        try:
            response = requests.post(
                "https://events.pagerduty.com/v2/enqueue",
                json={
                    "routing_key": routing_key,
                    "event_action": "trigger",
                    "payload": {
                        "summary": "Elder Notification",
                        "source": "elder",
                        "severity": "info",
                        "custom_details": payload,
                    },
                },
                timeout=10,
            )
            return {
                "success": response.status_code == 202,
                "channel": "pagerduty",
                "status_code": response.status_code,
            }
        except Exception as e:
            return {"success": False, "channel": "pagerduty", "error": str(e)}

    # ===========================
    # Event Broadcasting Methods
    # ===========================

    def broadcast_event(
        self, event_type: str, payload: Dict[str, Any], organization_id: int
    ) -> Dict[str, Any]:
        """
        Broadcast an event to all applicable webhooks and notification rules.

        Args:
            event_type: Event type (e.g., "entity.created")
            payload: Event payload
            organization_id: Organization ID

        Returns:
            Broadcast result with counts
        """
        # Find webhooks subscribed to this event
        webhooks = self.db(
            (self.db.webhooks.organization_id == organization_id)
            & (self.db.webhooks.enabled is True)
        ).select()

        webhook_results = []
        for webhook in webhooks:
            events = json.loads(webhook.events_json)
            if event_type in events:
                try:
                    result = self.deliver_webhook(webhook.id, event_type, payload)
                    webhook_results.append(result)
                except Exception as e:
                    webhook_results.append(
                        {"webhook_id": webhook.id, "success": False, "error": str(e)}
                    )

        # Find notification rules for this event
        rules = self.db(
            (self.db.notification_rules.organization_id == organization_id)
            & (self.db.notification_rules.enabled is True)
        ).select()

        notification_results = []
        for rule in rules:
            events = json.loads(rule.events_json)
            if event_type in events:
                try:
                    result = self._send_notification(rule, payload)
                    notification_results.append(result)
                except Exception as e:
                    notification_results.append(
                        {"rule_id": rule.id, "success": False, "error": str(e)}
                    )

        return {
            "event_type": event_type,
            "webhooks_triggered": len(webhook_results),
            "webhooks_successful": sum(1 for r in webhook_results if r.get("success")),
            "notifications_triggered": len(notification_results),
            "notifications_successful": sum(
                1 for r in notification_results if r.get("success")
            ),
            "webhook_results": webhook_results,
            "notification_results": notification_results,
        }

    # ===========================
    # Helper Methods
    # ===========================

    def _generate_signature(self, secret: str, payload: str) -> str:
        """
        Generate HMAC signature for webhook payload.

        Args:
            secret: Shared secret
            payload: JSON payload string

        Returns:
            HMAC signature (sha256 hex)
        """
        return hmac.new(
            secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    def _sanitize_webhook(self, webhook_dict: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sanitize webhook dictionary (mask secret).

        Args:
            webhook_dict: Webhook dictionary

        Returns:
            Sanitized webhook dictionary
        """
        if "secret" in webhook_dict and webhook_dict["secret"]:
            webhook_dict["secret"] = "***masked***"

        # Parse JSON fields for API response
        if "events_json" in webhook_dict:
            webhook_dict["events"] = json.loads(webhook_dict["events_json"])
            del webhook_dict["events_json"]

        if "headers_json" in webhook_dict and webhook_dict["headers_json"]:
            webhook_dict["headers"] = json.loads(webhook_dict["headers_json"])
            del webhook_dict["headers_json"]

        return webhook_dict
