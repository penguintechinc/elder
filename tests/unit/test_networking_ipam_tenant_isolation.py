"""Two-tenant isolation tests for the networking and IPAM routes.

Every read, list, create, update and delete under ``/api/v1/networking`` and
``/api/v1/ipam`` must resolve objects only inside the caller's verified
tenant: foreign ids answer 404 (never 403, so ids cannot be enumerated),
lists never contain foreign rows, creates refuse foreign references, and a
missing tenant claim answers 403.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from quart import current_app

OWN_TENANT = 1
NET = "/api/v1/networking"
IPAM = "/api/v1/ipam"
SCOPES = [
    "infrastructure:read",
    "infrastructure:write",
    "ipam:read",
    "ipam:write",
]


def _user(superuser: bool = False) -> MagicMock:
    """Return a stand-in authenticated user bound to the caller's tenant.

    IPAM update/delete sit behind ``resource_role_required``, which only a
    superuser passes on these paths; tenant scoping must still hold for it.
    """
    return MagicMock(id=1, is_superuser=superuser, tenant_id=OWN_TENANT)


def _auth(generate_token, tenant_id: int = OWN_TENANT) -> dict:
    """Build an Authorization header for ``tenant_id`` with networking scopes."""
    return {
        "Authorization": f"Bearer {generate_token(tenant_id=tenant_id, scopes=SCOPES)}"
    }


def _seed(db) -> dict:
    """Seed parallel networking and IPAM objects for two tenants.

    Also seeds legacy cross-tenant links (a connection and a mapping from an
    own network to foreign objects) that must never be surfaced.
    """
    tag = uuid4().hex[:10]
    now = datetime.now(UTC)
    foreign_tenant = db.tenants.insert(
        name=f"NetIso Foreign {tag}", slug=f"netiso-{tag}", is_active=True
    )
    ids: dict = {"tag": tag, "foreign_tenant": foreign_tenant}
    octet = int(tag[:4], 16) % 250
    for side, tenant in (("a", OWN_TENANT), ("b", foreign_tenant)):
        org = db.organizations.insert(
            name=f"netiso-{side}-{tag}-org",
            tenant_id=tenant,
            created_at=now,
            updated_at=now,
        )
        ids[f"org_{side}"] = org
        for n in (1, 2):
            ids[f"net_{side}{n}"] = db.networking_resources.insert(
                name=f"netiso-{side}{n}-{tag}",
                network_type="subnet",
                organization_id=org,
                attributes={},
                status_metadata={},
                tags=[],
            )
        ids[f"ent_{side}"] = db.entities.insert(
            name=f"netiso-ent-{side}-{tag}",
            type="compute",
            organization_id=org,
            created_at=now,
            updated_at=now,
        )
        ids[f"conn_{side}"] = db.network_topology.insert(
            source_network_id=ids[f"net_{side}1"],
            target_network_id=ids[f"net_{side}2"],
            connection_type="peering",
        )
        ids[f"map_{side}"] = db.network_entity_mappings.insert(
            network_id=ids[f"net_{side}1"],
            entity_id=ids[f"ent_{side}"],
            relationship_type="hosts",
        )
        second = 1 if side == "a" else 2
        ids[f"vlan_{side}"] = db.ipam_vlans.insert(
            vid=100 + second,
            name=f"netiso-vlan-{side}-{tag}",
            organization_id=org,
            tenant_id=tenant,
            status="active",
            created_at=now,
            updated_at=now,
        )
        ids[f"prefix_{side}"] = db.ipam_prefixes.insert(
            prefix=f"10.{octet}.{second}.0/24",
            description=f"netiso-prefix-{side}-{tag}",
            organization_id=org,
            tenant_id=tenant,
            status="active",
            is_pool=False,
            created_at=now,
            updated_at=now,
        )
        # A childless prefix, so an own delete is not blocked by addresses.
        ids[f"prefix_{side}_leaf"] = db.ipam_prefixes.insert(
            prefix=f"10.{octet}.{second + 10}.0/24",
            description=f"netiso-leaf-{side}-{tag}",
            organization_id=org,
            tenant_id=tenant,
            status="active",
            is_pool=False,
            created_at=now,
            updated_at=now,
        )
        ids[f"addr_{side}"] = db.ipam_addresses.insert(
            address=f"10.{octet}.{second}.10/32",
            description=f"netiso-addr-{side}-{tag}",
            prefix_id=ids[f"prefix_{side}"],
            tenant_id=tenant,
            status="active",
            created_at=now,
            updated_at=now,
        )
    ids["octet"] = octet
    # Legacy cross-tenant links that predate scoping.
    ids["conn_cross"] = db.network_topology.insert(
        source_network_id=ids["net_a1"],
        target_network_id=ids["net_b1"],
        connection_type="peering",
    )
    ids["map_cross"] = db.network_entity_mappings.insert(
        network_id=ids["net_a1"],
        entity_id=ids["ent_b"],
        relationship_type="hosts",
    )
    db.commit()
    return ids


@pytest_asyncio.fixture
async def seeded(app):
    """Seed the two-tenant fixture inside an app context."""
    async with app.app_context():
        return _seed(current_app.db)


async def _row(app, table: str, row_id: int):
    """Fetch a row directly from the DB, bypassing the API."""
    async with app.app_context():
        db = current_app.db
        return db(getattr(db, table).id == row_id).select().first()


def _items(data) -> list:
    """Return the item list from any networking/IPAM list response shape."""
    for key in ("networks", "connections", "mappings", "items"):
        if isinstance(data, dict) and key in data:
            return data[key]
    raise AssertionError(f"no item list in response keys {list(data)}")


# Single-object reads: (path template, id key) for each tenant-owned object.
READS = [
    (NET + "/networks/{}", "net_{}1"),
    (NET + "/topology/connections/{}", "conn_{}"),
    (NET + "/mappings/{}", "map_{}"),
    (IPAM + "/prefixes/{}", "prefix_{}"),
    (IPAM + "/prefixes/{}/tree", "prefix_{}"),
    (IPAM + "/addresses/{}", "addr_{}"),
    (IPAM + "/vlans/{}", "vlan_{}"),
]
READ_IDS = [r[0].split("/api/v1/")[1] for r in READS]


class TestReads:
    """GET by id returns own objects and 404s foreign ones."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("path", "key"), READS, ids=READ_IDS)
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_foreign_id_is_not_found(
        self, mock_user, path, key, async_client, generate_token, seeded
    ):
        """Another tenant's object id answers 404 and leaks nothing."""
        mock_user.return_value = _user()
        resp = await async_client.get(
            path.format(seeded[key.format("b")]), headers=_auth(generate_token)
        )
        body = (await resp.get_data()).decode()
        assert resp.status_code == 404, body[:200]
        assert "netiso-b" not in body

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("path", "key"), READS, ids=READ_IDS)
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_own_id_is_returned(
        self, mock_user, path, key, async_client, generate_token, seeded
    ):
        """The caller's own object is still readable."""
        mock_user.return_value = _user()
        resp = await async_client.get(
            path.format(seeded[key.format("a")]), headers=_auth(generate_token)
        )
        body = (await resp.get_data()).decode()
        assert resp.status_code == 200, body[:200]
        assert (await resp.get_json())["id"] == seeded[key.format("a")]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("path", "key"),
        [
            (NET + "/topology/connections/{}", "conn_cross"),
            (NET + "/mappings/{}", "map_cross"),
        ],
        ids=["connection", "mapping"],
    )
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_cross_tenant_link_is_not_found(
        self, mock_user, path, key, async_client, generate_token, seeded
    ):
        """A link with one endpoint in another tenant is not readable."""
        mock_user.return_value = _user()
        resp = await async_client.get(
            path.format(seeded[key]), headers=_auth(generate_token)
        )
        assert resp.status_code == 404


# Lists: (url template, own key or None, foreign key). ``{org_b}``/``{net_b1}``
# filters point at the foreign tenant and must yield nothing foreign.
LISTS = [
    (NET + "/networks?limit=200", "net_a1", "net_b1"),
    (NET + "/networks?organization_id={org_b}", None, "net_b1"),
    (NET + "/topology/connections", "conn_a", "conn_b"),
    (NET + "/topology/connections", None, "conn_cross"),
    (NET + "/topology/connections?network_id={net_b1}", None, "conn_b"),
    (NET + "/mappings", "map_a", "map_b"),
    (NET + "/mappings", None, "map_cross"),
    (NET + "/mappings?network_id={net_b1}", None, "map_b"),
    (IPAM + "/prefixes?per_page=100&search=netiso-", "prefix_a", "prefix_b"),
    (IPAM + "/prefixes?organization_id={org_b}", None, "prefix_b"),
    (IPAM + "/addresses?per_page=100&search=netiso-", "addr_a", "addr_b"),
    (IPAM + "/addresses?prefix_id={prefix_b}", None, "addr_b"),
    (IPAM + "/vlans?per_page=100&search=netiso-", "vlan_a", "vlan_b"),
    (IPAM + "/vlans?organization_id={org_b}", None, "vlan_b"),
]


class TestLists:
    """List endpoints never include another tenant's rows."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("url", "own", "foreign"), LISTS)
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_list_excludes_foreign_rows(
        self, mock_user, url, own, foreign, async_client, generate_token, seeded
    ):
        """Foreign ids are absent; own ids are present where unfiltered."""
        mock_user.return_value = _user()
        resp = await async_client.get(
            url.format(**seeded), headers=_auth(generate_token)
        )
        body = (await resp.get_data()).decode()
        assert resp.status_code == 200, body[:200]
        ids = {item["id"] for item in _items(await resp.get_json())}
        assert seeded[foreign] not in ids
        assert "netiso-b" not in body
        if own:
            assert seeded[own] in ids


def _net_body(seeded, **extra) -> dict:
    """Return a minimal valid network create body in the caller's org."""
    body = {
        "name": f"netiso-new-{uuid4().hex[:8]}",
        "network_type": "subnet",
        "organization_id": seeded["org_a"],
    }
    body.update(extra)
    return body


def _prefix_body(seeded, **extra) -> dict:
    """Return a minimal valid prefix create body in the caller's org."""
    body = {
        "prefix": f"10.{seeded['octet']}.{50 + int(uuid4().hex[:2], 16) % 150}.0/24",
        "organization_id": seeded["org_a"],
    }
    body.update(extra)
    return body


# Creates that reference a foreign object: (path, body builder, table to
# check stays unchanged).
FOREIGN_CREATES = [
    (
        "networks-foreign-org",
        NET + "/networks",
        lambda s: _net_body(s, organization_id=s["org_b"]),
        "networking_resources",
    ),
    (
        "networks-foreign-parent",
        NET + "/networks",
        lambda s: _net_body(s, parent_id=s["net_b1"]),
        "networking_resources",
    ),
    (
        "connection-foreign-target",
        NET + "/topology/connections",
        lambda s: {
            "source_network_id": s["net_a1"],
            "target_network_id": s["net_b1"],
            "connection_type": "peering",
        },
        "network_topology",
    ),
    (
        "connection-foreign-both",
        NET + "/topology/connections",
        lambda s: {
            "source_network_id": s["net_b1"],
            "target_network_id": s["net_b2"],
            "connection_type": "peering",
        },
        "network_topology",
    ),
    (
        "mapping-foreign-entity",
        NET + "/mappings",
        lambda s: {
            "network_id": s["net_a1"],
            "entity_id": s["ent_b"],
            "relationship_type": "hosts",
        },
        "network_entity_mappings",
    ),
    (
        "mapping-foreign-network",
        NET + "/mappings",
        lambda s: {
            "network_id": s["net_b1"],
            "entity_id": s["ent_a"],
            "relationship_type": "hosts",
        },
        "network_entity_mappings",
    ),
    (
        "prefix-foreign-org",
        IPAM + "/prefixes",
        lambda s: _prefix_body(s, organization_id=s["org_b"]),
        "ipam_prefixes",
    ),
    (
        "prefix-foreign-parent",
        IPAM + "/prefixes",
        lambda s: _prefix_body(s, parent_id=s["prefix_b"]),
        "ipam_prefixes",
    ),
    (
        "prefix-foreign-vlan",
        IPAM + "/prefixes",
        lambda s: _prefix_body(s, vlan_id=s["vlan_b"]),
        "ipam_prefixes",
    ),
    (
        "address-foreign-prefix",
        IPAM + "/addresses",
        lambda s: {"address": f"10.{s['octet']}.2.99/32", "prefix_id": s["prefix_b"]},
        "ipam_addresses",
    ),
    (
        "vlan-foreign-org",
        IPAM + "/vlans",
        lambda s: {"vid": 300, "name": "netiso-x", "organization_id": s["org_b"]},
        "ipam_vlans",
    ),
]

OWN_CREATES = [
    ("network", NET + "/networks", lambda s: _net_body(s)),
    (
        "connection",
        NET + "/topology/connections",
        lambda s: {
            "source_network_id": s["net_a2"],
            "target_network_id": s["net_a1"],
            "connection_type": "peering",
        },
    ),
    (
        "mapping",
        NET + "/mappings",
        lambda s: {
            "network_id": s["net_a2"],
            "entity_id": s["ent_a"],
            "relationship_type": "hosts",
        },
    ),
    (
        "prefix",
        IPAM + "/prefixes",
        lambda s: _prefix_body(s, parent_id=s["prefix_a"], vlan_id=s["vlan_a"]),
    ),
    (
        "address",
        IPAM + "/addresses",
        lambda s: {"address": f"10.{s['octet']}.1.77/32", "prefix_id": s["prefix_a"]},
    ),
    (
        "vlan",
        IPAM + "/vlans",
        lambda s: {"vid": 400, "name": "netiso-own", "organization_id": s["org_a"]},
    ),
]


async def _count(app, table: str) -> int:
    """Count rows in ``table``."""
    async with app.app_context():
        db = current_app.db
        return db(getattr(db, table).id > 0).count()


class TestCreates:
    """Creates refuse references to another tenant's objects."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("path", "build", "table"),
        [c[1:] for c in FOREIGN_CREATES],
        ids=[c[0] for c in FOREIGN_CREATES],
    )
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_with_foreign_reference_is_not_found(
        self, mock_user, path, build, table, async_client, generate_token, seeded, app
    ):
        """A foreign organization/network/entity/prefix/VLAN reference answers 404 and writes nothing."""
        mock_user.return_value = _user()
        before = await _count(app, table)
        resp = await async_client.post(
            path, json=build(seeded), headers=_auth(generate_token)
        )
        body = (await resp.get_data()).decode()
        assert resp.status_code == 404, body[:200]
        assert await _count(app, table) == before

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("path", "build"), [c[1:] for c in OWN_CREATES], ids=[c[0] for c in OWN_CREATES]
    )
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_in_own_tenant_succeeds(
        self, mock_user, path, build, async_client, generate_token, seeded
    ):
        """Creating with only own references still works."""
        mock_user.return_value = _user()
        resp = await async_client.post(
            path, json=build(seeded), headers=_auth(generate_token)
        )
        body = (await resp.get_data()).decode()
        assert resp.status_code == 201, body[:200]


# Updates: (id, method, path template, body builder, table, row key).
UPDATES = [
    (
        "network-foreign",
        "PATCH",
        NET + "/networks/{}",
        "net_b1",
        lambda s: {"name": "pwned"},
        "networking_resources",
        "name",
    ),
    (
        "prefix-foreign",
        "PUT",
        IPAM + "/prefixes/{}",
        "prefix_b",
        lambda s: {"description": "pwned"},
        "ipam_prefixes",
        "description",
    ),
    (
        "address-foreign",
        "PUT",
        IPAM + "/addresses/{}",
        "addr_b",
        lambda s: {"description": "pwned"},
        "ipam_addresses",
        "description",
    ),
    (
        "vlan-foreign",
        "PUT",
        IPAM + "/vlans/{}",
        "vlan_b",
        lambda s: {"name": "pwned"},
        "ipam_vlans",
        "name",
    ),
    (
        "prefix-to-foreign-org",
        "PUT",
        IPAM + "/prefixes/{}",
        "prefix_a",
        lambda s: {"organization_id": s["org_b"]},
        "ipam_prefixes",
        "organization_id",
    ),
    (
        "prefix-to-foreign-parent",
        "PUT",
        IPAM + "/prefixes/{}",
        "prefix_a",
        lambda s: {"parent_id": s["prefix_b"]},
        "ipam_prefixes",
        "parent_id",
    ),
    (
        "prefix-to-foreign-vlan",
        "PUT",
        IPAM + "/prefixes/{}",
        "prefix_a",
        lambda s: {"vlan_id": s["vlan_b"]},
        "ipam_prefixes",
        "vlan_id",
    ),
    (
        "address-to-foreign-prefix",
        "PUT",
        IPAM + "/addresses/{}",
        "addr_a",
        lambda s: {"prefix_id": s["prefix_b"]},
        "ipam_addresses",
        "prefix_id",
    ),
    (
        "vlan-to-foreign-org",
        "PUT",
        IPAM + "/vlans/{}",
        "vlan_a",
        lambda s: {"organization_id": s["org_b"]},
        "ipam_vlans",
        "organization_id",
    ),
]


class TestUpdates:
    """Updates cannot touch foreign rows or re-point own rows at foreign ones."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "path", "key", "build", "table", "field"),
        [u[1:] for u in UPDATES],
        ids=[u[0] for u in UPDATES],
    )
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_update_is_refused(
        self,
        mock_user,
        method,
        path,
        key,
        build,
        table,
        field,
        async_client,
        generate_token,
        seeded,
        app,
    ):
        """The request answers 404 and the stored row is unchanged."""
        mock_user.return_value = _user(superuser=True)
        before = (await _row(app, table, seeded[key]))[field]
        resp = await async_client.open(
            path.format(seeded[key]),
            method=method,
            json=build(seeded),
            headers=_auth(generate_token),
        )
        body = (await resp.get_data()).decode()
        assert resp.status_code == 404, body[:200]
        assert (await _row(app, table, seeded[key]))[field] == before

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "path", "key", "body"),
        [
            (
                "PATCH",
                NET + "/networks/{}",
                "net_a1",
                {"description": "ok", "mtu": 9000},
            ),
            ("PUT", IPAM + "/prefixes/{}", "prefix_a", {"description": "ok"}),
            ("PUT", IPAM + "/addresses/{}", "addr_a", {"description": "ok"}),
            ("PUT", IPAM + "/vlans/{}", "vlan_a", {"description": "ok"}),
        ],
        ids=["network", "prefix", "address", "vlan"],
    )
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_update_own_succeeds(
        self, mock_user, method, path, key, body, async_client, generate_token, seeded
    ):
        """Own rows remain updatable."""
        mock_user.return_value = _user(superuser=True)
        resp = await async_client.open(
            path.format(seeded[key]),
            method=method,
            json=body,
            headers=_auth(generate_token),
        )
        text = (await resp.get_data()).decode()
        assert resp.status_code == 200, text[:200]
        assert (await resp.get_json())["description"] == "ok"


DELETES = [
    ("network", NET + "/networks/{}", "net_{}1", "networking_resources"),
    ("network-hard", NET + "/networks/{}?hard=true", "net_{}2", "networking_resources"),
    ("connection", NET + "/topology/connections/{}", "conn_{}", "network_topology"),
    ("mapping", NET + "/mappings/{}", "map_{}", "network_entity_mappings"),
    ("prefix", IPAM + "/prefixes/{}", "prefix_{}_leaf", "ipam_prefixes"),
    ("address", IPAM + "/addresses/{}", "addr_{}", "ipam_addresses"),
    ("vlan", IPAM + "/vlans/{}", "vlan_{}", "ipam_vlans"),
]


class TestDeletes:
    """Deletes only reach the caller's own rows."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("path", "key", "table"), [d[1:] for d in DELETES], ids=[d[0] for d in DELETES]
    )
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_delete_foreign_is_not_found(
        self, mock_user, path, key, table, async_client, generate_token, seeded, app
    ):
        """Deleting another tenant's row answers 404 and the row survives intact."""
        mock_user.return_value = _user(superuser=True)
        row_id = seeded[key.format("b")]
        before = dict(await _row(app, table, row_id))
        resp = await async_client.delete(
            path.format(row_id), headers=_auth(generate_token)
        )
        assert resp.status_code == 404
        after = await _row(app, table, row_id)
        assert after is not None
        assert dict(after) == before

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("path", "key", "table"), [d[1:] for d in DELETES], ids=[d[0] for d in DELETES]
    )
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_delete_own_succeeds(
        self, mock_user, path, key, table, async_client, generate_token, seeded
    ):
        """Own rows remain deletable."""
        mock_user.return_value = _user(superuser=True)
        resp = await async_client.delete(
            path.format(seeded[key.format("a")]), headers=_auth(generate_token)
        )
        text = (await resp.get_data()).decode()
        assert resp.status_code in (200, 204), text[:200]


class TestTopologyGraph:
    """The topology graph with entities is scoped and does not error."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_include_entities_returns_only_own_entities(
        self, mock_user, async_client, generate_token, seeded
    ):
        """include_entities=true answers 200 and drops the cross-tenant mapping."""
        mock_user.return_value = _user()
        resp = await async_client.get(
            f"{NET}/topology/graph?organization_id={seeded['org_a']}&include_entities=true",
            headers=_auth(generate_token),
        )
        body = (await resp.get_data()).decode()
        assert resp.status_code == 200, body[:200]
        data = await resp.get_json()
        assert {n["id"] for n in data["entity_nodes"]} == {f"entity_{seeded['ent_a']}"}
        assert {e["target"] for e in data["entity_edges"]} == {
            f"entity_{seeded['ent_a']}"
        }
        assert {n["id"] for n in data["nodes"]} == {
            f"network_{seeded['net_a1']}",
            f"network_{seeded['net_a2']}",
        }
        assert "netiso-b" not in body
        assert f"connection_{seeded['conn_cross']}" not in body

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_foreign_organization_is_not_found(
        self, mock_user, async_client, generate_token, seeded
    ):
        """Another tenant's organization answers 404."""
        mock_user.return_value = _user()
        resp = await async_client.get(
            f"{NET}/topology/graph?organization_id={seeded['org_b']}&include_entities=true",
            headers=_auth(generate_token),
        )
        assert resp.status_code == 404


MISSING_TENANT = [
    ("GET", NET + "/networks"),
    ("GET", NET + "/networks/{net_a1}"),
    ("GET", NET + "/topology/connections"),
    ("GET", NET + "/mappings"),
    ("DELETE", NET + "/mappings/{map_a}"),
    ("GET", IPAM + "/prefixes"),
    ("GET", IPAM + "/prefixes/{prefix_a}"),
    ("GET", IPAM + "/addresses"),
    ("GET", IPAM + "/vlans/{vlan_a}"),
]


class TestMissingTenant:
    """A token without a tenant claim is refused outright."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("method", "url"), MISSING_TENANT)
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_zero_tenant_is_forbidden(
        self, mock_user, method, url, async_client, generate_token, seeded
    ):
        """tenant=0 answers 403 before any lookup."""
        mock_user.return_value = _user(superuser=True)
        resp = await async_client.open(
            url.format(**seeded), method=method, headers=_auth(generate_token, 0)
        )
        assert resp.status_code == 403
