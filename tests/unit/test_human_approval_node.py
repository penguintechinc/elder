"""Unit tests for the Human Approval playbook action node (`action_human_approval`)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.api.modules.streams.node_catalog import BUILTIN_UI_META
from apps.worker.streams.executor.node_registry import NodeRegistry, discover_nodes
from apps.worker.streams.nodes.actions.human_approval import HumanApprovalAction


def _make_context(config: dict, **extra) -> dict:
    ctx = {
        "execution_id": "exec-approval-1",
        "playbook_id": 42,
        "node_id": "node-approval-1",
        "config": config,
        "global_config": extra.pop(
            "global_config",
            {
                "elder_api_url": "http://api.test.local:8080",
                "_elder_auth_token": "tok-worker-secret",
            },
        ),
    }
    ctx.update(extra)
    return ctx


class TestHumanApprovalRegistrationAndCatalog:
    """Verify node registry wiring and UI catalog metadata."""

    def test_action_human_approval_registered_in_node_registry(self):
        discover_nodes()
        cls = NodeRegistry.get("action_human_approval")
        assert cls is HumanApprovalAction
        assert cls.node_type == "action_human_approval"
        assert cls.category == "actions"
        assert cls.name == "Human Approval"

        inputs = cls.inputs()
        assert len(inputs) == 1
        assert inputs[0]["name"] == "in"
        assert inputs[0]["required"] is False
        assert inputs[0]["data_type"] == "any"

        outputs = cls.outputs()
        output_names = [o["name"] for o in outputs]
        assert output_names == ["approved", "rejected"]
        assert all(o["data_type"] == "any" for o in outputs)

    def test_catalog_ui_meta_present_and_complete(self):
        assert "action_human_approval" in BUILTIN_UI_META
        spec = BUILTIN_UI_META["action_human_approval"]
        assert spec["stage"] == "process"
        assert spec["subcategory"] == "Approvals"
        assert spec["icon"] == "UserCheck"
        assert spec["color"] == "#f59e0b"

        fields = {f["field"]: f for f in spec["config_schema"]}
        assert {
            "title",
            "instructions",
            "approver_ids",
            "required_approvals",
            "timeout_minutes",
        }.issubset(fields.keys())

        assert fields["title"]["required"] is True
        assert fields["title"]["supports_expressions"] is True
        assert fields["instructions"]["type"] == "textarea"
        assert fields["instructions"]["supports_expressions"] is True
        assert fields["approver_ids"]["supports_expressions"] is True
        assert fields["required_approvals"]["default"] == 1
        assert fields["timeout_minutes"]["default"] == 1440

        out_names = {o["name"] for o in spec["output_schema"]}
        assert {
            "paused_for_approval",
            "approval_title",
            "approver_ids",
            "required_approvals",
            "timeout_minutes",
        }.issubset(out_names)


class TestHumanApprovalExecute:
    """Verify execution, interpolation, API request, and paused metadata."""

    @pytest.mark.asyncio
    async def test_execute_posts_interpolated_payload_and_returns_paused_metadata(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"status": "paused_for_approval"}'

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        node = HumanApprovalAction(
            _make_context(
                {
                    "title": "Approve deploy for {{$json.service}}",
                    "instructions": "Verify change {{$json.ticket}} before continuing",
                    "approver_ids": "{{$json.approvers}}",
                    "required_approvals": 2,
                    "timeout_minutes": 720,
                }
            )
        )

        with patch(
            "apps.worker.streams.nodes.actions.human_approval.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await node.execute(
                {
                    "in": {
                        "service": "billing-api",
                        "ticket": "CHG-99",
                        "approvers": "10, 20",
                    }
                }
            )

        mock_client.post.assert_awaited_once_with(
            "http://api.test.local:8080/api/v1/streams/42/executions/exec-approval-1/request-approval",
            json={
                "node_id": "node-approval-1",
                "title": "Approve deploy for billing-api",
                "instructions": "Verify change CHG-99 before continuing",
                "approver_ids": [10, 20],
                "required_approvals": 2,
                "timeout_minutes": 720,
            },
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": "Bearer tok-worker-secret",
            },
        )

        assert result["approved"]["data"] is None
        assert result["approved"]["source_node_id"] == "node-approval-1"
        assert result["approved"]["metadata"]["paused_for_approval"] is True
        assert (
            result["approved"]["metadata"]["approval_title"]
            == "Approve deploy for billing-api"
        )
        assert result["approved"]["metadata"]["approver_ids"] == [10, 20]
        assert result["approved"]["metadata"]["required_approvals"] == 2
        assert result["approved"]["metadata"]["timeout_minutes"] == 720

        assert result["rejected"]["data"] is None
        assert result["rejected"]["source_node_id"] == "node-approval-1"
        assert result["rejected"]["metadata"]["paused_for_approval"] is True

    @pytest.mark.asyncio
    async def test_execute_uses_env_fallback_for_url_and_token(self, monkeypatch):
        monkeypatch.setenv("ELDER_API_URL", "http://env-api:9000/")
        monkeypatch.setenv("ELDER_INTERNAL_TOKEN", "env-internal-tok")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "{}"

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        node = HumanApprovalAction(
            _make_context(
                {
                    "title": "Manual Sign-off",
                    "approver_ids": "5",
                },
                global_config={},
            )
        )

        with patch(
            "apps.worker.streams.nodes.actions.human_approval.httpx.AsyncClient",
            return_value=mock_client,
        ):
            await node.execute({})

        call_args = mock_client.post.call_args
        assert (
            call_args.args[0]
            == "http://env-api:9000/api/v1/streams/42/executions/exec-approval-1/request-approval"
        )
        assert call_args.kwargs["headers"]["Authorization"] == "Bearer env-internal-tok"
        assert call_args.kwargs["json"]["approver_ids"] == [5]
        assert call_args.kwargs["json"]["required_approvals"] == 1
        assert call_args.kwargs["json"]["timeout_minutes"] == 1440

    @pytest.mark.asyncio
    async def test_execute_raises_runtime_error_on_api_failure(self):
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = '{"error": "Access denied"}'

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        node = HumanApprovalAction(_make_context({"title": "Approve access"}))

        with (
            patch(
                "apps.worker.streams.nodes.actions.human_approval.httpx.AsyncClient",
                return_value=mock_client,
            ),
            pytest.raises(RuntimeError, match="HTTP 403"),
        ):
            await node.execute({})


class TestHumanApprovalValidation:
    """Verify static config validation and runtime argument validation."""

    def test_validate_config_reports_missing_title_and_bad_values(self):
        node = HumanApprovalAction(_make_context({}))
        errors = node.validate_config(
            {
                "title": "   ",
                "approver_ids": "1,abc",
                "required_approvals": 0,
                "timeout_minutes": -10,
            }
        )
        assert any("title" in e for e in errors)
        assert any("approver" in e.lower() for e in errors)
        assert any("required_approvals" in e for e in errors)
        assert any("timeout_minutes" in e for e in errors)

    def test_validate_config_allows_expressions_and_valid_values(self):
        node = HumanApprovalAction(_make_context({}))
        errors = node.validate_config(
            {
                "title": "Approve {{$json.name}}",
                "approver_ids": "{{$json.approver_ids}}",
                "required_approvals": 2,
                "timeout_minutes": 60,
            }
        )
        assert errors == []

    @pytest.mark.asyncio
    async def test_execute_raises_on_missing_title(self):
        node = HumanApprovalAction(_make_context({"title": ""}))
        with pytest.raises(ValueError, match="title is required"):
            await node.execute({})

    @pytest.mark.asyncio
    async def test_execute_raises_on_invalid_approver_ids(self):
        node = HumanApprovalAction(
            _make_context({"title": "Approve", "approver_ids": "1,notanint"})
        )
        with pytest.raises(ValueError, match="Invalid approver identity ID"):
            await node.execute({})

    @pytest.mark.asyncio
    async def test_execute_raises_on_missing_execution_context(self):
        node = HumanApprovalAction(
            _make_context({"title": "Approve"}, playbook_id=None, execution_id="")
        )
        with pytest.raises(ValueError, match="playbook_id and execution_id"):
            await node.execute({})
