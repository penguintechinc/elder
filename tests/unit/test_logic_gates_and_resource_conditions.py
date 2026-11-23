"""Unit tests for playbook logic gates (AND/OR/NOT) and resource state/relationship conditionals."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.api.modules.streams.node_catalog import BUILTIN_UI_META
from apps.worker.streams.nodes.conditionals.if_then import IfThenConditional
from apps.worker.streams.nodes.conditionals.logic_gates import (
    AndConditional,
    NotConditional,
    OrConditional,
)
from apps.worker.streams.nodes.conditionals.while_loop import WhileConditional


def _ctx(
    config: dict,
    *,
    associated_resource: dict | None = None,
    global_config: dict | None = None,
) -> dict:
    return {
        "node_id": "node-1",
        "config": config,
        "global_config": global_config if global_config is not None else {},
        "node_outputs": {},
        "trigger_data": {},
        "associated_resource": associated_resource,
    }


def test_node_catalog_exposes_conditions_on_logic_gates():
    for gate_type in ("conditional_and", "conditional_or", "conditional_not"):
        spec = BUILTIN_UI_META[gate_type]
        fields = [f["field"] for f in spec["config_schema"]]
        assert "conditions" in fields, f"{gate_type} missing conditions field"
    not_fields = [
        f["field"] for f in BUILTIN_UI_META["conditional_not"]["config_schema"]
    ]
    assert "logic" in not_fields


@pytest.mark.asyncio
async def test_and_gate_evaluates_configured_conditions_freeform_and_upstream():
    node = AndConditional(
        _ctx(
            {
                "conditions": [
                    {
                        "source": "input",
                        "field": "severity",
                        "operator": "eq",
                        "value": "high",
                    },
                    {
                        "source": "input",
                        "field": "{{$json.owner}}",
                        "operator": "eq",
                        "value": "{{$json.expected_owner}}",
                    },
                ]
            }
        )
    )
    payload = {"severity": "high", "owner": "alice", "expected_owner": "alice"}
    out = await node.execute({"in1": payload})
    assert out["true"]["data"] == payload
    assert out["false"]["data"] is None

    mismatch = {"severity": "high", "owner": "alice", "expected_owner": "bob"}
    out_false = await node.execute({"in1": mismatch})
    assert out_false["true"]["data"] is None
    assert out_false["false"]["data"] == mismatch


@pytest.mark.asyncio
async def test_or_gate_evaluates_configured_conditions():
    node = OrConditional(
        _ctx(
            {
                "conditions": [
                    {"field": "env", "operator": "eq", "value": "prod"},
                    {"field": "severity", "operator": "eq", "value": "critical"},
                ]
            }
        )
    )
    payload = {"env": "staging", "severity": "critical"}
    out = await node.execute({"in1": payload})
    assert out["true"]["data"] == payload
    assert out["false"]["data"] is None

    payload_miss = {"env": "staging", "severity": "low"}
    out_miss = await node.execute({"in1": payload_miss})
    assert out_miss["true"]["data"] is None
    assert out_miss["false"]["data"] == payload_miss


@pytest.mark.asyncio
async def test_not_gate_inverts_configured_conditions():
    node = NotConditional(
        _ctx(
            {
                "logic": "and",
                "conditions": [
                    {"field": "status", "operator": "eq", "value": "resolved"},
                ],
            }
        )
    )
    open_payload = {"status": "open"}
    out = await node.execute({"in": open_payload})
    assert out["true"]["data"] == open_payload
    assert out["false"]["data"] is None

    resolved_payload = {"status": "resolved"}
    out_false = await node.execute({"in": resolved_payload})
    assert out_false["true"]["data"] is None
    assert out_false["false"]["data"] == resolved_payload


@pytest.mark.asyncio
async def test_logic_gates_preserve_port_boolean_backwards_compatibility():
    and_node = AndConditional(_ctx({}))
    out_and = await and_node.execute({"in1": True, "in2": False})
    assert out_and["true"]["data"] is False
    assert out_and["false"]["data"] is True

    or_node = OrConditional(_ctx({}))
    out_or = await or_node.execute({"in1": False, "in2": True})
    assert out_or["true"]["data"] is True
    assert out_or["false"]["data"] is False

    not_node = NotConditional(_ctx({}))
    out_not = await not_node.execute({"in": True})
    assert out_not["true"]["data"] is False
    assert out_not["false"]["data"] is True


@pytest.mark.asyncio
async def test_if_then_evaluates_associated_resource_state_and_relationships():
    def _mock_response(payload: dict, status_code: int = 200) -> MagicMock:
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = payload
        return resp

    async def _fake_get(url: str, params=None, headers=None):
        if "/api/v1/identities/42" in url:
            return _mock_response(
                {
                    "id": 42,
                    "name": "Jane Doe",
                    "status": "locked",
                    "is_active": False,
                }
            )
        if "/api/v1/dependencies" in url:
            if params and params.get("source_type") == "identity":
                return _mock_response(
                    {
                        "items": [
                            {
                                "id": 1,
                                "source_type": "identity",
                                "source_id": 42,
                                "target_type": "entity",
                                "target_id": 99,
                                "dependency_type": "owns",
                            }
                        ]
                    }
                )
            return _mock_response({"items": []})
        return _mock_response({}, status_code=404)

    mock_client = AsyncMock()
    mock_client.get.side_effect = _fake_get
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    node = IfThenConditional(
        _ctx(
            {
                "logic": "and",
                "conditions": [
                    {
                        "source": "resource",
                        "field": "is_locked",
                        "operator": "eq",
                        "value": "true",
                    },
                    {
                        "source": "resource",
                        "field": "relationships.count",
                        "operator": "gt",
                        "value": "0",
                    },
                    {
                        "source": "resource",
                        "field": "relationships.related_ids",
                        "operator": "contains",
                        "value": 99,
                    },
                ],
            },
            associated_resource={
                "resource_type": "identity",
                "resource_id": 42,
                "resource_name": "Jane Doe",
            },
        )
    )

    with patch(
        "apps.worker.streams.nodes.conditionals.if_then.httpx.AsyncClient",
        return_value=mock_cm,
    ):
        payload = {"event": "login_attempt"}
        out = await node.execute({"in": payload})
        assert out["true"]["data"] == payload
        assert out["false"]["data"] is None


@pytest.mark.asyncio
async def test_if_then_supports_bare_string_any_field_search_and_prior_fields():
    ctx = _ctx(
        {
            "logic": "and",
            "conditions": [
                {
                    "source": "any_field",
                    "operator": "contains",
                    "value": "prod-db-01",
                },
                {
                    "source": "input",
                    "field": "{{$node.step1.region}}",
                    "operator": "eq",
                    "value": "us-east-1",
                },
            ],
        }
    )
    ctx["node_outputs"] = {"step1": {"region": "us-east-1", "host": "prod-db-01"}}
    node = IfThenConditional(ctx)

    payload = {"summary": "Alert on prod-db-01 disk usage"}
    out = await node.execute({"in": payload})
    assert out["true"]["data"] == payload
    assert out["false"]["data"] is None

    # Negative bare-string check: no field contains "forbidden"
    neg_node = IfThenConditional(
        _ctx(
            {
                "logic": "and",
                "conditions": [
                    {
                        "source": "any_field",
                        "operator": "not_contains",
                        "value": "forbidden",
                    }
                ],
            }
        )
    )
    out_clean = await neg_node.execute({"in": {"msg": "all good", "tag": "ok"}})
    assert out_clean["true"]["data"] == {"msg": "all good", "tag": "ok"}

    out_hit = await neg_node.execute({"in": {"msg": "forbidden token"}})
    assert out_hit["true"]["data"] is None


@pytest.mark.asyncio
async def test_while_loop_supports_multi_conditions_and_legacy_condition():
    assert "conditions" in [
        f["field"] for f in BUILTIN_UI_META["conditional_while"]["config_schema"]
    ]

    # New multi-condition mode with AND + bare string
    node = WhileConditional(
        _ctx(
            {
                "logic": "and",
                "conditions": [
                    {
                        "source": "input",
                        "field": "remaining",
                        "operator": "gt",
                        "value": "0",
                    },
                    {"source": "any_field", "operator": "contains", "value": "pending"},
                ],
            }
        )
    )
    out_loop = await node.execute({"in": {"remaining": 3, "state": "pending_sync"}})
    assert out_loop["loop"]["data"] == {"remaining": 3, "state": "pending_sync"}
    assert out_loop["done"]["data"] is None

    out_done = await node.execute({"in": {"remaining": 0, "state": "pending_sync"}})
    assert out_done["loop"]["data"] is None
    assert out_done["done"]["data"] == {"remaining": 0, "state": "pending_sync"}

    # Legacy single condition dict backwards compatibility
    legacy = WhileConditional(
        _ctx({"condition": {"field": "remaining", "operator": "gt", "value": 0}})
    )
    out_legacy = await legacy.execute({"in": {"remaining": 2}})
    assert out_legacy["loop"]["data"] == {"remaining": 2}
    assert out_legacy["done"]["data"] is None
