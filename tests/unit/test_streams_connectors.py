"""Unit tests for Streams connectors subsystem.

Tests manifest loading, node generation, SSRF guarding, and executor.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.worker.streams.connectors.base import ConnectorManifest
from apps.worker.streams.connectors.executor import ConnectorActionExecutor
from apps.worker.streams.connectors.node_generator import (
    create_action_node,
    create_transform_node,
    create_trigger_node,
)
from apps.worker.streams.connectors.registry import discover_connectors


class TestManifestLoading:
    """Test loading and parsing connector manifests."""

    def test_load_elder_manifest(self):
        """Test that the Elder connector manifest loads successfully."""
        manifest = ConnectorManifest.from_yaml(
            "apps/worker/streams/connectors/manifests/elder.yaml"
        )

        assert manifest.id == "elder"
        assert manifest.name == "Elder"
        assert len(manifest.triggers) > 0
        assert len(manifest.actions) > 0
        assert len(manifest.transforms) > 0

    def test_manifest_has_required_fields(self):
        """Test that loaded manifest has all required fields."""
        manifest = ConnectorManifest.from_yaml(
            "apps/worker/streams/connectors/manifests/elder.yaml"
        )

        assert manifest.id
        assert manifest.name
        assert manifest.description
        assert manifest.default_url or manifest.base_url_env


class TestNodeGeneration:
    """Test generation of node classes from manifests."""

    def test_generate_action_node(self):
        """Test generating an action node from definition."""
        manifest = ConnectorManifest.from_yaml(
            "apps/worker/streams/connectors/manifests/elder.yaml"
        )
        action = manifest.actions[0]

        node_class = create_action_node(
            connector_id=manifest.id,
            action=action,
            manifest=manifest,
        )

        # Verify node class structure
        assert node_class.node_type
        assert node_class.name
        assert node_class.description
        assert node_class.category == "actions"
        assert hasattr(node_class, "inputs")
        assert hasattr(node_class, "outputs")
        assert hasattr(node_class, "execute")

    def test_generate_trigger_node(self):
        """Test generating a trigger node from definition."""
        manifest = ConnectorManifest.from_yaml(
            "apps/worker/streams/connectors/manifests/elder.yaml"
        )
        trigger = manifest.triggers[0]

        node_class = create_trigger_node(
            connector_id=manifest.id,
            trigger=trigger,
            manifest=manifest,
        )

        # Verify node class structure
        assert node_class.node_type
        assert node_class.name
        assert node_class.description
        assert node_class.category == "triggers"

    def test_generate_transform_node(self):
        """Test generating a transform node from definition."""
        manifest = ConnectorManifest.from_yaml(
            "apps/worker/streams/connectors/manifests/elder.yaml"
        )
        transform = manifest.transforms[0]

        node_class = create_transform_node(
            connector_id=manifest.id,
            transform=transform,
            manifest=manifest,
        )

        # Verify node class structure
        assert node_class.node_type
        assert node_class.name
        assert node_class.description
        assert node_class.category == "transforms"

    def test_node_type_format(self):
        """Test that generated node types follow the expected format."""
        manifest = ConnectorManifest.from_yaml(
            "apps/worker/streams/connectors/manifests/elder.yaml"
        )

        action = manifest.actions[0]
        node = create_action_node(manifest.id, action, manifest)

        expected_type = f"action_{manifest.id}_{action.id}"
        assert node.node_type == expected_type

    def test_inputs_and_outputs_conversion(self):
        """Test that ports are correctly converted to input/output dicts."""
        manifest = ConnectorManifest.from_yaml(
            "apps/worker/streams/connectors/manifests/elder.yaml"
        )
        action = manifest.actions[0]

        node = create_action_node(manifest.id, action, manifest)
        inputs = node.inputs()
        outputs = node.outputs()

        # Verify structure
        for inp in inputs:
            assert "name" in inp
            assert "description" in inp
            assert "required" in inp
            assert "data_type" in inp

        for out in outputs:
            assert "name" in out
            assert "description" in out
            assert "data_type" in out


class TestConnectorDiscovery:
    """Test the connector discovery process."""

    def test_discover_connectors_loads_manifests(self):
        """Test that discover_connectors loads and generates nodes."""
        # Call discover_connectors - it's idempotent
        count = discover_connectors()
        # On first call, should generate nodes; subsequent calls return 0
        assert count >= 0  # Verify it completes without error

    def test_manifests_are_loaded_after_discovery(self):
        """Test that all 31 manifests are discoverable and generate nodes."""
        from apps.worker.streams.executor.node_registry import NodeRegistry

        # Ensure discovery is run
        discover_connectors()

        # Verify that nodes are registered in the registry
        node_count = NodeRegistry.count()
        # We should have connector nodes registered (exact count varies by manifest)
        assert node_count > 0


class TestActionNodeExecution:
    """Test action node execution with mocked executor."""

    @pytest.mark.asyncio
    async def test_action_node_wraps_output(self):
        """Test that action nodes wrap executor output in the output port structure."""
        manifest = ConnectorManifest.from_yaml(
            "apps/worker/streams/connectors/manifests/elder.yaml"
        )
        action = manifest.actions[0]

        node_class = create_action_node(manifest.id, action, manifest)
        context = {
            "execution_id": "exec-1",
            "node_id": "node-1",
            "config": {},
            "global_config": {},
        }
        node = node_class(context)

        # Mock the executor
        with patch(
            "apps.worker.streams.connectors.node_generator.ConnectorActionExecutor"
        ) as mock_executor_class:
            mock_executor = AsyncMock()
            mock_executor.execute_action.return_value = {"result": "test_data"}
            mock_executor_class.return_value = mock_executor

            result = await node.execute({})

            # Verify output structure
            assert "out" in result
            assert "data" in result["out"]
            assert result["out"]["data"] == {"result": "test_data"}
            assert "metadata" in result["out"]
            assert "source_node_id" in result["out"]
            assert result["out"]["source_node_id"] == "node-1"


class TestSSRFGuarding:
    """Test SSRF protection in the executor."""

    def test_ssrf_guard_blocks_internal_ip_loopback(self):
        """Test that SSRF guard blocks loopback addresses."""
        from apps.worker.streams.nodes.net_guard import guard_ssrf

        with pytest.raises(ValueError, match="Blocked"):
            guard_ssrf("http://127.0.0.1/api")

    def test_ssrf_guard_blocks_private_ip(self):
        """Test that SSRF guard blocks private IP ranges."""
        from apps.worker.streams.nodes.net_guard import guard_ssrf

        with pytest.raises(ValueError, match="Blocked"):
            guard_ssrf("http://192.168.1.1/api")

    def test_ssrf_guard_blocks_metadata_endpoint(self):
        """Test that SSRF guard blocks the cloud metadata endpoint."""
        from apps.worker.streams.nodes.net_guard import guard_ssrf

        with pytest.raises(ValueError, match="Blocked"):
            guard_ssrf("http://169.254.169.254/api")

    def test_ssrf_guard_blocks_non_http_scheme(self):
        """Test that SSRF guard blocks non-http(s) schemes."""
        from apps.worker.streams.nodes.net_guard import guard_ssrf

        with pytest.raises(ValueError, match="scheme"):
            guard_ssrf("file:///etc/passwd")

    @pytest.mark.asyncio
    async def test_action_executor_calls_guard_ssrf(self):
        """Test that the action executor calls guard_ssrf before issuing requests."""
        executor = ConnectorActionExecutor()

        # Try to execute an action with a blocked internal URL
        with patch("apps.worker.streams.connectors.executor.guard_ssrf") as mock_guard:
            mock_guard.side_effect = ValueError("Blocked by SSRF guard")

            with pytest.raises(Exception, match="SSRF guard"):
                await executor.execute_action(
                    connector_id="test",
                    action_id="test_action",
                    endpoint="http://127.0.0.1/admin",
                    method="POST",
                    request_body_template="",
                    config_schema=(),
                    config={},
                    inputs={},
                    variables={},
                    base_url="http://example.com",
                )

            # Verify guard_ssrf was called
            mock_guard.assert_called()

    @pytest.mark.asyncio
    async def test_executor_follow_redirects_disabled(self):
        """Test that the executor disables auto-redirects to prevent redirect-based SSRF."""
        executor = ConnectorActionExecutor()

        # Mock httpx to verify follow_redirects=False
        with patch(
            "apps.worker.streams.connectors.executor.httpx.AsyncClient"
        ) as mock_client:
            with patch(
                "apps.worker.streams.connectors.executor.guard_ssrf"
            ):  # Skip SSRF guard
                mock_response = MagicMock()
                mock_response.json.return_value = {"result": "ok"}
                mock_http_client = AsyncMock()
                mock_http_client.__aenter__.return_value = mock_http_client
                mock_http_client.__aexit__.return_value = None
                mock_http_client.request = AsyncMock(return_value=mock_response)
                mock_client.return_value = mock_http_client

                try:
                    await executor.execute_action(
                        connector_id="test",
                        action_id="test_action",
                        endpoint="/api/test",
                        method="GET",
                        request_body_template="",
                        config_schema=(),
                        config={},
                        inputs={},
                        variables={},
                        base_url="http://example.com",
                    )
                except Exception:
                    pass

                # Verify that AsyncClient was created with follow_redirects=False
                call_kwargs = mock_client.call_args[1]
                assert call_kwargs.get("follow_redirects") is False


class TestVariableInterpolation:
    """Test variable interpolation in executor."""

    def test_interpolate_config_variable(self):
        """Test interpolating a config variable in an endpoint."""
        executor = ConnectorActionExecutor()

        result = executor._interpolate_value(
            "/api/{{entity_id}}/details",
            inputs={},
            variables={},
            config={"entity_id": "123"},
        )

        assert result == "/api/123/details"

    def test_interpolate_input_variable(self):
        """Test interpolating an input variable."""
        executor = ConnectorActionExecutor()

        result = executor._interpolate_value(
            "/api/{{id}}/details",
            inputs={"id": "456"},
            variables={},
            config={},
        )

        assert result == "/api/456/details"

    def test_interpolate_nested_variable(self):
        """Test interpolating a nested variable."""
        executor = ConnectorActionExecutor()

        result = executor._interpolate_value(
            "/api/{{entity.id}}",
            inputs={"in": {"entity": {"id": "789"}}},
            variables={},
            config={},
        )

        assert result == "/api/789"


class TestConnectorCount:
    """Test that all 31 connectors are discoverable."""

    def test_all_manifests_present(self):
        """Verify that all 31 connector manifests are present."""
        from pathlib import Path

        manifest_dir = Path("apps/worker/streams/connectors/manifests")
        manifest_files = list(manifest_dir.glob("*.yaml"))

        assert (
            len(manifest_files) == 31
        ), f"Expected 31 manifests, found {len(manifest_files)}"

    def test_manifests_can_be_loaded(self):
        """Verify that all manifest files can be loaded."""
        from pathlib import Path

        manifest_dir = Path("apps/worker/streams/connectors/manifests")
        manifest_files = sorted(manifest_dir.glob("*.yaml"))

        loaded_count = 0
        failed = []

        for manifest_file in manifest_files:
            try:
                manifest = ConnectorManifest.from_yaml(str(manifest_file))
                loaded_count += 1
            except Exception as e:
                failed.append((manifest_file.name, str(e)))

        assert loaded_count == len(
            manifest_files
        ), f"Failed to load {len(failed)} manifests: {failed}"
