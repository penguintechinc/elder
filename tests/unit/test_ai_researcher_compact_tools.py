"""Unit tests for compact MCP tool schema conversion in AI Researcher."""

from __future__ import annotations

import json

from apps.api.services.ai_researcher_mcp import to_ollama_tools_from_mcp


def test_to_ollama_tools_compacts_verbose_descriptions_and_schemas() -> None:
    verbose_tools = [
        {
            "name": "list_entities",
            "description": (
                "List infrastructure entities for the current tenant. "
                "Supports filtering by name, type, status, tags, and pagination. "
                "Returns paginated items with metadata."
            ),
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "search": {
                        "type": "string",
                        "title": "Search Query",
                        "description": "Free-text substring matched across entity names and descriptions.",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["active", "inactive"],
                        "description": "Lifecycle state filter for entities.",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string", "description": "Tag name"},
                        "description": "Filter by tags.",
                    },
                },
                "required": ["search"],
            },
        }
    ]

    compact = to_ollama_tools_from_mcp(verbose_tools)
    assert len(compact) == 1
    fn = compact[0]["function"]
    assert fn["name"] == "list_entities"
    assert fn["description"] == "List infrastructure entities for the current tenant"
    assert fn["parameters"] == {
        "type": "object",
        "properties": {
            "search": {"type": "string"},
            "status": {"type": "string", "enum": ["active", "inactive"]},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["search"],
    }
    assert len(json.dumps(compact)) < len(json.dumps(verbose_tools)) * 0.65
