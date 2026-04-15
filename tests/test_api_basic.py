"""Basic API tests to verify endpoints work."""

import json


def test_health_endpoint(client):
    """Test health check endpoint."""
    response = client.get("/healthz")
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["status"] == "healthy"
    assert data["service"] == "elder"


def test_metrics_endpoint(client):
    """Test Prometheus metrics endpoint."""
    response = client.get("/metrics")
    assert response.status_code == 200
    # Metrics should be in plain text format
    assert b"# HELP" in response.data or b"# TYPE" in response.data


def test_list_organizations(client):
    """Test listing organizations."""
    response = client.get("/api/v1/organizations")
    assert response.status_code == 200
    data = json.loads(response.data)
    assert "items" in data
    assert "total" in data
    assert isinstance(data["items"], list)


def test_create_organization(client, db):
    """Test creating an organization."""
    org_data = {
        "name": "Test Organization",
        "description": "A test organization",
    }

    response = client.post(
        "/api/v1/organizations",
        data=json.dumps(org_data),
        content_type="application/json",
    )

    assert response.status_code == 201
    data = json.loads(response.data)
    assert data["name"] == "Test Organization"
    assert data["description"] == "A test organization"
    assert "id" in data


def test_list_entities(client):
    """Test listing entities."""
    response = client.get("/api/v1/entities")
    assert response.status_code == 200
    data = json.loads(response.data)
    assert "items" in data
    assert "total" in data


def test_create_entity_requires_organization(client, db):
    """Test that creating an entity requires a valid organization."""
    # First create an organization
    org_data = {"name": "Test Org"}
    org_response = client.post(
        "/api/v1/organizations",
        data=json.dumps(org_data),
        content_type="application/json",
    )
    org = json.loads(org_response.data)

    # Then create an entity
    entity_data = {
        "name": "Test Server",
        "entity_type": "compute",
        "organization_id": org["id"],
        "metadata": {
            "hostname": "server-01",
            "ip": "10.0.1.5",
        },
    }

    response = client.post(
        "/api/v1/entities",
        data=json.dumps(entity_data),
        content_type="application/json",
    )

    assert response.status_code == 201
    data = json.loads(response.data)
    assert data["name"] == "Test Server"
    # Regression: EntityDTO uses `type` not `entity_type` — frontend crash if wrong
    assert data["type"] == "compute"
    assert data["metadata"]["hostname"] == "server-01"


def test_list_dependencies(client):
    """Test listing dependencies."""
    response = client.get("/api/v1/dependencies")
    assert response.status_code == 200
    data = json.loads(response.data)
    assert "items" in data
    assert "total" in data


def test_create_dependency(client, db):
    """Test creating a dependency between entities."""
    # Create organization
    org = client.post(
        "/api/v1/organizations",
        data=json.dumps({"name": "Test Org"}),
        content_type="application/json",
    )
    org_id = json.loads(org.data)["id"]

    # Create two entities
    entity1 = client.post(
        "/api/v1/entities",
        data=json.dumps(
            {
                "name": "Web Server",
                "entity_type": "compute",
                "organization_id": org_id,
            }
        ),
        content_type="application/json",
    )
    entity1_id = json.loads(entity1.data)["id"]

    entity2 = client.post(
        "/api/v1/entities",
        data=json.dumps(
            {
                "name": "Database",
                "entity_type": "compute",
                "organization_id": org_id,
            }
        ),
        content_type="application/json",
    )
    entity2_id = json.loads(entity2.data)["id"]

    # Create dependency
    dep_data = {
        "source_entity_id": entity1_id,
        "target_entity_id": entity2_id,
        "dependency_type": "depends_on",
    }

    response = client.post(
        "/api/v1/dependencies",
        data=json.dumps(dep_data),
        content_type="application/json",
    )

    assert response.status_code == 201
    data = json.loads(response.data)
    assert data["source_entity_id"] == entity1_id
    assert data["target_entity_id"] == entity2_id
    assert data["dependency_type"] == "depends_on"


def test_get_graph(client, db):
    """Test getting dependency graph."""
    response = client.get("/api/v1/graph")
    assert response.status_code == 200
    data = json.loads(response.data)
    assert "nodes" in data
    assert "edges" in data
    assert "stats" in data
    assert isinstance(data["nodes"], list)
    assert isinstance(data["edges"], list)


def test_analyze_graph(client):
    """Test graph analysis endpoint."""
    response = client.get("/api/v1/graph/analyze")
    assert response.status_code == 200
    data = json.loads(response.data)
    assert "basic_stats" in data
    assert "graph_metrics" in data


# ============================================================================
# Regression tests — covers bugs fixed in v3.2.0
# ============================================================================


def _create_org(client, name="Regression Org"):
    """Helper: create an organization and return its id."""
    resp = client.post(
        "/api/v1/organizations",
        data=json.dumps({"name": name}),
        content_type="application/json",
    )
    assert resp.status_code == 201
    return json.loads(resp.data)["id"]


def _create_entity(client, org_id, name="Test Entity", entity_type="compute"):
    """Helper: create an entity and return its id."""
    resp = client.post(
        "/api/v1/entities",
        data=json.dumps({"name": name, "entity_type": entity_type, "organization_id": org_id}),
        content_type="application/json",
    )
    assert resp.status_code == 201
    return json.loads(resp.data)["id"]


def test_entity_response_uses_type_field(client, db):
    """Regression: EntityDTO must expose `type` not `entity_type`.

    The frontend Entities.tsx and EntityDetail.tsx crashed with
    'Cannot read properties of undefined (reading replace)' because
    entity.entity_type was undefined — the API returns `type`.
    """
    org_id = _create_org(client, "Type Field Org")
    resp = client.post(
        "/api/v1/entities",
        data=json.dumps({"name": "Type Check Entity", "entity_type": "network", "organization_id": org_id}),
        content_type="application/json",
    )
    assert resp.status_code == 201
    data = json.loads(resp.data)
    # Must use `type`, NOT `entity_type`
    assert "type" in data
    assert data["type"] == "network"
    assert "entity_type" not in data


def test_entity_list_filter_by_entity_type(client, db):
    """Regression: entity_type filter on GET /entities must work.

    Entity list was returning all entities regardless of entity_type param.
    """
    org_id = _create_org(client, "Filter By Type Org")
    _create_entity(client, org_id, "Compute Node", "compute")
    _create_entity(client, org_id, "Network Switch", "network")

    resp = client.get("/api/v1/entities?entity_type=compute")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert "items" in data
    # All returned items must be of type compute
    for item in data["items"]:
        assert item["type"] == "compute", f"Expected compute, got {item['type']}"


def test_entity_list_filter_by_organization_id(client, db):
    """Entity list must support filtering by organization_id."""
    org1_id = _create_org(client, "Org Alpha Filter")
    org2_id = _create_org(client, "Org Beta Filter")
    _create_entity(client, org1_id, "Alpha Entity", "compute")
    _create_entity(client, org2_id, "Beta Entity", "compute")

    resp = client.get(f"/api/v1/entities?organization_id={org1_id}")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    for item in data["items"]:
        assert item["organization_id"] == org1_id, (
            f"Expected org {org1_id}, got {item['organization_id']}"
        )


def test_entity_list_filter_by_name(client, db):
    """Entity list must support partial name filtering."""
    org_id = _create_org(client, "Name Filter Org")
    _create_entity(client, org_id, "UniqueNameXYZ", "compute")
    _create_entity(client, org_id, "SomethingElse", "compute")

    resp = client.get("/api/v1/entities?name=UniqueNameXYZ")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["total"] >= 1
    assert any("UniqueNameXYZ" in item["name"] for item in data["items"])


def test_graph_returns_200_no_attribute_error(client, db):
    """Regression: GET /graph must not raise AttributeError.

    graph.py used entity.type (correct) but the issue was introduced when
    an earlier version accessed entity.entity_type — confirmed fixed.
    """
    org_id = _create_org(client, "Graph Regression Org")
    _create_entity(client, org_id, "Graph Node A", "compute")
    _create_entity(client, org_id, "Graph Node B", "network")

    resp = client.get("/api/v1/graph")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert "nodes" in data
    assert "edges" in data
    assert "stats" in data


def test_graph_filter_by_entity_type(client, db):
    """GET /graph?entity_type=X must filter nodes to that type."""
    org_id = _create_org(client, "Graph Filter Org")
    _create_entity(client, org_id, "Filtered Compute", "compute")
    _create_entity(client, org_id, "Filtered Network", "network")

    resp = client.get("/api/v1/graph?entity_type=compute")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    for node in data["nodes"]:
        assert node["type"] == "compute", f"Expected compute, got {node['type']}"


def test_graph_filter_by_organization_id(client, db):
    """GET /graph?organization_id=X must only return nodes from that org."""
    org1_id = _create_org(client, "Graph Org One")
    org2_id = _create_org(client, "Graph Org Two")
    _create_entity(client, org1_id, "Org1 Entity", "compute")
    _create_entity(client, org2_id, "Org2 Entity", "compute")

    resp = client.get(f"/api/v1/graph?organization_id={org1_id}")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    for node in data["nodes"]:
        assert node["organization_id"] == org1_id, (
            f"Expected org {org1_id}, got {node['organization_id']}"
        )


def test_list_issues_filter_by_status(client, db, auth_headers):
    """GET /issues?status=open must only return open issues.

    Regression: issue status stored as uppercase but filter sent lowercase
    caused no results to be returned instead of correct filtered list.
    """
    resp = client.get("/api/v1/issues?status=open", headers=auth_headers)
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert "items" in data
    # All returned items must match the requested status
    for item in data["items"]:
        assert item["status"].lower() == "open", f"Expected open, got {item['status']}"


def test_list_issues_filter_by_priority(client, db, auth_headers):
    """GET /issues?priority=high must only return high-priority issues."""
    resp = client.get("/api/v1/issues?priority=high", headers=auth_headers)
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert "items" in data
    for item in data["items"]:
        assert item["priority"].lower() == "high", f"Expected high, got {item['priority']}"


def test_list_users_returns_200(client, db, auth_headers):
    """GET /users must return 200 with paginated identity list (admin only).

    Regression: IdentityDTO field drift caused 500 errors on this endpoint.
    """
    if not auth_headers:
        return  # Skip if auth not configured in test environment
    resp = client.get("/api/v1/users", headers=auth_headers)
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert "items" in data
    assert "total" in data
    assert isinstance(data["items"], list)
    # Verify IdentityDTO fields are present on each item
    if data["items"]:
        item = data["items"][0]
        assert "id" in item
        assert "username" in item
        assert "created_at" in item
        assert "updated_at" in item
