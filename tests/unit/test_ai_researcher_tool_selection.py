"""Unit tests for AI Researcher query-relevant tool selection (anti-bloat)."""

from __future__ import annotations

from apps.api.services.ai_researcher_mcp import select_relevant_tools


def _make_tool(name: str, description: str = "") -> dict[str, object]:
    return {
        "name": name,
        "description": description or f"Tool {name}",
        "inputSchema": {"type": "object", "properties": {}},
    }


class TestSelectRelevantTools:
    """Verify tool list is bounded to avoid overflowing local LLM context windows."""

    def test_returns_all_when_under_cap(self) -> None:
        tools = [_make_tool(f"list_item_{i}") for i in range(5)]
        selected = select_relevant_tools(tools, "anything", max_tools=12)
        assert [t["name"] for t in selected] == [t["name"] for t in tools]

    def test_caps_large_catalog_and_preserves_core_discovery_tools(self) -> None:
        tools = [
            _make_tool("universal_search", "Search across all Elder resources"),
            _make_tool("search_graph", "Search resource dependency graph"),
            _make_tool("resolve_ref", "Resolve resource reference"),
            _make_tool("get_ref_backlinks", "Get backlinks for a resource"),
            _make_tool(
                "list_vulnerabilities", "List security vulnerabilities and CVEs"
            ),
            _make_tool("get_vulnerability", "Get details of a single vulnerability"),
            _make_tool("list_entities", "List infrastructure servers, hosts, and VMs"),
            *[
                _make_tool(f"list_unrelated_{i}", "Unrelated resource")
                for i in range(40)
            ],
        ]

        selected = select_relevant_tools(
            tools,
            "Show me critical CVE security vulnerabilities",
            max_tools=8,
        )
        names = [t["name"] for t in selected]
        assert len(names) == 8
        assert "universal_search" in names
        assert "search_graph" in names
        assert "resolve_ref" in names
        assert "get_ref_backlinks" in names
        assert "list_vulnerabilities" in names
        assert "get_vulnerability" in names

    def test_domain_synonyms_surface_matching_tools(self) -> None:
        tools = [
            _make_tool("universal_search", "Search across all resources"),
            _make_tool("list_entities", "List entities"),
            _make_tool("list_helpdesk_tickets", "List helpdesk tickets"),
            _make_tool("list_ipam_prefixes", "List IPAM prefixes"),
            *[_make_tool(f"list_misc_{i}", "Miscellaneous item") for i in range(25)],
        ]

        selected = select_relevant_tools(
            tools,
            "Which servers have open subnet allocations?",
            max_tools=5,
        )
        names = [t["name"] for t in selected]
        assert len(names) == 5
        assert "universal_search" in names
        assert "list_entities" in names
        assert "list_ipam_prefixes" in names
