"""Unit tests for SMTP email delivery utility."""

from __future__ import annotations

import smtplib
import ssl
from unittest.mock import MagicMock, patch

import pytest

from apps.api.utils.email import (
    EmailDeliveryError,
    EmailNotConfiguredError,
    SMTPConfig,
    is_smtp_configured,
    load_smtp_config,
    send_email,
    send_email_sync,
)


class TestLoadSMTPConfig:
    """Tests for loading SMTPConfig from environment."""

    def test_unconfigured_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in (
            "SMTP_HOST",
            "SMTP_PORT",
            "SMTP_USERNAME",
            "SMTP_PASSWORD",
            "SMTP_USE_TLS",
            "SMTP_USE_SSL",
            "SMTP_FROM_EMAIL",
            "SMTP_FROM_NAME",
            "SMTP_EHLO_HOST",
            "SMTP_TIMEOUT",
        ):
            monkeypatch.delenv(var, raising=False)

        cfg = load_smtp_config()
        assert cfg.is_configured is False
        assert is_smtp_configured() is False
        assert cfg.port == 587
        assert cfg.use_tls is True
        assert cfg.use_ssl is False
        assert cfg.from_name == "Elder"
        assert cfg.timeout == 30

    def test_loads_passbolt_compatible_gmail_relay_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SMTP_HOST", "smtp-relay.gmail.com")
        monkeypatch.setenv("SMTP_PORT", "587")
        monkeypatch.setenv("SMTP_USERNAME", "relay-user@penguintech.io")
        monkeypatch.setenv("SMTP_PASSWORD", "test-secret-not-real")
        monkeypatch.setenv("SMTP_USE_TLS", "true")
        monkeypatch.setenv("SMTP_USE_SSL", "false")
        monkeypatch.setenv("SMTP_FROM_EMAIL", "no-reply@penguintech.io")
        monkeypatch.setenv("SMTP_FROM_NAME", "Elder")
        monkeypatch.setenv("SMTP_EHLO_HOST", "elder-alpha.penguintech.cloud")
        monkeypatch.setenv("SMTP_TIMEOUT", "30")

        cfg = load_smtp_config()
        assert cfg.is_configured is True
        assert is_smtp_configured() is True
        assert cfg.host == "smtp-relay.gmail.com"
        assert cfg.port == 587
        assert cfg.ehlo_host == "elder-alpha.penguintech.cloud"
        assert cfg.from_email == "no-reply@penguintech.io"


class TestSendEmail:
    """Tests for synchronous and asynchronous SMTP delivery."""

    def test_fails_loud_when_not_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SMTP_HOST", raising=False)
        monkeypatch.delenv("SMTP_FROM_EMAIL", raising=False)
        with pytest.raises(EmailNotConfiguredError):
            send_email_sync(
                to_addresses=["user@penguintech.io"],
                subject="Test",
                body_text="Hello",
            )

    def test_rejects_empty_recipients(self) -> None:
        cfg = SMTPConfig(
            host="smtp-relay.gmail.com", from_email="no-reply@penguintech.io"
        )
        with pytest.raises(ValueError, match="At least one recipient"):
            send_email_sync(
                to_addresses=[],
                subject="Test",
                body_text="Hello",
                config=cfg,
            )

    def test_starttls_delivery_with_auth_and_ehlo(self) -> None:
        cfg = SMTPConfig(
            host="smtp-relay.gmail.com",
            port=587,
            username="relay@penguintech.io",
            password="test-password",
            use_tls=True,
            use_ssl=False,
            from_email="no-reply@penguintech.io",
            from_name="Elder",
            ehlo_host="elder-alpha.penguintech.cloud",
            timeout=30,
        )
        mock_smtp = MagicMock()
        mock_smtp.__enter__.return_value = mock_smtp

        with patch("apps.api.utils.email.smtplib.SMTP", return_value=mock_smtp) as cls:
            result = send_email_sync(
                to_addresses=["alice@penguintech.io"],
                subject="Welcome to Elder",
                body_text="Plain body",
                body_html="<p>HTML body</p>",
                config=cfg,
            )

        assert result == {
            "success": True,
            "smtp_host": "smtp-relay.gmail.com",
            "smtp_port": 587,
            "recipient_count": 1,
        }
        cls.assert_called_once_with(
            host="smtp-relay.gmail.com",
            port=587,
            local_hostname="elder-alpha.penguintech.cloud",
            timeout=30,
        )
        assert mock_smtp.ehlo.call_count == 2
        mock_smtp.starttls.assert_called_once()
        tls_ctx = mock_smtp.starttls.call_args.kwargs["context"]
        assert isinstance(tls_ctx, ssl.SSLContext)
        assert tls_ctx.minimum_version == ssl.TLSVersion.TLSv1_2
        mock_smtp.login.assert_called_once_with("relay@penguintech.io", "test-password")
        mock_smtp.send_message.assert_called_once()

    def test_ssl_delivery_mode(self) -> None:
        cfg = SMTPConfig(
            host="smtp.example.com",
            port=465,
            use_tls=False,
            use_ssl=True,
            from_email="no-reply@penguintech.io",
        )
        mock_smtp = MagicMock()
        mock_smtp.__enter__.return_value = mock_smtp

        with patch(
            "apps.api.utils.email.smtplib.SMTP_SSL", return_value=mock_smtp
        ) as cls:
            result = send_email_sync(
                to_addresses=["alice@penguintech.io"],
                subject="SSL Test",
                body_text="Hello",
                config=cfg,
            )

        assert result["success"] is True
        cls.assert_called_once()
        mock_smtp.login.assert_not_called()
        mock_smtp.send_message.assert_called_once()

    def test_smtp_exception_raises_email_delivery_error(self) -> None:
        cfg = SMTPConfig(
            host="smtp-relay.gmail.com",
            from_email="no-reply@penguintech.io",
        )
        mock_smtp = MagicMock()
        mock_smtp.__enter__.return_value = mock_smtp
        mock_smtp.send_message.side_effect = smtplib.SMTPAuthenticationError(
            535, b"auth failed"
        )

        with patch("apps.api.utils.email.smtplib.SMTP", return_value=mock_smtp):
            with pytest.raises(EmailDeliveryError, match="SMTPAuthenticationError"):
                send_email_sync(
                    to_addresses=["alice@penguintech.io"],
                    subject="Fail",
                    body_text="Body",
                    config=cfg,
                )

    @pytest.mark.asyncio
    async def test_async_send_email_delegates_to_thread(self) -> None:
        cfg = SMTPConfig(
            host="smtp-relay.gmail.com",
            from_email="no-reply@penguintech.io",
        )
        mock_smtp = MagicMock()
        mock_smtp.__enter__.return_value = mock_smtp

        with patch("apps.api.utils.email.smtplib.SMTP", return_value=mock_smtp):
            result = await send_email(
                to_addresses=["alice@penguintech.io"],
                subject="Async Test",
                body_text="Hello",
                config=cfg,
            )
        assert result["success"] is True


class TestWebhookEmailNotification:
    """Verify webhook email notification fails loud when SMTP is unconfigured."""

    def test_unconfigured_smtp_returns_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from apps.api.services.webhooks.service import WebhookService

        monkeypatch.delenv("SMTP_HOST", raising=False)
        monkeypatch.delenv("SMTP_FROM_EMAIL", raising=False)
        svc = WebhookService(db=MagicMock())
        res = svc._send_email_notification(
            {"recipients": ["ops@penguintech.io"]},
            {"title": "Disk Full"},
        )
        assert res["success"] is False
        assert res["channel"] == "email"
        assert "SMTP is not configured" in res["error"]
