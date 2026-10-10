"""Unit tests for the Alertmanager client (shared/alerting/alertmanager.py).

These are pure unit tests: the httpx network layer is mocked, so no live
Alertmanager is required. They exercise payload construction, priority→severity
mapping, optional-field handling, the success/failure return contract, and the
module-level singleton + convenience helpers.
"""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import shared.alerting.alertmanager as am
from shared.alerting.alertmanager import (
    AlertmanagerClient,
    get_alertmanager_client,
    send_incident_alert,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset the module-level singleton around every test."""
    am._alertmanager_client = None
    yield
    am._alertmanager_client = None


def _mock_async_client(post_mock):
    """Build a MagicMock that behaves like httpx.AsyncClient used as a ctx mgr."""
    client = MagicMock()
    client.post = post_mock
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


class TestInit:
    def test_default_url_from_env(self, monkeypatch):
        monkeypatch.setenv("ALERTMANAGER_URL", "http://am.local:9093")
        client = AlertmanagerClient()
        assert client.alertmanager_url == "http://am.local:9093"
        assert client.api_url == "http://am.local:9093/api/v2/alerts"

    def test_default_url_fallback(self, monkeypatch):
        monkeypatch.delenv("ALERTMANAGER_URL", raising=False)
        client = AlertmanagerClient()
        assert client.alertmanager_url == "http://alertmanager:9093"
        assert client.api_url.endswith("/api/v2/alerts")

    def test_explicit_url_overrides_env(self, monkeypatch):
        monkeypatch.setenv("ALERTMANAGER_URL", "http://ignored:9093")
        client = AlertmanagerClient(alertmanager_url="http://explicit:9093")
        assert client.alertmanager_url == "http://explicit:9093"


class TestSendAlert:
    @pytest.mark.asyncio
    async def test_send_alert_success_builds_payload(self):
        post = AsyncMock(return_value=MagicMock(raise_for_status=MagicMock()))
        client = AlertmanagerClient(alertmanager_url="http://am:9093")
        with patch(
            "shared.alerting.alertmanager.httpx.AsyncClient",
            return_value=_mock_async_client(post),
        ):
            ok = await client.send_alert(
                alertname="MyAlert",
                labels={"severity": "high", "component": "x"},
                annotations={"summary": "hi"},
            )
        assert ok is True
        # One POST to the alerts endpoint with a one-element list payload.
        post.assert_awaited_once()
        args, kwargs = post.call_args
        assert args[0] == "http://am:9093/api/v2/alerts"
        payload = kwargs["json"]
        assert isinstance(payload, list) and len(payload) == 1
        alert = payload[0]
        assert alert["labels"]["alertname"] == "MyAlert"
        assert alert["labels"]["severity"] == "high"
        assert alert["annotations"] == {"summary": "hi"}
        # startsAt defaulted to an ISO timestamp; no endsAt when ongoing.
        assert "startsAt" in alert
        assert "endsAt" not in alert

    @pytest.mark.asyncio
    async def test_send_alert_includes_ends_at_when_provided(self):
        post = AsyncMock(return_value=MagicMock(raise_for_status=MagicMock()))
        ends = datetime(2026, 1, 1, tzinfo=UTC)
        client = AlertmanagerClient()
        with patch(
            "shared.alerting.alertmanager.httpx.AsyncClient",
            return_value=_mock_async_client(post),
        ):
            ok = await client.send_alert(
                alertname="A",
                labels={},
                annotations={},
                ends_at=ends,
            )
        assert ok is True
        alert = post.call_args.kwargs["json"][0]
        assert alert["endsAt"] == ends.isoformat()

    @pytest.mark.asyncio
    async def test_send_alert_uses_generator_url(self):
        post = AsyncMock(return_value=MagicMock(raise_for_status=MagicMock()))
        client = AlertmanagerClient()
        with patch(
            "shared.alerting.alertmanager.httpx.AsyncClient",
            return_value=_mock_async_client(post),
        ):
            await client.send_alert(
                alertname="A",
                labels={},
                annotations={},
                generator_url="https://elder/issues/5",
            )
        alert = post.call_args.kwargs["json"][0]
        assert alert["generatorURL"] == "https://elder/issues/5"

    @pytest.mark.asyncio
    async def test_send_alert_returns_false_on_http_error(self):
        post = AsyncMock(side_effect=httpx.HTTPError("boom"))
        client = AlertmanagerClient()
        with patch(
            "shared.alerting.alertmanager.httpx.AsyncClient",
            return_value=_mock_async_client(post),
        ):
            ok = await client.send_alert(alertname="A", labels={}, annotations={})
        assert ok is False

    @pytest.mark.asyncio
    async def test_send_alert_returns_false_on_unexpected_error(self):
        post = AsyncMock(side_effect=RuntimeError("unexpected"))
        client = AlertmanagerClient()
        with patch(
            "shared.alerting.alertmanager.httpx.AsyncClient",
            return_value=_mock_async_client(post),
        ):
            ok = await client.send_alert(alertname="A", labels={}, annotations={})
        assert ok is False

    @pytest.mark.asyncio
    async def test_send_alert_returns_false_when_raise_for_status_raises(self):
        resp = MagicMock()
        resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError("bad", request=None, response=None)
        )
        post = AsyncMock(return_value=resp)
        client = AlertmanagerClient()
        with patch(
            "shared.alerting.alertmanager.httpx.AsyncClient",
            return_value=_mock_async_client(post),
        ):
            ok = await client.send_alert(alertname="A", labels={}, annotations={})
        assert ok is False


class TestSendIncidentAlert:
    @pytest.mark.parametrize(
        "priority,expected_severity",
        [
            ("low", "warning"),
            ("medium", "warning"),
            ("high", "high"),
            ("critical", "critical"),
            ("CRITICAL", "critical"),
            ("bogus", "warning"),
        ],
    )
    @pytest.mark.asyncio
    async def test_priority_maps_to_severity(self, priority, expected_severity):
        client = AlertmanagerClient()
        with patch.object(
            client, "send_alert", new=AsyncMock(return_value=True)
        ) as mock_send:
            ok = await client.send_incident_alert(
                issue_id=42,
                issue_title="DB down",
                priority=priority,
                organization_name="Acme",
                organization_id=7,
            )
        assert ok is True
        labels = mock_send.call_args.kwargs["labels"]
        assert labels["severity"] == expected_severity
        assert labels["issue_id"] == "42"
        assert labels["organization_id"] == "7"
        assert labels["component"] == "incident"
        assert mock_send.call_args.kwargs["alertname"] == "ElderIncidentIssue"

    @pytest.mark.asyncio
    async def test_optional_fields_included(self):
        client = AlertmanagerClient()
        with patch.object(
            client, "send_alert", new=AsyncMock(return_value=True)
        ) as mock_send:
            await client.send_incident_alert(
                issue_id=1,
                issue_title="t",
                priority="high",
                organization_name="Acme",
                organization_id=1,
                description="something broke",
                assigned_to="alice",
                entities=["web-1", "db-2"],
                web_url="https://elder/issues/1",
            )
        kwargs = mock_send.call_args.kwargs
        assert kwargs["labels"]["assigned_to"] == "alice"
        assert kwargs["annotations"]["description"] == "something broke"
        assert kwargs["annotations"]["entities"] == "web-1, db-2"
        assert kwargs["annotations"]["url"] == "https://elder/issues/1"
        assert kwargs["generator_url"] == "https://elder/issues/1"

    @pytest.mark.asyncio
    async def test_defaults_when_optionals_absent(self):
        client = AlertmanagerClient()
        with patch.object(
            client, "send_alert", new=AsyncMock(return_value=True)
        ) as mock_send:
            await client.send_incident_alert(
                issue_id=1,
                issue_title="t",
                priority="low",
                organization_name="Acme",
                organization_id=1,
            )
        kwargs = mock_send.call_args.kwargs
        assert "assigned_to" not in kwargs["labels"]
        assert kwargs["annotations"]["description"] == "No description provided"
        assert "entities" not in kwargs["annotations"]
        assert "url" not in kwargs["annotations"]


class TestResolveIncidentAlert:
    @pytest.mark.asyncio
    async def test_resolve_sets_ends_at_and_info_severity(self):
        client = AlertmanagerClient()
        with patch.object(
            client, "send_alert", new=AsyncMock(return_value=True)
        ) as mock_send:
            ok = await client.resolve_incident_alert(
                issue_id=9,
                issue_title="Outage",
                organization_name="Acme",
                organization_id=3,
            )
        assert ok is True
        kwargs = mock_send.call_args.kwargs
        assert kwargs["labels"]["severity"] == "info"
        assert kwargs["labels"]["issue_id"] == "9"
        assert kwargs["ends_at"] is not None
        assert "Resolved" in kwargs["annotations"]["summary"]


class TestSingletonAndHelpers:
    def test_get_client_returns_singleton(self):
        a = get_alertmanager_client()
        b = get_alertmanager_client()
        assert a is b

    @pytest.mark.asyncio
    async def test_module_send_incident_alert_delegates(self):
        fake = MagicMock()
        fake.send_incident_alert = AsyncMock(return_value=True)
        with patch(
            "shared.alerting.alertmanager.get_alertmanager_client", return_value=fake
        ):
            ok = await send_incident_alert(
                issue_id=5,
                issue_title="t",
                priority="high",
                organization_name="Acme",
                organization_id=2,
                description="d",
            )
        assert ok is True
        fake.send_incident_alert.assert_awaited_once()
        assert fake.send_incident_alert.call_args.kwargs["issue_id"] == 5
        assert fake.send_incident_alert.call_args.kwargs["description"] == "d"
