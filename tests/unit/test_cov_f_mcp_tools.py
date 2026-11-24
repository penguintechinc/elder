"""Contract tests for the curated MCP tool modules against a stubbed Elder upstream.

Every tool is driven through the real ``ElderClient`` with an
``httpx.MockTransport`` standing in for hub-api, so the assertions are about
the request Elder would actually receive.
"""

import importlib
import inspect
import json
from typing import Any
from urllib.parse import parse_qsl

import httpx
import pytest

from apps.mcp.client import ElderAPIError, ElderClient
from apps.mcp.tools import relationships, resources
from apps.mcp.tools.entities import list_data_stores, update_entity
from apps.mcp.tools.knowledge import list_stream_executions
from tests.unit._cov_f_helpers import fake_secret

TOKEN = fake_secret("mcp", "access", "token")

# (module, function, HTTP method, path template using the tool's own argument names)
ROUTES: list[tuple[str, str, str, str]] = [
    ("entities", "search_entities", "GET", "/api/v1/entities"),
    ("entities", "get_entity", "GET", "/api/v1/entities/{entity_id}"),
    ("entities", "update_entity", "PATCH", "/api/v1/entities/{entity_id}"),
    ("entities", "list_data_stores", "GET", "/api/v1/data-stores"),
    ("entities", "get_data_store", "GET", "/api/v1/data-stores/{data_store_id}"),
    ("entities", "list_networks", "GET", "/api/v1/networking/networks"),
    ("entities", "get_network", "GET", "/api/v1/networking/networks/{network_id}"),
    ("entities", "get_networking_topology", "GET", "/api/v1/networking/topology/graph"),
    ("entities", "list_organizations", "GET", "/api/v1/organizations"),
    ("entities", "get_organization", "GET", "/api/v1/organizations/{organization_id}"),
    (
        "entities",
        "get_organization_children",
        "GET",
        "/api/v1/organizations/{organization_id}/children",
    ),
    (
        "entities",
        "get_organization_tree_stats",
        "GET",
        "/api/v1/organizations/{org_id}/tree-stats",
    ),
    ("entities", "list_geo_locations", "GET", "/api/v1/geo/locations"),
    ("entities", "list_geo_regions", "GET", "/api/v1/geo/regions"),
    ("knowledge", "list_documents", "GET", "/api/v1/documents"),
    ("knowledge", "get_document", "GET", "/api/v1/documents/{slug}"),
    ("knowledge", "list_pages", "GET", "/api/v1/pages"),
    ("knowledge", "get_page", "GET", "/api/v1/pages/{slug}"),
    ("knowledge", "list_diagrams", "GET", "/api/v1/diagrams"),
    ("knowledge", "get_diagram", "GET", "/api/v1/diagrams/{diagram_id}"),
    ("knowledge", "list_ai_resources", "GET", "/api/v1/ai/resources"),
    ("knowledge", "get_ai_resource", "GET", "/api/v1/ai/resources/{resource_id}"),
    ("knowledge", "list_streams", "GET", "/api/v1/streams"),
    ("knowledge", "get_stream", "GET", "/api/v1/streams/{stream_id}"),
    (
        "knowledge",
        "list_stream_executions",
        "GET",
        "/api/v1/streams/{stream_id}/executions",
    ),
    ("knowledge", "list_flows", "GET", "/api/v1/flows"),
    ("knowledge", "list_flow_promotions", "GET", "/api/v1/flows/promotions"),
    ("operations", "list_oncall_rotations", "GET", "/api/v1/on-call/rotations"),
    (
        "operations",
        "get_current_oncall_org",
        "GET",
        "/api/v1/on-call/rotations/current/organization/{org_id}",
    ),
    (
        "operations",
        "get_current_oncall_service",
        "GET",
        "/api/v1/on-call/rotations/current/service/{service_id}",
    ),
    ("operations", "list_ipam_prefixes", "GET", "/api/v1/ipam/prefixes"),
    ("operations", "list_ipam_addresses", "GET", "/api/v1/ipam/addresses"),
    ("operations", "list_ipam_vlans", "GET", "/api/v1/ipam/vlans"),
    ("operations", "list_discovery_jobs", "GET", "/api/v1/discovery/jobs"),
    ("operations", "get_discovery_history", "GET", "/api/v1/discovery/history"),
    ("operations", "list_sync_configs", "GET", "/api/v1/sync/configs"),
    ("operations", "get_sync_status", "GET", "/api/v1/sync/status"),
    ("operations", "list_sync_conflicts", "GET", "/api/v1/sync/conflicts"),
    ("operations", "list_webhooks", "GET", "/api/v1/webhooks"),
    ("operations", "list_inbound_webhooks", "GET", "/api/v1/webhooks/inbound"),
    ("operations", "list_identities", "GET", "/api/v1/identities"),
    ("operations", "get_identity", "GET", "/api/v1/identities/{identity_id}"),
    ("operations", "list_identity_groups", "GET", "/api/v1/identities/groups"),
    ("operations", "list_backups", "GET", "/api/v1/backup"),
    ("operations", "get_backup_stats", "GET", "/api/v1/backup/stats"),
    ("operations", "list_system_logs", "GET", "/api/v1/logs"),
    (
        "relationships",
        "get_entity_relationships",
        "GET",
        "/api/v1/entities/{entity_id}/dependencies",
    ),
    ("relationships", "search_relationships", "GET", "/api/v1/dependencies"),
    ("relationships", "create_relationship", "POST", "/api/v1/dependencies"),
    (
        "relationships",
        "delete_relationship",
        "DELETE",
        "/api/v1/dependencies/{dependency_id}",
    ),
    ("relationships", "get_graph", "GET", "/api/v1/graph"),
    ("relationships", "analyze_graph", "GET", "/api/v1/graph/analyze"),
    ("relationships", "find_graph_path", "GET", "/api/v1/graph/path"),
    ("resources", "universal_search", "GET", "/api/v1/search"),
    ("resources", "search_graph", "POST", "/api/v1/search/graph"),
    ("resources", "resolve_ref", "GET", "/api/v1/refs/resolve"),
    ("resources", "get_ref_backlinks", "GET", "/api/v1/refs/backlinks"),
    ("resources", "lookup_village_id", "GET", "/id/{village_id}"),
    ("resources", "resolve_village_id", "GET", "/api/v1/id/{village_id}"),
    ("resources", "list_audit_logs", "GET", "/api/v1/audit-enterprise/logs"),
    ("security", "list_vulnerabilities", "GET", "/api/v1/vulnerabilities"),
    (
        "security",
        "get_vulnerability",
        "GET",
        "/api/v1/vulnerabilities/{vulnerability_id}",
    ),
    (
        "security",
        "get_vulnerability_dashboard",
        "GET",
        "/api/v1/vulnerabilities/dashboard",
    ),
    ("security", "list_sbom_components", "GET", "/api/v1/sbom/components"),
    ("security", "list_sbom_scans", "GET", "/api/v1/sbom/scans"),
    ("security", "search_services", "GET", "/api/v1/services"),
    ("security", "get_service", "GET", "/api/v1/services/{service_id}"),
    ("security", "get_service_sbom", "GET", "/api/v1/services/{service_id}/sbom"),
    ("security", "list_software", "GET", "/api/v1/software"),
    ("security", "get_software", "GET", "/api/v1/software/{software_id}"),
    ("security", "list_license_policies", "GET", "/api/v1/license-policies"),
    ("security", "list_certificates", "GET", "/api/v1/certificates"),
    ("security", "list_secrets_metadata", "GET", "/api/v1/secrets"),
    ("security", "list_kms_keys", "GET", "/api/v1/keys"),
    ("security", "list_access_reviews", "GET", "/api/v1/access-reviews"),
    ("security", "get_access_review", "GET", "/api/v1/access-reviews/{review_id}"),
    (
        "security",
        "list_pending_group_requests",
        "GET",
        "/api/v1/group-membership/requests/pending",
    ),
    (
        "security",
        "get_entity_resource_roles",
        "GET",
        "/api/v1/resource-roles/entities/{entity_id}/roles",
    ),
    ("work_management", "list_issues", "GET", "/api/v1/issues"),
    ("work_management", "get_issue", "GET", "/api/v1/issues/{issue_id}"),
    (
        "work_management",
        "get_issue_comments",
        "GET",
        "/api/v1/issues/{issue_id}/comments",
    ),
    ("work_management", "list_projects", "GET", "/api/v1/projects"),
    ("work_management", "get_project", "GET", "/api/v1/projects/{project_id}"),
    ("work_management", "list_milestones", "GET", "/api/v1/milestones"),
    (
        "work_management",
        "get_milestone_issues",
        "GET",
        "/api/v1/milestones/{milestone_id}/issues",
    ),
    ("work_management", "list_labels", "GET", "/api/v1/labels"),
    (
        "work_management",
        "get_entity_metadata",
        "GET",
        "/api/v1/metadata/entities/{entity_id}/metadata",
    ),
    ("work_management", "list_helpdesk_tickets", "GET", "/api/v1/helpdesk/tickets"),
    (
        "work_management",
        "get_helpdesk_ticket",
        "GET",
        "/api/v1/helpdesk/tickets/{ticket_id}",
    ),
    (
        "work_management",
        "get_helpdesk_stats",
        "GET",
        "/api/v1/helpdesk/dashboard/stats",
    ),
    ("work_management", "list_helpdesk_teams", "GET", "/api/v1/helpdesk/teams"),
]

# Arguments that are deliberately superseded or normalised rather than forwarded verbatim.
NOT_FORWARDED_VERBATIM = {
    ("search_relationships", "source_entity_id"),
    ("search_relationships", "target_entity_id"),
    ("search_relationships", "relationship_type"),
    ("get_entity_relationships", "direction"),
}
MODULES = sorted({module for module, *_ in ROUTES})
ROUTE_IDS = [f"{module}.{name}" for module, name, *_ in ROUTES]


class _Upstream:
    """Records every request hub-api would receive and replies with canned JSON."""

    def __init__(self, status: int = 200, body: Any = None):
        """Store the reply to send."""
        self.status = status
        self.body = {"ok": True} if body is None else body
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Record the request and return the canned reply."""
        self.requests.append(request)
        return httpx.Response(self.status, json=self.body)

    def client(self) -> ElderClient:
        """Return a real ElderClient wired to this upstream."""
        return ElderClient(
            base_url="http://hub-api.test.invalid",
            access_token=TOKEN,
            transport=httpx.MockTransport(self),
        )


def _tool(module: str, name: str) -> Any:
    """Import and return a tool coroutine function."""
    return getattr(importlib.import_module(f"apps.mcp.tools.{module}"), name)


def _sentinels(func: Any) -> dict[str, Any]:
    """Build a distinct, type-appropriate value for every tool argument."""
    values: dict[str, Any] = {}
    for index, (name, param) in enumerate(inspect.signature(func).parameters.items()):
        if name == "client":
            continue
        annotation = str(param.annotation)
        if "bool" in annotation:
            values[name] = True
        elif "list" in annotation:
            values[name] = [f"l{index}x"]
        elif "dict" in annotation:
            values[name] = {f"k{index}": f"d{index}x"}
        elif "int" in annotation:
            values[name] = 9000 + index
        else:
            values[name] = f"v{index}x"
    return values


def _required(func: Any, values: dict[str, Any]) -> dict[str, Any]:
    """Return only the arguments the tool cannot be called without."""
    params = inspect.signature(func).parameters
    return {
        name: value
        for name, value in values.items()
        if params[name].default is inspect.Parameter.empty
    }


def _leaves(value: Any) -> list[str]:
    """Flatten a JSON-like structure into its stringified scalar leaves."""
    if isinstance(value, dict):
        return [leaf for item in value.values() for leaf in _leaves(item)]
    if isinstance(value, list):
        return [leaf for item in value for leaf in _leaves(item)]
    if isinstance(value, bool):
        return [str(value).lower()]
    return [str(value)]


def _sent_values(request: httpx.Request) -> list[str]:
    """Return every scalar value Elder would see in the query string and JSON body."""
    sent = [value for _key, value in parse_qsl(request.url.query.decode())]
    if request.content:
        sent.extend(_leaves(json.loads(request.content)))
    return sent


def test_route_table_covers_every_curated_tool() -> None:
    """Every public tool coroutine in the curated modules has a contract entry."""
    declared = {(module, name) for module, name, *_ in ROUTES}
    discovered = set()
    for module in MODULES:
        mod = importlib.import_module(f"apps.mcp.tools.{module}")
        for name, func in inspect.getmembers(mod, inspect.iscoroutinefunction):
            if func.__module__ == mod.__name__ and not name.startswith("_"):
                discovered.add((module, func.__name__))
    assert discovered == declared


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("module", "name", "method", "template"), ROUTES, ids=ROUTE_IDS
)
async def test_tool_forwards_every_argument_to_its_route(
    module: str, name: str, method: str, template: str
) -> None:
    """A fully-specified call hits the documented route with all arguments and the token."""
    func = _tool(module, name)
    values = _sentinels(func)
    upstream = _Upstream(body={"tool": name})

    async with upstream.client() as client:
        result = await func(client, **values)

    assert result == {"tool": name}
    (request,) = upstream.requests
    assert request.method == method
    assert request.url.path == template.format(**values)
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    sent = _sent_values(request)
    for arg, value in values.items():
        if f"{{{arg}}}" in template or (name, arg) in NOT_FORWARDED_VERBATIM:
            continue
        for leaf in _leaves(value):
            assert leaf in sent, f"{name}: argument {arg!r} was not forwarded upstream"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("module", "name", "method", "template"), ROUTES, ids=ROUTE_IDS
)
async def test_tool_omits_unset_optional_arguments(
    module: str, name: str, method: str, template: str
) -> None:
    """Optional arguments left unset never reach Elder as 'None' or empty filters."""
    func = _tool(module, name)
    values = _sentinels(func)
    full, minimal = _Upstream(), _Upstream()
    async with full.client() as client:
        await func(client, **values)
    async with minimal.client() as client:
        await func(client, **_required(func, values))

    (full_request,) = full.requests
    (request,) = minimal.requests
    assert request.method == method
    sent = dict(parse_qsl(request.url.query.decode(), keep_blank_values=True))
    assert "None" not in sent.values() and "" not in sent.values()
    assert set(sent) <= set(dict(parse_qsl(full_request.url.query.decode())))
    if request.content:
        assert None not in json.loads(request.content).values()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("module", "name"),
    [
        ("entities", "get_entity"),
        ("security", "list_secrets_metadata"),
        ("relationships", "delete_relationship"),
        ("work_management", "list_issues"),
        ("knowledge", "get_document"),
        ("operations", "list_identities"),
    ],
)
@pytest.mark.parametrize("status", [401, 403, 404, 429, 500])
async def test_upstream_errors_surface_as_api_errors(
    module: str, name: str, status: int
) -> None:
    """Auth, not-found, throttling and server errors raise with the upstream status."""
    func = _tool(module, name)
    upstream = _Upstream(status=status, body={"message": "upstream said no"})
    async with upstream.client() as client:
        with pytest.raises(ElderAPIError) as excinfo:
            await func(client, **_required(func, _sentinels(func)))
    assert excinfo.value.status_code == status
    assert excinfo.value.message == "upstream said no"
    assert TOKEN not in str(excinfo.value)


class TestEntityTools:
    """Entity-specific request shaping."""

    @pytest.mark.asyncio
    async def test_update_entity_merges_metadata_under_attributes(self) -> None:
        """Legacy ``metadata`` is merged into ``attributes``, with attributes winning."""
        upstream = _Upstream()
        async with upstream.client() as client:
            await update_entity(
                client,
                7,
                name="web",
                description="",
                attributes={"tier": "gold"},
                metadata={"tier": "bronze", "owner": "core"},
            )
        assert json.loads(upstream.requests[0].content) == {
            "name": "web",
            "description": "",
            "attributes": {"tier": "gold", "owner": "core"},
        }

    @pytest.mark.asyncio
    async def test_update_entity_without_changes_sends_empty_patch(self) -> None:
        """No fields supplied means an empty PATCH body, not nulls."""
        upstream = _Upstream()
        async with upstream.client() as client:
            await update_entity(client, 7, attributes={}, metadata="not-a-dict")
        assert json.loads(upstream.requests[0].content) == {}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("flag", "expected"), [(True, "true"), (False, "false")])
    async def test_compliance_flags_are_lowercase_booleans(
        self, flag: bool, expected: str
    ) -> None:
        """Compliance filters are sent as lowercase strings, including explicit False."""
        upstream = _Upstream()
        async with upstream.client() as client:
            await list_data_stores(client, contains_pii=flag, contains_pci=flag)
        query = dict(parse_qsl(upstream.requests[0].url.query.decode()))
        assert (query["contains_pii"], query["contains_pci"]) == (expected, expected)
        assert "contains_phi" not in query


class TestRelationshipTools:
    """Relationship-specific request shaping."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("direction", "expected"),
        [
            ("both", "all"),
            ("INBOUND", "incoming"),
            ("incoming", "incoming"),
            ("Outbound", "outgoing"),
            ("outgoing", "outgoing"),
            ("sideways", "all"),
        ],
    )
    async def test_direction_aliases_are_normalised(
        self, direction: str, expected: str
    ) -> None:
        """Friendly direction names map to the API's vocabulary; unknowns mean all."""
        upstream = _Upstream()
        async with upstream.client() as client:
            await relationships.get_entity_relationships(client, 3, direction)
        assert dict(parse_qsl(upstream.requests[0].url.query.decode())) == {
            "direction": expected
        }

    @pytest.mark.asyncio
    async def test_search_aliases_fill_in_only_when_primary_missing(self) -> None:
        """Legacy *_entity_id / relationship_type aliases are fallbacks, not overrides."""
        upstream = _Upstream()
        async with upstream.client() as client:
            await relationships.search_relationships(
                client, source_entity_id=1, target_entity_id=2, relationship_type="uses"
            )
            await relationships.search_relationships(
                client,
                source_id=0,
                source_entity_id=1,
                dependency_type="contains",
                relationship_type="uses",
            )
        first, second = (
            dict(parse_qsl(r.url.query.decode())) for r in upstream.requests
        )
        assert (first["source_id"], first["target_id"], first["dependency_type"]) == (
            "1",
            "2",
            "uses",
        )
        assert (second["source_id"], second["dependency_type"]) == ("0", "contains")
        assert "target_id" not in second

    @pytest.mark.asyncio
    async def test_create_relationship_defaults_to_entity_endpoints(self) -> None:
        """A minimal create posts entity-to-entity with no metadata key."""
        upstream = _Upstream()
        async with upstream.client() as client:
            await relationships.create_relationship(client, 1, 2, "depends_on")
        assert json.loads(upstream.requests[0].content) == {
            "source_type": "entity",
            "source_id": 1,
            "target_type": "entity",
            "target_id": 2,
            "dependency_type": "depends_on",
        }

    @pytest.mark.asyncio
    async def test_graph_path_uses_from_and_to(self) -> None:
        """Path search maps the entity ids onto the API's from/to parameters."""
        upstream = _Upstream()
        async with upstream.client() as client:
            await relationships.find_graph_path(client, 11, 22)
        assert dict(parse_qsl(upstream.requests[0].url.query.decode())) == {
            "from": "11",
            "to": "22",
        }


class TestKnowledgeAndResourceTools:
    """Stream executions and VillageID handling."""

    @pytest.mark.asyncio
    async def test_stream_executions_global_route(self) -> None:
        """Without a stream id the global executions route is used."""
        upstream = _Upstream()
        async with upstream.client() as client:
            await list_stream_executions(client, status="failed")
        request = upstream.requests[0]
        assert request.url.path == "/api/v1/streams/executions"
        assert dict(parse_qsl(request.url.query.decode())) == {"status": "failed"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("tool", "prefix"),
        [("lookup_village_id", "/id/"), ("resolve_village_id", "/api/v1/id/")],
    )
    async def test_village_id_is_trimmed_and_percent_encoded(
        self, tool: str, prefix: str
    ) -> None:
        """VillageIDs are stripped and percent-encoded into a single path segment."""
        upstream = _Upstream()
        async with upstream.client() as client:
            await getattr(resources, tool)(client, "  a1b2-c3d4 e5  ")
        assert upstream.requests[0].url.raw_path.decode() == f"{prefix}a1b2-c3d4%20e5"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "village_id",
        [
            "",
            "   ",
            "../admin",
            "a/b",
            "a\\b",
            "a?x=1",
            "a#frag",
            "a\nb",
            "%2e%2e%2fadmin",
            "%252e%252e%252fadmin",
            "a%00b",
        ],
    )
    @pytest.mark.parametrize("tool", ["lookup_village_id", "resolve_village_id"])
    async def test_path_traversal_village_ids_are_rejected_before_any_request(
        self, tool: str, village_id: str
    ) -> None:
        """Traversal, separators and encoded variants are refused without calling Elder."""
        upstream = _Upstream()
        async with upstream.client() as client:
            with pytest.raises(ValueError, match="village_id"):
                await getattr(resources, tool)(client, village_id)
        assert upstream.requests == []

    @pytest.mark.asyncio
    async def test_search_graph_minimal_body(self) -> None:
        """Graph search posts only the start entity and depth unless filters are given."""
        upstream = _Upstream()
        async with upstream.client() as client:
            await resources.search_graph(client, 5)
        assert json.loads(upstream.requests[0].content) == {
            "start_entity_id": 5,
            "max_depth": 2,
        }

    def test_audit_log_alias_and_exports(self) -> None:
        """The legacy audit-log name is the same tool, and exports resolve."""
        assert resources.get_audit_logs is resources.list_audit_logs
        for name in resources.__all__:
            assert callable(getattr(resources, name))
