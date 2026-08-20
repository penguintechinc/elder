"""Unit tests for the service_nodes heartbeat table (apps/api/models/service_node.py)."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from quart import current_app


@pytest.mark.asyncio
class TestCountActiveNodes:
    """Tests for count_active_nodes() staleness filtering."""

    async def test_counts_only_recent_heartbeats(self, app):
        from apps.api.models.service_node import count_active_nodes

        async with app.app_context():
            db = current_app.db
            now = datetime.now(UTC)
            service_type = f"scanner-{uuid4().hex[:8]}"

            db.service_nodes.insert(
                service_type=service_type,
                pod_id=f"pod-a-{uuid4().hex[:8]}",
                heartbeat_ts=now,
            )
            db.service_nodes.insert(
                service_type=service_type,
                pod_id=f"pod-b-{uuid4().hex[:8]}",
                heartbeat_ts=now,
            )
            # Stale: heartbeat well outside the 90s staleness window.
            db.service_nodes.insert(
                service_type=service_type,
                pod_id=f"pod-c-{uuid4().hex[:8]}",
                heartbeat_ts=now - timedelta(minutes=5),
            )
            db.commit()

            count = count_active_nodes(db, service_type, stale_after_s=90)

            assert count == 2

    async def test_only_counts_matching_service_type(self, app):
        from apps.api.models.service_node import count_active_nodes

        async with app.app_context():
            db = current_app.db
            now = datetime.now(UTC)
            service_type_a = f"scanner-{uuid4().hex[:8]}"
            service_type_b = f"worker-{uuid4().hex[:8]}"

            db.service_nodes.insert(
                service_type=service_type_a,
                pod_id=f"pod-{uuid4().hex[:8]}",
                heartbeat_ts=now,
            )
            db.service_nodes.insert(
                service_type=service_type_b,
                pod_id=f"pod-{uuid4().hex[:8]}",
                heartbeat_ts=now,
            )
            db.commit()

            assert count_active_nodes(db, service_type_a, stale_after_s=90) == 1


@pytest.mark.asyncio
class TestRegisterNode:
    """Tests for register_node() upsert semantics."""

    async def test_insert_then_upsert_same_pod(self, app):
        from apps.api.models.service_node import register_node

        async with app.app_context():
            db = current_app.db
            pod_id = f"pod-{uuid4().hex[:8]}"

            first_id = register_node(db, "scanner", pod_id)
            db.commit()
            second_id = register_node(db, "scanner", pod_id)
            db.commit()

            assert first_id == second_id
            assert db(db.service_nodes.pod_id == pod_id).count() == 1

    async def test_register_sets_heartbeat_and_service_type(self, app):
        from apps.api.models.service_node import register_node

        async with app.app_context():
            db = current_app.db
            pod_id = f"pod-{uuid4().hex[:8]}"

            register_node(db, "scanner", pod_id)
            db.commit()

            row = db(db.service_nodes.pod_id == pod_id).select().first()
            assert row.service_type == "scanner"
            assert row.heartbeat_ts is not None


@pytest.mark.asyncio
class TestHeartbeatNode:
    """Tests for heartbeat_node() refresh semantics."""

    async def test_refreshes_heartbeat_ts(self, app):
        from apps.api.models.service_node import heartbeat_node, register_node

        async with app.app_context():
            db = current_app.db
            pod_id = f"pod-{uuid4().hex[:8]}"

            register_node(db, "scanner", pod_id)
            db.commit()
            # Force an old heartbeat so the refresh is observable.
            db(db.service_nodes.pod_id == pod_id).update(
                heartbeat_ts=datetime.now(UTC) - timedelta(minutes=10)
            )
            db.commit()
            old_row = db(db.service_nodes.pod_id == pod_id).select().first()

            updated = heartbeat_node(db, pod_id)
            db.commit()

            assert updated is True
            new_row = db(db.service_nodes.pod_id == pod_id).select().first()
            assert new_row.heartbeat_ts > old_row.heartbeat_ts

    async def test_returns_false_for_unregistered_pod(self, app):
        from apps.api.models.service_node import heartbeat_node

        async with app.app_context():
            db = current_app.db
            assert heartbeat_node(db, f"never-registered-{uuid4().hex[:8]}") is False
