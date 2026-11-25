"""Unit tests for cross-reference resolution in ``common/refs/registry.py``.

``resolve_by_village_id`` / ``resolve_ref`` run against the real Postgres with
two tenants so every scoping strategy (tenant column, string tenant, org
column, JSON-config org, village-id prefix) is proven to deny the other
tenant. The central ``village_ids`` lookup is exercised with small fake
engine/handle objects because that table is not part of the test schema.
"""

import secrets
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio

from apps.api.common.refs import registry as reg
from apps.api.utils.async_utils import run_in_threadpool


def _sfx():
    """Unique suffix for names."""
    return uuid4().hex[:10]


def _vid(tenant_id):
    """Build a well-formed village id embedding ``tenant_id``."""
    return f"{tenant_id:08x}-{secrets.randbits(63):016x}"


def _rtype(table):
    """Return the registered resolvable type for a table (must exist)."""
    resolvable = reg.get_type_by_table(table)
    assert resolvable is not None, f"{table} is not registered"
    return resolvable


@pytest_asyncio.fixture
async def world(app):
    """Two tenants, each with an organization carrying a village id."""
    db = app.db

    def _setup():
        now = datetime.now(UTC)
        out = {}
        for key in ("a", "b"):
            sfx = _sfx()
            tid = db.tenants.insert(
                name=f"cov-ref-{key}-{sfx}", slug=f"cov-ref-{key}-{sfx}", is_active=True
            )
            db(db.tenants.id == tid).update(village_id=_vid(tid))
            vid = _vid(tid)
            oid = db.organizations.insert(
                name=f"cov-org-{key}-{sfx}",
                tenant_id=tid,
                village_id=vid,
                created_at=now,
                updated_at=now,
            )
            out[key] = SimpleNamespace(
                tenant=tid,
                slug=f"cov-ref-{key}-{sfx}",
                org=oid,
                org_vid=vid,
                org_name=f"cov-org-{key}-{sfx}",
                tenant_vid=db.tenants[tid].village_id,
            )
        db.commit()
        return out

    return await run_in_threadpool(_setup)


class TestSmallHelpers:
    """Pure helper functions."""

    @pytest.mark.parametrize(
        "row, expected",
        [
            (SimpleNamespace(name="Primary"), "Primary"),
            (SimpleNamespace(name="  ", title="From title"), "From title"),
            (
                SimpleNamespace(name=None, username="", email="a@example.com"),
                "a@example.com",
            ),
            (SimpleNamespace(code=42), "42"),
            (SimpleNamespace(name=None), "99"),
        ],
    )
    def test_extract_title_fallbacks(self, row, expected):
        """Title falls back through display columns and finally to the id."""
        resolvable = reg.ResolvableType(type="t", module="m", table="x")
        assert reg._extract_title(row, resolvable, 99) == expected

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ({"a": 1}, {"a": 1}),
            ('{"a": 2}', {"a": 2}),
            ("[1, 2]", {}),
            ("{broken", {}),
            (None, {}),
            (7, {}),
        ],
    )
    def test_parse_json_config(self, raw, expected):
        """Config is coerced to a dict; anything else becomes an empty dict."""
        assert reg._parse_json_config(raw) == expected

    def test_vid_prefix_matching(self):
        """A village id only matches the tenant encoded in its prefix."""
        vid = _vid(42)
        assert reg._vid_matches_tenant_prefix(vid, 42) is True
        assert reg._vid_matches_tenant_prefix(vid, 43) is False
        assert reg._vid_matches_tenant_prefix("not-a-village-id", 42) is False

    @pytest.mark.parametrize(
        "exc, expected",
        [
            (RuntimeError('relation "village_ids" does not exist'), True),
            (RuntimeError("no such table: village_ids"), True),
            (RuntimeError("Table 'x.village_ids' doesn't exist"), True),
            (RuntimeError("village_ids: permission denied"), False),
            (RuntimeError('relation "entities" does not exist'), False),
            (type("UndefinedTable", (Exception,), {})("boom"), True),
        ],
    )
    def test_missing_table_error_detection(self, exc, expected):
        """Only a missing village_ids table counts as 'registry unavailable'."""
        assert reg._is_missing_table_error(exc) is expected

    def test_safe_accessors_swallow_lookup_errors(self):
        """Missing tables/columns yield None instead of raising."""

        class Boom:
            def __getattr__(self, name):
                raise KeyError(name)

        assert reg._get_table_safe(Boom(), "anything") is None
        assert reg._get_column_safe(Boom(), "anything") is None
        assert reg._get_tenant_org_ids(Boom(), 1) == set()
        assert reg._get_tenant_string_values(Boom(), 3) == ["3"]

    def test_type_lookup_by_table(self):
        """Registered tables map back to their type; unknown tables to None."""
        assert reg.get_type_by_table("organizations").type == "organization"
        assert reg.get_type_by_table(f"nope_{_sfx()}") is None


class _Conn:
    """Fake SQLAlchemy connection returning a canned first() row."""

    def __init__(self, hit=None, exc=None):
        self.hit, self.exc, self.params = hit, exc, None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, statement, params):
        """Record bind params and return a result whose first() is the canned hit."""
        if self.exc is not None:
            raise self.exc
        self.params = params
        return SimpleNamespace(first=lambda: self.hit)


class TestVillageIdsRegistryLookup:
    """_lookup_via_village_ids_registry result/availability contract."""

    def test_mock_db_is_registry_unavailable(self):
        """Test doubles never hit the registry table."""
        assert reg._lookup_via_village_ids_registry(MagicMock(), "v", 1) == (
            None,
            False,
        )

    @pytest.mark.parametrize("attr", ["engine", "_engine"])
    def test_engine_hit_is_tenant_scoped(self, attr):
        """A hit returns (table, id) and the query binds village id AND tenant."""
        conn = _Conn(hit=("organizations", "17"))
        db = SimpleNamespace(**{attr: SimpleNamespace(connect=lambda: conn)})
        if attr == "_engine":
            db.engine = None
        result = reg._lookup_via_village_ids_registry(db, "vid-1", 5)
        assert result == (("organizations", 17), True)
        assert conn.params == {"vid": "vid-1", "tid": 5}

    def test_engine_no_row_means_available_but_absent(self):
        """No tenant-matching row is reported as (None, True)."""
        db = SimpleNamespace(engine=SimpleNamespace(connect=lambda: _Conn(hit=None)))
        assert reg._lookup_via_village_ids_registry(db, "vid-1", 5) == (None, True)

    def test_engine_missing_table_falls_back(self):
        """A missing village_ids table reports the registry as unavailable."""
        exc = RuntimeError('relation "village_ids" does not exist')
        db = SimpleNamespace(engine=SimpleNamespace(connect=lambda: _Conn(exc=exc)))
        assert reg._lookup_via_village_ids_registry(db, "vid-1", 5) == (None, False)

    def test_engine_other_errors_propagate(self):
        """Unexpected DB errors are not swallowed (fail loud)."""
        exc = RuntimeError("connection refused")
        db = SimpleNamespace(engine=SimpleNamespace(connect=lambda: _Conn(exc=exc)))
        with pytest.raises(RuntimeError, match="connection refused"):
            reg._lookup_via_village_ids_registry(db, "vid-1", 5)

    def _pydal_handle(self, row, tables=("village_ids",), with_column=True):
        """Build a non-SQLAlchemy handle exposing a village_ids table."""

        class Cond:
            def __and__(self, other):
                return self

        class Col:
            def __eq__(self, other):
                return Cond()

        class Handle:
            engine = None
            _engine = None

            def __call__(self, query):
                return SimpleNamespace(
                    select=lambda *cols: SimpleNamespace(first=lambda: row)
                )

        handle = Handle()
        handle.tables = list(tables)
        table = SimpleNamespace(tenant_id=1, table_name="t", record_id="r")
        if with_column:
            table.village_id = Col()
        handle.village_ids = table
        return handle

    def test_pydal_handle_hit_and_miss(self):
        """A plain DAL handle with a village_ids table is queried directly."""
        hit = SimpleNamespace(table_name="tenants", record_id="3")
        assert reg._lookup_via_village_ids_registry(
            self._pydal_handle(hit), "v", 1
        ) == (
            ("tenants", 3),
            True,
        )
        assert reg._lookup_via_village_ids_registry(
            self._pydal_handle(None), "v", 1
        ) == (
            None,
            True,
        )

    def test_pydal_handle_without_registry_table(self):
        """No village_ids table (or column) means the registry is unavailable."""
        no_table = self._pydal_handle(None, tables=())
        no_column = self._pydal_handle(None, with_column=False)
        assert reg._lookup_via_village_ids_registry(no_table, "v", 1) == (None, False)
        assert reg._lookup_via_village_ids_registry(no_column, "v", 1) == (None, False)


class TestResolveByVillageId:
    """resolve_by_village_id against the real database (fallback scan path)."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "village_id, tenant",
        [
            (None, 1),
            ("", 1),
            (12345, 1),
            ("00000001-0000000000000001", None),
            ("00000001-0000000000000001", True),
            ("00000001-0000000000000001", "not-a-tenant"),
        ],
    )
    async def test_invalid_input_fails_closed(self, app, village_id, tenant):
        """Bad village ids or a missing/invalid tenant resolve to None without querying."""
        db = MagicMock()
        assert reg.resolve_by_village_id(db, village_id, tenant) is None
        db.assert_not_called()

    @pytest.mark.asyncio
    async def test_tenant_column_isolation(self, app, world):
        """An organization resolves for its own tenant and never for another."""
        a, b = world["a"], world["b"]
        async with app.app_context():
            own = reg.resolve_by_village_id(app.db, a.org_vid, a.tenant)
            cross = reg.resolve_by_village_id(app.db, a.org_vid, b.tenant)
            as_str = reg.resolve_by_village_id(app.db, a.org_vid, str(a.tenant))
        assert own == {
            "type": "organization",
            "id": a.org,
            "table_name": "organizations",
            "title": a.org_name,
            "module": own["module"],
        }
        assert cross is None
        assert as_str["id"] == a.org

    @pytest.mark.asyncio
    async def test_tenant_row_only_resolves_for_itself(self, app, world):
        """A tenant's own village id resolves only within that tenant."""
        a, b = world["a"], world["b"]
        async with app.app_context():
            own = reg.resolve_by_village_id(app.db, a.tenant_vid, a.tenant)
            cross = reg.resolve_by_village_id(app.db, a.tenant_vid, b.tenant)
        assert (own["type"], own["id"]) == ("tenant", a.tenant)
        assert cross is None

    @pytest.mark.asyncio
    async def test_unknown_village_id(self, app, world):
        """A well-formed but unused village id resolves to None."""
        async with app.app_context():
            assert (
                reg.resolve_by_village_id(
                    app.db, _vid(world["a"].tenant), world["a"].tenant
                )
                is None
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tenant_value", ["id", "slug"])
    async def test_string_tenant_table(self, app, world, tenant_value):
        """geo_regions (string tenant_id) match by tenant id or slug, never cross-tenant."""
        a, b = world["a"], world["b"]
        db = app.db
        vid = _vid(a.tenant)
        stored = str(a.tenant) if tenant_value == "id" else a.slug
        async with app.app_context():
            rid = db.geo_regions.insert(
                tenant_id=stored,
                code=f"cov-{_sfx()}",
                name="Cov Region",
                is_active=True,
                village_id=vid,
            )
            db.commit()
            own = reg.resolve_by_village_id(db, vid, a.tenant)
            cross = reg.resolve_by_village_id(db, vid, b.tenant)
            ref_own = reg.resolve_ref(
                db,
                _rtype("geo_regions").module,
                _rtype("geo_regions").type,
                rid,
                a.tenant,
            )
            ref_cross = reg.resolve_ref(
                db,
                _rtype("geo_regions").module,
                _rtype("geo_regions").type,
                rid,
                b.tenant,
            )
        assert (own["table_name"], own["id"], own["title"]) == (
            "geo_regions",
            rid,
            "Cov Region",
        )
        assert cross is None
        assert ref_own["title"] == "Cov Region"
        assert ref_cross is None

    def _job(self, db, vid, config):
        """Insert a discovery job with the given JSON config; return its id."""
        now = datetime.now(UTC)
        jid = db.discovery_jobs.insert(
            name=f"cov-job-{_sfx()}",
            provider="aws",
            config_json=config,
            schedule_interval=3600,
            enabled=True,
            village_id=vid,
            created_at=now,
            updated_at=now,
        )
        db.commit()
        return jid

    @pytest.mark.asyncio
    async def test_discovery_job_scoped_by_config_org(self, app, world):
        """Discovery jobs resolve only for the tenant owning the configured org."""
        a, b = world["a"], world["b"]
        db = app.db
        vid = _vid(a.tenant)
        rtype = _rtype("discovery_jobs")
        async with app.app_context():
            jid = self._job(db, vid, {"_organization_id": a.org})
            own = reg.resolve_by_village_id(db, vid, a.tenant)
            cross = reg.resolve_by_village_id(db, vid, b.tenant)
            ref_own = reg.resolve_ref(db, rtype.module, rtype.type, str(jid), a.tenant)
            ref_cross = reg.resolve_ref(db, rtype.module, rtype.type, jid, b.tenant)
            ref_missing = reg.resolve_ref(
                db, rtype.module, rtype.type, 2_000_000_300, a.tenant
            )
        assert (own["table_name"], own["id"]) == ("discovery_jobs", jid)
        assert cross is None
        assert ref_own["id"] == str(jid)
        assert ref_cross is None and ref_missing is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("config", [{}, {"_organization_id": "not-an-int"}])
    async def test_discovery_job_without_usable_org_is_unresolvable(
        self, app, world, config
    ):
        """Jobs lacking a valid owning org cannot be attributed to any tenant."""
        a = world["a"]
        db = app.db
        vid = _vid(a.tenant)
        rtype = _rtype("discovery_jobs")
        async with app.app_context():
            jid = self._job(db, vid, config)
            assert reg.resolve_by_village_id(db, vid, a.tenant) is None
            assert reg.resolve_ref(db, rtype.module, rtype.type, jid, a.tenant) is None

    @pytest.mark.asyncio
    async def test_tenant_without_orgs_resolves_no_org_scoped_rows(self, app, world):
        """A tenant with no organizations never resolves org-scoped resources."""
        a = world["a"]
        db = app.db
        vid = _vid(a.tenant)
        rtype = _rtype("discovery_jobs")
        async with app.app_context():
            sfx = _sfx()
            empty = db.tenants.insert(
                name=f"cov-e-{sfx}", slug=f"cov-e-{sfx}", is_active=True
            )
            db.commit()
            jid = self._job(db, vid, {"_organization_id": a.org})
            assert reg.resolve_by_village_id(db, vid, empty) is None
            assert reg.resolve_ref(db, rtype.module, rtype.type, jid, empty) is None

    def _sync_config(self, db, vid, config):
        """Insert a sync config with the given JSON config; return its id."""
        now = datetime.now(UTC)
        sid = db.sync_configs.insert(
            name=f"cov-sync-{_sfx()}",
            platform="github",
            enabled=True,
            sync_interval=300,
            batch_fallback_enabled=False,
            batch_size=10,
            two_way_create=False,
            webhook_enabled=False,
            config_json=config,
            village_id=vid,
            created_at=now,
            updated_at=now,
        )
        db.commit()
        return sid

    @pytest.mark.asyncio
    @pytest.mark.parametrize("key", ["elder_organization_id", "organization_id"])
    async def test_sync_config_scoped_by_config_org(self, app, world, key):
        """Sync configs with an org in their config follow that org's tenant."""
        a, b = world["a"], world["b"]
        db = app.db
        # Prefix deliberately encodes tenant B: the config org must win.
        vid = _vid(b.tenant)
        async with app.app_context():
            sid = self._sync_config(db, vid, {key: a.org})
            own = reg.resolve_by_village_id(db, vid, a.tenant)
            cross = reg.resolve_by_village_id(db, vid, b.tenant)
        assert (own["table_name"], own["id"]) == ("sync_configs", sid)
        assert cross is None

    @pytest.mark.asyncio
    async def test_sync_config_prefix_fallback_and_bad_org(self, app, world):
        """Without a config org the village-id prefix decides; a bad org value denies."""
        a, b = world["a"], world["b"]
        db = app.db
        vid_prefix, vid_bad = _vid(a.tenant), _vid(a.tenant)
        async with app.app_context():
            sid = self._sync_config(db, vid_prefix, None)
            self._sync_config(db, vid_bad, {"organization_id": "abc"})
            own = reg.resolve_by_village_id(db, vid_prefix, a.tenant)
            cross = reg.resolve_by_village_id(db, vid_prefix, b.tenant)
            bad = reg.resolve_by_village_id(db, vid_bad, a.tenant)
        assert own["id"] == sid
        assert cross is None
        assert bad is None

    @pytest.mark.asyncio
    async def test_registry_hit_resolves_exact_table(self, app, world):
        """With the central registry available only the registered table is consulted."""
        a, b = world["a"], world["b"]
        lookup = "apps.api.common.refs.registry._lookup_via_village_ids_registry"
        async with app.app_context():
            with patch(lookup, return_value=(("organizations", a.org), True)):
                hit = reg.resolve_by_village_id(app.db, a.org_vid, a.tenant)
                cross = reg.resolve_by_village_id(app.db, a.org_vid, b.tenant)
            with patch(lookup, return_value=(("organizations", a.org + 1), True)):
                wrong_pk = reg.resolve_by_village_id(app.db, a.org_vid, a.tenant)
            with patch(lookup, return_value=((f"unregistered_{_sfx()}", 1), True)):
                unknown_table = reg.resolve_by_village_id(app.db, a.org_vid, a.tenant)
            with patch(lookup, return_value=(None, True)):
                absent = reg.resolve_by_village_id(app.db, a.org_vid, a.tenant)
        assert hit["id"] == a.org
        assert cross is None
        assert wrong_pk is None
        assert unknown_table is None
        assert absent is None


class TestResolveRowInTypeFallbacks:
    """_resolve_row_in_type branches for tables the real schema cannot provide."""

    def test_missing_table_or_village_column(self, app):
        """Types whose table or village_id column is absent resolve to None."""
        missing_table = reg.ResolvableType(type="x", module="m", table=f"nope_{_sfx()}")
        no_column = reg.ResolvableType(
            type="x", module="m", table="tenants", village_id_column=f"nocol_{_sfx()}"
        )
        for resolvable in (missing_table, no_column):
            assert (
                reg._resolve_row_in_type(app.db, resolvable, _vid(1), 1, set, list)
                is None
            )

    def _fake(self, columns, row):
        """Fake db+table exposing only ``columns``; every query returns ``row``."""

        class Col:
            def __eq__(self, other):
                return Cond()

            def belongs(self, values):
                return Cond()

        class Cond:
            def __and__(self, other):
                return self

        class Table:
            def __getattr__(self, name):
                if name in columns:
                    return Col()
                raise AttributeError(name)

        class Db:
            queries = 0

            def __getattr__(self, name):
                return Table()

            def __call__(self, query):
                Db.queries += 1
                return SimpleNamespace(
                    select=lambda: SimpleNamespace(first=lambda: row)
                )

        return Db()

    def test_org_column_only_table(self):
        """A table with only organization_id resolves when the tenant has orgs."""
        row = SimpleNamespace(id=9, name="org scoped")
        resolvable = reg.ResolvableType(type="thing", module="m", table="things")
        db = self._fake({"village_id", "id", "organization_id"}, row)
        hit = reg._resolve_row_in_type(db, resolvable, _vid(1), 1, lambda: {4}, list)
        none = reg._resolve_row_in_type(db, resolvable, _vid(1), 1, set, list)
        assert hit == {
            "type": "thing",
            "id": 9,
            "table_name": "things",
            "title": "org scoped",
            "module": "m",
        }
        assert none is None

    def test_legacy_null_tenant_row_found_via_org(self):
        """A tenant-column miss falls back to NULL-tenant rows in the tenant's orgs."""
        resolvable = reg.ResolvableType(type="thing", module="m", table="things")
        results = iter([None, SimpleNamespace(id=3, name="legacy")])

        class Cond:
            def __and__(self, other):
                return self

        class Col:
            def __eq__(self, other):
                return Cond()

            def belongs(self, values):
                return Cond()

        table = SimpleNamespace(
            village_id=Col(), id=Col(), tenant_id=Col(), organization_id=Col()
        )

        class Db:
            things = table

            def __call__(self, query):
                return SimpleNamespace(
                    select=lambda: SimpleNamespace(first=lambda: next(results))
                )

        out = reg._resolve_row_in_type(Db(), resolvable, _vid(1), 1, lambda: {2}, list)
        assert (out["id"], out["title"]) == (3, "legacy")

    def test_unscopable_table_is_never_resolved(self):
        """A table with no tenancy signal at all is refused without a query."""
        resolvable = reg.ResolvableType(type="thing", module="m", table="things")
        db = self._fake({"village_id", "id"}, SimpleNamespace(id=1, name="leak"))
        assert reg._resolve_row_in_type(db, resolvable, _vid(1), 1, set, list) is None
        assert type(db).queries == 0

    @pytest.mark.parametrize("tenant, expect_hit", [(7, True), (8, False)])
    def test_vid_prefix_scoped_table(self, tenant, expect_hit):
        """Prefix-scoped tables resolve only when the id embeds the caller's tenant."""
        resolvable = reg.ResolvableType(type="label", module="m", table="issue_labels")
        db = self._fake({"village_id", "id"}, SimpleNamespace(id=5, name="bug"))
        out = reg._resolve_row_in_type(db, resolvable, _vid(7), tenant, set, list)
        assert (out is not None) is expect_hit


class TestResolveRef:
    """resolve_ref scoping against the real database."""

    @pytest.mark.asyncio
    async def test_unknown_type_wrong_module_and_bad_id(self, app, world):
        """Unknown types, module mismatches and non-numeric ids resolve to None."""
        a = world["a"]
        org = _rtype("organizations")
        async with app.app_context():
            assert reg.resolve_ref(app.db, "m", f"nope_{_sfx()}", 1, a.tenant) is None
            assert (
                reg.resolve_ref(app.db, "wrong-module", org.type, a.org, a.tenant)
                is None
            )
            assert (
                reg.resolve_ref(app.db, org.module, org.type, "1; DROP", a.tenant)
                is None
            )

    @pytest.mark.asyncio
    async def test_tenant_column_isolation(self, app, world):
        """An org reference resolves for its tenant and is denied cross-tenant."""
        a, b = world["a"], world["b"]
        org = _rtype("organizations")
        async with app.app_context():
            own = reg.resolve_ref(app.db, org.module, org.type, str(a.org), a.tenant)
            cross = reg.resolve_ref(app.db, org.module, org.type, a.org, b.tenant)
        assert own == {
            "id": str(a.org),
            "title": a.org_name,
            "type": org.type,
            "module": org.module,
        }
        assert cross is None

    @pytest.mark.asyncio
    async def test_tenant_type_only_self(self, app, world):
        """A tenant reference resolves only for that same tenant."""
        a, b = world["a"], world["b"]
        ten = _rtype("tenants")
        async with app.app_context():
            own = reg.resolve_ref(app.db, ten.module, ten.type, a.tenant, a.tenant)
            cross = reg.resolve_ref(app.db, ten.module, ten.type, a.tenant, b.tenant)
        assert own["id"] == a.tenant
        assert cross is None

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        strict=True,
        reason="bug: registry.py:1162 resolve_ref with tenant_id=None runs an unscoped "
        "query on tenant-owned tables (cross-tenant read); should fail closed like "
        "resolve_by_village_id (gh-237)",
    )
    async def test_missing_tenant_fails_closed(self, app, world):
        """Without a tenant no tenant-owned row may be resolved."""
        org = _rtype("organizations")
        async with app.app_context():
            assert (
                reg.resolve_ref(app.db, org.module, org.type, world["a"].org, None)
                is None
            )

    def test_missing_table_and_id_column(self, app):
        """Types pointing at a missing table or id column resolve to None."""
        name = f"covtype_{_sfx()}"
        with patch.dict(
            reg._REGISTRY,
            {
                name: reg.ResolvableType(type=name, module="m", table=f"nope_{_sfx()}"),
                name + "c": reg.ResolvableType(
                    type=name + "c",
                    module="m",
                    table="tenants",
                    id_column=f"nocol_{_sfx()}",
                ),
            },
        ):
            assert reg.resolve_ref(app.db, "m", name, 1, 1) is None
            assert reg.resolve_ref(app.db, "m", name + "c", 1, 1) is None

    def test_unscopable_table_denied_with_and_without_tenant(self):
        """A table without tenant/org columns is never resolved by numeric id."""
        name = f"covtype_{_sfx()}"

        class Table:
            id = SimpleNamespace()

            def __getattr__(self, attr):
                raise AttributeError(attr)

        db = MagicMock()
        db.unscoped_things = Table()
        with patch.dict(
            reg._REGISTRY,
            {name: reg.ResolvableType(type=name, module="m", table="unscoped_things")},
        ):
            assert reg.resolve_ref(db, "m", name, 1, 5) is None
            assert reg.resolve_ref(db, "m", name, 1, None) is None
        db.assert_not_called()

    def test_org_column_only_table(self):
        """Org-scoped tables resolve through the tenant's organizations only."""
        name = f"covtype_{_sfx()}"

        class Cond:
            def __and__(self, other):
                return self

        class Col:
            def __eq__(self, other):
                return Cond()

            def belongs(self, values):
                return Cond()

        class Table:
            id = Col()
            organization_id = Col()

            def __getattr__(self, attr):
                raise AttributeError(attr)

        class Db:
            org_things = Table()

            def __call__(self, query):
                return SimpleNamespace(
                    select=lambda: SimpleNamespace(
                        first=lambda: SimpleNamespace(id=2, name="scoped")
                    )
                )

        resolvable = reg.ResolvableType(type=name, module="m", table="org_things")
        with patch.dict(reg._REGISTRY, {name: resolvable}):
            with patch.object(reg, "_get_tenant_org_ids", return_value={8}):
                hit = reg.resolve_ref(Db(), "m", name, 2, 5)
            with patch.object(reg, "_get_tenant_org_ids", return_value=set()):
                no_orgs = reg.resolve_ref(Db(), "m", name, 2, 5)
        assert hit == {"id": 2, "title": "scoped", "type": name, "module": "m"}
        assert no_orgs is None


class TestFormatResolvableUrl:
    """format_resolvable_url for plain and playbook-nested patterns."""

    def test_plain_pattern(self):
        """A pattern without playbook placeholder needs no DB access."""
        resolvable = reg.ResolvableType(
            type="t", module="m", table="x", url_pattern="/things/{id}"
        )
        assert reg.format_resolvable_url(None, resolvable, 12) == "/things/12"

    def _db(self, row, columns=("id", "tenant_id")):
        """Fake DAL returning ``row`` for any query on a table with ``columns``."""

        class Cond:
            def __and__(self, other):
                self.scoped = True
                return self

        class Col:
            def __eq__(self, other):
                return Cond()

        class Table:
            def __getattr__(self, name):
                if name in columns:
                    return Col()
                raise AttributeError(name)

        class Db:
            seen = None
            runs = Table()

            def __call__(self, query):
                Db.seen = query
                return SimpleNamespace(
                    select=lambda: SimpleNamespace(first=lambda: row)
                )

        return Db()

    def test_playbook_pattern_branches(self):
        """Playbook URLs use the row's playbook id, else fall back to /streams."""
        resolvable = reg.ResolvableType(
            type="run",
            module="streams",
            table="runs",
            url_pattern="/streams/{playbook_id}/runs/{id}",
        )
        found = self._db(SimpleNamespace(playbook_id=4))
        assert (
            reg.format_resolvable_url(found, resolvable, 9, tenant_id=3)
            == "/streams/4/runs/9"
        )
        assert getattr(type(found).seen, "scoped", False) is True
        unscoped = self._db(SimpleNamespace(playbook_id=4))
        assert reg.format_resolvable_url(unscoped, resolvable, 9) == "/streams/4/runs/9"
        assert getattr(type(unscoped).seen, "scoped", False) is False
        for db in (
            None,
            self._db(None),
            self._db(SimpleNamespace(playbook_id=None)),
            self._db(SimpleNamespace(playbook_id=4), columns=()),
        ):
            assert (
                reg.format_resolvable_url(db, resolvable, 9, tenant_id=3) == "/streams"
            )
