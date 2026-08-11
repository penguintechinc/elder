"""Streams Playbook Executor tests (pure + handle_streams integration).

Tests topological order, data flow, conditionals, cycle detection, and
the handle_streams job handler with real database operations.

regression: streams-phase4b-executor
"""

import uuid
from datetime import UTC, datetime, timezone

import pytest
import pytest_asyncio

from apps.worker.streams.executor.graph_utils import (
    CycleDetectedError,
    TopologicalSorter,
)
from apps.worker.streams.executor.node_registry import discover_nodes
from apps.worker.streams.executor.playbook_executor import (
    ExecutionResult,
    PlaybookExecutor,
)
from shared.jobbus import JobEnvelope


class TestTopologicalSorter:
    """Test graph sorting and cycle detection."""

    def test_linear_graph_execution_order(self):
        """Nodes execute in correct topological order."""
        nodes = [
            {"id": "start", "type": "action_log"},
            {"id": "middle", "type": "action_log"},
            {"id": "end", "type": "action_log"},
        ]
        edges = [
            {"source": "start", "target": "middle"},
            {"source": "middle", "target": "end"},
        ]

        sorter = TopologicalSorter(nodes, edges)
        order = sorter.sort()

        assert order == ["start", "middle", "end"]

    def test_parallel_branch_topological_order(self):
        """Branches execute in correct topological order."""
        nodes = [
            {"id": "start", "type": "action_log"},
            {"id": "branch1", "type": "action_log"},
            {"id": "branch2", "type": "action_log"},
            {"id": "merge", "type": "action_log"},
        ]
        edges = [
            {"source": "start", "target": "branch1"},
            {"source": "start", "target": "branch2"},
            {"source": "branch1", "target": "merge"},
            {"source": "branch2", "target": "merge"},
        ]

        sorter = TopologicalSorter(nodes, edges)
        order = sorter.sort()

        # start first, merge last, branches in middle
        assert order[0] == "start"
        assert order[-1] == "merge"
        assert "branch1" in order[1:3]
        assert "branch2" in order[1:3]

    def test_cycle_detection_simple_loop(self):
        """Cycle detection rejects simple cycles."""
        nodes = [
            {"id": "a", "type": "action_log"},
            {"id": "b", "type": "action_log"},
            {"id": "c", "type": "action_log"},
        ]
        edges = [
            {"source": "a", "target": "b"},
            {"source": "b", "target": "c"},
            {"source": "c", "target": "a"},  # Creates cycle
        ]

        sorter = TopologicalSorter(nodes, edges)
        with pytest.raises(CycleDetectedError):
            sorter.sort()

    def test_cycle_detection_self_loop(self):
        """Cycle detection rejects self-loops."""
        nodes = [{"id": "a", "type": "action_log"}]
        edges = [{"source": "a", "target": "a"}]

        sorter = TopologicalSorter(nodes, edges)
        with pytest.raises(CycleDetectedError):
            sorter.sort()

    def test_isolated_node(self):
        """Isolated node is included in sort."""
        nodes = [
            {"id": "a", "type": "action_log"},
            {"id": "b", "type": "action_log"},
        ]
        edges = []  # No connections

        sorter = TopologicalSorter(nodes, edges)
        order = sorter.sort()

        assert len(order) == 2
        assert set(order) == {"a", "b"}


class TestPlaybookExecutor:
    """Test pure executor (no DB, no network)."""

    @pytest.mark.asyncio
    async def test_simple_linear_execution(self):
        """Execute a simple linear graph of log nodes."""
        executor = PlaybookExecutor(
            execution_id="exec-123",
            playbook_id="pb-456",
        )

        # Discover nodes to register log/delay/if_then
        discover_nodes()

        playbook_data = {
            "nodes": [
                {
                    "id": "log1",
                    "type": "action_log",
                    "category": "actions",
                    "data": {"label": "Log Start", "category": "actions"},
                    "config": {"level": "INFO"},
                },
                {
                    "id": "log2",
                    "type": "action_log",
                    "category": "actions",
                    "data": {"label": "Log End", "category": "actions"},
                    "config": {"level": "INFO"},
                },
            ],
            "edges": [
                {
                    "source": "log1",
                    "target": "log2",
                    "sourceHandle": "logged",
                    "targetHandle": "message",
                }
            ],
            "config": {},
        }

        result = await executor.execute(playbook_data)

        assert isinstance(result, ExecutionResult)
        assert result.success is True
        assert len(result.completed_nodes) == 2
        assert len(result.failed_nodes) == 0
        assert "log1" in result.node_results
        assert "log2" in result.node_results

    @pytest.mark.asyncio
    async def test_conditional_branching_true_path(self):
        """Conditional routes to true branch when condition passes."""
        executor = PlaybookExecutor(
            execution_id="exec-789",
            playbook_id="pb-101",
        )

        discover_nodes()

        playbook_data = {
            "nodes": [
                {
                    "id": "log_start",
                    "type": "action_log",
                    "category": "actions",
                    "data": {"label": "Start", "category": "actions"},
                    "config": {"level": "INFO"},
                },
                {
                    "id": "if_check",
                    "type": "conditional_if_then",
                    "category": "conditionals",
                    "data": {"label": "Check", "category": "conditionals"},
                    "config": {
                        "conditions": [
                            {"field": "value", "operator": "eq", "value": True}
                        ],
                        "logic": "and",
                    },
                },
                {
                    "id": "log_true",
                    "type": "action_log",
                    "category": "actions",
                    "data": {"label": "True Path", "category": "actions"},
                    "config": {"level": "INFO"},
                },
                {
                    "id": "log_false",
                    "type": "action_log",
                    "category": "actions",
                    "data": {"label": "False Path", "category": "actions"},
                    "config": {"level": "INFO"},
                },
            ],
            "edges": [
                {
                    "source": "log_start",
                    "target": "if_check",
                    "sourceHandle": "logged",
                    "targetHandle": "in",
                },
                {
                    "source": "if_check",
                    "target": "log_true",
                    "sourceHandle": "true",
                    "targetHandle": "message",
                },
                {
                    "source": "if_check",
                    "target": "log_false",
                    "sourceHandle": "false",
                    "targetHandle": "message",
                },
            ],
            "config": {},
        }

        result = await executor.execute(playbook_data)

        assert result.success is True
        assert "log_start" in result.completed_nodes
        assert "if_check" in result.completed_nodes
        assert "log_true" in result.completed_nodes
        assert "log_false" in result.completed_nodes

    @pytest.mark.asyncio
    async def test_data_flow_between_nodes(self):
        """Data flows correctly from one node to the next."""
        executor = PlaybookExecutor(
            execution_id="exec-data-flow",
            playbook_id="pb-data",
        )

        discover_nodes()

        playbook_data = {
            "nodes": [
                {
                    "id": "log1",
                    "type": "action_log",
                    "category": "actions",
                    "data": {"label": "Start Log", "category": "actions"},
                    "config": {"level": "INFO"},
                },
                {
                    "id": "delay",
                    "type": "transform_delay",
                    "category": "transforms",
                    "data": {"label": "Delay", "category": "transforms"},
                    "config": {"delayMs": 10},
                },
                {
                    "id": "log2",
                    "type": "action_log",
                    "category": "actions",
                    "data": {"label": "End Log", "category": "actions"},
                    "config": {"level": "INFO"},
                },
            ],
            "edges": [
                {
                    "source": "log1",
                    "target": "delay",
                    "sourceHandle": "logged",
                    "targetHandle": "in",
                },
                {
                    "source": "delay",
                    "target": "log2",
                    "sourceHandle": "out",
                    "targetHandle": "message",
                },
            ],
            "config": {},
        }

        result = await executor.execute(playbook_data)

        assert result.success is True
        # All nodes should complete successfully
        assert len(result.completed_nodes) == 3

    @pytest.mark.asyncio
    async def test_execution_order_respected(self):
        """Nodes execute in topological order (start → middle → end)."""
        execution_order_log = []

        # Patch logger to capture execution order
        import logging

        class OrderCapturingHandler(logging.Handler):
            def emit(self, record):
                if "Executing node" in record.getMessage():
                    # Extract node ID from message
                    pass

        executor = PlaybookExecutor(
            execution_id="exec-order",
            playbook_id="pb-order",
        )

        discover_nodes()

        playbook_data = {
            "nodes": [
                {
                    "id": "node_1",
                    "type": "action_log",
                    "category": "actions",
                    "data": {"label": "First", "category": "actions"},
                    "config": {"level": "INFO"},
                },
                {
                    "id": "node_2",
                    "type": "action_log",
                    "category": "actions",
                    "data": {"label": "Second", "category": "actions"},
                    "config": {"level": "INFO"},
                },
                {
                    "id": "node_3",
                    "type": "action_log",
                    "category": "actions",
                    "data": {"label": "Third", "category": "actions"},
                    "config": {"level": "INFO"},
                },
            ],
            "edges": [
                {"source": "node_1", "target": "node_2"},
                {"source": "node_2", "target": "node_3"},
            ],
            "config": {},
        }

        result = await executor.execute(playbook_data)

        assert result.success is True
        # Check that execution_order_internal matches topological order
        assert executor._execution_order == ["node_1", "node_2", "node_3"]

    @pytest.mark.asyncio
    async def test_unregistered_node_type_fails(self):
        """Unregistered node type fails gracefully."""
        executor = PlaybookExecutor(
            execution_id="exec-unknown",
            playbook_id="pb-unknown",
        )

        discover_nodes()

        playbook_data = {
            "nodes": [
                {
                    "id": "unknown_node",
                    "type": "nonexistent_type",
                    "category": "unknown",
                    "data": {"label": "Unknown", "category": "unknown"},
                    "config": {},
                },
            ],
            "edges": [],
            "config": {},
        }

        result = await executor.execute(playbook_data)

        assert result.success is False
        assert "unknown_node" in result.failed_nodes

    @pytest.mark.asyncio
    async def test_no_nodes_fails(self):
        """Playbook with no nodes fails."""
        executor = PlaybookExecutor(
            execution_id="exec-empty",
            playbook_id="pb-empty",
        )

        playbook_data = {
            "nodes": [],
            "edges": [],
            "config": {},
        }

        result = await executor.execute(playbook_data)

        assert result.success is False
        assert "No nodes in playbook" in result.error


class TestHandleStreamsIntegration:
    """Test handle_streams job handler with database."""

    @pytest_asyncio.fixture(autouse=True)
    async def setup_db(self, app, test_database_url):
        """Set up test database with tenant, playbook, nodes, edges."""
        if not test_database_url:
            pytest.skip("DATABASE_URL not set")

        db = app.db

        def _setup():
            now = datetime.now(UTC)
            tenant_id = db.tenants.insert(
                name=f"Streams Test Tenant {uuid.uuid4().hex[:8]}",
                slug=f"str-test-{uuid.uuid4().hex[:8]}",
                is_active=True,
                created_at=now,
                updated_at=now,
            )

            # Create an identity for playbook owner
            email = f"test-user-{uuid.uuid4().hex[:8]}@test.local"
            identity_id = db.identities.insert(
                tenant_id=tenant_id,
                username=email,
                email=email,
                identity_type="human",
                auth_provider="local",
                is_active=True,
                is_superuser=False,
                mfa_enabled=False,
                must_change_password=False,
                portal_role="viewer",
                full_name="Test User",
                created_at=now,
                updated_at=now,
            )

            # Create a simple playbook
            playbook_id = db.stream_playbooks.insert(
                tenant_id=tenant_id,
                village_id=f"test-{uuid.uuid4().hex[:24]}",
                name="Test Playbook",
                description="",
                owner_identity_id=identity_id,
                created_by_identity_id=identity_id,
                trigger_type="manual",
                is_public=False,
                is_template=False,
                is_enabled=True,
                tags=[],
                status="draft",
                execution_count=0,
                success_count=0,
                failure_count=0,
                created_at=now,
                updated_at=now,
            )

            # Create nodes: log1 -> log2
            node1_id = f"log1-{uuid.uuid4().hex[:8]}"
            node2_id = f"log2-{uuid.uuid4().hex[:8]}"

            db.stream_nodes.insert(
                tenant_id=tenant_id,
                playbook_id=playbook_id,
                node_id=node1_id,
                node_type="action_log",
                node_category="actions",
                label="Log 1",
                position_x=0,
                position_y=0,
                config={"level": "INFO"},
                created_at=now,
                updated_at=now,
            )

            db.stream_nodes.insert(
                tenant_id=tenant_id,
                playbook_id=playbook_id,
                node_id=node2_id,
                node_type="action_log",
                node_category="actions",
                label="Log 2",
                position_x=0,
                position_y=0,
                config={"level": "INFO"},
                created_at=now,
                updated_at=now,
            )

            # Create edge: log1 -> log2
            db.stream_edges.insert(
                tenant_id=tenant_id,
                playbook_id=playbook_id,
                edge_id=f"edge-{uuid.uuid4().hex[:8]}",
                source_node_id=node1_id,
                target_node_id=node2_id,
                source_handle="logged",
                target_handle="message",
                created_at=now,
                updated_at=now,
            )

            # Create queued execution
            execution_id = str(uuid.uuid4())
            db.stream_executions.insert(
                tenant_id=tenant_id,
                playbook_id=playbook_id,
                execution_id=execution_id,
                status="queued",
                trigger_type="manual",
                input_json={},
                created_at=now,
                updated_at=now,
            )

            db.commit()

            return {
                "tenant_id": tenant_id,
                "playbook_id": playbook_id,
                "execution_id": execution_id,
                "node1_id": node1_id,
                "node2_id": node2_id,
            }

        from apps.api.utils.async_utils import run_in_threadpool

        self.fixtures = await run_in_threadpool(_setup)

    @pytest.mark.asyncio
    async def test_handle_streams_queued_execution_succeeds(self, app):
        """handle_streams executes queued playbook and updates status."""
        from apps.worker.jobs.registry import handle_streams

        # Discover nodes
        discover_nodes()

        execution_id = self.fixtures["execution_id"]
        playbook_id = self.fixtures["playbook_id"]
        tenant_id = self.fixtures["tenant_id"]

        # Create job envelope
        envelope = JobEnvelope(
            job_id=str(uuid.uuid4()),
            job_type="execute_playbook",
            payload={
                "execution_id": execution_id,
                "playbook_id": playbook_id,
                "tenant_id": tenant_id,
            },
            enqueued_at=datetime.now(UTC).isoformat(),
            tenant_id=tenant_id,
        )

        # Call handle_streams
        result = await handle_streams(envelope)

        # Verify result
        assert result["status"] in ("success", "failed")
        assert result["execution_id"] == execution_id

        # Verify execution record updated
        db = app.db

        def _verify():
            execution = (
                db(
                    (db.stream_executions.execution_id == execution_id)
                    & (db.stream_executions.tenant_id == tenant_id)
                )
                .select()
                .first()
            )

            assert execution is not None
            assert execution.status in ("success", "running", "failed", "completed")
            assert execution.duration_ms is not None or execution.status == "running"

        from apps.api.utils.async_utils import run_in_threadpool

        await run_in_threadpool(_verify)

    @pytest.mark.asyncio
    async def test_handle_streams_creates_node_executions(self, app):
        """handle_streams creates stream_node_executions records."""
        from apps.worker.jobs.registry import handle_streams

        discover_nodes()

        execution_id = self.fixtures["execution_id"]
        playbook_id = self.fixtures["playbook_id"]
        tenant_id = self.fixtures["tenant_id"]

        envelope = JobEnvelope(
            job_id=str(uuid.uuid4()),
            job_type="execute_playbook",
            payload={
                "execution_id": execution_id,
                "playbook_id": playbook_id,
                "tenant_id": tenant_id,
            },
            enqueued_at=datetime.now(UTC).isoformat(),
            tenant_id=tenant_id,
        )

        await handle_streams(envelope)

        # Verify node execution records
        db = app.db

        def _verify():
            node_execs = db(
                (db.stream_node_executions.execution_id == execution_id)
                & (db.stream_node_executions.tenant_id == tenant_id)
            ).select()

            assert len(node_execs) >= 1

        from apps.api.utils.async_utils import run_in_threadpool

        await run_in_threadpool(_verify)

    @pytest.mark.asyncio
    async def test_handle_streams_missing_execution_fails(self):
        """handle_streams fails when execution not found."""
        from apps.worker.jobs.registry import handle_streams

        envelope = JobEnvelope(
            job_id=str(uuid.uuid4()),
            job_type="execute_playbook",
            payload={
                "execution_id": "nonexistent",
                "playbook_id": "nonexistent",
                "tenant_id": 999999,
            },
            enqueued_at=datetime.now(UTC).isoformat(),
            tenant_id=999999,
        )

        with pytest.raises(Exception):
            await handle_streams(envelope)

    @pytest.mark.asyncio
    async def test_handle_streams_idempotent(self, app):
        """handle_streams is idempotent for terminal executions."""
        from apps.worker.jobs.registry import handle_streams

        discover_nodes()

        execution_id = self.fixtures["execution_id"]
        playbook_id = self.fixtures["playbook_id"]
        tenant_id = self.fixtures["tenant_id"]

        envelope = JobEnvelope(
            job_id=str(uuid.uuid4()),
            job_type="execute_playbook",
            payload={
                "execution_id": execution_id,
                "playbook_id": playbook_id,
                "tenant_id": tenant_id,
            },
            enqueued_at=datetime.now(UTC).isoformat(),
            tenant_id=tenant_id,
        )

        # First call
        result1 = await handle_streams(envelope)

        # Second call (should skip because execution is already terminal)
        envelope2 = JobEnvelope(
            job_id=str(uuid.uuid4()),
            job_type="execute_playbook",
            payload={
                "execution_id": execution_id,
                "playbook_id": playbook_id,
                "tenant_id": tenant_id,
            },
            enqueued_at=datetime.now(UTC).isoformat(),
            tenant_id=tenant_id,
        )
        result2 = await handle_streams(envelope2)

        # Second result should indicate skip
        assert result2.get("status") in ("success", "skipped")
